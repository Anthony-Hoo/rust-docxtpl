# -*- coding: utf-8 -*-
"""Execution-path counters and optional stage timings.

Counters are always on (a handful of increments per render). Wall-clock stage
timings are recorded only after :func:`enable_timings` or when the environment
variable ``DOCXTPL_TIMINGS=1`` is set. Nothing here ever records template
content or context data.
"""
from contextlib import contextmanager
import os
import threading
import time

_lock = threading.Lock()
_counters = {}
_timings = {}
_timings_enabled = os.environ.get("DOCXTPL_TIMINGS", "") not in ("", "0")


def count(name, amount=1):
    with _lock:
        _counters[name] = _counters.get(name, 0) + amount


def enable_timings(enabled=True):
    global _timings_enabled
    _timings_enabled = bool(enabled)


@contextmanager
def stage(name):
    """Accumulate wall-clock seconds of a stage (no-op unless enabled)."""
    if not _timings_enabled:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        with _lock:
            calls, total = _timings.get(name, (0, 0.0))
            _timings[name] = (calls + 1, total + elapsed)


def stats():
    """Snapshot: ``{"counters": {...}, "timings": {stage: {"calls", "seconds"}}}``."""
    with _lock:
        return {
            "counters": dict(_counters),
            "timings": {k: {"calls": c, "seconds": s} for k, (c, s) in _timings.items()},
        }


def reset_stats():
    with _lock:
        _counters.clear()
        _timings.clear()
