"""The native kernels must equal the docxtpl 0.20.1 reference byte for byte."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from docxtpl import _native, _reference
import fuzz
from conftest import word_xml_parts


def test_real_word_parts(corpus):
    checked = 0
    for document in corpus:
        for name, xml in word_xml_parts(document):
            assert _native.patch_xml(xml) == _reference.patch_xml(xml), (document, name)
            mismatch = fuzz.check(xml)
            assert mismatch is None, (document, name, mismatch[0])
            checked += 1
    assert checked


def test_patched_parts_survive_render_transforms(corpus):
    # pre/post transforms see patched XML in production
    for document in corpus[:12]:
        for name, xml in word_xml_parts(document):
            patched = _reference.patch_xml(xml)
            assert (_native.pre_render(patched) or patched) == fuzz.reference_pre_render(patched)
            staged = fuzz.reference_pre_render(patched).replace("</w:t>", "a\tb\nc\x07d\x0ce</w:t>", 50)
            assert (_native.post_render(staged) or staged) == fuzz.reference_post_render(staged)


def test_fuzz_smoke():
    index, text, mismatch, _ = fuzz.run(20000, seed=20260921)
    assert mismatch is None, (index, text, mismatch)


def test_declines_what_it_cannot_reproduce():
    # backslashes are regex-template escapes in the reference implementation
    cell = "<w:tc><w:tcPr></w:tcPr><w:p><w:r><w:t>{% colspan a\\1 %}</w:t></w:r></w:p></w:tc>"
    assert _native.patch_xml(cell) is None
