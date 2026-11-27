"""Qualify the explicit Linux-only handoff bench without changing FORGE analysis."""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import sys
import time
import unittest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from examples.handoff_bench import build, run_bench, verify_run


def qualify(out_dir):
    out = Path(out_dir).absolute()
    verify_run.require(sys.platform.startswith('linux'), 'Linux resource supervision required')
    verify_run.require(not out.exists() and not out.is_symlink(), 'New qualification output required')
    verify_run.require(not any(path.is_symlink() for path in out.parents), 'Symlink output path')
    verify_run.require(not out.is_relative_to(REPO) and not REPO.is_relative_to(out),
                       'Qualification output must be outside repository')
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(HERE / 'tests'), pattern='test_verifier.py')
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    (out / 'tests.log').write_text(stream.getvalue())
    summary = {'schema': 1, 'status': 'FAIL',
               'scope': 'Explicit trusted Linux supervisor qualification; synthetic handoff bench only.',
               'tests': {'count': result.testsRun, 'failures': len(result.failures),
                         'errors': len(result.errors), 'skips': len(result.skipped),
                         'failed_ids': [test.id() for test, _ in result.failures],
                         'error_ids': [test.id() for test, _ in result.errors]},
               'core_sha256': build.CORE_SHA256, 'harness_sha256': verify_run.HARNESS_SHA256}
    if result.testsRun == 0 or result.failures or result.errors or result.skipped:
        summary['elapsed_seconds'] = time.monotonic() - started
        run_bench.save(out / 'qualification.json', summary)
        return summary
    benchmark = run_bench.run(out / 'bench', execute_reviewed=True)
    summary.update(status=benchmark['status'], legacy=benchmark['legacy'],
                   handoff_cases=len(benchmark['cases']),
                   handoff_passed=sum(row['checks']['passed'] for row in benchmark['cases']),
                   product_sha256=benchmark['product_sha256'],
                   freeze_sha256=benchmark['freeze_sha256'], profile_sha256=benchmark['profile_sha256'],
                   failures=benchmark['failures'], elapsed_seconds=time.monotonic() - started,
                   limitations=benchmark['limitations'])
    if summary['legacy']['exact_cases'] != 24 or summary['handoff_passed'] != 5:
        summary['status'] = 'FAIL'
    run_bench.save(out / 'qualification.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    try:
        result = qualify(args.out)
        print(json.dumps(result, indent=2))
        return 0 if result['status'] == 'PASS' else 1
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
