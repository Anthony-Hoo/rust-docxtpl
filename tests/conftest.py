from pathlib import Path
import zipfile

import pytest

KIT_ROOT = Path(__file__).resolve().parents[2]


def corpus_documents():
    """Private fixture kit next to this repository (absent in public checkouts)."""
    found = []
    for folder in ("templates", "cases"):
        found += sorted((KIT_ROOT / folder).rglob("*.docx"))
    return found


def word_xml_parts(path):
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.startswith("word/") and name.endswith(".xml") and "/_rels/" not in name:
                yield name, archive.read(name).decode("utf-8")


@pytest.fixture(scope="session")
def corpus():
    documents = corpus_documents()
    if not documents:
        pytest.skip("private DOCX corpus not available")
    return documents
