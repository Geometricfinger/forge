"""One prospective, fixed RFC-vector trial and its data-only report adapter.

Only run() launches the reviewed collector, in a fresh fixed worker. Preparation
and verification use FORGE's ordinary metadata serialization but do not evaluate
the RFC inputs or call the vendor serializer. Local records are not execution or
chronology attestation, and the worker is not a hostile-code sandbox.
"""
from __future__ import annotations

import copy
import datetime
import importlib.machinery
import json
import math
import os
import platform
import re
import secrets
import sys
import time
from pathlib import Path

# The isolated worker has no script-directory import path. Standard-library
# paths stay first; only this reviewed checkout is appended, never a request path.
if __package__ in (None, ''):
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from forge_core import behavior_review as behavior, corpus, interop
from forge_core.bounded_io import run_bounded
from forge_core.common import Blocked, canonical, loads, path_in_repo, read, safe_path, sha, write
from forge_core.engine import ROOT
from forge_core.transfer_review import evaluate_with_contracts
from forge_core.workspace import code_binding

PROTOCOL = 'forge.reviewed-rfc8785-vectors.v1'
MAX_BYTES = 1_000_000
MAX_PACKET_BYTES = 8_000_000
MAX_FILES = 64
WORKER_SECONDS = 30
SUBJECTS = {
    'baseline': {'module': 'forge_core.common', 'name': 'canonical',
                 'path': 'forge_core/common.py', 'raw_key': 'existing_forge_canonical',
                 'negative_exception': 'ValueError'},
    'candidate': {'module': 'forge_core._vendor.rfc8785._impl', 'name': 'dumps',
                  'path': 'forge_core/_vendor/rfc8785/_impl.py', 'raw_key': 'selected_component',
                  'negative_exception': 'FloatDomainError'},
}
INPUT_FILES = tuple(sorted(set(interop.PINS) | {
    'forge_core/common.py', 'forge_core/contract_trial.py', 'forge_core/interop.py',
    'forge_core/reviewed_evaluator.py', 'forge_core/bounded_io.py',
    'forge_core/__init__.py', 'forge_core/_vendor/__init__.py',
}))
ARTIFACT_NAMES = tuple(['inputs/' + name for name in INPUT_FILES] +
                       ['code-binding.json', 'fixtures.json', 'protocol.json'])
ENVIRONMENT_FIELDS = {'python_implementation', 'python_version', 'python_build', 'python_executable',
                      'system', 'release', 'machine', 'byteorder', 'float_mant_dig',
                      'float_max_exp'}
LIMITS = [
    'Published previously visible examples, not blind holdouts or standards certification.',
    'Fixed component serialization only; no Node, object-admission, application or deployment evaluation.',
    'Local byte, origin and sequence checks are not execution authentication or memory attestation.',
    'The interpreter, standard library and complete dependency environment are not independently attested.',
]
_HASH = re.compile(r'[0-9a-f]{64}')
_HEX = re.compile(r'(?:[0-9a-f]{2})*')
_IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_.]*')


def _fail(code):
    raise Blocked('REVIEWED_TRIAL_' + code)


