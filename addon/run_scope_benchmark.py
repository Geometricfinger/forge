"""Reproduce the fixed per-source scope-cache comparison. No source code execution."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import statistics
import symtable
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


def load_probe(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = ROOT
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hound', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists() or args.out.is_symlink() or out.is_relative_to(ROOT):
        raise ValueError('NEW_OUTSIDE_OUTPUT_REQUIRED')
    contract = json.loads((ROOT / 'contracts/scope_cache_contract.json').read_text())
    fixture = (ROOT / 'contracts/scope_cache_fixture.py').read_bytes()
    if hashlib.sha256(fixture).hexdigest() != contract['fixture_sha256']:
        raise ValueError('FIXTURE_CHANGED')
    profile = json.loads((ROOT / 'profiles/understanding.json').read_text())
    stages = {}
    for label, relative in [('before', 'reference/mission_probe_before_scope_cache.py'),
                            ('after', 'tool/mission_probe.py')]:
        file = ROOT / relative
        module = load_probe(file, 'scope_comparison_' + label)
        rows = []
        for index in range(3):
            start = time.perf_counter()
            with patch('symtable.symtable', wraps=symtable.symtable) as counter:
                report = module.scan(fixture, 'synthetic:scope-cache', profile, args.hound.resolve())
            rows.append({'iteration': index + 1, 'seconds': time.perf_counter() - start,
                         'symbol_table_builds': counter.call_count, 'findings': len(report['findings'])})
        stages[label] = {'probe_sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                         'rows': rows, 'median_seconds': statistics.median(r['seconds'] for r in rows)}
    passed = (all(r['findings'] == contract['required_findings'] for s in stages.values() for r in s['rows'])
              and all(r['symbol_table_builds'] <= contract['maximum_symbol_table_builds_per_scan']
                      for r in stages['after']['rows'])
              and all(r['symbol_table_builds'] == 80 for r in stages['before']['rows']))
    report = {'status': 'OBSERVED_COST_REDUCTION' if passed else 'BLOCKED', 'stages': stages,
              'fixture_sha256': contract['fixture_sha256'], 'fixture_executed': False,
              'scope': 'Developer-authored synthetic 80-nested-function fixture; not overall FORGE throughput.',
              'release_approved': False, 'independent_evaluation': False}
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    print(json.dumps({'status': report['status'], 'output': str(out)}))
    return 0 if passed else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, ImportError) as error:
        print(json.dumps({'status': 'BLOCKED', 'error_type': type(error).__name__}))
        raise SystemExit(2)
