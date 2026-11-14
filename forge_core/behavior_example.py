"""Synthetic source and recorded observations for behavioral review examples.

The source is indexed, never imported. Observation records are illustrative data,
not claimed executions. This helper does not provide an application test runner.
"""
from copy import deepcopy
from pathlib import Path

from . import behavior_review as behavior, corpus
from .common import Blocked, canonical, read, safe_path, sha, write
from .engine import ROOT
from .transfer_review import evaluate_with_contracts

SOURCE = b'''def bounded_score(value):
    if not isinstance(value, bool) and isinstance(value, (int, float)) and 0 <= value <= 1:
        return {"value": float(value), "accepted": True}
    return {"value": 0.0, "accepted": False}
'''


def declarations():
    statement = 'A synthetic workflow needs a finite score between zero and one before publishing it.'
    ref = {'source': 'example', 'start': 0, 'end': len(statement), 'quote': statement}
    bundle = {
        'schema': 1, 'title': 'Bound a score before publishing', 'as_of': '2026-01-01',
        'max_age_days': 365, 'public_brief': 'Synthetic behavioral review example.',
        'sources': [{'id': 'example', 'text': statement, 'kind': 'synthetic', 'public': False,
                     'origin_group': 'synthetic-example', 'uri': 'fixture://behavior/example',
                     'observed_on': '2026-01-01', 'derived_from': [],
                     'capture_method': 'synthetic_fixture'}],
        'workflows': [{'id': 'publish-score', 'title': 'Publish a bounded score', 'actor': 'operator',
                       'domain': 'synthetic metrics', 'purpose': 'Keep published scalar values in range.',
                       'initial_fields': ['raw_score'],
                       'steps': [{'id': 'publish', 'action': 'Publish the bounded score.',
                                  'requires': ['bounded_score'], 'produces': ['published_score'],
                                  'evidence': [ref]}],
                       'needs': [{'id': 'bounded', 'step': 'publish', 'field': 'bounded_score',
                                  'pattern': 'verification_gap', 'why': statement, 'basis': 'inferred',
                                  'evidence': [ref]}]}],
        'observations': [],
        'mechanisms': [{'id': 'range-guard', 'title': 'Check the score boundary',
                        'domain': 'numeric validation', 'purpose': 'Reject invalid scalar scores.',
                        'mechanism': 'Return a bounded value and an acceptance indicator.',
                        'addresses': ['verification_gap'], 'requires': ['raw_score'],
                        'provides': ['bounded_score'], 'limits': ['Synthetic example only.'],
                        'evidence': [ref], 'code_queries': ['symbol:bounded_score']}],
        'preferences': {},
    }
    def slot(kind):
        return {'kind': kind, 'unit': 'dimensionless', 'frame': 'n/a', 'clock': 'n/a'}
    registry = {
        'schema': 1, 'bundle_sha256': sha(canonical(bundle)),
        'annotation_basis': 'SUPERVISOR_DECLARED_NOT_INDEPENDENTLY_VERIFIED',
        'mechanisms': {'range-guard': {
            'mechanism_sha256': sha(canonical(bundle['mechanisms'][0])),
            'source_bindings': {'example': sha(statement.encode())},
            'contract': {'id': 'range-guard', 'domain': 'numeric validation',
                         'inputs': {'raw_score': slot('raw_score')},
                         'outputs': [slot('bounded_score')], 'effects': [], 'preserves': []}}},
        'workflows': {'publish-score': {
            'workflow_sha256': sha(canonical(bundle['workflows'][0])),
            'available_contracts': {'raw_score': slot('raw_score')},
            'need_contracts': {'bounded': {
                'roles': [{'id': 'score-boundary', 'mode': 'direct', 'contract': slot('bounded_score')}],
                'allowed_effects': [], 'required_preserves': [], 'domain': 'synthetic metrics'}}}},
    }
    return bundle, registry


