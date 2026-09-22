# -*- coding: utf-8 -*-
"""Optional on-disk cache of compiled template code, shared between processes.

The process-local cache (``_cache.py``) only helps the second render inside
the same interpreter. An application that serves renders from many
short-lived worker processes pays Jinja2's lex/parse/compile of a multi-MB
source on nearly every request. This layer keeps the ``marshal``-serialised
code object on disk under a key made of the template source digest and a
*stable* environment fingerprint: names, modules and code of custom filters
and tests instead of function identities, qualified by the Python, Jinja2 and
marshal versions.

It is off unless ``DOCXTPL_CODE_CACHE_DIR`` names a directory (or
:func:`configure` is called). Like ``jinja2.FileSystemBytecodeCache`` this
executes code loaded from that directory, so only the application may write
there. Unreadable or corrupt entries count as misses and are deleted. Nothing
stored here can contain a render context: a code object holds the template
text as constants and nothing else.
"""
import hashlib
import importlib.util
import marshal
import os
import sys
import tempfile
import threading
import types

import jinja2

from . import _stats

_ENV_DIR = "DOCXTPL_CODE_CACHE_DIR"
_ENV_MAX_ENTRIES = "DOCXTPL_CODE_CACHE_MAX_ENTRIES"
DEFAULT_MAX_ENTRIES = 512
_PRUNE_EVERY = 32
_FORMAT = 1
_SUFFIX = ".jinja-code"

_lock = threading.Lock()
_configured = False
_directory = None
_max_entries = DEFAULT_MAX_ENTRIES
_puts = 0


def _read_environment():
    global _configured, _directory, _max_entries
    directory = os.environ.get(_ENV_DIR, "").strip()
    _directory = directory or None
    try:
        _max_entries = max(1, int(os.environ.get(_ENV_MAX_ENTRIES, DEFAULT_MAX_ENTRIES)))
    except ValueError:
        _max_entries = DEFAULT_MAX_ENTRIES
    _configured = True


def configure(directory=None, max_entries=None):
    """Enable the cache in ``directory`` (``None`` disables it)."""
    global _configured, _directory, _max_entries
    with _lock:
        _directory = str(directory) if directory else None
        if max_entries is not None:
            _max_entries = max(1, int(max_entries))
        _configured = True


def directory():
    if not _configured:
        with _lock:
            if not _configured:
                _read_environment()
    return _directory


def info():
    path = directory()
    entries = 0
    if path and os.path.isdir(path):
        entries = sum(1 for name in os.listdir(path) if name.endswith(_SUFFIX))
    return {"directory": path, "entries": entries, "max_entries": _max_entries}


# -- keys ---------------------------------------------------------------------

_RUNTIME = "|".join((
    str(_FORMAT),
    sys.implementation.cache_tag or sys.version,
    importlib.util.MAGIC_NUMBER.hex(),
    str(marshal.version),
    jinja2.__version__,
))


def _describe_callable(function):
    """Identity-free description: what the function is, not where it lives in memory."""
    code = getattr(function, "__code__", None)
    if isinstance(code, types.CodeType):
        try:
            body = hashlib.sha256(marshal.dumps(code)).hexdigest()
        except ValueError:  # unmarshallable constant
            return None
    else:
        body = repr(function)  # builtins repr without an address
    return "%s:%s:%s" % (getattr(function, "__module__", None), getattr(function, "__qualname__", None), body)


def _stable(value):
    """Deterministic text for a compile fingerprint; ``None`` if any part is not describable."""
    if value is None or isinstance(value, (str, int, float, bool, bytes)):
        return repr(value)
    if isinstance(value, (tuple, list)):
        parts = [_stable(item) for item in value]
        return None if None in parts else "(" + ",".join(parts) + ")"
    if isinstance(value, (frozenset, set)):
        parts = [_stable(item) for item in value]
        return None if None in parts else "{" + ",".join(sorted(parts)) + "}"
    if isinstance(value, type):
        return "class %s.%s" % (value.__module__, value.__qualname__)
    if callable(value):
        return _describe_callable(value)
    return None


def key_for(source_digest, fingerprint):
    """Hex key, or ``None`` when the environment cannot be described stably."""
    described = _stable(fingerprint)
    if described is None:
        return None
    hasher = hashlib.sha256()
    hasher.update(_RUNTIME.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(source_digest)
    hasher.update(b"\0")
    hasher.update(described.encode("utf-8"))
    return hasher.hexdigest()


# -- storage ------------------------------------------------------------------

def _path(path, key):
    return os.path.join(path, key + _SUFFIX)


def get(source_digest, fingerprint):
    path = directory()
    if path is None:
        return None
    key = key_for(source_digest, fingerprint)
    if key is None:
        return None
    file_name = _path(path, key)
    try:
        with open(file_name, "rb") as handle:
            code = marshal.load(handle)
        if not isinstance(code, types.CodeType):
            raise ValueError("not a code object")
    except FileNotFoundError:
        _stats.count("code_cache_miss")
        return None
    except Exception:
        _stats.count("code_cache_error")
        try:
            os.remove(file_name)
        except OSError:
            pass
        return None
    _stats.count("code_cache_hit")
    return code


def put(source_digest, fingerprint, code):
    global _puts
    path = directory()
    if path is None:
        return False
    key = key_for(source_digest, fingerprint)
    if key is None:
        return False
    try:
        payload = marshal.dumps(code)
        os.makedirs(path, exist_ok=True)
        # Written next to the target and renamed: readers only ever see whole files.
        fd, temporary = tempfile.mkstemp(prefix=".tmp-", suffix=_SUFFIX, dir=path)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
            os.replace(temporary, _path(path, key))
        except BaseException:
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise
    except Exception:
        _stats.count("code_cache_error")
        return False
    _stats.count("code_cache_store")
    with _lock:
        _puts += 1
        prune = _puts % _PRUNE_EVERY == 0
    if prune:
        _prune(path)
    return True


def _prune(path):
    """Drop the least recently modified entries beyond ``max_entries``."""
    try:
        entries = []
        with os.scandir(path) as listing:
            for entry in listing:
                if entry.name.endswith(_SUFFIX) and not entry.name.startswith(".tmp-"):
                    entries.append((entry.stat().st_mtime, entry.path))
        entries.sort()
        for _, stale in entries[: max(0, len(entries) - _max_entries)]:
            try:
                os.remove(stale)
            except OSError:
                pass
    except OSError:
        pass
