"""Build, freeze and explicitly execute the same archive on both bench modes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

try:
    from . import boundary_harness, build, verify_run
except ImportError:
    import boundary_harness
    import build
    import verify_run


HERE = Path(__file__).resolve().parent
LIMITS = {'wall_seconds': 5, 'cpu_seconds': 3, 'address_space_bytes': 536_870_912,
          'file_size_bytes': 1_000_000, 'combined_stream_bytes': 262_144,
          'total_work_file_bytes': 4_000_000, 'max_work_files': 48, 'open_files': 64}


def reference(path, root):
    raw = verify_run.regular(path, build.APP_BYTES)
    return {'path': Path(path).relative_to(root).as_posix(), 'bytes': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest()}


def save(path, value):
    raw = build.canonical(value)
    verify_run.require(len(raw) <= verify_run.MAX_RECORD_BYTES, 'Saved record size limit')
    with Path(path).open('xb') as stream:
        stream.write(raw)


def copy_file(source, destination):
    raw = verify_run.regular(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as stream:
        stream.write(raw)


def transport_expected(fields, exports):
    return {'termination': 'EXITED', 'exit_code': 0,
            'stdout': {'utf8': True, 'json': True, 'unicode_scalars': True, 'complete': True,
                       'fields': {name: {'present': True, 'value': value} for name, value in fields.items()}},
            'stderr_empty': True, 'inputs_preserved': True, 'product_preserved': True,
            'exports': {name: {'kind': 'REGULAR', 'utf8': True} for name in exports}}


def make_profile(frozen):
    legacy_source = frozen / 'inputs/manual-baseline/input-batch.json'
    cases = [{'id': 'legacy-24', 'arguments': ['--input', 'input-batch.json'],
              'files': [{'path': 'input-batch.json', 'artifact': reference(legacy_source, frozen)}],
              'links': [], 'exports': [],
              'projections': [{'name': 'status', 'path': ['status']},
                              {'name': 'case_count', 'path': ['cases'], 'measure': 'length'}],
              'expected': transport_expected({'status': 'EVALUATED_DECLARED_MODELS', 'case_count': 24}, [])}]
    fixture_root = frozen / 'inputs/handoff'
    expected = verify_run.strict_json(verify_run.regular(fixture_root / 'expected.json'))
    for row in expected['cases']:
        name = row['fixture']
        source = fixture_root / verify_run.relative(name)
        handoff = verify_run.strict_json(verify_run.regular(source))
        verify_run.require(handoff['case_id'] == row['case_id'], 'Fixture case identity disagreement')
        files = [{'path': name, 'artifact': reference(source, frozen)}]
        names = {name}
        for artifact in handoff['raw_artifacts']:
            artifact_name = verify_run.relative(artifact['path']).as_posix()
            verify_run.require(artifact_name not in names, 'Duplicate fixture artifact path')
            names.add(artifact_name)
            artifact_path = source.parent / artifact_name
            raw = verify_run.regular(artifact_path)
            verify_run.require(len(raw) == artifact['bytes'] and build.digest(raw) == artifact['sha256'],
                               'Raw handoff artifact pin mismatch')
            files.append({'path': artifact_name, 'artifact': reference(artifact_path, frozen)})
        exports = ['result/plan.json', 'result/burden.json', 'result/receipt.json']
        cases.append({'id': row['case_id'],
                      'arguments': ['--input', name, '--mode', 'handoff', '--out', 'result'],
                      'files': files, 'links': [], 'exports': exports,
                      'projections': [{'name': 'case_id', 'path': ['plan', 'case_id']},
                                      {'name': 'source', 'path': ['plan', 'source']}],
                      'expected': transport_expected({'case_id': row['case_id'], 'source': row['source']}, exports)})
    return {'schema': 1, 'protocol': boundary_harness.PROTOCOL,
            'name': 'handoff-shared-planner-bench',
            'scope': 'Original24 and five independent synthetic handoffs through one reviewed archive; no human savings or vendor compatibility claim.',
            'limits': dict(LIMITS), 'cases': cases}


def run(out_dir, bench_root=HERE, *, execute_reviewed=False):
    bench_root = Path(bench_root).absolute()
    out = Path(out_dir).absolute()
    verify_run.require(not out.exists() and not out.is_symlink(), 'New output directory required')
    verify_run.require(not any(path.is_symlink() for path in out.parents), 'Symlink output path')
    verify_run.require(not out.is_relative_to(bench_root) and not bench_root.is_relative_to(out),
                       'Output must be separate from the reviewed example')
    repository = HERE.parents[1]
    verify_run.require(not (repository / 'forge.py').is_file() or not out.is_relative_to(repository),
                       'Bench output must be outside the FORGE checkout')
    # Check independent anchors before copying or generating any execution record.
    verify_run.require(build.digest(verify_run.regular(bench_root / 'inputs/handoff/freeze.json'))
                       == verify_run.HANDOFF_FREEZE, 'Independent handoff freeze changed')
    verify_run.require(build.digest(verify_run.regular(bench_root / 'inputs/manual-baseline/freeze.json'))
                       == verify_run.BASELINE_FREEZE, 'Independent original freeze changed')
    out.mkdir(parents=True, exist_ok=False)
    built = build.build_product(out / 'build', bench_root)
    frozen = out / 'frozen'
    frozen.mkdir()
    for name in ['SPEC.md', 'HANDOFF_SPEC.md', 'SOURCE_FIELDS.json', 'preservation_pins.json',
                 'boundary_harness.py']:
        copy_file(bench_root / name, frozen / name)
    for subdirectory in ['manual-baseline', 'handoff']:
        source_root = bench_root / 'inputs' / subdirectory
        for path in sorted(source_root.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                copy_file(path, frozen / 'inputs' / subdirectory / path.relative_to(source_root))
    profile = make_profile(frozen)
    save(frozen / 'profile.json', profile)
    boundary_harness.validate_profile(verify_run.regular(frozen / 'profile.json'), frozen)
    files = [reference(path, out) for path in sorted(frozen.rglob('*')) if path.is_file()]
    files.extend([reference(out / 'build/product.pyz', out), reference(out / 'build/build.json', out)])
    binding = {'schema': 1, 'phase': 'FROZEN_BEFORE_EXECUTION',
               'scope': 'Caller-retained local byte binding; not authentication of execution or chronology.',
               'product': reference(out / 'build/product.pyz', out),
               'members': built['members'], 'harness': reference(frozen / 'boundary_harness.py', out),
               'profile': reference(frozen / 'profile.json', out),
               'handoff_expected': reference(frozen / 'inputs/handoff/expected.json', out),
               'files': files}
    save(frozen / 'binding.json', binding)
    anchor = build.digest(verify_run.regular(frozen / 'binding.json'))
    (frozen / 'binding.sha256').write_text(anchor + '\n')
    if not execute_reviewed:
        summary = {'schema': 1, 'status': 'FROZEN_NOT_EXECUTED', 'product_executed': False,
                   'product_sha256': built['product']['sha256'], 'freeze_sha256': anchor,
                   'legacy_cases': 24, 'handoff_cases': len(profile['cases']) - 1}
        save(out / 'summary.json', summary)
        return summary
    boundary_harness.run_profile(out / 'build/product.pyz', built['product']['sha256'],
                                 frozen / 'profile.json', out / 'execution', authorized=True)
    result = verify_run.verify_run(frozen / 'binding.json', out / 'execution', trusted_freeze_sha256=anchor)
    save(out / 'benchmark.json', result)
    save(out / 'summary.json', {'schema': 1, 'status': result['status'], 'product_executed': True,
        'product_sha256': result['product_sha256'], 'freeze_sha256': anchor,
        'legacy_exact_cases': result['legacy']['exact_cases'], 'legacy_total_cases': 24,
        'handoff_exact_cases': sum(row['checks']['passed'] for row in result['cases']),
        'handoff_total_cases': len(result['cases']), 'failures': result['failures']})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--execute-reviewed-product', action='store_true')
    args = parser.parse_args()
    try:
        result = run(args.out, execute_reviewed=args.execute_reviewed_product)
        print(json.dumps({key: value for key, value in result.items()
                          if key in ('status', 'product_executed', 'product_sha256', 'freeze_sha256',
                                     'legacy', 'legacy_cases', 'handoff_cases', 'failures')}, indent=2))
        return 0 if result['status'] in ('PASS', 'FROZEN_NOT_EXECUTED') else 1
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
