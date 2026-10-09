#!/bin/bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"
if ! command -v python3 >/dev/null 2>&1; then
  printf '\nFORGE needs an existing Python 3.11+ interpreter. No installation was attempted.\n'
  read -r -p 'Press Return to close. ' _
  exit 2
fi
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 2)'; then
  printf '\nYour selected Python is older than 3.11. No project dependencies were changed.\n'
  exit 2
fi
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$ROOT/forge.py" serve
