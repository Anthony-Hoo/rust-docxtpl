#!/usr/bin/env python3
"""Build README.pypi.md, the long description shown on PyPI.

PyPI renders a single file and does not resolve links relative to the
repository, so the English and Simplified Chinese READMEs are concatenated
and their repository-relative links are rewritten to absolute GitHub URLs.
README.md and README.zh-CN.md stay the only sources; run this script after
editing either one (``--check`` in CI fails when the generated file is stale).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_BLOB = "https://github.com/Anthony-Hoo/rust-docxtpl/blob/main/"
OUTPUT = ROOT / "README.pypi.md"
# PyPI strips HTML anchors, so the language switch of each part links to the
# other file on GitHub and says that the other language follows/precedes.
SOURCES = (
    ("README.md", f"English | [简体中文]({REPO_BLOB}README.zh-CN.md)（中文全文见下方）"),
    ("README.zh-CN.md", f"[English]({REPO_BLOB}README.md) (full English text above) | 简体中文"),
)
LANGUAGE_SWITCH = re.compile(
    r"^(\[English\]\(README\.md\)|English) \| (\[简体中文\]\(README\.zh-CN\.md\)|简体中文)$", re.M
)
RELATIVE_LINK = re.compile(r"\]\((?!https?://|#|mailto:)([^)\s]+)\)")


def render(source: Path, switch: str) -> str:
    text = source.read_text(encoding="utf-8")
    text, replaced = LANGUAGE_SWITCH.subn(switch, text, count=1)
    if replaced != 1:
        raise SystemExit(f"{source.name}: language switch line not found")
    return RELATIVE_LINK.sub(lambda m: f"]({REPO_BLOB}{m.group(1)})", text).strip() + "\n"


def build() -> str:
    parts = [render(ROOT / name, switch) for name, switch in SOURCES]
    return "\n\n---\n\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if README.pypi.md is out of date")
    args = parser.parse_args()
    expected = build()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != expected:
            print(f"{OUTPUT.name} is stale; run scripts/pypi_readme.py", file=sys.stderr)
            return 1
        return 0
    OUTPUT.write_text(expected, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(expected)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
