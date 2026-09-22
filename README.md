# rust-docxtpl

[![CI](https://github.com/Anthony-Hoo/rust-docxtpl/actions/workflows/ci.yml/badge.svg)](https://github.com/Anthony-Hoo/rust-docxtpl/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/rust-docxtpl)](https://pypi.org/project/rust-docxtpl/)

English | [简体中文](README.zh-CN.md)

Rust-accelerated, drop-in distribution of the `docxtpl` import package.
Compatibility baseline: **docxtpl 0.20.1** (python-docx 1.2.0, Jinja2 3.1.x).

```diff
- docxtpl==0.20.1
+ rust-docxtpl
```

```bash
pip uninstall docxtpl && pip install rust-docxtpl
```

Wheels: CPython 3.10–3.14 on Linux (x86_64, aarch64; manylinux2014),
Windows (x86_64) and macOS (arm64, x86_64). Other platforms build from the
sdist with a Rust toolchain (`pip install rust-docxtpl` does that automatically
when `cargo` is on `PATH`).

Application code, templates and context data stay exactly as they are:

```python
from docxtpl import DocxTemplate, InlineImage, RichText

tpl = DocxTemplate(path)
tpl.get_undeclared_template_variables()
tpl.render(context, jinja_env)
tpl.save(output)
```

Jinja2 evaluation is still done by the real Jinja2 with your `Environment`
(filters, tests, globals, `Undefined`, autoescape, Python objects in the
context), and documents are still real python-docx / lxml objects that you can
modify before and after `render()`. Do **not** install this next to the
original `docxtpl` distribution: both own the `docxtpl` package, and importing
fails with an explanatory `ImportError` if they are mixed.

## What is faster, and why

Measured on the largest real-world template available to the authors (3.7 MB
`document.xml`, 141k elements); see [Benchmarks](#benchmarks).

| Upstream cost | Cause | What this package does |
|---|---|---|
| ~88 % of `render()` in `map_tree()` | `root.replace(body, tree)` makes lxml re-home every node of the *outgoing* body. lxml's namespace cache never hits for namespaces declared on the root (it stores `(new, new)` instead of `(old, new)`, `proxy.pxi:_fixCNs`), so the work is quadratic in the body size | The rendered body is attached to a twin of the root element and `Document._element` / `DocumentPart._element` are pointed at it. Nothing is unlinked; the old tree stays complete, as upstream leaves it |
| `patch_xml()`: ~20 backtracking regex passes over the whole XML, per part, per scan, per render | Python `re` with lookaround | Hand-written Rust scanners (`crates/core`), byte-exact, GIL released |
| Every `get_undeclared_template_variables()` reloads, patches and parses the template | no reuse | Result cached by SHA-256 of the template bytes + environment fingerprint |
| Jinja2 lexes, parses and compiles the multi-MB source on every render | `from_string` has no cache | Code object shared between *equivalent* environments |

## Compatibility contract

Output is compared with upstream using a strict OOXML comparator
(all parts, element order, attributes, namespace bindings, text, tails,
relationship ids, media bytes).

| API | Status |
|---|---|
| `DocxTemplate(path / PathLike / stream)`, `render`, `save`, `init_docx`, `get_docx`, `.docx`, attribute proxying | same code path as upstream |
| `patch_xml(str) -> str` | native; byte-identical (differential fuzzing + 35 real templates) |
| `get_undeclared_template_variables(jinja_env=None, context=None)` | cached; always analyses the template *file*, returns a fresh `set`, restores stream positions |
| `InlineImage` (subclassable, `_insert_image`, `_add_hyperlink`), `RichText`/`R`, `RichTextParagraph`/`RP`, `Listing`, `Subdoc`, `new_subdoc(path)` | upstream code, verbatim |
| `replace_pic/media/embedded/zipname`, `reset_replacements`, `build_url_id`, `python -m docxtpl` | upstream code, verbatim |
| Subclass overrides of `patch_xml`, `xml_to_string`, `resolve_listing`, `map_tree`, … | honoured; the affected cache / native shortcut is bypassed |

Deliberate differences:

1. After `render()`, `tpl.docx._element` is a new root element object unless
   some other object referenced the old one (then upstream's slow path runs
   and identity is kept). `Document`, `DocumentPart`, relationships and all
   other parts keep their identity. Objects obtained *before* `render()`
   (including python-docx's cached `document._body`) stay complete and stale,
   exactly as with upstream; their `getparent()` chain ends at the old root
   instead of at the old body.
2. Re-entering `render()` on one instance raises `RuntimeError`.
3. A custom filter that is *impure* and applied to *constants only* is still
   evaluated on every render (such templates are never served from the code
   cache); nothing to do for users.

### Caches

Process-local LRU, byte-budgeted (`DOCXTPL_CACHE_BYTES`, default 32 MiB,
`0` disables; or `docxtpl.set_cache_budget()`), keyed by content digests,
never by path, mtime or `id()`. It stores variable-name sets, patched
template XML and compiled code objects: never a context, image, rendered
document or callback result. Environments are fingerprinted on every call
(they are mutable); subclasses of `Environment`, extensions, a `finalize`
hook, callable `autoescape`, or filters/tests that are closures, bound
methods or callable objects disable the corresponding layer.

Compiled code can additionally be shared *between processes* through an
on-disk cache: set `DOCXTPL_CODE_CACHE_DIR=/path` (or call
`docxtpl.configure_code_cache(path, max_entries=512)`). A pre-fork server
whose workers are recycled otherwise lexes, parses and compiles the multi-MB
source on nearly every request. Entries are `marshal`ed code objects keyed by
the source digest plus a stable description of the environment (names and
code of custom filters/tests, `undefined`, policies, lexer settings) and the
Python/Jinja2/marshal versions; corrupt entries are dropped, the directory is
bounded by `DOCXTPL_CODE_CACHE_MAX_ENTRIES`. As with
`jinja2.FileSystemBytecodeCache`, code from that directory is executed, so
only the application may write there. Off by default.

### Observability

```python
import docxtpl
docxtpl.enable_timings()          # or DOCXTPL_TIMINGS=1
...
docxtpl.stats()
# {'counters': {'patch_xml_native': 19, 'patch_xml_reference': 0, 'map_tree_swap': 1,
#               'map_tree_replace': 0, 'jinja_compile_reused': 19, 'cache_code_hit': 19, ...},
#  'timings': {'render': {'calls': 1, 'seconds': 0.17}, ...}}
docxtpl.cache_info()
```

`*_reference` / `map_tree_replace` / `jinja_compile_plain` count executions of
the upstream-equivalent slow paths. No template text or context data is ever
recorded.

### Optional: python-docx XPath cache (`docxtpl.accel`)

Off by default. `docxtpl.accel.enable()` (or `DOCXTPL_ACCEL=1`) replaces one
method, `BaseOxmlElement.xpath`, with an equivalent that reuses compiled
expressions instead of building an lxml evaluator per call. Measured on the
largest report: reading all cell paragraph text 0.24 s -> 0.085 s; table
normalisation and docxcompose merges are unchanged (they are not XPath-bound).
Expected gain in the full export is a few percent, which is why it is opt-in
and why python-docx is **not** re-implemented: its objects *are* lxml
elements that applications and docxcompose manipulate directly, parsing /
XPath / serialisation already run in C, and a cached `qn()` measured no gain.

## Layout

```
crates/core   pure Rust kernels (no Python): patch.rs, render.rs, scan.rs
crates/py     PyO3 bindings -> docxtpl._native
python/docxtpl
  template.py      upstream DocxTemplate with the hot paths rerouted
  _reference.py    upstream regex code, verbatim: fallback + test oracle
  _jinja.py        environment fingerprints, compiled-code reuse
  _cache.py        byte-budgeted LRU       _stats.py  counters / timings
  accel.py         opt-in python-docx XPath cache
tests/        pytest: differential (fuzz + optional private corpus), facade behaviour
.github/      ci.yml (clippy, cargo test, pytest on 3 OSes), release.yml (wheels -> PyPI)
```

Each Rust pass quotes the regex it replaces and states the matching rule it
implements. When a rule is in doubt, `_reference.py` is the specification and
`tests/fuzz.py` is the judge:

```bash
python -m venv .venv && . .venv/bin/activate && pip install maturin pytest
maturin develop --release                      # builds docxtpl._native into .venv
cargo test && cargo clippy --all-targets -- -D warnings
pytest tests                                   # differential tests on a private
                                               # DOCX corpus are skipped when absent
python tests/fuzz.py 500000 7                  # grammar fuzzing against _reference.py
python tests/make_golden.py                    # after changing fuzz grammar / reference
```

## Benchmarks

The (private) benchmark harness alternates fresh processes of both
environments (AB/BA); iteration 1 of a process is *cold* (empty caches),
later ones *warm*.
Numbers below: 13th-gen Core i9 laptop, WSL2, CPython 3.12.14, lxml 5.3.1;
p50 of the library layer = two variable scans + `render()`. The templates are
private production documents (a 141k-element test report, six test-record
templates, a cover page) and are not part of this repository; the numbers
are indicative only.

A = `.venv-baseline`, B = `.venv-candidate`; 4 rounds x 3 iterations per process.

| case | library A p50 | B cold p50 | B warm p50 | cold B/A | warm B/A | warm p95 B/A | CPU warm B/A | peak RSS cold B/A |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| remote-report-tpl194 | 13.047 s | 0.829 s | 0.166 s | 0.063 | 0.013 | 0.013 | 0.020 | 0.77 |
| remote-records-tpl177 | 1.213 s | 0.190 s | 0.027 s | 0.156 | 0.022 | 0.023 | 0.050 | 0.74 |
| remote-records-tpl178 | 0.280 s | 0.075 s | 0.009 s | 0.265 | 0.032 | 0.034 | 0.066 | 0.95 |
| remote-records-tpl180 | 1.312 s | 0.188 s | 0.029 s | 0.143 | 0.022 | 0.027 | 0.047 | 0.82 |
| remote-records-tpl181 | 0.383 s | 0.076 s | 0.007 s | 0.192 | 0.019 | 0.019 | 0.037 | 0.81 |
| remote-records-tpl183 | 0.758 s | 0.128 s | 0.019 s | 0.168 | 0.026 | 0.029 | 0.046 | 0.88 |
| remote-records-tpl185 | 0.028 s | 0.013 s | 0.002 s | 0.419 | 0.078 | 0.082 | 0.456 | 0.89 |
| local-cover-tpl185 | 0.029 s | 0.013 s | 0.002 s | 0.411 | 0.073 | 0.077 | 0.446 | 0.89 |
| synthetic-object-protocol | 0.017 s | 0.014 s | 0.003 s | 0.665 | 0.149 | 0.126 | 0.469 | 1.00 |

Control group (PRD 8.2-9), report template, same facade and algorithms with the
Rust kernels switched off (`DOCXTPL_NATIVE=0`): library layer 2.63 s cold /
1.10 s warm, versus 0.83 s / 0.17 s with them: the Rust kernels remove 68 % /
85 % of what the Python-only optimisation leaves.


## Building wheels

`maturin build --release` produces a wheel for the local platform.
`.github/workflows/release.yml` builds the full wheel matrix plus the sdist
on every `v*` tag and publishes them to PyPI through
[trusted publishing](https://docs.pypi.org/trusted-publishers/) (no API
token stored in the repository); `ci.yml` runs clippy, `cargo test` and the
pytest suite on Linux, Windows and macOS for every push and pull request.

## License

LGPL-2.1-only, as a derivative of docxtpl. See `LICENSE` and `NOTICE`.
