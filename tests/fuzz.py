"""Grammar-based differential fuzzing: native kernels vs. the Python reference.

Inputs are random concatenations of the fragments the original regexes key
on, so unbalanced, nested and adversarial delimiter layouts are common.

    python tests/fuzz.py [cases] [seed]
"""
import random
import sys

from docxtpl import _native, _reference

FRAGMENTS = [
    "<w:tc>", "<w:tc w:x=\"1\">", "</w:tc>", "</w:tc >", "<w:tcPr>", "<w:tcPr w:a=\"b\">", "</w:tcPr>",
    "<w:tcPrChange>", "<w:tr>", "<w:tr w:rsid=\"0\">", "</w:tr>", "<w:trPr/>", "<w:tbl>", "</w:tbl>",
    "<w:p>", "<w:p w:rsid=\"1\">", "</w:p>", "<w:pPr>", "</w:pPr>", "<w:pPr><w:jc w:val=\"left\"/></w:pPr>",
    "<w:r>", "<w:r w:rsid=\"2\">", "</w:r>", "<w:rPr>", "</w:rPr>", "<w:rPr><w:b/></w:rPr>",
    "<w:t>", "<w:t xml:space=\"preserve\">", "</w:t>", "<w:t></w:t>", "<w:tab/>", "<w:t", "<w:t ",
    "<w:gridSpan w:val=\"2\"/>", "<w:gridSpan w:val=\"12\" w:x=\"/\"/>", "<w:gridSpan", "w:gridSpan w:val=\"",
    "<w:shd w:val=\"clear\" w:fill=\"FFFFFF\"/>", "<w:shd", "<w:vMerge/>", "<w:proofErr w:type=\"spellStart\"/>",
    "{{", "}}", "{%", "%}", "{#", "#}", "{%-", "-%}", "{{-", "-}}", "{", "}", "%", "#", "-",
    "{_{", "}_}", "{_%", "%_}", "_",
    "colspan", "cellbg", "vm", "hm", "for x in y", "endfor", "if a", "endif", "a.b", "loop.length",
    "p ", "tr ", "tc ", "r ", "p", "tr", "tc", "r", " ", "  ", "\n", "\t", "\a", "\f", "\r", "\x1c", "\xa0", "　",
    "&lt;", "&gt;", "&amp;", "&#8216;", "&", "“", "”", "‘", "’", "é", "中文", "\U0001f600",
    "<", ">", "/", "\"", "'", "\\", "\\1", "\\g<1>", "1", "23", "٣", "x", "text",
]
MACROS = [
    "{% colspan n %}", "{%colspan a.b%}", "{% colspan %}", "{% colspan x % y %}", "{%\u3000colspan\tz  %}",
    "{% cellbg c %}", "{%cellbg 'FF0000' %}", "{% vm %}", "{%vm%}", "{% hm %}", "{%\nhm\n%}",
    "{%p if x %}", "{%p endif %}", "{%tr for a in b %}", "{%tr endfor %}", "{%tc for c in d %}", "{%tc endfor %}",
    "{%r if y %}", "{{r rich }}", "{{r\nrich}}", "{{p sub }}", "{{tr x }}", "{%p if a % b %}", "{%p x }}", "{{p y %}",
    "{#p note #}", "{#tr note #}", "{#tc n } #}", "{#r note #}", "{%- if z %}", "{% endif -%}", "{{- v -}}",
    "{{ v }}", "{{ a &lt; b }}", "{% if a &gt; \u201cq\u201d %}", "{{ v|f(\u2018s\u2019) }}", "{{}}", "{%%}", "{{ a\nb }}",
    "<w:tc><w:tcPr><w:tcW w:w=\"5\"/></w:tcPr><w:p><w:r><w:t>", "</w:t></w:r></w:p></w:tc>",
    "<w:tc><w:tcPr><w:gridSpan w:val=\"3\"/></w:tcPr><w:p><w:r><w:t>",
    "<w:r><w:rPr><w:i/></w:rPr><w:t></w:t></w:r>", "<w:p><w:pPr><w:jc w:val=\"center\"/></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t>",
    "</w:t></w:r><w:r><w:t>", "</w:t></w:r><w:r><w:t xml:space=\"preserve\">", "</w:t></w:r></w:p><w:p><w:r><w:t>",
]
WEIGHTED = FRAGMENTS + MACROS * 4 + ["{{", "}}", "{%", "%}", "<w:t>", "</w:t>", "<w:tc>", "</w:tc>", "<w:p>", "</w:p>",
                        "<w:r>", "</w:r>", " ", " ", "x"] * 3


