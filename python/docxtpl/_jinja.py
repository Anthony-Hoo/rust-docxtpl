# -*- coding: utf-8 -*-
"""What may be reused between Jinja2 environments, and when.

docxtpl receives arbitrary, mutable ``jinja2.Environment`` objects, and the
application typically builds a new one per render. Results are therefore
never remembered per environment object. Instead each function below derives
a *fingerprint*: a hashable value covering every environment property that
can influence the result. ``None`` means "cannot be established" and the
caller must take the plain Jinja2 path.
"""
import hashlib
from types import BuiltinFunctionType, FunctionType

from jinja2 import Environment, Template, nodes
from jinja2.compiler import CodeGenerator
from jinja2.defaults import DEFAULT_FILTERS, DEFAULT_TESTS

from . import _stats
from ._cache import cache, str_nbytes


def _lexer_settings(env):
    return (
        env.block_start_string,
        env.block_end_string,
        env.variable_start_string,
        env.variable_end_string,
        env.comment_start_string,
        env.comment_end_string,
        env.line_statement_prefix,
        env.line_comment_prefix,
        env.trim_blocks,
        env.lstrip_blocks,
        env.newline_sequence,
        env.keep_trailing_newline,
    )


_HOOKS = frozenset((
    "from_string", "compile", "parse", "_parse", "_generate", "_compile", "_tokenize",
    "preprocess", "lex", "make_globals", "handle_exception", "get_template",
))


def _is_plain(env):
    # Subclasses (or methods patched onto the instance) may change parsing or
    # compilation; extensions may rewrite the token stream or the AST.
    return type(env) is Environment and not env.extensions and _HOOKS.isdisjoint(vars(env))


def parse_fingerprint(env):
    """Covers ``find_undeclared_variables(env.parse(source))``.

    Besides the lexer settings, compiling the AST consults only the *names*
    of registered filters and tests (unknown ones raise) and ``is_async``.
    """
    if not _is_plain(env):
        return None
    return _lexer_settings(env) + (env.is_async, frozenset(env.filters), frozenset(env.tests))


def _stateless(registry, defaults):
    """``(name, callable)`` pairs, or ``None`` if a callable might capture
    per-request state (closure, bound method, partial, callable object):
    such objects must neither be kept alive by a cache key nor be assumed
    equivalent between environments."""
    items = []
    for name, function in registry.items():
        if function is not defaults.get(name):
            if type(function) is FunctionType:
                if function.__closure__ is not None:
                    return None
            elif type(function) is not BuiltinFunctionType:
                return None
        items.append((name, function))
    return frozenset(items)


def compile_fingerprint(env):
    """Covers the code object produced by ``env.compile(source)``.

    Code generation depends on the lexer settings, ``autoescape``,
    ``optimized``, ``is_async``, ``finalize`` and, through constant folding
    and call conventions, on the identity of every filter and test, on
    ``policies`` and on ``undefined``. Globals are resolved at run time.
    """
    if (
        not _is_plain(env)
        or env.template_class is not Template
        or env.code_generator_class is not CodeGenerator
        or env.finalize is not None
        or not isinstance(env.autoescape, bool)
    ):
        return None
    filters = _stateless(env.filters, DEFAULT_FILTERS)
    tests = _stateless(env.tests, DEFAULT_TESTS)
    if filters is None or tests is None:
        return None
    # Policy values are plain data (the defaults include a dict), so their
    # repr is a faithful, hashable stand-in.
    policies = tuple(sorted((str(name), repr(value)) for name, value in env.policies.items()))
    return _lexer_settings(env) + (
        env.autoescape, env.optimized, env.is_async, env.undefined, filters, tests, policies,
    )


def _folds_custom_callable(env, ast):
    """True if the optimizer could evaluate a user-supplied filter or test at
    compile time (all of its inputs are constants). Upstream re-evaluates it
    on every render, so such a template must be recompiled every time."""
    for node in ast.find_all((nodes.Filter, nodes.Test)):
        registry, defaults = (
            (env.filters, DEFAULT_FILTERS) if isinstance(node, nodes.Filter) else (env.tests, DEFAULT_TESTS)
        )
        function = registry.get(node.name)
        if function is not None and function is not defaults.get(node.name):
            if next(node.find_all(nodes.Name), None) is None:
                return True
    return False


_UNCACHEABLE = object()


def template_from_string(env, source):
    """``env.from_string(source)`` with the compiled code shared between
    equivalent environments. Any irregularity (including template errors)
    is delegated to Jinja2 itself so that exceptions stay canonical."""
    if cache.enabled:
        fingerprint = compile_fingerprint(env)
        if fingerprint is not None:
            try:
                digest = hashlib.sha256(source.encode("utf-8")).digest()
            except (AttributeError, UnicodeEncodeError):
                digest = None
            if digest is not None:
                key = ("code", digest, fingerprint)
                code = cache.get(key)
                if code is None:
                    code = _compile(env, source, key)
                if code is not _UNCACHEABLE and code is not None:
                    _stats.count("jinja_compile_reused")
                    return env.template_class.from_code(env, code, env.make_globals(None), None)
    _stats.count("jinja_compile_plain")
    return env.from_string(source)


def _compile(env, source, key):
    try:
        ast = env.parse(source)
        has_custom = any(
            function is not DEFAULT_FILTERS.get(name) for name, function in env.filters.items()
        ) or any(function is not DEFAULT_TESTS.get(name) for name, function in env.tests.items())
        if has_custom and _folds_custom_callable(env, ast):
            cache.put(key, _UNCACHEABLE, 64)
            return _UNCACHEABLE
        code = env.compile(ast)
    except Exception:
        return None  # let env.from_string() raise the canonical error
    # The code object's constants hold the template text once more.
    cache.put(key, code, str_nbytes(source) + 4096)
    return code
