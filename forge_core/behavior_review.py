"""Bound source-linked behavioral contracts to externally recorded observations.

This module reads selected artifacts and evaluates two data-only assertions. It
never imports, executes or installs the inspected source, harness or receipt.
Matching bytes and assertions do not authenticate execution, translation of an
external report, chronological freezing, semantic meaning or release readiness.
"""
from __future__ import annotations

import copy
import math
import re
from pathlib import Path

from .common import Blocked, canonical, path_in_repo, read, safe_path, sha
from .transfer_review import ContractError, evaluate_with_contracts

VERSION = 'forge-behavior-review-0.1'
MAX_BYTES = 1_000_000
MAX_CASES = 256
MAX_ASSERTIONS = 16
MAX_ARTIFACTS = 64
MAX_ARTIFACT_BYTES = 8_000_000
MAX_TOTAL_ARTIFACT_BYTES = 32_000_000
_IDENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}')
_HASH = re.compile(r'[0-9a-f]{64}')
_FIT = {'DECLARED_DIRECT_FIT', 'DECLARED_SUPPORT_FIT'}
_AUTHORITY = {
    'runtime_verified': False,
    'execution_authorized': False,
    'execution_authenticated': False,
    'translation_fidelity_verified': False,
    'case_input_mapping_verified': False,
    'chronological_freeze_verified': False,
    'annotation_truth_verified': False,
    'independent_evaluation': False,
    'reuse_approved': False,
    'release_approved': False,
    'market_validated': False,
    'novelty_established': False,
    'source_code_executed': False,
    'model_calls': 0,
    'network_requests': 0,
}


def _fail(code):
    raise Blocked('BEHAVIOR_' + code)


def _fields(value, names):
    if type(value) is not dict or set(value) != set(names):
        _fail('FIELDS')


def _text(value, limit=1200, *, empty=False):
    if (type(value) is not str or len(value) > limit or
            (not empty and not value.strip()) or any(ord(c) < 32 for c in value)):
        _fail('TEXT')
    return value


def _identifier(value):
    if type(value) is not str or not _IDENT.fullmatch(value):
        _fail('ID')
    return value


def _hash(value):
    if type(value) is not str or not _HASH.fullmatch(value):
        _fail('DIGEST')
    return value


def _list(value, limit, minimum=0):
    if type(value) is not list or not minimum <= len(value) <= limit:
        _fail('LIST_BOUNDS')
    return value


def _json_value(value, depth=0, count=None):
    """Bound native API inputs too; JSON parsing is not the only entry point."""
    if count is None:
        count = [0]
    count[0] += 1
    if depth > 16 or count[0] > 100_000:
        _fail('JSON_BOUNDS')
    kind = type(value)
    if kind in (type(None), bool):
        return
    if kind is int:
        if value.bit_length() > 1024:
            _fail('JSON_BOUNDS')
        return
    if kind is float:
        if not math.isfinite(value):
            _fail('NONFINITE_JSON')
        return
    if kind is str:
        if len(value) > 20_000:
            _fail('JSON_BOUNDS')
        return
    if kind is list:
        if len(value) > 4096:
            _fail('JSON_BOUNDS')
        for item in value:
            _json_value(item, depth + 1, count)
        return
    if kind is dict:
        if len(value) > 4096 or any(type(key) is not str for key in value):
            _fail('JSON_BOUNDS')
        for key, item in value.items():
            _json_value(key, depth + 1, count)
            _json_value(item, depth + 1, count)
        return
    _fail('JSON_VALUE')


def _encoded(value):
    _json_value(value)
    try:
        body = canonical(value)
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise Blocked('BEHAVIOR_JSON_VALUE') from exc
    if len(body) > MAX_BYTES:
        _fail('INPUT_BOUNDS')
    return body


def _same(left, right):
    # Deliberate exact JSON spelling semantics: true != 1 and 1 != 1.0.
    return _encoded(left) == _encoded(right)


def _path(value):
    try:
        return path_in_repo(value)
    except (Blocked, TypeError, ValueError) as exc:
        raise Blocked('BEHAVIOR_ARTIFACT_PATH') from exc


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _strings(value, limit=32, *, minimum=0):
    _list(value, limit, minimum)
    for item in value:
        _text(item, 1200)
    if len(set(value)) != len(value):
        _fail('DUPLICATE_VALUE')