def prepare(workbench, out, run_id='behavior-example'):
    """Create a new, entirely synthetic example outside source and workspace."""
    workbench.guard()
    out = safe_path(out)
    if (out.exists() or out.is_relative_to(ROOT) or out.is_relative_to(workbench.home) or
            workbench.home.is_relative_to(out)):
        raise Blocked('NEW_EXTERNAL_OUTPUT_REQUIRED')
    out.mkdir(parents=True)
    source = out / 'source'
    artifacts = out / 'artifacts'
    source.mkdir(); artifacts.mkdir()
    write(source / 'component.py', SOURCE, new=True)
    write(artifacts / 'component.py', SOURCE, new=True)
    manifest = {'schema': 1, 'files': [{'provider': 'synthetic', 'file_id': 'component.py',
                'path': 'component.py', 'size': len(SOURCE), 'sha256': sha(SOURCE),
                'source_url': 'fixture://component.py'}]}
    write(out / 'manifest.json', canonical(manifest), new=True)
    report = corpus.run(workbench, source, out / 'manifest.json',
                        workbench.home / 'corpora' / run_id, max_containers=1)
    bundle, registry = declarations()
    corpora = [(run_id, report)]
    base = evaluate_with_contracts(bundle, corpora, registry)
    row = base['transfer_reviews'][0]
    lead = row['code_leads'][0]['candidates'][0]
    selection = {'hypothesis': row['hypothesis'], 'mechanism': 'range-guard',
                 'corpus': run_id, 'candidate_id': lead['definition_id']}
    plan = behavior.draft(bundle, corpora, registry, selection)
    fixtures = [
        {'id': 'middle', 'category': 'positive', 'input': 0.5, 'value': 0.5, 'accepted': True},
        {'id': 'zero', 'category': 'positive', 'input': 0.0, 'value': 0.0, 'accepted': True},
        {'id': 'below', 'category': 'negative', 'input': -0.1, 'value': 0.0, 'accepted': False},
        {'id': 'wrong-type', 'category': 'negative', 'input': 'invalid', 'value': 0.0, 'accepted': False},
    ]
    payloads = {
        'specification.json': canonical({'objective': 'Preserve valid scores; reject invalid values.',
                                         'cases': fixtures, 'synthetic_observations_only': True}),
        'fixtures.json': canonical(fixtures),
        'harness-description.txt': b'Illustrative recorded observations. No source or harness is executed.\n',
    }
    for name, data in payloads.items():
        write(artifacts / name, data, new=True)
    contract = deepcopy(plan['contract'])
    contract['objective'] = 'Reported outputs preserve the four declared scalar cases and stay within [0,1].'
    contract['protocol'] = {
        'specification_sha256': sha(payloads['specification.json']),
        'harness_sha256': sha(payloads['harness-description.txt']),
        'fixture_manifest_sha256': sha(payloads['fixtures.json']),
        'environment': {'mode': 'synthetic recorded-observation example'},
        'scope': {'real': ['Synthetic data preparation'], 'substituted': ['Component execution'],
                  'unexecuted': ['Application integration']},
        'limitations': ['Observations are illustrative data, not actual component execution.'],
    }
    all_inputs = {'component.py': SOURCE, **payloads}
    contract['tested_subject'] = {
        'description': 'Exact indexed synthetic component; not executed.', 'subject_artifact': 'component.py',
        'artifacts': [{'path': name, 'size': len(data), 'sha256': sha(data)}
                      for name, data in sorted(all_inputs.items())],
    }
    contract['cases'] = [{
        'id': case['id'], 'category': case['category'], 'input_sha256': sha(canonical(case['input'])),
        'assertions': [
            {'id': 'bounded', 'field': 'value', 'op': 'finite_range', 'minimum': 0.0, 'maximum': 1.0},
            {'id': 'value', 'field': 'value', 'op': 'equal', 'expected': case['value']},
            {'id': 'accepted', 'field': 'accepted', 'op': 'equal', 'expected': case['accepted']},
        ],
    } for case in fixtures]
    behavior.validate_contract(contract)
    for name, data in {'bundle.json': bundle, 'registry.json': registry, 'plan.json': plan,
                       'contract.json': contract, 'result-template.json': behavior.result_template(contract)}.items():
        write(out / name, canonical(data), new=True)
    return {'out': out, 'artifact_root': artifacts, 'source_root': source, 'selection': selection,
            'bundle': bundle, 'registry': registry, 'corpora': corpora, 'contract': contract,
            'fixtures': fixtures, 'transfer_report': base}


