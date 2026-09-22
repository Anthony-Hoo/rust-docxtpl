"""Regenerate crates/core/tests/golden.json from the Python reference.

    python tests/make_golden.py

The Rust test-suite replays these cases without needing Python. Inputs are
hand-written edge cases per pass plus a deterministic slice of the fuzzer.
"""
import json
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).parent))

from docxtpl import _reference  # noqa: E402
import fuzz  # noqa: E402

CELL = "<w:tc><w:tcPr><w:tcW w:w=\"5\"/></w:tcPr><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:tc>"
HANDWRITTEN = [
    "",
    "plain text without any tag",
    "<w:t>{</w:t><w:t>{ name }</w:t><w:t>}</w:t>",
    "<w:r><w:t>{{ na</w:t></w:r><w:r><w:t xml:space=\"preserve\">me }}</w:t></w:r>",
    "{<a>{<b>% x %<c>}<d>}",
    "{% if x %}unterminated </w:t><w:t>tail",
    CELL % "{% colspan n %}" + CELL % "{% cellbg color %}",
    CELL % "{%colspan a\\1%}",
    "<w:tc><w:tcPr><w:gridSpan w:val=\"2\"/></w:tcPr><w:p><w:r><w:t></w:t></w:r><w:r><w:t>{% colspan 3 %}x</w:t></w:r></w:p></w:tc>",
    CELL % "a{% vm %}b" + CELL % "{% hm %}",
    "<w:tc><w:tcPr><w:gridSpan w:val=\"2\"/></w:tcPr><w:p><w:r><w:t>{%hm%}t</w:t></w:r></w:p></w:tc>",
    "<w:tc><w:tcPr><w:gridSpan w:val=\"٣\"/></w:tcPr><w:p><w:r><w:t>{%hm%}</w:t></w:r></w:p></w:tc>",
    "<w:p><w:r><w:t>{%p if x %}</w:t></w:r></w:p><w:p><w:r><w:t>body</w:t></w:r></w:p><w:p><w:r><w:t>{%p endif %}</w:t></w:r></w:p>",
    "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>{%tr for a in b %}</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t>{{ a }}</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t>{%tr endfor %}</w:t></w:r></w:p></w:tc></w:tr></w:tbl>",
    "<w:p><w:pPr/><w:r><w:rPr><w:b/></w:rPr><w:t>x {{r rich }} y {%r if z %}</w:t></w:r></w:p>",
    "<w:p><w:r><w:t>{#p note #}</w:t></w:r></w:p><w:p><w:r><w:t>{%p if a % b %}</w:t></w:r></w:p>",
    "<w:p><w:r><w:t>a</w:t></w:r></w:p><w:p><w:r><w:t>{%- if x %}b{% endif -%}</w:t></w:r></w:p><w:p><w:r><w:tab/><w:t>c</w:t></w:r></w:p>",
    "<w:t>{{ a &lt; b }} {% if c &gt; “q” %}{{ d|f(‘s’) }}&#8216;{{}}}}</w:t>",
    "<w:t>{{ a\nb }}</w:t><w:t>{{ c }}</w:t>",
    "{{ 中文 }}<w:t>\U0001f600{{ v }}</w:t>",
]
RENDERED = [
    "\n<w:p><w:r><w:t>a\tb\nc</w:t></w:r></w:p>\n<w:p w:x=\"1\"><w:pPr><w:jc/></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t xml:space=\"preserve\">d\x07e\x0cf</w:t></w:r></w:p>",
    "<w:p><w:r><w:rPr>\n</w:rPr><w:rPr><w:i/></w:rPr><w:t>a\tb</w:t></w:r></w:p>",
    "{_{ literal }_} {_% raw %_} {_{_%",
    "\n<w:pPr>\n<w:p>no newline handling inside\n<w:p >",
]


def main():
    rng = random.Random(62368)
    sources = HANDWRITTEN + [fuzz.random_document(rng) for _ in range(400)]
    rendered = RENDERED + [fuzz.random_document(rng) for _ in range(200)]
    cases = {"patch_xml": [], "pre_render": [], "post_render": []}
    for text in sources:
        status, expected = fuzz.outcome(_reference.patch_xml, text)
        # None: the reference raised (regex template error) -> native must decline
        cases["patch_xml"].append([text, expected if status == "ok" else None])
    for text in rendered + sources[:100]:
        cases["pre_render"].append([text, fuzz.reference_pre_render(text)])
        cases["post_render"].append([text, fuzz.reference_post_render(text)])
    target = Path(__file__).resolve().parents[1] / "crates" / "core" / "tests" / "golden.json"
    target.write_text(json.dumps(cases, ensure_ascii=True, indent=0), encoding="ascii")
    print({name: len(rows) for name, rows in cases.items()}, "->", target)


if __name__ == "__main__":
    main()
