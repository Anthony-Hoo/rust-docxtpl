# Changelog

Versions of `rust-docxtpl` are independent of docxtpl. The compatibility
baseline is recorded per release; `docxtpl.__version__` keeps reporting it.

## 0.1.0 (2026-09-22) — baseline docxtpl 0.20.1

Same public API, same output. Differences in mechanism:

- `docxtpl.__all__` declares the public names: the package ships `py.typed`,
  and without it pyright reports `from docxtpl import DocxTemplate` as a
  private import (found when type-checking the real application).
- `patch_xml`, the text steps of `render_xml_part` and `resolve_listing` run
  in Rust (`docxtpl._native`), with the GIL released. Inputs the native path
  declines (lone surrogates, backslash in a `colspan`/`cellbg` expression,
  non-ASCII digits in a `gridSpan` next to `{% hm %}`) use the unchanged
  Python implementation.
- `render()` no longer unlinks the old `<w:body>` (quadratic inside lxml);
  it installs the rendered body on a twin of the root element. Falls back to
  upstream's `root.replace()` when the root element is referenced elsewhere.
- `get_undeclared_template_variables()` caches per template *content* and
  Jinja2 environment *fingerprint*.
- Compiled Jinja2 code is shared between equivalent environments.
- New, additive API: `docxtpl.stats()`, `reset_stats()`, `enable_timings()`,
  `cache_info()`, `cache_clear()`, `set_cache_budget()`.
- Opt-in `docxtpl.accel.enable()` / `DOCXTPL_ACCEL=1`: compiled-XPath cache for
  python-docx elements (off by default; nothing is patched unless requested).
- `DOCXTPL_NATIVE=0` runs the same algorithms with the Python reference kernels
  (benchmark control group).
- `render()` raises `RuntimeError` when re-entered on the same instance
  (upstream: undefined behaviour).
- Importing fails with a clear `ImportError` if the original `docxtpl`
  distribution is installed in the same environment.
