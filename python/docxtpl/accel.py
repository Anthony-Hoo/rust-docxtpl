# -*- coding: utf-8 -*-
"""Optional, explicit speed-ups for libraries *around* docxtpl.

Nothing in this module is active unless the application (or its operator)
asks for it:

    import docxtpl.accel
    docxtpl.accel.enable()            # or: environment variable DOCXTPL_ACCEL=1

python-docx XPath cache
-----------------------
``BaseOxmlElement.xpath(expr)`` calls lxml's ``_Element.xpath``, which builds
and tears down an XPath evaluator (namespace + function registration) on
every call. python-docx and docxcompose issue tens of thousands of such
calls with a few dozen distinct expressions (``Paragraph.text``, ``runs``,
``_Row.cells``, style and numbering look-ups). ``enable()`` replaces that one
method with an equivalent that evaluates a cached, compiled ``etree.XPath``.

Equivalence: same expression, same namespace map (the cache key includes a
snapshot of ``docx.oxml.ns.nsmap``, so later registrations are honoured),
same defaults (``regexp``, ``smart_strings``). Anything irregular, including
XPath errors, is re-run through the original method so exceptions are the
ones python-docx users already see. Compiled expressions are thread-safe.

This is the only patch offered: caching ``qn()`` was measured too and gains
nothing. python-docx itself is not replaced, wrapped or re-implemented.
"""
import functools
import os

from docx.oxml import ns as _ns
from docx.oxml.xmlchemy import BaseOxmlElement
from lxml import etree

from . import _stats

_original_xpath = BaseOxmlElement.xpath


@functools.lru_cache(maxsize=1024)
def _compiled(expression, namespaces):
    return etree.XPath(expression, namespaces=dict(namespaces))


def _cached_xpath(self, xpath_str):
    try:
        return _compiled(xpath_str, tuple(_ns.nsmap.items()))(self)
    except Exception:
        return _original_xpath(self, xpath_str)


_cached_xpath.__doc__ = _original_xpath.__doc__
_cached_xpath.__name__ = "xpath"


def enable():
    """Idempotent. Process-wide: affects every python-docx element."""
    if BaseOxmlElement.xpath is not _cached_xpath:
        BaseOxmlElement.xpath = _cached_xpath
        _stats.count("accel_enabled")


def disable():
    if BaseOxmlElement.xpath is _cached_xpath:
        BaseOxmlElement.xpath = _original_xpath
    _compiled.cache_clear()


def is_enabled():
    return BaseOxmlElement.xpath is _cached_xpath


def _enable_from_environment():
    if os.environ.get("DOCXTPL_ACCEL", "") not in ("", "0"):
        enable()
