#!/usr/bin/env python3
"""Index synthetic source, then assess illustrative observations without running it."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from forge_core.behavior_example import run
from forge_core.common import Blocked, safe_path
from forge_core.workspace import initialize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    out = safe_path(parser.parse_args().out)
    if out.exists() or out.is_relative_to(ROOT) or ROOT.is_relative_to(out):
        raise Blocked('NEW_EXTERNAL_OUTPUT_REQUIRED')
    workbench = initialize(out / 'home')
    print(json.dumps(run(workbench, out / 'example'), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (Blocked, OSError) as exc:
        print(json.dumps({'status': 'BLOCKED', 'reason': str(exc)}))
        raise SystemExit(2)
