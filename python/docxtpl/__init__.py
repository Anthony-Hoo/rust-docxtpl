# -*- coding: utf-8 -*-
"""
Created : 2015-03-12

@author: Eric Lapouyade

rust-docxtpl: Rust-accelerated drop-in distribution of the ``docxtpl`` import
package, behaviour-compatible with docxtpl 0.20.1.
"""
__version__ = "0.20.1"
#: Version of the rust-docxtpl distribution (``__version__`` stays the
#: compatibility baseline so that version checks in applications keep working).
__rust_docxtpl_version__ = "0.1.0"

# flake8: noqa
from . import _install_check

_install_check.check()
del _install_check

from .inline_image import InlineImage
from .listing import Listing
from .richtext import RichText, R, RichTextParagraph, RP
from .subdoc import Subdoc
from .template import DocxTemplate
from ._stats import enable_timings, reset_stats, stats
from ._cache import cache as _cache


def cache_info():
    """Size and budget of the process-local template analysis cache."""
    return _cache.info()


def cache_clear():
    _cache.clear()


def set_cache_budget(nbytes):
    """Bound the analysis cache (bytes per process); ``0`` disables it."""
    _cache.set_budget(nbytes)


# The package ships ``py.typed``: without ``__all__`` type checkers treat the
# re-exports above as private ("DocxTemplate is not exported from docxtpl").
__all__ = [
    "DocxTemplate", "InlineImage", "Listing", "Subdoc",
    "RichText", "R", "RichTextParagraph", "RP",
    "stats", "reset_stats", "enable_timings",
    "cache_info", "cache_clear", "set_cache_budget", "accel",
]

# Opt-in only (DOCXTPL_ACCEL=1 or docxtpl.accel.enable()); see accel.py.
from . import accel
accel._enable_from_environment()