def recorded_evidence(context, name='pass', *, fail=False, incomplete=False):
    """Write illustrative observations after the contract has been fixed."""
    rows = [{'id': case['id'], 'outcome': 'OBSERVED',
             'observations': {'value': case['value'], 'accepted': case['accepted']},
             'detail': 'Synthetic recorded observations; no component execution.'}
            for case in context['fixtures']]
    if fail:
        rows[2]['observations'] = {'value': -0.1, 'accepted': True}
    if incomplete:
        rows[-1] = {'id': rows[-1]['id'], 'outcome': 'SKIPPED', 'observations': {},
                    'detail': 'Synthetic skipped case.'}
    contract_hash = sha(canonical(context['contract']))
    report = canonical({'contract_sha256': contract_hash, 'cases': rows,
                        'synthetic_observations_only': True, 'source_code_executed': False})
    # The outcome artifact is deliberately absent from the pre-run contract.
    artifact_name = name + '-report.json'
    write(context['artifact_root'] / artifact_name, report, new=True)
    evidence = {'schema': 1, 'contract_sha256': contract_hash,
                'reporter': 'Synthetic example data; not an execution witness',
                'report_sha256': sha(report),
                'artifacts': [{'path': artifact_name, 'size': len(report), 'sha256': sha(report)}],
                'cases': rows}
    write(context['out'] / (name + '-evidence.json'), canonical(evidence), new=True)
    return evidence


def run(workbench, out):
    context = prepare(workbench, out)
    frozen = read(context['out'] / 'contract.json')
    results = {}
    for name, flags in [('pass', {}), ('fail', {'fail': True}), ('incomplete', {'incomplete': True})]:
        evidence = recorded_evidence(context, name, **flags)
        value = behavior.evaluate(context['bundle'], context['corpora'], context['registry'],
                                  context['contract'], evidence, artifact_root=context['artifact_root'])
        if (any(value['behavioral_review']['authority'].values()) or
                value['behavioral_review']['artifact_verification']['artifact_bytes_verified'] is not True):
            raise Blocked('BEHAVIOR_EXAMPLE_AUTHORITY')
        if ({key: value[key] for key in context['transfer_report']} != context['transfer_report'] or
                read(context['out'] / 'contract.json') != frozen or
                read(context['source_root'] / 'component.py') != SOURCE):
            raise Blocked('BEHAVIOR_EXAMPLE_CHANGED')
        write(context['out'] / (name + '-review.json'), canonical(value), new=True)
        results[name] = value['behavioral_review']['status']
    expected = {'pass': 'REPORTED_PASS', 'fail': 'REPORTED_FAIL', 'incomplete': 'REPORTED_INCOMPLETE'}
    if results != expected:raise Blocked('BEHAVIOR_EXAMPLE_STATUS')
    summary = {'status': 'BEHAVIORAL_REVIEW_EXAMPLE_PASSED', 'results': results,
               'cases_per_report': len(context['fixtures']), 'contract_sha256': sha(frozen),
               'same_contract_for_all_outcomes': True, 'source_code_executed': False,
               'observations': 'SYNTHETIC_DATA_NOT_EXECUTION_RESULTS',
               'original_opportunity_and_transfer_preserved': True, 'out': str(context['out'])}
    write(context['out'] / 'summary.json', canonical(summary), new=True)
    return summary