def _artifacts(rows):
    rows = _list(rows, MAX_ARTIFACTS, 1)
    names = set()
    total = 0
    for item in rows:
        _fields(item, {'path', 'size', 'sha256'})
        _path(item['path'])
        _hash(item['sha256'])
        if item['path'] in names:
            _fail('DUPLICATE_ARTIFACT')
        names.add(item['path'])
        if type(item['size']) is not int or not 0 <= item['size'] <= MAX_ARTIFACT_BYTES:
            _fail('ARTIFACT_BOUNDS')
        total += item['size']
    if total > MAX_TOTAL_ARTIFACT_BYTES:
        _fail('ARTIFACT_BOUNDS')
    return rows


def _candidate_id(lead):
    return lead.get('definition_id') or lead.get('source_id')


def _target(review, selection):
    _fields(selection, {'hypothesis', 'mechanism', 'corpus', 'candidate_id'})
    for key in selection:
        _text(selection[key], 2000)
    hypotheses = [h for h in review['original_report']['hypotheses']
                  if h['id'] == selection['hypothesis']]
    rows = [r for r in review['transfer_reviews']
            if r['hypothesis'] == selection['hypothesis']]
    if len(hypotheses) != 1 or len(rows) != 1:
        _fail('TARGET')
    hypothesis, row = hypotheses[0], rows[0]
    pairs = [p for p in row['pairs'] if p['mechanism'] == selection['mechanism']]
    if (row['status'] != 'DECLARATION_REVIEW_ONLY' or len(pairs) != 1 or
            pairs[0]['status'] not in _FIT or
            selection['mechanism'] not in row['experiment_candidates']):
        _fail('TARGET_NOT_SELECTED')
    candidates = [candidate for group in row['code_leads']
                  if group['mechanism'] == selection['mechanism']
                  for candidate in group['candidates']
                  if candidate['corpus'] == selection['corpus'] and
                  _candidate_id(candidate) == selection['candidate_id']]
    if len(candidates) != 1:
        _fail('CODE_LEAD')
    lead = candidates[0]
    _hash(lead.get('source_sha256'))
    _text(lead.get('source_id'), 2000)
    if lead.get('runtime_tested') is not False or lead.get('rights_reviewed') is not False:
        _fail('CODE_LEAD_AUTHORITY')
    return {
        'transfer_review_sha256': sha(canonical(review)),
        'hypothesis': hypothesis['id'],
        'workflow': hypothesis['workflow'],
        'need': hypothesis['need']['id'],
        'mechanism': selection['mechanism'],
        'role': pairs[0]['role_id'],
        'code_lead': copy.deepcopy(lead),
        'code_lead_sha256': sha(canonical(lead)),
    }


def _transfer(bundle, corpora, registry):
    try:
        return evaluate_with_contracts(bundle, corpora, registry)
    except ContractError as exc:
        raise Blocked('BEHAVIOR_TRANSFER_' + str(exc)) from exc


def draft(bundle, corpora, registry, selection):
    """Return an explicitly incomplete operator template from current code leads."""
    review = _transfer(bundle, corpora, registry)
    target = _target(review, selection)
    contract = {
        'schema': 1,
        'target': target,
        'objective': '',
        'protocol': {
            'specification_sha256': None, 'harness_sha256': None,
            'fixture_manifest_sha256': None, 'environment': {},
            'scope': {'real': [], 'substituted': [], 'unexecuted': []},
            'limitations': [],
        },
        'tested_subject': {'description': '', 'subject_artifact': None, 'artifacts': []},
        'cases': [],
    }
    return {'status': 'DRAFT_REQUIRES_OPERATOR_INPUT', 'transfer_report': review,
            'contract': contract, 'authority': copy.deepcopy(_AUTHORITY)}


