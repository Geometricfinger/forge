"""Recorded observations are useful evidence without becoming execution authority."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from forge_core import behavior_review as behavior
from forge_core.common import Blocked, canonical, loads, sha


class BehavioralReview(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        source = b'def bounded(value):\n    return max(0.0, min(value, 1.0))\n'
        files = {'candidate.py': source, 'spec.json': b'{"scope":"synthetic bounded value"}',
                 'harness.py': b'raise AssertionError("must never execute")\n',
                 'fixtures.json': b'[0.25,-1.0]',
                 'recorded.json': b'{"source":"synthetic externally recorded observations"}'}
        for name, body in files.items():
            (self.root / name).write_bytes(body)
        lead = {'corpus': 'synthetic', 'query': 'bounded', 'name': 'bounded',
                'definition_id': 'definition_' + 'd' * 64,
                'source_id': 'source_' + 'e' * 64, 'source_sha256': sha(source),
                'path': 'candidate.py', 'start_line': 1, 'end_line': 2,
                'index_binding': {'source': sha(source)},
                'runtime_tested': False, 'rights_reviewed': False}
        self.base = {'id': 'transfer_synthetic', 'binding': {'fixture': 'record'},
                     'original_report': {'hypotheses': [
                         {'id': 'hyp_synthetic', 'workflow': 'bounded-work', 'need': {'id': 'bounded-result'}}]},
                     'transfer_reviews': [{'hypothesis': 'hyp_synthetic', 'workflow': 'bounded-work',
                         'status': 'DECLARATION_REVIEW_ONLY',
                         'pairs': [{'mechanism': 'clamp', 'status': 'DECLARED_SUPPORT_FIT', 'role_id': 'bounded-helper'}],
                         'experiment_candidates': ['clamp'],
                         'code_leads': [{'mechanism': 'clamp', 'candidates': [lead]}]}],
                     'authority': {'execute_source': False, 'release_approved': False}}
        self.transfer = mock.patch.object(behavior, '_transfer', side_effect=lambda *_: copy.deepcopy(self.base))
        self.transfer.start()
        self.addCleanup(self.transfer.stop)
        selection = {'hypothesis': 'hyp_synthetic', 'mechanism': 'clamp', 'corpus': 'synthetic',
                     'candidate_id': lead['definition_id']}
        self.selection = selection
        self.contract = behavior.draft({}, [], {}, selection)['contract']
        self.contract.update(objective='Return a bounded number and preserve already valid values.',
            protocol={'specification_sha256': sha(files['spec.json']), 'harness_sha256': sha(files['harness.py']),
                      'fixture_manifest_sha256': sha(files['fixtures.json']),
                      'environment': {'python': 'reviewed synthetic runtime'},
                      'scope': {'real': ['synthetic bounded function'], 'substituted': ['synthetic caller'],
                                'unexecuted': ['whole application']},
                      'limitations': ['Reported synthetic observations only.']},
            tested_subject={'description': 'exact indexed synthetic candidate', 'subject_artifact': 'candidate.py',
                            'artifacts': [{'path': name, 'size': len(body), 'sha256': sha(body)}
                                          for name, body in sorted(files.items()) if name != 'recorded.json']},
            cases=[{'id': 'valid', 'category': 'positive', 'input_sha256': sha(b'0.25'),
                    'assertions': [{'id': 'bounded', 'field': 'value', 'op': 'finite_range', 'minimum': 0.0, 'maximum': 1.0},
                                   {'id': 'preserved', 'field': 'value', 'op': 'equal', 'expected': 0.25}]},
                   {'id': 'out_of_range', 'category': 'negative', 'input_sha256': sha(b'-1.0'),
                    'assertions': [{'id': 'bounded', 'field': 'value', 'op': 'finite_range', 'minimum': 0.0, 'maximum': 1.0}]}])
        self.evidence = {'schema': 1, 'contract_sha256': sha(canonical(self.contract)),
                         'reporter': 'synthetic external supervisor', 'report_sha256': sha(files['recorded.json']),
                         'artifacts': [{'path': 'recorded.json', 'size': len(files['recorded.json']),
                                        'sha256': sha(files['recorded.json'])}],
                         'cases': [{'id': 'valid', 'outcome': 'OBSERVED', 'observations': {'value': 0.25}, 'detail': ''},
                                   {'id': 'out_of_range', 'outcome': 'OBSERVED', 'observations': {'value': 0.0}, 'detail': ''}]}

    def evaluate(self, contract=None, evidence=None):
        return behavior.evaluate({}, [], {}, self.contract if contract is None else contract,
                                 self.evidence if evidence is None else evidence, artifact_root=self.root)

    def test_complete_pass_is_reported_not_authenticated(self):
        result = self.evaluate()
        self.assertEqual(result['behavioral_review']['status'], 'REPORTED_PASS')
        layer = result['behavioral_review']
        self.assertEqual(layer['summary']['counts']['PASS'], 2)
        self.assertTrue(layer['artifact_verification']['artifact_bytes_verified'])
        self.assertFalse(layer['artifact_verification']['whole_artifact_root_verified'])
        for key, value in layer['authority'].items():
            self.assertIs(value, False) if type(value) is bool else self.assertEqual(value, 0)
        self.assertEqual({key: value for key, value in result.items() if key != 'behavioral_review'}, self.base)

    def test_draft_is_incomplete_not_an_execution_request(self):
        draft = behavior.draft({}, [], {}, self.selection)
        self.assertEqual(draft['status'], 'DRAFT_REQUIRES_OPERATOR_INPUT')
        self.assertEqual(draft['contract']['cases'], [])
        with self.assertRaises(Blocked):
            behavior.validate_contract(draft['contract'])
        self.assertFalse(draft['authority']['execution_authorized'])

    def test_no_evidence_remains_plan(self):
        result = behavior.evaluate({}, [], {}, self.contract, artifact_root=self.root)['behavioral_review']
        self.assertEqual(result['status'], 'PLAN_ONLY_NOT_EXECUTED')
        self.assertEqual(result['summary']['counts']['MISSING'], 2)

    def test_result_template_is_case_complete_and_explicitly_not_run(self):
        result = behavior.result_template(self.contract)
        self.assertEqual(result['contract_sha256'], sha(canonical(self.contract)))
        self.assertEqual([r['id'] for r in result['cases']], ['valid', 'out_of_range'])
        self.assertTrue(all(r['outcome'] == 'NOT_RUN' for r in result['cases']))
        with self.assertRaises(Blocked):
            self.evaluate(evidence=result)
        result.update(reporter=self.evidence['reporter'], report_sha256=self.evidence['report_sha256'],
                      artifacts=copy.deepcopy(self.evidence['artifacts']))
        self.assertEqual(self.evaluate(evidence=result)['behavioral_review']['status'], 'REPORTED_INCOMPLETE')

    def test_failed_observation_not_claimed_summary_controls_outcome(self):
        self.evidence['cases'][1]['observations']['value'] = -1.0
        result = self.evaluate()['behavioral_review']
        self.assertEqual(result['status'], 'REPORTED_FAIL')
        self.assertEqual(result['summary']['failing_case_ids'], ['out_of_range'])
        self.evidence['passed'] = True
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_FIELDS'):
            self.evaluate()

    def test_skipped_missing_not_run_and_errors_remain_distinct(self):
        for outcome, expected in [('SKIPPED', 'REPORTED_INCOMPLETE'),
                                  ('NOT_RUN', 'REPORTED_INCOMPLETE'), ('ERROR', 'REPORTED_FAIL')]:
            evidence = copy.deepcopy(self.evidence)
            evidence['cases'][1].update(outcome=outcome, observations={}, detail='recorded reason')
            result = self.evaluate(evidence=evidence)['behavioral_review']
            self.assertEqual(result['status'], expected)
            self.assertEqual(result['summary']['counts'][outcome], 1)
        self.evidence['cases'].pop()
        result = self.evaluate()['behavioral_review']
        self.assertEqual(result['status'], 'REPORTED_INCOMPLETE')
        self.assertEqual(result['summary']['counts']['MISSING'], 1)

    def test_failure_does_not_hide_missing_case_or_observation(self):
        self.evidence['cases'].pop()
        self.evidence['cases'][0]['observations'] = {'value': -1.0}
        self.contract['cases'][0]['assertions'].append(
            {'id': 'missing', 'field': 'other', 'op': 'equal', 'expected': None})
        self.evidence['contract_sha256'] = sha(canonical(self.contract))
        result = self.evaluate()['behavioral_review']
        self.assertEqual(result['status'], 'REPORTED_FAIL')
        self.assertEqual(result['summary']['failing_case_ids'], ['valid'])
        self.assertEqual(result['summary']['incomplete_case_ids'], ['valid', 'out_of_range'])

    def test_missing_observation_is_not_null_zero_or_success(self):
        self.evidence['cases'][0]['observations'] = {}
        result = self.evaluate()['behavioral_review']
        self.assertEqual(result['status'], 'REPORTED_INCOMPLETE')
        self.assertEqual(result['cases'][0]['status'], 'INCOMPLETE_OBSERVATIONS')
        self.assertNotIn('observed', result['cases'][0]['assertions'][0])

    def test_range_is_inclusive_and_rejects_bool_and_numeric_strings(self):
        for value, expected in [(0.0, 'PASS'), (1, 'PASS'), (False, 'FAIL'), ('0.5', 'FAIL'), (1.01, 'FAIL')]:
            evidence = copy.deepcopy(self.evidence)
            evidence['cases'][1]['observations']['value'] = value
            self.assertEqual(self.evaluate(evidence=evidence)['behavioral_review']['cases'][1]['status'], expected)

    def test_json_equality_is_typed_recursively(self):
        self.contract['cases'][0]['assertions'] = [{'id': 'exact', 'field': 'value', 'op': 'equal',
                                                  'expected': {'nested': [1, True, 1.0]}}]
        self.evidence['contract_sha256'] = sha(canonical(self.contract))
        for value, expected in [({'nested': [1, True, 1.0]}, 'PASS'),
                                ({'nested': [True, 1, 1.0]}, 'FAIL'),
                                ({'nested': [1, True, 1]}, 'FAIL')]:
            evidence = copy.deepcopy(self.evidence)
            evidence['cases'][0]['observations']['value'] = value
            self.assertEqual(self.evaluate(evidence=evidence)['behavioral_review']['cases'][0]['status'], expected)

    def test_incomplete_does_not_skip_validation_of_later_bad_row(self):
        self.evidence['cases'][0].update(outcome='NOT_RUN', observations={}, detail='not run')
        self.evidence['cases'][1]['execute_source'] = True
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_FIELDS'):
            self.evaluate()

    def test_duplicate_and_unknown_result_cases_are_rejected(self):
        for changed in ('valid', 'unlisted'):
            evidence = copy.deepcopy(self.evidence)
            evidence['cases'][1]['id'] = changed
            with self.assertRaisesRegex(Blocked, 'BEHAVIOR_RESULT_CASE_IDENTITY'):
                self.evaluate(evidence=evidence)

    def test_case_order_does_not_reorder_contract_display(self):
        self.evidence['cases'].reverse()
        self.assertEqual([c['id'] for c in self.evaluate()['behavioral_review']['cases']], ['valid', 'out_of_range'])

    def test_repeated_review_is_deterministic_and_inputs_unchanged(self):
        before = copy.deepcopy((self.base, self.contract, self.evidence))
        first = self.evaluate()
        self.assertEqual(first, self.evaluate())
        self.assertEqual((self.base, self.contract, self.evidence), before)

    def test_changed_contract_cannot_reuse_old_evidence(self):
        for change in ('environment', 'expectation', 'objective'):
            contract = copy.deepcopy(self.contract)
            if change == 'environment':
                contract['protocol']['environment']['python'] = 'other runtime'
            elif change == 'expectation':
                contract['cases'][1]['assertions'][0]['maximum'] = 2.0
            else:
                contract['objective'] = 'different claim'
            with self.assertRaisesRegex(Blocked, 'BEHAVIOR_CONTRACT_BINDING'):
                self.evaluate(contract=contract)

    def test_current_transfer_changed_cannot_inherit_contract(self):
        self.base['binding']['fixture'] = 'changed bundle or registry'
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_TARGET_BINDING'):
            self.evaluate()

    def test_wrong_workflow_need_and_role_cannot_transplant_receipt(self):
        for field in ('workflow', 'need', 'role'):
            contract = copy.deepcopy(self.contract)
            contract['target'][field] = 'other'
            evidence = copy.deepcopy(self.evidence)
            evidence['contract_sha256'] = sha(canonical(contract))
            with self.assertRaisesRegex(Blocked, 'BEHAVIOR_TARGET_BINDING'):
                self.evaluate(contract, evidence)

    def test_changed_or_unselected_mechanism_cannot_gain_authority(self):
        for status in ('MISSING_INPUTS', 'CONTRACT_MISMATCH', 'UNRESOLVED_CONTRACT'):
            self.base['transfer_reviews'][0]['pairs'][0]['status'] = status
            with self.assertRaisesRegex(Blocked, 'BEHAVIOR_TARGET_NOT_SELECTED'):
                self.evaluate()
        self.base['transfer_reviews'][0]['pairs'][0]['status'] = 'DECLARED_SUPPORT_FIT'
        self.base['transfer_reviews'][0]['experiment_candidates'] = []
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_TARGET_NOT_SELECTED'):
            self.evaluate()

    def test_original_evidence_gate_cannot_be_overridden(self):
        self.base['transfer_reviews'][0]['status'] = 'ORIGINAL_EVIDENCE_GATE_RETAINED'
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_TARGET_NOT_SELECTED'):
            self.evaluate()

    def test_other_candidate_and_ambiguous_code_lead_are_rejected(self):
        selection = copy.deepcopy(self.selection)
        selection['candidate_id'] = 'unknown'
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_CODE_LEAD'):
            behavior.draft({}, [], {}, selection)
        group = self.base['transfer_reviews'][0]['code_leads'][0]
        group['candidates'].append(copy.deepcopy(group['candidates'][0]))
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_CODE_LEAD'):
            behavior.draft({}, [], {}, self.selection)

    def test_subject_artifact_must_match_actual_indexed_source(self):
        self.contract['tested_subject']['subject_artifact'] = 'harness.py'
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_SUBJECT_ARTIFACT_BINDING'):
            behavior.validate_contract(self.contract)

    def test_modified_indexed_source_changes_binding(self):
        lead = self.base['transfer_reviews'][0]['code_leads'][0]['candidates'][0]
        lead['source_sha256'] = 'f' * 64
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_TARGET_BINDING'):
            self.evaluate()

    def test_actual_changed_candidate_bytes_are_rejected(self):
        (self.root / 'candidate.py').write_bytes(b'def bounded(value):\n    return value\n')
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_ARTIFACT_MISMATCH'):
            self.evaluate()

    def test_missing_artifact_root_never_claims_byte_verification(self):
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_ARTIFACT_ROOT_REQUIRED'):
            behavior.preflight_artifacts(self.contract, None, self.evidence)
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_ARTIFACT_ROOT'):
            behavior.preflight_artifacts(self.contract, self.root / 'absent', self.evidence)

    def test_protocol_hashes_require_actual_declared_nonempty_artifacts(self):
        for name in ('specification_sha256', 'harness_sha256', 'fixture_manifest_sha256'):
            contract = copy.deepcopy(self.contract)
            contract['protocol'][name] = 'a' * 64
            with self.assertRaisesRegex(Blocked, 'BEHAVIOR_PROTOCOL_ARTIFACT_BINDING'):
                behavior.validate_contract(contract)
        (self.root / 'harness.py').unlink()
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_ARTIFACT_READ'):
            behavior.preflight_artifacts(self.contract, self.root, self.evidence)

    def test_external_report_requires_actual_hash_bound_bytes(self):
        self.evidence['report_sha256'] = 'a' * 64
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_REPORT_ARTIFACT_BINDING'):
            self.evaluate()
        self.evidence['report_sha256'] = self.evidence['artifacts'][0]['sha256']
        (self.root / 'recorded.json').write_text('{"changed":true}')
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_ARTIFACT_MISMATCH'):
            self.evaluate()

    def test_artifacts_are_data_and_extra_root_files_are_not_claimed_verified(self):
        (self.root / 'extra.py').write_text('raise AssertionError("not executed")\n')
        verification = behavior.preflight_artifacts(self.contract, self.root, self.evidence)
        self.assertEqual(len(verification['artifacts']), 5)
        self.assertFalse(verification['whole_artifact_root_verified'])
        self.assertFalse(verification['source_code_executed'])
        self.assertEqual(self.evaluate()['behavioral_review']['status'], 'REPORTED_PASS')

    def test_symlink_artifact_and_root_rejected(self):
        original = self.root / 'candidate.py'
        saved = self.root / 'saved.py'
        original.rename(saved)
        original.symlink_to(saved)
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_ARTIFACT_READ'):
            behavior.preflight_artifacts(self.contract, self.root, self.evidence)
        linked = self.root / 'linked-root'
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_ARTIFACT_READ'):
            behavior.preflight_artifacts(self.contract, linked, self.evidence)

    def test_path_traversal_absolute_and_ambiguous_paths_rejected(self):
        for path in ('../candidate.py', '/candidate.py', './candidate.py', 'a//candidate.py', 'a\\candidate.py', 'a:candidate.py'):
            contract = copy.deepcopy(self.contract)
            contract['tested_subject']['artifacts'][0]['path'] = path
            with self.assertRaisesRegex(Blocked, 'BEHAVIOR_ARTIFACT_PATH'):
                behavior.validate_contract(contract)

    def test_nonfinite_json_and_duplicate_json_keys_rejected(self):
        for value in (float('nan'), float('inf'), float('-inf')):
            evidence = copy.deepcopy(self.evidence)
            evidence['cases'][0]['observations']['value'] = value
            with self.assertRaisesRegex(Blocked, 'BEHAVIOR_NONFINITE_JSON'):
                self.evaluate(evidence=evidence)
        with self.assertRaisesRegex(Blocked, 'DUPLICATE_JSON_KEY'):
            loads(b'{"schema":1,"schema":1}')

    def test_boolean_schema_sizes_and_unhashable_tags_rejected_cleanly(self):
        for mutate in (lambda c: c.update(schema=True),
                       lambda c: c['tested_subject']['artifacts'][0].update(size=True),
                       lambda c: c['cases'][0].update(category=[])):
            contract = copy.deepcopy(self.contract)
            mutate(contract)
            with self.assertRaises(Blocked):
                behavior.validate_contract(contract)
        for malformed in ([], {}):
            for via_json in (False, True):
                contract = copy.deepcopy(self.contract)
                contract['cases'][0]['category'] = malformed
                if via_json:
                    contract = loads(canonical(contract))
                with self.assertRaisesRegex(Blocked, 'BEHAVIOR_CASE_CATEGORY'):
                    behavior.validate_contract(contract)
                evidence = copy.deepcopy(self.evidence)
                evidence['cases'][0]['outcome'] = malformed
                if via_json:
                    evidence = loads(canonical(evidence))
                with self.assertRaisesRegex(Blocked, 'BEHAVIOR_RESULT_OUTCOME'):
                    self.evaluate(evidence=evidence)

    def test_prerun_contract_stays_fixed_across_different_outcome_artifacts(self):
        original_contract = canonical(self.contract)
        contract_digest = sha(original_contract)
        outcomes = []
        for name, value in [('passed.json', 0.0), ('failed.json', -1.0)]:
            # An external report can bind the contract without a digest cycle.
            report = canonical({'contract_sha256': contract_digest, 'observed_value': value})
            (self.root / name).write_bytes(report)
            evidence = copy.deepcopy(self.evidence)
            evidence['artifacts'] = [{'path': name, 'size': len(report), 'sha256': sha(report)}]
            evidence['report_sha256'] = sha(report)
            evidence['cases'][1]['observations']['value'] = value
            outcomes.append(self.evaluate(evidence=evidence)['behavioral_review'])
        self.assertEqual([x['status'] for x in outcomes], ['REPORTED_PASS', 'REPORTED_FAIL'])
        self.assertEqual(canonical(self.contract), original_contract)
        self.assertTrue(all(x['binding']['contract_sha256'] == contract_digest for x in outcomes))
        self.assertNotEqual(outcomes[0]['id'], outcomes[1]['id'])

    def test_duplicate_paths_across_contract_and_result_artifacts_rejected(self):
        self.evidence['artifacts'].append(copy.deepcopy(self.contract['tested_subject']['artifacts'][0]))
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_DUPLICATE_ARTIFACT'):
            self.evaluate()

    def test_combined_contract_and_result_artifact_budget_is_bounded(self):
        evidence = copy.deepcopy(self.evidence)
        contract = copy.deepcopy(self.contract)
        contract['tested_subject']['artifacts'][0]['size'] = behavior.MAX_ARTIFACT_BYTES
        for index in range(3):
            evidence['artifacts'].append({'path': 'large-' + str(index), 'size': behavior.MAX_ARTIFACT_BYTES,
                                          'sha256': str(index) * 64})
        evidence['contract_sha256'] = sha(canonical(contract))
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_ARTIFACT_BOUNDS'):
            behavior.preflight_artifacts(contract, self.root, evidence)

    def test_unknown_authority_commands_and_expression_operators_rejected(self):
        for field, value in [('execute_source', True), ('release_approved', 1), ('command', 'python candidate.py')]:
            contract = copy.deepcopy(self.contract)
            contract[field] = value
            with self.assertRaisesRegex(Blocked, 'BEHAVIOR_FIELDS'):
                behavior.validate_contract(contract)
        self.contract['cases'][0]['assertions'][0]['op'] = 'eval'
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_ASSERTION_OP'):
            behavior.validate_contract(self.contract)

    def test_duplicate_case_assertion_and_artifact_identities_rejected(self):
        for section in ('cases', 'assertions', 'artifacts'):
            contract = copy.deepcopy(self.contract)
            rows = (contract['cases'] if section == 'cases' else
                    contract['cases'][0]['assertions'] if section == 'assertions' else
                    contract['tested_subject']['artifacts'])
            rows.append(copy.deepcopy(rows[0]))
            with self.assertRaisesRegex(Blocked, 'BEHAVIOR_DUPLICATE_'):
                behavior.validate_contract(contract)

    def test_empty_cases_assertions_or_missing_control_categories_rejected(self):
        for section in ('cases', 'assertions', 'categories'):
            contract = copy.deepcopy(self.contract)
            if section == 'cases':
                contract['cases'] = []
            elif section == 'assertions':
                contract['cases'][0]['assertions'] = []
            else:
                contract['cases'][1]['category'] = 'positive'
            with self.assertRaises(Blocked):
                behavior.validate_contract(contract)

    def test_range_endpoints_must_be_ordered_finite_numbers(self):
        for minimum, maximum in [(True, 1), (0, '1'), (2, 1), (0, float('inf'))]:
            contract = copy.deepcopy(self.contract)
            contract['cases'][0]['assertions'][0].update(minimum=minimum, maximum=maximum)
            with self.assertRaises(Blocked):
                behavior.validate_contract(contract)

    def test_bounds_precede_work_and_no_output_is_created(self):
        before = set(self.root.iterdir())
        contract = copy.deepcopy(self.contract)
        contract['tested_subject']['artifacts'][0]['size'] = behavior.MAX_ARTIFACT_BYTES + 1
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_ARTIFACT_BOUNDS'):
            behavior.preflight_artifacts(contract, self.root, self.evidence)
        self.assertEqual(set(self.root.iterdir()), before)
        contract = copy.deepcopy(self.contract)
        nested = 0
        for _ in range(20):
            nested = [nested]
        contract['cases'][0]['assertions'][1]['expected'] = nested
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_JSON_BOUNDS'):
            behavior.validate_contract(contract)

    def test_scope_overlap_and_unexecuted_observations_are_rejected(self):
        contract = copy.deepcopy(self.contract)
        contract['protocol']['scope']['substituted'] = ['synthetic bounded function']
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_SCOPE_CONFLICT'):
            behavior.validate_contract(contract)
        self.evidence['cases'][0].update(outcome='SKIPPED', detail='Skipped but supplied outputs')
        with self.assertRaisesRegex(Blocked, 'BEHAVIOR_UNEXECUTED_OBSERVATIONS'):
            self.evaluate()

    def test_changed_artifact_during_transfer_review_is_detected(self):
        def changed(*_):
            (self.root / 'candidate.py').write_text('changed after first artifact check')
            return copy.deepcopy(self.base)
        with mock.patch.object(behavior, '_transfer', side_effect=changed):
            with self.assertRaisesRegex(Blocked, 'BEHAVIOR_ARTIFACT_MISMATCH'):
                self.evaluate()


if __name__ == '__main__':
    unittest.main()
