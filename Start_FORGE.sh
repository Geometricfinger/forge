#!/bin/sh
set -eu
cd "$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 2)'
export PYTHONDONTWRITEBYTECODE=1
exec python3 forge.py serve