def validate_contract(contract):
    """Validate a completed contract without filesystem access or source execution."""
    _encoded(contract)
    _fields(contract, {'schema', 'target', 'objective', 'protocol', 'tested_subject', 'cases'})
    if type(contract['schema']) is not int or contract['schema'] != 1:
        _fail('SCHEMA')
    _text(contract['objective'])
    target = contract['target']
    _fields(target, {'transfer_review_sha256', 'hypothesis', 'workflow', 'need',
                     'mechanism', 'role', 'code_lead', 'code_lead_sha256'})
    for key in ('transfer_review_sha256', 'code_lead_sha256'):
        _hash(target[key])
    for key in ('hypothesis', 'workflow', 'need', 'mechanism', 'role'):
        _text(target[key], 2000)
    lead = target['code_lead']
    if type(lead) is not dict:
        _fail('CODE_LEAD')
    for key in ('corpus', 'source_id'):
        _text(lead.get(key), 2000)
    _text(_candidate_id(lead), 2000)
    _hash(lead.get('source_sha256'))
    if (lead.get('runtime_tested') is not False or lead.get('rights_reviewed') is not False or
            target['code_lead_sha256'] != sha(canonical(lead))):
        _fail('CODE_LEAD_BINDING')
    protocol = contract['protocol']
    _fields(protocol, {'specification_sha256', 'harness_sha256', 'fixture_manifest_sha256',
                       'environment', 'scope', 'limitations'})
    for key in ('specification_sha256', 'harness_sha256', 'fixture_manifest_sha256'):
        _hash(protocol[key])
    environment = protocol['environment']
    if type(environment) is not dict or not 1 <= len(environment) <= 32:
        _fail('ENVIRONMENT')
    for key, value in environment.items():
        _text(key, 80)
        _text(value, 500)
    _fields(protocol['scope'], {'real', 'substituted', 'unexecuted'})
    for key, rows in protocol['scope'].items():
        _strings(rows, minimum=1 if key == 'real' else 0)
    if (set(protocol['scope']['real']) & set(protocol['scope']['substituted']) or
            set(protocol['scope']['real']) & set(protocol['scope']['unexecuted']) or
            set(protocol['scope']['substituted']) & set(protocol['scope']['unexecuted'])):
        _fail('SCOPE_CONFLICT')
    _strings(protocol['limitations'], minimum=1)
    subject = contract['tested_subject']
    _fields(subject, {'description', 'subject_artifact', 'artifacts'})
    _text(subject['description'])
    _path(subject['subject_artifact'])
    artifacts = _artifacts(subject['artifacts'])
    selected = [item for item in artifacts if item['path'] == subject['subject_artifact']]
    if (len(selected) != 1 or selected[0]['size'] == 0 or
            selected[0]['sha256'] != lead['source_sha256']):
        _fail('SUBJECT_ARTIFACT_BINDING')
    nonempty_hashes = {item['sha256'] for item in artifacts if item['size'] > 0}
    if any(protocol[key] not in nonempty_hashes for key in
           ('specification_sha256', 'harness_sha256', 'fixture_manifest_sha256')):
        _fail('PROTOCOL_ARTIFACT_BINDING')
    cases = _list(contract['cases'], MAX_CASES, 2)
    seen = set()
    categories = set()
    for case in cases:
        _fields(case, {'id', 'category', 'input_sha256', 'assertions'})
        _identifier(case['id'])
        _hash(case['input_sha256'])
        if case['id'] in seen:
            _fail('DUPLICATE_CASE')
        seen.add(case['id'])
        if type(case['category']) is not str or case['category'] not in {'positive', 'negative', 'regression'}:
            _fail('CASE_CATEGORY')
        categories.add(case['category'])
        assertions = _list(case['assertions'], MAX_ASSERTIONS, 1)
        ids = set()
        for assertion in assertions:
            if type(assertion) is not dict:
                _fail('ASSERTION')
            operator = assertion.get('op')
            if operator == 'equal':
                _fields(assertion, {'id', 'field', 'op', 'expected'})
                _encoded(assertion['expected'])
            elif operator == 'finite_range':
                _fields(assertion, {'id', 'field', 'op', 'minimum', 'maximum'})
                if (not _number(assertion['minimum']) or not _number(assertion['maximum']) or
                        assertion['minimum'] > assertion['maximum']):
                    _fail('RANGE')
            else:
                _fail('ASSERTION_OP')
            _identifier(assertion['id'])
            _identifier(assertion['field'])
            if assertion['id'] in ids:
                _fail('DUPLICATE_ASSERTION')
            ids.add(assertion['id'])
    if not {'positive', 'negative'} <= categories:
        _fail('CONTROL_CATEGORIES')
    return copy.deepcopy(contract)


def result_template(contract):
    """Create an incomplete data-only receipt skeleton; no tests are launched."""
    contract = validate_contract(contract)
    return {'schema': 1, 'contract_sha256': sha(canonical(contract)),
            'reporter': '', 'report_sha256': None, 'artifacts': [],
            'cases': [{'id': case['id'], 'outcome': 'NOT_RUN', 'observations': {},
                       'detail': 'External execution not reported.'} for case in contract['cases']]}


