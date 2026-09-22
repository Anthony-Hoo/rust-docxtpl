# -*- coding: utf-8 -*-
"""Bounded, process-local cache of results derived from *template text only*.

Nothing stored here can contain a render context, an image, a rendered
document or the result of a user callback:

* ``("variables", sha256(template file), parse fingerprint)`` ->
  undeclared variable names;
* ``("source", sha256(template file))`` -> patched XML handed to
  ``env.parse`` (only for environments without a usable fingerprint);
* ``("code", sha256(jinja source), compile fingerprint)`` -> compiled
  template code object (see ``_jinja.py``).

Keys are content digests, never paths, mtimes or ``id()`` values. One LRU
with a byte budget (``DOCXTPL_CACHE_BYTES``, default 32 MiB per process, ``0``
disables every layer) bounds the total; an entry larger than the budget is
not stored.
"""
from collections import OrderedDict
import os
import threading

from . import _stats

DEFAULT_BUDGET = 32 * 1024 * 1024


def _budget_from_env():
    try:
        return max(0, int(os.environ.get("DOCXTPL_CACHE_BYTES", DEFAULT_BUDGET)))
    except ValueError:
        return DEFAULT_BUDGET


def str_nbytes(text):
    """Memory of a CPython ``str`` payload: 1, 2 or 4 bytes per code point."""
    if text.isascii():
        return len(text)
    return len(text) * (2 if max(text) < "\U00010000" else 4)


class BudgetLRU(object):
    def __init__(self, budget=None):
        self._lock = threading.Lock()
        self._budget = _budget_from_env() if budget is None else budget
        self._entries = OrderedDict()  # key -> (value, nbytes)
        self._nbytes = 0

    @property
    def enabled(self):
        return self._budget > 0

    def set_budget(self, nbytes):
        with self._lock:
            self._budget = max(0, int(nbytes))
            self._evict()

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._nbytes = 0

    def info(self):
        with self._lock:
            layers = {}
            for key, (_, nbytes) in self._entries.items():
                entries, total = layers.get(key[0], (0, 0))
                layers[key[0]] = (entries + 1, total + nbytes)
            return {
                "budget_bytes": self._budget,
                "used_bytes": self._nbytes,
                "layers": {k: {"entries": e, "bytes": b} for k, (e, b) in layers.items()},
            }

    def get(self, key):
        with self._lock:
            item = self._entries.get(key)
            if item is not None:
                self._entries.move_to_end(key)
        _stats.count("cache_%s_%s" % (key[0], "miss" if item is None else "hit"))
        return None if item is None else item[0]

    def put(self, key, value, nbytes):
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._nbytes -= old[1]
            if nbytes > self._budget:
                return
            self._entries[key] = (value, nbytes)
            self._nbytes += nbytes
            self._evict()

    def _evict(self):
        while self._entries and self._nbytes > self._budget:
            _, (_, nbytes) = self._entries.popitem(last=False)
            self._nbytes -= nbytes


cache = BudgetLRU()