def _fields(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        _fail('FIELDS')


def _text(value, limit=512):
    if type(value) is not str or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        _fail('TEXT')
    return value


def _digest(value):
    if type(value) is not str or not _HASH.fullmatch(value):
        _fail('DIGEST')
    return value


def _json(value):
    """Bound native API objects as well as strictly parsed JSON."""
    pending = [(value, 0)]; count = 0
    while pending:
        item, depth = pending.pop(); count += 1
        if depth > 16 or count > 100_000:
            _fail('JSON_BOUNDS')
        kind = type(item)
        if kind in (type(None), bool):
            continue
        if kind is int:
            if item.bit_length() > 1024:
                _fail('JSON_BOUNDS')
        elif kind is float:
            if not math.isfinite(item):
                _fail('JSON_BOUNDS')
        elif kind is str:
            if len(item) > MAX_BYTES:
                _fail('JSON_BOUNDS')
        elif kind is list:
            if len(item) > 4096:
                _fail('JSON_BOUNDS')
            pending.extend((x, depth + 1) for x in item)
        elif kind is dict:
            if len(item) > 4096 or any(type(k) is not str for k in item):
                _fail('JSON_BOUNDS')
            pending.extend((x, depth + 1) for pair in item.items() for x in pair)
        else:
            _fail('JSON_VALUE')
    try:
        data = canonical(value)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        raise Blocked('REVIEWED_TRIAL_JSON_VALUE') from exc
    if len(data) > MAX_BYTES:
        _fail('JSON_BOUNDS')
    return data


def _load(path):
    try:
        value = loads(read(path, MAX_BYTES))
        _json(value)
        return value
    except (RecursionError, OverflowError, TypeError) as exc:
        raise Blocked('REVIEWED_TRIAL_JSON_VALUE') from exc


def _same(left, right):
    return _json(left) == _json(right)


def _environment():
    return {'python_implementation': sys.implementation.name,
            'python_version': platform.python_version(),
            'python_build': ' '.join(platform.python_build()),
            'python_executable': str(Path(sys.executable).resolve()),
            'system': platform.system(), 'release': platform.release(),
            'machine': platform.machine(), 'byteorder': sys.byteorder,
            'float_mant_dig': str(sys.float_info.mant_dig),
            'float_max_exp': str(sys.float_info.max_exp)}


def _check_environment(value):
    _fields(value, ENVIRONMENT_FIELDS)
    for item in value.values():
        _text(item)
    _recorded_absolute(value['python_executable'])


def _recorded_absolute(value):
    """Validate archived path spelling without consulting the verifier's host."""
    _text(value, 4000)
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or str(path) != value:
        _fail('RECORDED_PATH')
    return value


def _binding(value):
    if type(value) is not dict or not 1 <= len(value) <= 1024:
        _fail('CODE_BINDING')
    for name, digest in value.items():
        path_in_repo(name); _digest(digest)
    if not set(INPUT_FILES) <= set(value):
        _fail('CODE_BINDING')


def _overlap(a, b):
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def _directory_ancestors(path):
    _text(str(path), 4000)
    if any(p.exists() and not p.is_dir() for p in (path, *path.parents)):
        _fail('DIRECTORY_PATH')


def _recorded_source_roots(home):
    """Protect all existing corpus sources without opening a ledger transaction."""
    folder = safe_path(home / 'corpora')
    if not folder.exists():
        return []
    if not folder.is_dir():
        _fail('CORPUS_BINDING')
    entries = []
    for entry in folder.iterdir():
        entries.append(entry)
        if len(entries) > 512:
            _fail('CORPUS_BOUNDS')
    current = code_binding(); roots = []
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir() or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', entry.name):
            _fail('CORPUS_BINDING')
        binding = _load(entry / 'binding.json')
        _fields(binding, {'schema', 'code', 'manifest_sha256', 'source_root', 'profile_sha256'})
        if type(binding['schema']) is not int or binding['schema'] != corpus.SCHEMA or not _same(binding['code'], current):
            _fail('CORPUS_BINDING')
        _digest(binding['manifest_sha256']); _digest(binding['profile_sha256'])
        _recorded_absolute(binding['source_root'])
        roots.append(safe_path(binding['source_root']))
    return roots


def preflight(home, out, prepared=None):
    """Reject unsafe destinations before the CLI initializes any workspace."""
    home, out = safe_path(home), safe_path(out)
    if _overlap(home, ROOT) or _overlap(out, ROOT) or _overlap(out, home):
        _fail('SEPARATE_OUTPUT')
    if out.exists():
        _fail('OUTPUT_EXISTS')
    _directory_ancestors(home); _directory_ancestors(out)
    if any(_overlap(out, root) for root in _recorded_source_roots(home)):
        _fail('SEPARATE_OUTPUT')
    prior = safe_path(prepared) if prepared is not None else None
    if prior is not None:
        if _overlap(prior, out) or _overlap(prior, home) or _overlap(prior, ROOT):
            _fail('SEPARATE_OUTPUT')
        if not home.is_dir():
            _fail('EXISTING_HOME_REQUIRED')
        _load_prepared(prior)
    return {'home': home, 'out': out, 'prepared': prior}


def _files(folder):
    """An exact, bounded regular-file inventory, including empty directories."""
    folder = safe_path(folder)
    if not folder.is_dir():
        _fail('PACKET_DIRECTORY')
    found = {}; directories = set()
    for base, dirs, names in os.walk(folder, followlinks=False):
        for name in dirs:
            p = Path(base) / name
            if p.is_symlink():
                _fail('PACKET_FILE_TYPE')
            directories.add(p.relative_to(folder).as_posix())
        for name in names:
            p = Path(base) / name
            if p.is_symlink() or not p.is_file():
                _fail('PACKET_FILE_TYPE')
            rel = p.relative_to(folder).as_posix(); path_in_repo(rel)
            found[rel] = p
        if len(found) > MAX_FILES + 1 or len(directories) > MAX_FILES:
            _fail('PACKET_BOUNDS')
    parents = {p.as_posix() for name in found for p in Path(name).parents if str(p) != '.'}
    if directories != parents:
        _fail('PACKET_MEMBERS')
    return found


def _save_packet(folder, kind):
    files = _files(folder)
    if 'packet.json' in files:
        _fail('OUTPUT_EXISTS')
    records = {}
    for name, path in sorted(files.items()):
        data = read(path, MAX_BYTES)
        records[name] = {'size': len(data), 'sha256': sha(data)}
    if sum(r['size'] for r in records.values()) > MAX_PACKET_BYTES:
        _fail('PACKET_BOUNDS')
    write(folder / 'packet.json', _json({'schema': 1, 'kind': kind, 'files': records}), new=True)


def _packet(folder, kind):
    folder = safe_path(folder); files = _files(folder)
    packet = _load(folder / 'packet.json')
    _fields(packet, {'schema', 'kind', 'files'})
    if type(packet['schema']) is not int or packet['schema'] != 1 or packet['kind'] != kind:
        _fail('PACKET_SCHEMA')
    records = packet['files']
    if type(records) is not dict or not 1 <= len(records) <= MAX_FILES or set(files) != set(records) | {'packet.json'}:
        _fail('PACKET_MEMBERS')
    total = 0
    for name, row in records.items():
        path_in_repo(name); _fields(row, {'size', 'sha256'}); _digest(row['sha256'])
        if type(row['size']) is not int or not 0 <= row['size'] <= MAX_BYTES:
            _fail('PACKET_BOUNDS')
        data = read(files[name], MAX_BYTES); total += len(data)
        if len(data) != row['size'] or sha(data) != row['sha256']:
            _fail('PACKET_HASH')
    if total > MAX_PACKET_BYTES:
        _fail('PACKET_BOUNDS')
    return records


def _fixtures():
    interop.verify_pins()  # Reads pinned bytes; does not import or call the vendor.
    out = []
    for filename, kind in [('rfc8785_numbers.json', 'binary64_be'),
                           ('rfc8785_objects.json', 'utf8_json_text')]:
        source = _load(ROOT / 'contracts/interop' / filename)
        for original in source['cases']:
            raw = (bytes.fromhex(original['ieee754_hex']) if kind == 'binary64_be'
                   else original['input'].encode('utf-8'))
            out.append({'id': original['id'], 'category': 'negative' if original.get('expected', '') is None else 'positive',
                        'kind': kind, 'input_hex': raw.hex(), 'input_sha256': sha(raw),
                        'origin': source['origin'], 'fixture': 'contracts/interop/' + filename,
                        'original': original})
    if len(out) != 28 or [r['id'] for r in out if r['category'] == 'negative'] != ['RFC-B-10', 'RFC-B-11']:
        _fail('FIXTURE_SET')
    return {'schema': 1, 'protocol': PROTOCOL, 'cases': out}


def _specification():
    return {'schema': 1, 'protocol': PROTOCOL, 'subjects': copy.deepcopy(SUBJECTS),
            'collector': 'forge_core.contract_trial.external_vectors',
            'cases_per_subject': 28, 'positive_cases': 26, 'negative_cases': 2,
            'comparison': 'Exact serialized text/bytes and exact subject-specific domain errors.',
            'worker_flags': ['-I', '-S', '-B'], 'fresh_bytecode_prefix': True,
            'limits': {'timeout_seconds': WORKER_SECONDS, 'output_bytes': MAX_BYTES},
            'limitations': list(LIMITS)}


def _declarations():
    statement = 'A synthetic evidence-export workflow needs serialized bytes before exchanging a record; format behavior needs separate testing.'
    ref = {'source': 'workflow', 'start': 0, 'end': len(statement), 'quote': statement}
    mechanisms = [{'id': role, 'title': 'Reviewed ' + role + ' serializer',
                   'domain': 'JSON serialization', 'purpose': 'Return serialized bytes for fixed format evaluation.',
                   'mechanism': 'Call the separately reviewed fixed serializer through its declared value-to-bytes interface.',
                   'addresses': ['verification_gap'], 'requires': ['json_value'], 'provides': ['serialized_bytes'],
                   'limits': ['Declared interface only; RFC behavior is not assumed.'], 'evidence': [ref],
                   'code_queries': ['symbol:' + subject['name']]}
                  for role, subject in SUBJECTS.items()]
    workflow = {'id': 'export-record', 'title': 'Exchange a serialized record', 'actor': 'supervising engineer',
                'domain': 'JSON serialization', 'purpose': 'Compare fixed serializers against published output examples.',
                'initial_fields': ['json_value'],
                'steps': [{'id': 'exchange', 'action': 'Exchange the serialized bytes.',
                           'requires': ['serialized_bytes'], 'produces': ['exchanged_record'], 'evidence': [ref]}],
                'needs': [{'id': 'serialization', 'step': 'exchange', 'field': 'serialized_bytes',
                           'pattern': 'verification_gap', 'why': statement, 'basis': 'inferred', 'evidence': [ref]}]}
    bundle = {'schema': 1, 'title': 'Reviewed RFC serialization comparison', 'as_of': '2026-01-01',
              'max_age_days': 3650, 'public_brief': 'Published-vector comparison in a synthetic handoff workflow.',
              'sources': [{'id': 'workflow', 'text': statement, 'kind': 'synthetic', 'public': False,
                           'origin_group': 'reviewed-rfc-example', 'uri': 'fixture://reviewed/rfc8785',
                           'observed_on': '2026-01-01', 'derived_from': [], 'capture_method': 'synthetic_fixture'}],
              'workflows': [workflow], 'observations': [], 'mechanisms': mechanisms, 'preferences': {}}
    def slot(kind):
        return {'kind': kind, 'unit': 'n/a', 'frame': 'n/a', 'clock': 'n/a'}
    registry = {'schema': 1, 'bundle_sha256': sha(canonical(bundle)),
                'annotation_basis': 'SUPERVISOR_DECLARED_NOT_INDEPENDENTLY_VERIFIED',
                'mechanisms': {m['id']: {'mechanism_sha256': sha(canonical(m)),
                    'source_bindings': {'workflow': sha(statement.encode())},
                    'contract': {'id': m['id'], 'domain': m['domain'],
                                 'inputs': {'json_value': slot('json_value')}, 'outputs': [slot('serialized_bytes')],
                                 'effects': [], 'preserves': []}} for m in mechanisms},
                'workflows': {'export-record': {'workflow_sha256': sha(canonical(workflow)),
                    'available_contracts': {'json_value': slot('json_value')},
                    'need_contracts': {'serialization': {'roles': [{'id': 'serialization', 'mode': 'support',
                                                                  'contract': slot('serialized_bytes')}],
                       'allowed_effects': [], 'required_preserves': [], 'domain': workflow['domain']}}}}}
    return bundle, registry


def _artifact_records(folder):
    return [{'path': name, 'size': len(data), 'sha256': sha(data)}
            for name in sorted(ARTIFACT_NAMES) for data in [read(folder / name, MAX_BYTES)]]


def _manifest(role, data):
    name = SUBJECTS[role]['path']
    return {'schema': 1, 'files': [{'provider': 'local', 'file_id': name, 'path': name,
                                  'size': len(data), 'sha256': sha(data), 'source_url': 'local://' + name}]}


def _contract(bundle, registry, corpora, role, artifacts, environment):
    base = evaluate_with_contracts(bundle, corpora, registry)
    selected = SUBJECTS[role]
    rows = [(row, lead) for row in base['transfer_reviews'] for group in row['code_leads']
            if group['mechanism'] == role for lead in group['candidates']
            if lead['name'] == selected['name'] and lead['path'] == selected['path']]
    if len(rows) != 1:
        _fail('CODE_LEAD')
    row, lead = rows[0]
    contract = behavior.draft(bundle, corpora, registry, {'hypothesis': row['hypothesis'],
        'mechanism': role, 'corpus': lead['corpus'], 'candidate_id': lead['definition_id']})['contract']
    hashes = {r['path']: r['sha256'] for r in artifacts}
    contract['objective'] = 'Match the 28 pinned published RFC serialization outputs and declared domain rejections.'
    contract['protocol'] = {'specification_sha256': hashes['protocol.json'],
        'harness_sha256': hashes['inputs/forge_core/contract_trial.py'],
        'fixture_manifest_sha256': hashes['fixtures.json'], 'environment': dict(environment),
        'scope': {'real': ['Fixed reviewed serializer and published RFC inputs'],
                  'substituted': ['Synthetic workflow used for the declared handoff'],
                  'unexecuted': ['Node reference', 'Object admission adapter', 'Applications and deployment']},
        'limitations': list(LIMITS)}
    contract['tested_subject'] = {'description': 'Fixed reviewed ' + role + ': ' + selected['module'] + '.' + selected['name'],
        'subject_artifact': 'inputs/' + selected['path'], 'artifacts': copy.deepcopy(artifacts)}
    cases = []
    for case in _fixtures()['cases']:
        original = case['original']
        if case['kind'] == 'binary64_be':
            assertions = [{'id': 'serialization', 'field': 'serialized_text', 'op': 'equal', 'expected': original['expected']},
                          {'id': 'domain', 'field': 'exception_type', 'op': 'equal',
                           'expected': selected['negative_exception'] if original['expected'] is None else None}]
        else:
            assertions = [{'id': 'serialization', 'field': 'serialized_hex', 'op': 'equal', 'expected': original['expected_hex']}]
        cases.append({'id': case['id'], 'category': case['category'], 'input_sha256': case['input_sha256'],
                      'assertions': assertions})
    contract['cases'] = cases
    behavior.validate_contract(contract)
    return contract


def _plan(contracts, corpora, artifacts, binding, environment):
    return {'schema': 1, 'protocol': PROTOCOL, 'status': 'PREPARED_NOT_EXECUTED',
            'environment': dict(environment), 'code_binding_sha256': sha(canonical(binding)),
            'input_manifest_sha256': sha(canonical(artifacts)),
            'subjects': {role: {**subject, 'definition_id': contracts[role]['target']['code_lead']['definition_id'],
                               'source_sha256': contracts[role]['target']['code_lead']['source_sha256']}
                         for role, subject in SUBJECTS.items()},
            'corpora': {role: {'name': corpora[i][0], 'report_sha256': sha(canonical(corpora[i][1]))}
                        for i, role in enumerate(SUBJECTS)},
            'contract_sha256': {role: sha(canonical(contract)) for role, contract in contracts.items()}}


def _prepared_summary(plan):
    return {'schema': 1, 'status': 'REVIEWED_TRIAL_PREPARED', 'protocol': PROTOCOL,
            'plan_sha256': sha(canonical(plan)), 'executed': False, 'cases_per_subject': 28,
            'positive_cases': 26, 'negative_cases': 2, 'execution_authenticated': False,
            'independent_evaluation': False, 'release_approved': False}


def _prepared_names():
    return {'plan.json', 'summary.json', 'bundle.json', 'registry.json',
            *('contracts/' + r + '.json' for r in SUBJECTS),
            *('corpora/' + r + '.json' for r in SUBJECTS),
            *('manifests/' + r + '.json' for r in SUBJECTS),
            *('artifacts/' + n for n in ARTIFACT_NAMES)}


def _load_prepared(folder):
    folder = safe_path(folder); records = _packet(folder, 'prepared')
    if set(records) != _prepared_names():
        _fail('PACKET_MEMBERS')
    plan = _load(folder / 'plan.json')
    _fields(plan, {'schema', 'protocol', 'status', 'environment', 'code_binding_sha256',
                  'input_manifest_sha256', 'subjects', 'corpora', 'contract_sha256'})
    _check_environment(plan['environment'])
    artifact_root = folder / 'artifacts'
    binding = _load(artifact_root / 'code-binding.json'); _binding(binding)
    if not _same(_load(artifact_root / 'fixtures.json'), _fixtures()) or not _same(_load(artifact_root / 'protocol.json'), _specification()):
        _fail('FIXTURE_BINDING')
    for name in INPUT_FILES:
        digest = sha(read(artifact_root / 'inputs' / name, MAX_BYTES))
        if digest != binding[name] or (name in interop.PINS and digest != interop.PINS[name]):
            _fail('INPUT_BINDING')
    bundle, registry = _declarations()
    if not _same(_load(folder / 'bundle.json'), bundle) or not _same(_load(folder / 'registry.json'), registry):
        _fail('DECLARATION_BINDING')
    _fields(plan['corpora'], SUBJECTS)
    corpora = []
    for role in SUBJECTS:
        item = plan['corpora'][role]; _fields(item, {'name', 'report_sha256'})
        if type(item['name']) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', item['name']):
            _fail('CORPUS_BINDING')
        report = _load(folder / 'corpora' / (role + '.json'))
        if (type(report) is not dict or report.get('status') != 'COMPLETED_FOR_SELECTED_CONTAINERS' or report.get('upstream_code_executed') is not False
                or report.get('source_bodies_retained') is not False):
            _fail('CORPUS_BINDING')
        data = read(artifact_root / 'inputs' / SUBJECTS[role]['path'], MAX_BYTES)
        manifest = _manifest(role, data)
        if not _same(_load(folder / 'manifests' / (role + '.json')), manifest) or report.get('corpus_manifest_sha256') != sha(canonical(manifest)):
            _fail('CORPUS_BINDING')
        corpora.append((item['name'], report))
    if len({name for name, _ in corpora}) != 2:
        _fail('CORPUS_BINDING')
    artifacts = _artifact_records(artifact_root)
    contracts = {role: _contract(bundle, registry, corpora, role, artifacts, plan['environment']) for role in SUBJECTS}
    for role, expected in contracts.items():
        if not _same(_load(folder / 'contracts' / (role + '.json')), expected):
            _fail('CONTRACT_BINDING')
        behavior.preflight_artifacts(expected, artifact_root)
    if not _same(plan, _plan(contracts, corpora, artifacts, binding, plan['environment'])):
        _fail('PLAN_BINDING')
    if not _same(_load(folder / 'summary.json'), _prepared_summary(plan)):
        _fail('SUMMARY')
    return {'folder': folder, 'plan': plan, 'plan_sha256': sha(read(folder / 'plan.json', MAX_BYTES)),
            'binding': binding, 'bundle': bundle, 'registry': registry, 'corpora': corpora,
            'contracts': contracts, 'artifacts': artifacts, 'files': records}


def prepare(workbench, out):
    paths = preflight(workbench.home, out); out = paths['out']
    workbench.guard(); interop.verify_pins()
    binding = code_binding(); environment = _environment()
    source = {name: read(ROOT / name, MAX_BYTES) for name in INPUT_FILES}
    out.mkdir(parents=True, mode=0o700)
    for name, data in source.items():
        write(out / 'artifacts/inputs' / name, data, new=True)
    for name, value in [('code-binding.json', binding), ('fixtures.json', _fixtures()), ('protocol.json', _specification())]:
        write(out / 'artifacts' / name, _json(value), new=True)
    corpora = []
    profile = {'schema': 1, 'id': 'reviewed-serialization', 'title': 'Read-only exact serializer leads',
               'targets': [{'api': 'json.dumps', 'capability': 'json_serialization'}]}
    for role, subject in SUBJECTS.items():
        manifest = _manifest(role, source[subject['path']])
        manifest_path = out / 'manifests' / (role + '.json')
        write(manifest_path, _json(manifest), new=True)
        name = 'reviewed-' + role + '-' + sha(str(out).encode())[:16]
        report = corpus.run(workbench, ROOT, manifest_path, workbench.home / 'corpora' / name,
                            profile=profile, max_containers=1)
        if report['status'] != 'COMPLETED_FOR_SELECTED_CONTAINERS':
            _fail('CORPUS_INCOMPLETE')
        write(out / 'corpora' / (role + '.json'), _json(report), new=True)
        corpora.append((name, report))
    bundle, registry = _declarations(); artifacts = _artifact_records(out / 'artifacts')
    contracts = {role: _contract(bundle, registry, corpora, role, artifacts, environment) for role in SUBJECTS}
    plan = _plan(contracts, corpora, artifacts, binding, environment)
    for role, contract in contracts.items():
        behavior.preflight_artifacts(contract, out / 'artifacts')
        write(out / 'contracts' / (role + '.json'), _json(contract), new=True)
    workbench.guard()
    if binding != code_binding() or environment != _environment() or any(read(ROOT / n, MAX_BYTES) != b for n, b in source.items()):
        _fail('INPUT_CHANGED')
    summary = _prepared_summary(plan)
    for name, value in [('bundle.json', bundle), ('registry.json', registry), ('plan.json', plan), ('summary.json', summary)]:
        write(out / name, _json(value), new=True)
    _save_packet(out, 'prepared')
    return summary


def adapt(raw_vectors):
    """Translate only the known fixed collector schema; never invoke a subject."""
    _json(raw_vectors)
    _fields(raw_vectors, {'case_count', 'cases', 'summary', 'expectation_origin', 'independent_evaluator'})
    fixtures = _fixtures()['cases']
    if (type(raw_vectors['case_count']) is not int or raw_vectors['case_count'] != 28 or
            type(raw_vectors['cases']) is not list or len(raw_vectors['cases']) != 28 or
            raw_vectors['independent_evaluator'] is not False or
            raw_vectors['expectation_origin'] != 'Published external examples; local transcription and evaluator by FORGE developer'):
        _fail('RAW_SCHEMA')
    _fields(raw_vectors['summary'], {s['raw_key'] for s in SUBJECTS.values()})
    observations = {role: [] for role in SUBJECTS}; passed = {role: 0 for role in SUBJECTS}
    for row, case in zip(raw_vectors['cases'], fixtures):
        numeric = case['kind'] == 'binary64_be'; expected = case['original']
        expectation_key = 'expected' if numeric else 'expected_hex'
        _fields(row, {'id', 'origin', expectation_key, *(s['raw_key'] for s in SUBJECTS.values())})
        if (type(row['id']) is not str or row['id'] != case['id'] or
                row['origin'] != ('RFC 8785 Appendix B' if numeric else 'RFC 8785 section 3.2') or
                not _same(row[expectation_key], expected[expectation_key])):
            _fail('RAW_CASE_BINDING')
        for role, subject in SUBJECTS.items():
            outcome = row[subject['raw_key']]
            _fields(outcome, {'actual', 'error', 'passed'} if numeric else {'actual_hex', 'passed'})
            if type(outcome['passed']) is not bool:
                _fail('RAW_BOOLEAN')
            if numeric:
                value, error = outcome['actual'], outcome['error']
                if value is not None:
                    if type(value) is not str or len(value) > 2000:
                        _fail('RAW_OUTCOME')
                    try:
                        value.encode('utf-8', errors='strict')
                    except UnicodeError as exc:
                        raise Blocked('REVIEWED_TRIAL_RAW_OUTCOME') from exc
                    if error is not None:
                        _fail('RAW_OUTCOME')
                elif type(error) is not str or not _IDENT.fullmatch(error) or len(error) > 100:
                    _fail('RAW_OUTCOME')
                wanted_error = subject['negative_exception'] if expected['expected'] is None else None
                agrees = value == expected['expected'] and error == wanted_error
                observed = {'serialized_text': value, 'exception_type': error}
            else:
                value = outcome['actual_hex']
                if type(value) is not str or len(value) > 20_000 or not _HEX.fullmatch(value):
                    _fail('RAW_OUTCOME')
                agrees = value == expected['expected_hex']; observed = {'serialized_hex': value}
            if outcome['passed'] is not agrees:
                _fail('RAW_FLAG_DISAGREEMENT')
            passed[role] += int(agrees)
            observations[role].append({'id': case['id'], 'outcome': 'OBSERVED', 'observations': observed,
                'detail': 'Mechanically copied from the fixed collector raw fields; local provenance is not authenticated.'})
    for role, subject in SUBJECTS.items():
        total = raw_vectors['summary'][subject['raw_key']]
        if type(total) is not int or total != passed[role]:
            _fail('RAW_TOTAL_DISAGREEMENT')
    return {'cases': observations, 'counts': {role: {'passed': n, 'failed': 28 - n, 'cases': 28} for role, n in passed.items()}}


def _error(exc):
    return {'type': type(exc).__name__, 'reason': str(exc) if isinstance(exc, Blocked) else 'FIXED_WORKER_EXCEPTION'}


def _check_error(value):
    _fields(value, {'type', 'reason'})
    for v in value.values():
        _text(v, 300)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _check_time(value):
    _text(value, 80)
    try:
        parsed = datetime.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            _fail('TIME')
        return parsed
    except ValueError as exc:
        raise Blocked('REVIEWED_TRIAL_TIME') from exc


def _snapshot():
    try:
        return {'binding': code_binding(), 'error': None}
    except (OSError, ValueError) as exc:
        return {'binding': None, 'error': _error(exc)}


def _check_snapshot(value):
    _fields(value, {'binding', 'error'})
    if value['binding'] is None:
        _check_error(value['error'])
    elif value['error'] is None:
        _binding(value['binding'])
    else:
        _fail('SOURCE_SNAPSHOT')


def _flags():
    return {'isolated': sys.flags.isolated, 'no_site': sys.flags.no_site,
            'dont_write_bytecode': int(sys.dont_write_bytecode), 'pycache_prefix': sys.pycache_prefix}


def _check_flags(value, prefix):
    _fields(value, {'isolated', 'no_site', 'dont_write_bytecode', 'pycache_prefix'})
    if any(type(value[k]) is not int or value[k] != 1 for k in ('isolated', 'no_site', 'dont_write_bytecode')) or value['pycache_prefix'] != prefix:
        _fail('WORKER_FLAGS')


def _module_origins(binding, prefix):
    found = {}
    for name, module in list(sys.modules.items()):
        if name != 'forge_core' and not name.startswith('forge_core.'):
            continue
        path = Path(module.__file__).resolve()
        spec = module.__spec__
        if (spec is None or not isinstance(spec.loader, importlib.machinery.SourceFileLoader) or
                Path(spec.origin).resolve() != path or not path.is_relative_to(ROOT)):
            _fail('MODULE_ORIGIN')
        rel = path.relative_to(ROOT).as_posix()
        cached = Path(module.__cached__)
        if not cached.is_relative_to(prefix) or cached.exists() or rel not in binding or sha(read(path, MAX_BYTES)) != binding[rel]:
            _fail('MODULE_ORIGIN')
        found[name] = {'path': rel, 'sha256': binding[rel], 'cached_bytes_present': False}
    return found


def _check_origins(value, binding):
    if type(value) is not dict or not 1 <= len(value) <= 100:
        _fail('MODULE_ORIGIN')
    required = {'forge_core.common', 'forge_core.contract_trial', 'forge_core.interop',
                'forge_core._vendor.rfc8785', 'forge_core._vendor.rfc8785._impl'}
    if not required <= set(value):
        _fail('MODULE_ORIGIN')
    for name, row in value.items():
        _fields(row, {'path', 'sha256', 'cached_bytes_present'})
        if type(name) is not str or not _IDENT.fullmatch(name) or not (name == 'forge_core' or name.startswith('forge_core.')):
            _fail('MODULE_ORIGIN')
        expected = name.replace('.', '/')
        if row['path'] not in (expected + '.py', expected + '/__init__.py') or row['path'] not in binding or row['sha256'] != binding[row['path']] or row['cached_bytes_present'] is not False:
            _fail('MODULE_ORIGIN')


def _request(context, start):
    return {'schema': 1, 'protocol': PROTOCOL, 'run_id': start['run_id'],
            'plan_sha256': context['plan_sha256'], 'contract_sha256': context['plan']['contract_sha256'],
            'start_sha256': sha(canonical(start)), 'code_binding': context['binding'],
            'environment': context['plan']['environment'], 'cache_prefix': start['cache_prefix']}


def _worker(request):
    """Private fixed worker. The request carries bindings, never executable choices."""
    _json(request)
    _fields(request, {'schema', 'protocol', 'run_id', 'plan_sha256', 'contract_sha256', 'start_sha256',
                      'code_binding', 'environment', 'cache_prefix'})
    if type(request['schema']) is not int or request['schema'] != 1 or request['protocol'] != PROTOCOL:
        _fail('WORKER_REQUEST')
    for key in ('run_id', 'plan_sha256', 'start_sha256'):
        _digest(request[key])
    _fields(request['contract_sha256'], SUBJECTS)
    for v in request['contract_sha256'].values():
        _digest(v)
    _binding(request['code_binding']); _check_environment(request['environment'])
    prefix = safe_path(request['cache_prefix'])
    report = {'schema': 1, 'protocol': PROTOCOL, 'run_id': request['run_id'],
        'plan_sha256': request['plan_sha256'], 'contract_sha256': request['contract_sha256'],
        'start_sha256': request['start_sha256'], 'request_sha256': sha(canonical(request)),
        'environment_before': _environment(), 'environment_after': None, 'flags': _flags(),
        'source_before': _snapshot(), 'source_after': None, 'module_origins': {},
        'started_utc': _now(), 'finished_utc': None, 'elapsed_seconds': 0.0,
        'collector_calls': 0, 'status': 'FAILED', 'error': None, 'vectors': None}
    started = time.monotonic()
    try:
        _check_flags(report['flags'], str(prefix))
        if not prefix.is_dir() or list(prefix.iterdir()):
            _fail('WORKER_CACHE')
        if report['environment_before'] != request['environment'] or report['source_before'] != {'binding': request['code_binding'], 'error': None}:
            _fail('WORKER_INPUT_CHANGED')
        interop.verify_pins()
        from forge_core import common, contract_trial
        from forge_core._vendor import rfc8785
        from forge_core._vendor.rfc8785 import _impl
        report['module_origins'] = _module_origins(request['code_binding'], prefix)
        _check_origins(report['module_origins'], request['code_binding'])
        if (contract_trial.canonical is not common.canonical or rfc8785.dumps is not _impl.dumps or
                common.canonical.__module__ != SUBJECTS['baseline']['module'] or
                _impl.dumps.__module__ != SUBJECTS['candidate']['module'] or
                Path(common.canonical.__code__.co_filename).resolve() != ROOT / SUBJECTS['baseline']['path'] or
                Path(_impl.dumps.__code__.co_filename).resolve() != ROOT / SUBJECTS['candidate']['path']):
            _fail('CALLABLE_ORIGIN')
        report['collector_calls'] = 1
        report['vectors'] = contract_trial.external_vectors()
        report['status'] = 'COMPLETE'
    except Exception as exc:
        report['error'] = _error(exc)
    report['source_after'] = _snapshot(); report['environment_after'] = _environment()
    report['finished_utc'] = _now(); report['elapsed_seconds'] = time.monotonic() - started
    if (report['source_after'] != {'binding': request['code_binding'], 'error': None} or
            report['environment_after'] != request['environment']):
        report['status'] = 'FAILED'; report['error'] = {'type': 'Blocked', 'reason': 'REVIEWED_TRIAL_WORKER_INPUT_CHANGED'}
    return report


def _check_worker(context, start, request, report):
    _json(report)
    _fields(report, {'schema', 'protocol', 'run_id', 'plan_sha256', 'contract_sha256', 'start_sha256',
        'request_sha256', 'environment_before', 'environment_after', 'flags', 'source_before', 'source_after',
        'module_origins', 'started_utc', 'finished_utc', 'elapsed_seconds', 'collector_calls', 'status', 'error', 'vectors'})
    for key in ('schema', 'protocol', 'run_id', 'plan_sha256', 'contract_sha256', 'start_sha256'):
        if not _same(report[key], request[key]):
            _fail('WORKER_BINDING')
    if report['request_sha256'] != sha(canonical(request)):
        _fail('WORKER_BINDING')
    _check_environment(report['environment_before']); _check_environment(report['environment_after'])
    _check_flags(report['flags'], start['cache_prefix'])
    _check_snapshot(report['source_before']); _check_snapshot(report['source_after'])
    began = _check_time(report['started_utc']); finished = _check_time(report['finished_utc'])
    if began < _check_time(start['started_utc']) or finished < began:
        _fail('TIME_ORDER')
    if (type(report['elapsed_seconds']) not in (float, int) or not math.isfinite(report['elapsed_seconds']) or
            not 0 <= report['elapsed_seconds'] <= WORKER_SECONDS + 10 or type(report['collector_calls']) is not int or
            report['collector_calls'] not in (0, 1) or type(report['status']) is not str or report['status'] not in {'COMPLETE', 'FAILED'}):
        _fail('WORKER_METADATA')
    if report['status'] == 'FAILED':
        _check_error(report['error'])
        if type(report['module_origins']) is not dict:
            _fail('MODULE_ORIGIN')
        if report['module_origins']:
            _check_origins(report['module_origins'], context['binding'])
        if report['vectors'] is not None:
            adapt(report['vectors'])
        return None
    if (report['error'] is not None or report['collector_calls'] != 1 or
            report['environment_before'] != context['plan']['environment'] or
            report['environment_after'] != context['plan']['environment'] or
            report['source_before'] != {'binding': context['binding'], 'error': None} or
            report['source_after'] != {'binding': context['binding'], 'error': None}):
        _fail('WORKER_BINDING')
    _check_origins(report['module_origins'], context['binding'])
    return adapt(report['vectors'])


def _cache_state(out):
    folder = out / 'worker-cache'
    try:
        if not folder.exists():
            return {'status': 'UNSAFE', 'files': {}, 'error': {'type': 'Blocked', 'reason': 'REVIEWED_TRIAL_WORKER_CACHE'}}
        files = _files(folder)
        if not files:
            folder.rmdir()
            return {'status': 'CLEAN', 'files': {}, 'error': None}
        records = {}
        for name, path in files.items():
            data = read(path, MAX_BYTES)
            records['worker-cache/' + name] = {'size': len(data), 'sha256': sha(data)}
        return {'status': 'NOT_EMPTY', 'files': records, 'error': None}
    except (OSError, ValueError) as exc:
        return {'status': 'UNSAFE', 'files': {}, 'error': _error(exc)}


def _check_cache(value):
    _fields(value, {'status', 'files', 'error'})
    if type(value['status']) is not str or value['status'] not in {'CLEAN', 'NOT_EMPTY', 'UNSAFE'} or type(value['files']) is not dict or len(value['files']) > MAX_FILES:
        _fail('WORKER_CACHE')
    for name, record in value['files'].items():
        path_in_repo(name); _fields(record, {'size', 'sha256'}); _digest(record['sha256'])
        if not name.startswith('worker-cache/') or type(record['size']) is not int or not 0 <= record['size'] <= MAX_BYTES:
            _fail('WORKER_CACHE')
    if value['status'] == 'CLEAN':
        if value['files'] or value['error'] is not None:
            _fail('WORKER_CACHE')
    elif value['status'] == 'NOT_EMPTY':
        if not value['files'] or value['error'] is not None:
            _fail('WORKER_CACHE')
    else:
        _check_error(value['error'])


def _assessment(context, start, request, raw, supervision_error, after, prepared_unchanged, cache):
    _check_snapshot(after)
    _check_cache(cache)
    if type(prepared_unchanged) is not bool:
        _fail('RECEIPT')
    result = {'status': 'REVIEWED_TRIAL_EXECUTION_FAILED', 'error': None, 'collector_calls': None,
              'complete_raw_report': False, 'source_preserved': after == {'binding': context['binding'], 'error': None},
              'prepared_preserved': prepared_unchanged, 'translated': None, 'worker': None}
    if raw is None:
        _check_error(supervision_error); result['error'] = supervision_error
    else:
        if supervision_error is not None:
            _fail('RECEIPT')
        try:
            worker = loads(raw); _json(worker)
            translated = _check_worker(context, start, request, worker)
            result.update(worker=worker, collector_calls=worker['collector_calls'])
            result['source_preserved'] = result['source_preserved'] and all(
                worker[key] == {'binding': context['binding'], 'error': None}
                for key in ('source_before', 'source_after'))
            if worker['status'] == 'FAILED':
                result['error'] = worker['error']
            else:
                result.update(status='REVIEWED_TRIAL_COMPLETED', complete_raw_report=True, translated=translated)
        except (Blocked, ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
            result['error'] = _error(exc)
    if not result['source_preserved'] or not prepared_unchanged:
        result.update(status='REVIEWED_TRIAL_EXECUTION_FAILED', complete_raw_report=False,
                      translated=None, error={'type': 'Blocked', 'reason': 'REVIEWED_TRIAL_INPUT_CHANGED'})
    if cache['status'] != 'CLEAN':
        result.update(status='REVIEWED_TRIAL_EXECUTION_FAILED', complete_raw_report=False,
                      translated=None, error={'type': 'Blocked', 'reason': 'REVIEWED_TRIAL_WORKER_CACHE'})
    return result


def _evidence_reviews(context, raw, translated, artifact_root):
    evidence = {}; reviews = {}
    for role, contract in context['contracts'].items():
        evidence[role] = {'schema': 1, 'contract_sha256': sha(canonical(contract)),
            'reporter': 'Fixed reviewed RFC collector; mechanically mapped local observations, not external attestation',
            'report_sha256': sha(raw), 'artifacts': [{'path': 'raw-report.json', 'size': len(raw), 'sha256': sha(raw)}],
            'cases': translated['cases'][role]}
        reviews[role] = behavior.evaluate(context['bundle'], context['corpora'], context['registry'],
                                          contract, evidence[role], artifact_root=artifact_root)
    return evidence, reviews


def _run_summary(context, start, assessed, reviews):
    translated = assessed['translated']
    worker = assessed['worker']
    captured = worker is not None and worker['vectors'] is not None
    return {'schema': 1, 'status': assessed['status'], 'protocol': PROTOCOL,
            'plan_sha256': context['plan_sha256'], 'run_id': start['run_id'],
            'results': {role: r['behavioral_review']['status'] for role, r in reviews.items()},
            'counts': translated['counts'] if translated is not None else {},
            'collector_calls': assessed['collector_calls'], 'complete_raw_report': assessed['complete_raw_report'],
            'source_preserved': assessed['source_preserved'], 'prepared_preserved': assessed['prepared_preserved'],
            'unobserved_case_ids': [] if captured else [r['id'] for r in _fixtures()['cases']],
            'adapter_checks': {'fixed_fixture_mapping_checked': translated is not None,
                               'raw_observation_mapping_checked': translated is not None,
                               'recorded_environment_checked': translated is not None,
                               'local_plan_start_binding_checked': True},
            'execution_authenticated': False, 'chronological_freeze_authenticated': False,
            'independent_evaluation': False, 'dependency_completeness_verified': False,
            'release_approved': False, 'model_calls': 0, 'network_requests': 0,
            'limitations': list(LIMITS)}


def run(workbench, prepared, out):
    paths = preflight(workbench.home, out, prepared); out, prepared = paths['out'], paths['prepared']
    workbench.guard(); context = _load_prepared(prepared)
    if context['binding'] != code_binding() or context['plan']['environment'] != _environment():
        _fail('PREPARED_RUNTIME_CHANGED')
    cache = out / 'worker-cache'
    start = {'schema': 1, 'protocol': PROTOCOL, 'run_id': secrets.token_hex(32),
             'plan_sha256': context['plan_sha256'], 'contract_sha256': context['plan']['contract_sha256'],
             'environment': _environment(), 'source_before': _snapshot(),
             'started_utc': _now(), 'cache_prefix': str(cache)}
    if (start['environment'] != context['plan']['environment'] or
            start['source_before'] != {'binding': context['binding'], 'error': None}):
        _fail('PREPARED_RUNTIME_CHANGED')
    out.mkdir(parents=True, mode=0o700)
    for name in ARTIFACT_NAMES:
        write(out / 'artifacts' / name, read(prepared / 'artifacts' / name, MAX_BYTES), new=True)
    cache.mkdir(mode=0o700)
    write(out / 'start.json', _json(start), new=True)
    request = _request(context, start); write(out / 'request.json', _json(request), new=True)
    raw = None; supervision_error = None
    try:
        raw = run_bounded([sys.executable, '-I', '-S', '-B', '-X', 'pycache_prefix=' + str(cache),
                           str(ROOT / 'forge_core/reviewed_evaluator.py'), '--fixed-worker'], out,
                          {'PATH': os.defpath, 'LANG': 'C.UTF-8'}, timeout=WORKER_SECONDS,
                          input=_json(request), output_limit=MAX_BYTES)
    except (OSError, ValueError) as exc:
        supervision_error = _error(exc)
    # Preserve returned bytes before any post-run source/cache integrity check.
    if raw is not None:
        write(out / 'artifacts/raw-report.json', raw, new=True)
    after = _snapshot()
    prepared_unchanged = False
    try:
        prepared_unchanged = _load_prepared(prepared)['files'] == context['files']
    except (OSError, ValueError):
        pass
    cache_state = _cache_state(out)
    assessed = _assessment(context, start, request, raw, supervision_error, after, prepared_unchanged, cache_state)
    receipt = {'schema': 1, 'protocol': PROTOCOL, 'run_id': start['run_id'],
        'plan_sha256': context['plan_sha256'], 'start_sha256': sha(canonical(start)),
        'request_sha256': sha(canonical(request)), 'raw_report_sha256': sha(raw) if raw is not None else None,
        'source_after': after, 'prepared_unchanged': prepared_unchanged, 'supervision_error': supervision_error,
        'cache': cache_state, 'status': assessed['status'], 'error': assessed['error']}
    evidence = {}; reviews = {}
    if assessed['translated'] is not None:
        evidence, reviews = _evidence_reviews(context, raw, assessed['translated'], out / 'artifacts')
    for role in reviews:
        write(out / 'evidence' / (role + '.json'), _json(evidence[role]), new=True)
        write(out / 'reviews' / (role + '.json'), _json(reviews[role]), new=True)
    summary = _run_summary(context, start, assessed, reviews)
    write(out / 'receipt.json', _json(receipt), new=True)
    write(out / 'summary.json', _json(summary), new=True)
    try:
        _save_packet(out, 'result')
    except (OSError, ValueError) as exc:
        write(out / 'packet-incomplete.json', _json({'schema': 1,
            'status': 'REVIEWED_TRIAL_PACKET_INCOMPLETE', 'error': _error(exc),
            'raw_report_preserved': raw is not None, 'execution_authenticated': False}), new=True)
        raise Blocked('REVIEWED_TRIAL_PACKET_INCOMPLETE') from exc
    return summary


def verify(prepared, result):
    """Recompute saved-data checks; never run the collector or vendor again."""
    prepared, result = safe_path(prepared), safe_path(result)
    if _overlap(prepared, result) or _overlap(prepared, ROOT) or _overlap(result, ROOT):
        _fail('SEPARATE_OUTPUT')
    context = _load_prepared(prepared); records = _packet(result, 'result')
    for name in ARTIFACT_NAMES:
        if read(result / 'artifacts' / name, MAX_BYTES) != read(prepared / 'artifacts' / name, MAX_BYTES):
            _fail('INPUT_BINDING')
    start = _load(result / 'start.json')
    _fields(start, {'schema', 'protocol', 'run_id', 'plan_sha256', 'contract_sha256',
                    'environment', 'source_before', 'started_utc', 'cache_prefix'})
    _digest(start['run_id']); _check_time(start['started_utc']); _recorded_absolute(start['cache_prefix'])
    if (type(start['schema']) is not int or start['schema'] != 1 or start['protocol'] != PROTOCOL or
            start['plan_sha256'] != context['plan_sha256'] or not _same(start['contract_sha256'], context['plan']['contract_sha256']) or
            not _same(start['environment'], context['plan']['environment']) or
            not _same(start['source_before'], {'binding': context['binding'], 'error': None})):
        _fail('START_BINDING')
    request = _request(context, start)
    if not _same(_load(result / 'request.json'), request):
        _fail('REQUEST_BINDING')
    receipt = _load(result / 'receipt.json')
    _fields(receipt, {'schema', 'protocol', 'run_id', 'plan_sha256', 'start_sha256', 'request_sha256',
                     'raw_report_sha256', 'source_after', 'prepared_unchanged', 'supervision_error', 'cache', 'status', 'error'})
    raw = read(result / 'artifacts/raw-report.json', MAX_BYTES) if 'artifacts/raw-report.json' in records else None
    assessed = _assessment(context, start, request, raw, receipt['supervision_error'],
                           receipt['source_after'], receipt['prepared_unchanged'], receipt['cache'])
    expected_receipt = {'schema': 1, 'protocol': PROTOCOL, 'run_id': start['run_id'],
        'plan_sha256': context['plan_sha256'], 'start_sha256': sha(canonical(start)),
        'request_sha256': sha(canonical(request)), 'raw_report_sha256': sha(raw) if raw is not None else None,
        'source_after': receipt['source_after'], 'prepared_unchanged': receipt['prepared_unchanged'],
        'supervision_error': receipt['supervision_error'], 'cache': receipt['cache'], 'status': assessed['status'], 'error': assessed['error']}
    if not _same(receipt, expected_receipt):
        _fail('RECEIPT')
    names = {'start.json', 'request.json', 'receipt.json', 'summary.json',
             *('artifacts/' + name for name in ARTIFACT_NAMES)}
    for name, record in receipt['cache']['files'].items():
        names.add(name)
        if not _same(records.get(name), record):
            _fail('WORKER_CACHE')
    if raw is not None:
        names.add('artifacts/raw-report.json')
    reviews = {}
    if assessed['translated'] is not None:
        evidence, reviews = _evidence_reviews(context, raw, assessed['translated'], result / 'artifacts')
        for role in SUBJECTS:
            for folder, value in [('evidence', evidence[role]), ('reviews', reviews[role])]:
                name = folder + '/' + role + '.json'; names.add(name)
                if not _same(_load(result / name), value):
                    _fail('TRANSLATION_BINDING')
    if set(records) != names:
        _fail('PACKET_MEMBERS')
    summary = _run_summary(context, start, assessed, reviews)
    if not _same(_load(result / 'summary.json'), summary):
        _fail('SUMMARY')
    return {'schema': 1, 'status': 'REVIEWED_TRIAL_VERIFIED',
            'qualification': 'COMPLETED' if summary['status'] == 'REVIEWED_TRIAL_COMPLETED' else 'EXECUTION_FAILED',
            'plan_sha256': context['plan_sha256'], 'run_id': start['run_id'],
            'results': summary['results'], 'counts': summary['counts'], 'executed': False,
            'files_checked': {'prepared': len(context['files']) + 1, 'result': len(records) + 1},
            'execution_authenticated': False, 'chronological_freeze_authenticated': False,
            'independent_evaluation': False, 'release_approved': False}


if __name__ == '__main__':
    if sys.argv[1:] != ['--fixed-worker']:
        raise SystemExit('Only the fixed private worker entrypoint is supported.')
    try:
        payload = sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            _fail('JSON_BOUNDS')
        sys.stdout.buffer.write(_json(_worker(loads(payload))))
    except Exception as exc:
        sys.stdout.buffer.write(_json({'status': 'WORKER_ADMISSION_FAILED', 'error': _error(exc)}))