def _evidence(contract, evidence):
    _encoded(evidence)
    _fields(evidence, {'schema', 'contract_sha256', 'reporter', 'report_sha256', 'artifacts', 'cases'})
    if type(evidence['schema']) is not int or evidence['schema'] != 1:
        _fail('SCHEMA')
    if _hash(evidence['contract_sha256']) != sha(canonical(contract)):
        _fail('CONTRACT_BINDING')
    _text(evidence['reporter'], 500)
    _hash(evidence['report_sha256'])
    artifacts = _artifacts(evidence['artifacts'])
    if evidence['report_sha256'] not in {a['sha256'] for a in artifacts if a['size'] > 0}:
        _fail('REPORT_ARTIFACT_BINDING')
    rows = _list(evidence['cases'], MAX_CASES)
    known = {case['id'] for case in contract['cases']}
    seen = set()
    for row in rows:
        _fields(row, {'id', 'outcome', 'observations', 'detail'})
        _identifier(row['id'])
        if row['id'] not in known or row['id'] in seen:
            _fail('RESULT_CASE_IDENTITY')
        seen.add(row['id'])
        if type(row['outcome']) is not str or row['outcome'] not in {'OBSERVED', 'ERROR', 'SKIPPED', 'NOT_RUN'}:
            _fail('RESULT_OUTCOME')
        _text(row['detail'], 2000, empty=row['outcome'] == 'OBSERVED')
        observations = row['observations']
        if type(observations) is not dict or len(observations) > 64:
            _fail('OBSERVATIONS')
        for key in observations:
            _identifier(key)
        if row['outcome'] != 'OBSERVED' and observations:
            _fail('UNEXECUTED_OBSERVATIONS')
    return copy.deepcopy(evidence)


def preflight_artifacts(contract, artifact_root, evidence=None):
    """Hash selected local files before a CLI creates a workspace or output.

    The root's undeclared files are not inspected. This verifies the listed bytes,
    not complete dependencies or the external report's claimed execution.
    """
    contract = validate_contract(contract)
    if evidence is not None:
        evidence = _evidence(contract, evidence)
    selected_artifacts = contract['tested_subject']['artifacts'] + ([] if evidence is None else evidence['artifacts'])
    # One budget and unique logical membership across pre-run and post-run inputs.
    _artifacts(selected_artifacts)
    if artifact_root is None:
        _fail('ARTIFACT_ROOT_REQUIRED')
    try:
        root = safe_path(artifact_root)
        if not root.is_dir():
            _fail('ARTIFACT_ROOT')
        verified = []
        for artifact in sorted(selected_artifacts, key=lambda a: a['path']):
            path = safe_path(root / artifact['path'])
            if not path.is_relative_to(root):
                _fail('ARTIFACT_PATH')
            data = read(path, MAX_ARTIFACT_BYTES)
            if len(data) != artifact['size'] or sha(data) != artifact['sha256']:
                _fail('ARTIFACT_MISMATCH')
            verified.append(copy.deepcopy(artifact))
    except Blocked as exc:
        if str(exc).startswith('BEHAVIOR_'):
            raise
        raise Blocked('BEHAVIOR_ARTIFACT_READ') from exc
    except (OSError, TypeError, ValueError) as exc:
        raise Blocked('BEHAVIOR_ARTIFACT_READ') from exc
    references = {'subject': contract['target']['code_lead']['source_sha256'],
                  **{key: contract['protocol'][key] for key in
                     ('specification_sha256', 'harness_sha256', 'fixture_manifest_sha256')}}
    if evidence is not None:
        references['external_report_sha256'] = evidence['report_sha256']
    return {'schema': 1, 'status': 'SELECTED_ARTIFACT_BYTES_VERIFIED',
            'artifacts': verified, 'artifact_manifest_sha256': sha(canonical(verified)),
            'verified_reference_hashes': references, 'artifact_bytes_verified': True,
            'whole_artifact_root_verified': False, 'dependency_completeness_verified': False,
            'case_input_mapping_verified': False, 'report_translation_verified': False,
            'source_code_executed': False, 'external_execution_authenticated': False}