def random_document(rng):
    return "".join(rng.choice(WEIGHTED) for _ in range(rng.randint(1, rng.choice((8, 30, 120)))))


def outcome(function, *args):
    try:
        return ("ok", function(*args))
    except Exception as exc:  # the reference can raise re.error on odd templates
        return ("error", type(exc).__name__)


def reference_post_render(xml):
    import re
    xml = re.sub(r"\n<w:p([ >])", r"<w:p\1", xml)
    xml = xml.replace("{_{", "{{").replace("}_}", "}}").replace("{_%", "{%").replace("%_}", "%}")
    return _reference.resolve_listing(xml)


def reference_pre_render(xml):
    import re
    return re.sub(r"<w:p([ >])", r"\n<w:p\1", xml)


def check(text):
    """Returns a description of the first mismatch, or None."""
    native = _native.patch_xml(text)
    if native is not None:  # None: native declined, the reference is used
        expected = outcome(_reference.patch_xml, text)
        if expected != ("ok", native):
            return "patch_xml", expected, native
    for name, native_fn, reference_fn in (
        ("pre_render", _native.pre_render, reference_pre_render),
        ("post_render", _native.post_render, reference_post_render),
    ):
        native = native_fn(text)
        expected = reference_fn(text)
        if (text if native is None else native) != expected:
            return name, expected, native
    return None


def minimize(text):
    """Greedy delta debugging so that a failure report stays readable."""
    size = max(1, len(text) // 2)
    while size >= 1:
        start = 0
        while start < len(text):
            candidate = text[:start] + text[start + size:]
            if candidate and check(candidate):
                text = candidate
            else:
                start += size
        size //= 2
    return text


def run(cases, seed):
    rng = random.Random(seed)
    declined = 0
    for index in range(cases):
        text = random_document(rng)
        declined += _native.patch_xml(text) is None
        mismatch = check(text)
        if mismatch:
            return index, text, mismatch, declined
    return None, None, None, declined


def coverage(cases, seed):
    """How often each interesting rewrite fired in the reference (sanity check of the grammar)."""
    rng = random.Random(seed)
    markers = {"colspan": 'w:gridSpan w:val="{{', "cellbg": 'w:fill="{{', "vm": "w:vMerge w:val=", "hm": "loop.length",
               "hm_scaled": "* loop.length", "preserve": 'xml:space="preserve">', "listing_tab": "<w:tab/></w:r>",
               "page_break": 'w:br w:type="page"'}
    hits = dict.fromkeys(markers, 0)
    changed = 0
    for _ in range(min(cases, 20000)):
        text = random_document(rng)
        status, patched = outcome(_reference.patch_xml, text)
        if status != "ok":
            continue
        changed += patched != text
        listed = reference_post_render(text)
        for name, marker in markers.items():
            hits[name] += (marker in patched and marker not in text) or (marker in listed and marker not in text)
    print("coverage over first %d: changed=%d %s" % (min(cases, 20000), changed, hits))


if __name__ == "__main__":
    cases = int(sys.argv[1]) if len(sys.argv) > 1 else 200000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    index, text, mismatch, declined = run(cases, seed)
    if mismatch:
        text = minimize(text)
        mismatch = check(text)
        print("MISMATCH at case", index, "in", mismatch[0], "(minimized)")
        print("input   :", repr(text))
        print("expected:", repr(mismatch[1]))
        print("native  :", repr(mismatch[2]))
        sys.exit(1)
    coverage(cases, seed)
    print("ok: %d cases, %d declined by native patch_xml" % (cases, declined))
