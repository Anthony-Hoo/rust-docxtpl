#!/usr/bin/env bash
# Build the release wheel and (re)install it into the candidate environment.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
python="${1:-${VIRTUAL_ENV:+$VIRTUAL_ENV/bin/python}}"
python="${python:-$(command -v python3)}"
cd "$here"
rm -rf dist
uvx maturin build --release -i "$python" -o dist >/dev/null
uv pip install -q --python "$python" --no-deps --reinstall dist/*.whl
echo "installed $(ls dist)"