def _case_result(case, observed):
    if observed is None:
        return {'id': case['id'], 'category': case['category'], 'status': 'MISSING',
                'reported_outcome': None, 'assertions': [], 'observations': {},
                'detail': 'No recorded result for this required case.'}
    result = {'id': case['id'], 'category': case['category'],
              'reported_outcome': observed['outcome'], 'assertions': [],
              'observations': copy.deepcopy(observed['observations']), 'detail': observed['detail']}
    if observed['outcome'] != 'OBSERVED':
        return {**result, 'status': observed['outcome']}
    for assertion in case['assertions']:
        field = assertion['field']
        check = copy.deepcopy(assertion)
        if field not in observed['observations']:
            check['status'] = 'MISSING_OBSERVATION'
        else:
            actual = observed['observations'][field]
            check['observed'] = copy.deepcopy(actual)
            if assertion['op'] == 'equal':
                passed = _same(actual, assertion['expected'])
            else:
                passed = (_number(actual) and
                          assertion['minimum'] <= actual <= assertion['maximum'])
            check['status'] = 'AGREES' if passed else 'DISAGREES'
        result['assertions'].append(check)
    states = {check['status'] for check in result['assertions']}
    result['status'] = ('FAIL' if 'DISAGREES' in states else
                        'INCOMPLETE_OBSERVATIONS' if 'MISSING_OBSERVATION' in states else 'PASS')
    return result


def evaluate(bundle, corpora, registry, contract, evidence=None, *, artifact_root):
    """Recompute transfer applicability and assertions without executing source."""
    contract = validate_contract(contract)
    evidence = None if evidence is None else _evidence(contract, evidence)
    artifacts = preflight_artifacts(contract, artifact_root, evidence)
    review = _transfer(bundle, corpora, registry)
    before = sha(canonical(review))
    target = contract['target']
    expected = _target(review, {'hypothesis': target['hypothesis'],
                               'mechanism': target['mechanism'],
                               'corpus': target['code_lead']['corpus'],
                               'candidate_id': _candidate_id(target['code_lead'])})
    if not _same(target, expected):
        _fail('TARGET_BINDING')
    received = {} if evidence is None else {case['id']: case for case in evidence['cases']}
    cases = [_case_result(case, received.get(case['id'])) for case in contract['cases']]
    counts = {name: sum(case['status'] == name for case in cases) for name in
              ('PASS', 'FAIL', 'ERROR', 'SKIPPED', 'NOT_RUN', 'MISSING', 'INCOMPLETE_OBSERVATIONS')}
    incomplete_ids = [case['id'] for case in cases if case['status'] in
                      {'SKIPPED', 'NOT_RUN', 'MISSING', 'INCOMPLETE_OBSERVATIONS'} or
                      any(check['status'] == 'MISSING_OBSERVATION' for check in case['assertions'])]
    failing_ids = [case['id'] for case in cases if case['status'] in {'FAIL', 'ERROR'}]
    status = ('PLAN_ONLY_NOT_EXECUTED' if evidence is None else
              'REPORTED_FAIL' if failing_ids else
              'REPORTED_INCOMPLETE' if incomplete_ids else 'REPORTED_PASS')
    # Check that supplied files did not change during this review. This is a
    # trusted-directory consistency check, not hostile-host isolation.
    if artifacts != preflight_artifacts(contract, artifact_root, evidence):
        _fail('ARTIFACTS_CHANGED_DURING_REVIEW')
    binding = {'version': VERSION, 'module_sha256': sha(read(Path(__file__))),
               'transfer_review_sha256': before, 'contract_sha256': sha(canonical(contract)),
               'evidence_sha256': None if evidence is None else sha(canonical(evidence)),
               'artifact_manifest_sha256': artifacts['artifact_manifest_sha256']}
    layer = {'schema': 1, 'id': 'behavior_' + sha(canonical(binding)), 'binding': binding,
             'status': status, 'target': copy.deepcopy(target), 'objective': contract['objective'],
             'contract': contract, 'reported_evidence': evidence, 'cases': cases,
             'summary': {'expected_cases': len(cases), 'recorded_cases': len(received),
                         'counts': counts, 'failing_case_ids': failing_ids,
                         'incomplete_case_ids': incomplete_ids},
             'artifact_verification': artifacts, 'authority': copy.deepcopy(_AUTHORITY),
             'evidence_basis': 'EXTERNAL_RECORDED_OBSERVATIONS_NOT_EXECUTION_ATTESTATION',
             'limits': ['Assertions are recomputed from supplied observations; external execution is not authenticated.',
                        'The operator translates the hash-bound external report; translation fidelity is not verified.',
                        'Case input hashes are operator-declared mappings into the bound fixtures, not independently verified case extraction.',
                        'Source and artifact bytes are bound locally; complete dependencies and semantic suitability remain unverified.',
                        'A supplied contract and hashes do not prove it was frozen before candidate work or execution.',
                        'Original opportunity, declaration, rights and release decisions remain unchanged.']}
    if sha(canonical(review)) != before:
        _fail('ORIGINAL_REPORT_CHANGED')
    return {**review, 'behavioral_review': layer}
