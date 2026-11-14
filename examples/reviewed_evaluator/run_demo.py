#!/usr/bin/env python3
"""Prepare, run and verify the fixed reviewed RFC 8785 experiment."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from forge_core.common import Blocked, canonical, safe_path, write
from forge_core.reviewed_evaluator import prepare, preflight, run, verify
from forge_core.workspace import initialize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    out = safe_path(parser.parse_args().out)
    if out.exists() or out.is_relative_to(ROOT) or ROOT.is_relative_to(out):
        raise Blocked('NEW_EXTERNAL_OUTPUT_REQUIRED')
    preflight(out / 'home', out / 'prepared')
    workbench = initialize(out / 'home')
    prepared = prepare(workbench, out / 'prepared')
    result = run(workbench, out / 'prepared', out / 'result')
    verified = verify(out / 'prepared', out / 'result')
    summary = {'prepared': prepared, 'result': result, 'verification': verified}
    write(out / 'SUMMARY.json', canonical(summary), new=True)
    print(json.dumps(summary, indent=2))
    return 0 if result['status'] == 'REVIEWED_TRIAL_COMPLETED' else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (Blocked, OSError, ValueError) as exc:
        print(json.dumps({'status': 'BLOCKED', 'reason': str(exc)}))
        raise SystemExit(2)
