"""Fixed reviewed execution, exact translation, and data-only packet replay."""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forge_core import reviewed_evaluator as trial
from forge_core.common import Blocked, canonical, loads, read, sha, write
from forge_core.workspace import initialize


def inventory(folder):
    return {p.relative_to(folder).as_posix(): sha(p.read_bytes())
            for p in folder.rglob('*') if p.is_file()}


def repack(folder):
    (folder / 'packet.json').unlink()
    trial._save_packet(folder, 'result' if (folder / 'receipt.json').exists() else 'prepared')


class ReviewedEvaluator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name).resolve()
        cls.workbench = initialize(cls.root / 'home')
        cls.prepared = cls.root / 'prepared'
        cls.prepared_summary = trial.prepare(cls.workbench, cls.prepared)
        cls.result = cls.root / 'result'
        cls.summary = trial.run(cls.workbench, cls.prepared, cls.result)
        cls.raw = loads(read(cls.result / 'artifacts/raw-report.json'))
        cls.vectors = cls.raw['vectors']

    def clone(self, source, label):
        destination = self.root / (self.id().split('.')[-1] + '-' + label)
        shutil.copytree(source, destination)
        return destination

    def test_real_fixed_trial_and_exact_selected_sources(self):
        self.assertEqual(self.prepared_summary['status'], 'REVIEWED_TRIAL_PREPARED')
        self.assertIs(self.prepared_summary['executed'], False)
        self.assertEqual(self.summary['status'], 'REVIEWED_TRIAL_COMPLETED')
        self.assertEqual(self.summary['collector_calls'], 1)
        self.assertEqual(self.summary['counts']['candidate'], {'passed': 28, 'failed': 0, 'cases': 28})
        self.assertGreater(self.summary['counts']['baseline']['failed'], 0)
        self.assertEqual(self.summary['counts']['baseline']['cases'], 28)
        contracts = [loads(read(self.prepared / 'contracts' / (role + '.json'))) for role in trial.SUBJECTS]
        self.assertNotEqual(contracts[0]['target']['code_lead']['definition_id'], contracts[1]['target']['code_lead']['definition_id'])
        self.assertNotEqual(contracts[0]['target']['code_lead']['corpus'], contracts[1]['target']['code_lead']['corpus'])
        for role, contract in zip(trial.SUBJECTS, contracts):
            self.assertEqual(contract['target']['code_lead']['name'], trial.SUBJECTS[role]['name'])
            self.assertEqual(contract['target']['code_lead']['source_sha256'],
                             sha(read(self.prepared / 'artifacts' / contract['tested_subject']['subject_artifact'])))
            self.assertEqual(len(contract['cases']), 28)
            self.assertEqual(sum(c['category'] == 'negative' for c in contract['cases']), 2)

    def test_actual_child_environment_flags_and_origins(self):
        plan = loads(read(self.prepared / 'plan.json'))
        self.assertEqual(self.raw['environment_before'], plan['environment'])
        self.assertEqual(self.raw['environment_after'], plan['environment'])
        for field in ('isolated', 'no_site', 'dont_write_bytecode'):
            self.assertIs(type(self.raw['flags'][field]), int)
            self.assertEqual(self.raw['flags'][field], 1)
        self.assertEqual(self.raw['flags']['pycache_prefix'], str(self.result / 'worker-cache'))
        self.assertFalse((self.result / 'worker-cache').exists())
        for name, record in self.raw['module_origins'].items():
            self.assertTrue(name.startswith('forge_core'))
            self.assertIs(record['cached_bytes_present'], False)
            self.assertEqual(record['sha256'], self.raw['source_before']['binding'][record['path']])
        self.assertIn('forge_core._vendor.rfc8785._impl', self.raw['module_origins'])

    def test_source_and_prepared_records_preserved(self):
        self.assertTrue(self.summary['source_preserved'])
        self.assertTrue(self.summary['prepared_preserved'])
        self.assertEqual(self.raw['source_before'], self.raw['source_after'])
        self.assertEqual(loads(read(self.prepared / 'summary.json')), self.prepared_summary)

    def test_prepare_never_calls_collector_or_vendor(self):
        out = self.root / 'extra-preparation'
        with patch('forge_core.contract_trial.external_vectors', side_effect=AssertionError('trial invoked')), \
                patch('forge_core._vendor.rfc8785.dumps', side_effect=AssertionError('vendor invoked')):
            value = trial.prepare(self.workbench, out)
        self.assertEqual(value['status'], 'REVIEWED_TRIAL_PREPARED')
        self.assertFalse(value['executed'])
        self.assertFalse(any('raw-report' in p for p in inventory(out)))

    def test_verify_is_data_only_deterministic_and_portable(self):
        before = inventory(self.prepared), inventory(self.result)
        with patch('forge_core.contract_trial.external_vectors', side_effect=AssertionError('trial invoked')), \
                patch('forge_core.contract_trial.verify_packet', side_effect=AssertionError('executing verifier invoked')), \
                patch('forge_core.interop.normalize_record', side_effect=AssertionError('vendor invoked')), \
                patch.object(trial, 'run_bounded', side_effect=AssertionError('worker invoked')), \
                patch.object(trial, '_environment', side_effect=AssertionError('verifier environment compared')):
            first = trial.verify(self.prepared, self.result)
            self.assertEqual(first, trial.verify(self.prepared, self.result))
            relocated_prepared = self.clone(self.prepared, 'moved-prepared')
            relocated_result = self.clone(self.result, 'moved-result')
            self.assertEqual(first, trial.verify(relocated_prepared, relocated_result))
        self.assertEqual(first['qualification'], 'COMPLETED')
        self.assertFalse(first['executed'])
        self.assertEqual(before, (inventory(self.prepared), inventory(self.result)))

    def test_raw_outputs_are_exact_serializations(self):
        value = copy.deepcopy(self.vectors)
        value['cases'][0]['selected_component']['actual'] = '0.0'
        value['cases'][0]['selected_component']['passed'] = False
        value['summary']['selected_component'] -= 1
        result = trial.adapt(value)
        self.assertEqual(result['counts']['candidate'], {'passed': 27, 'failed': 1, 'cases': 28})
        self.assertEqual(result['cases']['candidate'][0]['observations']['serialized_text'], '0.0')

    def test_object_output_bytes_are_not_normalized(self):
        value = copy.deepcopy(self.vectors)
        value['cases'][-1]['selected_component']['actual_hex'] = '7b7d'
        value['cases'][-1]['selected_component']['passed'] = False
        value['summary']['selected_component'] -= 1
        self.assertEqual(trial.adapt(value)['counts']['candidate']['failed'], 1)

    def test_empty_and_control_character_outputs_remain_measured_failures(self):
        for actual in ('', '\n', '0\x00'):
            value = copy.deepcopy(self.vectors)
            value['cases'][0]['selected_component'].update(actual=actual, passed=False)
            value['summary']['selected_component'] -= 1
            translated = trial.adapt(value)
            self.assertEqual(translated['counts']['candidate']['failed'], 1)
            self.assertEqual(translated['cases']['candidate'][0]['observations']['serialized_text'], actual)

    def test_wrong_exception_never_satisfies_rejection(self):
        for role, subject in trial.SUBJECTS.items():
            for name in ('RuntimeError', 'ImportError', 'UnicodeError', 'OverflowError', 'TimeoutError'):
                with self.subTest(role=role, error=name):
                    value = copy.deepcopy(self.vectors)
                    outcome = value['cases'][9][subject['raw_key']]
                    outcome['error'] = name
                    with self.assertRaises(Blocked):
                        trial.adapt(value)  # Optimistic original flag disagrees.
                    outcome['passed'] = False
                    value['summary'][subject['raw_key']] -= 1
                    adapted = trial.adapt(value)
                    self.assertEqual(adapted['cases'][role][9]['observations']['exception_type'], name)
                    self.assertEqual(adapted['counts'][role]['failed'], self.summary['counts'][role]['failed'] + 1)

    def test_missing_duplicate_extra_and_reordered_cases_rejected(self):
        values = []
        x = copy.deepcopy(self.vectors); x['cases'].pop(); values.append(x)
        x = copy.deepcopy(self.vectors); x['cases'][0] = copy.deepcopy(x['cases'][1]); values.append(x)
        x = copy.deepcopy(self.vectors); x['cases'].append(copy.deepcopy(x['cases'][0])); values.append(x)
        x = copy.deepcopy(self.vectors); x['cases'][0], x['cases'][1] = x['cases'][1], x['cases'][0]; values.append(x)
        for x in values:
            with self.subTest(length=len(x['cases'])), self.assertRaises(Blocked):
                trial.adapt(x)

    def test_expected_value_and_subject_transplant_rejected(self):
        value = copy.deepcopy(self.vectors)
        value['cases'][0]['expected'] = '0.0'
        with self.assertRaises(Blocked):
            trial.adapt(value)
        value = copy.deepcopy(self.vectors)
        value['cases'][9]['selected_component'] = copy.deepcopy(value['cases'][9]['existing_forge_canonical'])
        with self.assertRaises(Blocked):
            trial.adapt(value)

    def test_flags_and_counts_are_recomputed(self):
        for mutate in (
            lambda x: x['cases'][0]['selected_component'].update(passed=False),
            lambda x: x['cases'][0]['existing_forge_canonical'].update(passed=True),
            lambda x: x['summary'].update(selected_component=99),
            lambda x: x['summary'].update(selected_component=True),
            lambda x: x.update(case_count=True),
        ):
            value = copy.deepcopy(self.vectors); mutate(value)
            with self.assertRaises(Blocked):
                trial.adapt(value)

    def test_invalid_native_types_are_structured_refusals(self):
        bad_values = [[], {}, True, 1, float('nan'), float('inf')]
        for bad in bad_values:
            for field in ('actual', 'error'):
                with self.subTest(value=repr(bad), field=field):
                    value = copy.deepcopy(self.vectors)
                    value['cases'][0]['selected_component'][field] = bad
                    with self.assertRaises(Blocked):
                        trial.adapt(value)
        deep = []
        for _ in range(30):
            deep = [deep]
        with self.assertRaises(Blocked):
            trial.adapt(deep)

    def test_inconsistent_output_error_and_invalid_hex(self):
        for mutate in (
            lambda x: x['cases'][0]['selected_component'].update(error='ValueError'),
            lambda x: x['cases'][9]['selected_component'].update(error=None),
            lambda x: x['cases'][-1]['selected_component'].update(actual_hex='XYZ'),
            lambda x: x['cases'][-1]['selected_component'].update(actual_hex='a'),
            lambda x: x.update(execution_authenticated=True),
        ):
            value = copy.deepcopy(self.vectors); mutate(value)
            with self.assertRaises(Blocked):
                trial.adapt(value)

    def test_fixture_inputs_and_future_outputs_are_separate(self):
        fixtures = loads(read(self.prepared / 'artifacts/fixtures.json'))['cases']
        self.assertEqual(fixtures[0]['input_hex'], '0000000000000000')
        self.assertEqual(fixtures[1]['input_hex'], '8000000000000000')
        self.assertEqual(fixtures[9]['input_hex'], '7fffffffffffffff')
        for role in trial.SUBJECTS:
            contract = loads(read(self.prepared / 'contracts' / (role + '.json')))
            self.assertEqual([x['input_sha256'] for x in contract['cases']], [sha(bytes.fromhex(x['input_hex'])) for x in fixtures])
            self.assertNotIn('raw-report.json', [x['path'] for x in contract['tested_subject']['artifacts']])
            self.assertEqual(loads(read(self.result / 'evidence' / (role + '.json')))['report_sha256'], sha(read(self.result / 'artifacts/raw-report.json')))

    def test_start_and_contract_are_saved_before_worker(self):
        out = self.root / 'ordered-failure'
        def stopped(*args, **kwargs):
            self.assertTrue((out / 'start.json').is_file())
            self.assertTrue((out / 'request.json').is_file())
            self.assertTrue((self.prepared / 'contracts/candidate.json').is_file())
            request = loads(kwargs['input'])
            self.assertEqual(request['start_sha256'], sha(read(out / 'start.json')))
            self.assertEqual(request['plan_sha256'], sha(read(self.prepared / 'plan.json')))
            raise Blocked('TOOL_TIMEOUT')
        with patch.object(trial, 'run_bounded', side_effect=stopped) as worker:
            summary = trial.run(self.workbench, self.prepared, out)
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(summary['status'], 'REVIEWED_TRIAL_EXECUTION_FAILED')
        self.assertEqual(summary['results'], {})
        self.assertEqual(summary['counts'], {})
        self.assertEqual(len(summary['unobserved_case_ids']), 28)
        self.assertIsNone(summary['collector_calls'])
        self.assertFalse((out / 'evidence').exists())
        self.assertFalse((out / 'reviews').exists())
        self.assertEqual(trial.verify(self.prepared, out)['qualification'], 'EXECUTION_FAILED')

    def test_changed_runtime_prevents_worker_and_output(self):
        out = self.root / 'changed-runtime'
        binding = copy.deepcopy(self.raw['source_before']['binding'])
        binding['forge_core/common.py'] = 'f' * 64
        with patch.object(trial, 'code_binding', return_value=binding), \
                patch.object(trial, 'run_bounded', side_effect=AssertionError('worker invoked')) as worker:
            with self.assertRaises(Blocked):
                trial.run(self.workbench, self.prepared, out)
        worker.assert_not_called()
        self.assertFalse(out.exists())

    def test_changed_environment_prevents_worker_and_output(self):
        out = self.root / 'changed-environment'
        environment = dict(self.raw['environment_before'], python_version='0.0.0')
        with patch.object(trial, '_environment', return_value=environment), \
                patch.object(trial, 'run_bounded', side_effect=AssertionError('worker invoked')):
            with self.assertRaises(Blocked):
                trial.run(self.workbench, self.prepared, out)
        self.assertFalse(out.exists())

    def test_changed_actual_start_snapshot_prevents_writes_and_worker(self):
        out = self.root / 'changed-start-snapshot'
        changed = copy.deepcopy(self.raw['source_before'])
        changed['binding']['forge_core/common.py'] = 'f' * 64
        with patch.object(trial, '_snapshot', return_value=changed), \
                patch.object(trial, 'run_bounded', side_effect=AssertionError('worker invoked')) as worker:
            with self.assertRaises(Blocked):
                trial.run(self.workbench, self.prepared, out)
        worker.assert_not_called()
        self.assertFalse(out.exists())

    def test_changed_actual_start_environment_prevents_writes_and_worker(self):
        out = self.root / 'changed-start-environment'
        original = self.raw['environment_before']
        changed = dict(original, python_version='0.0.0')
        with patch.object(trial, '_environment', side_effect=[original, changed]), \
                patch.object(trial, 'run_bounded', side_effect=AssertionError('worker invoked')) as worker:
            with self.assertRaises(Blocked):
                trial.run(self.workbench, self.prepared, out)
        worker.assert_not_called()
        self.assertFalse(out.exists())

    def test_changed_inputs_during_worker_preserve_failed_evidence(self):
        out = self.root / 'changed-during'
        expected = copy.deepcopy(self.raw['source_before'])
        changed = copy.deepcopy(expected)
        changed['binding']['forge_core/common.py'] = 'f' * 64
        with patch.object(trial, '_snapshot', side_effect=[expected, changed]), \
                patch.object(trial, 'run_bounded', side_effect=Blocked('TOOL_TIMEOUT')):
            summary = trial.run(self.workbench, self.prepared, out)
        self.assertEqual(summary['status'], 'REVIEWED_TRIAL_EXECUTION_FAILED')
        self.assertFalse(summary['source_preserved'])
        self.assertEqual(trial.verify(self.prepared, out)['qualification'], 'EXECUTION_FAILED')

    def test_ordinary_collector_error_preserves_unknown_cases(self):
        out = self.root / 'collector-error'
        def failed_worker(*args, **kwargs):
            request = loads(kwargs['input'])
            worker = copy.deepcopy(self.raw)
            for key in ('run_id', 'plan_sha256', 'contract_sha256', 'start_sha256'):
                worker[key] = request[key]
            worker.update(request_sha256=sha(canonical(request)), status='FAILED', vectors=None,
                          error={'type': 'RuntimeError', 'reason': 'FIXED_WORKER_EXCEPTION'})
            worker['flags']['pycache_prefix'] = request['cache_prefix']
            worker['started_utc'] = worker['finished_utc'] = trial._now()
            worker['elapsed_seconds'] = 0.0
            return canonical(worker)
        with patch.object(trial, 'run_bounded', side_effect=failed_worker):
            summary = trial.run(self.workbench, self.prepared, out)
        self.assertEqual(summary['status'], 'REVIEWED_TRIAL_EXECUTION_FAILED')
        self.assertEqual(summary['collector_calls'], 1)
        self.assertEqual(len(summary['unobserved_case_ids']), 28)
        self.assertEqual(trial.verify(self.prepared, out)['qualification'], 'EXECUTION_FAILED')

    def test_valid_wrong_output_is_complete_failed_behavior(self):
        out = self.root / 'valid-measured-failure'
        def wrong_worker(*args, **kwargs):
            request = loads(kwargs['input']); worker = copy.deepcopy(self.raw)
            for key in ('run_id', 'plan_sha256', 'contract_sha256', 'start_sha256'):
                worker[key] = request[key]
            worker['request_sha256'] = sha(canonical(request))
            worker['flags']['pycache_prefix'] = request['cache_prefix']
            worker['started_utc'] = worker['finished_utc'] = trial._now()
            worker['elapsed_seconds'] = 0.0
            vector = worker['vectors']['cases'][0]['selected_component']
            vector.update(actual='WRONG', passed=False)
            worker['vectors']['summary']['selected_component'] -= 1
            return canonical(worker)
        with patch.object(trial, 'run_bounded', side_effect=wrong_worker):
            summary = trial.run(self.workbench, self.prepared, out)
        self.assertEqual(summary['status'], 'REVIEWED_TRIAL_COMPLETED')
        self.assertEqual(summary['results']['candidate'], 'REPORTED_FAIL')
        self.assertEqual(summary['counts']['candidate']['failed'], 1)
        verified = trial.verify(self.prepared, out)
        self.assertEqual(verified['qualification'], 'COMPLETED')
        self.assertEqual(verified['results']['candidate'], 'REPORTED_FAIL')
        self.assertFalse(verified['execution_authenticated'])

    def test_generic_authority_and_transfer_decisions_preserved(self):
        context = trial._load_prepared(self.prepared)
        base = trial.evaluate_with_contracts(context['bundle'], context['corpora'], context['registry'])
        for role in trial.SUBJECTS:
            review = loads(read(self.result / 'reviews' / (role + '.json')))
            self.assertEqual({k: v for k, v in review.items() if k != 'behavioral_review'}, base)
            self.assertFalse(any(review['behavioral_review']['authority'].values()))
        for name in ('execution_authenticated', 'chronological_freeze_authenticated',
                     'independent_evaluation', 'dependency_completeness_verified', 'release_approved'):
            self.assertIs(self.summary[name], False)

    def test_rehashed_case_or_summary_edit_still_refused(self):
        changed = self.clone(self.result, 'changed')
        evidence = loads(read(changed / 'evidence/candidate.json'))
        evidence['cases'][0]['observations']['serialized_text'] = '0.0'
        write(changed / 'evidence/candidate.json', canonical(evidence))
        repack(changed)
        with self.assertRaises(Blocked):
            trial.verify(self.prepared, changed)
        changed = self.clone(self.result, 'summary')
        summary = loads(read(changed / 'summary.json')); summary['counts']['baseline']['failed'] = 0
        write(changed / 'summary.json', canonical(summary)); repack(changed)
        with self.assertRaises(Blocked):
            trial.verify(self.prepared, changed)

    def test_rehashed_plan_expectation_or_fixture_edit_refused(self):
        for filename, mutate in (
            ('contracts/candidate.json', lambda x: x['cases'][0]['assertions'][0].update(expected='0.0')),
            ('artifacts/fixtures.json', lambda x: x['cases'][0].update(input_hex='8000000000000000')),
        ):
            changed = self.clone(self.prepared, filename.replace('/', '-'))
            value = loads(read(changed / filename)); mutate(value)
            write(changed / filename, canonical(value)); repack(changed)
            with self.assertRaises(Blocked):
                trial.verify(changed, self.result)

    def test_exact_membership_and_byte_checks(self):
        for action in ('extra', 'missing', 'changed', 'empty-directory'):
            changed = self.clone(self.result, action)
            if action == 'extra':
                write(changed / 'extra.json', b'{}', new=True)
            elif action == 'missing':
                (changed / 'artifacts/raw-report.json').unlink()
            elif action == 'empty-directory':
                (changed / 'extra-empty').mkdir()
            else:
                (changed / 'artifacts/raw-report.json').write_bytes(b'{}')
            with self.subTest(action=action), self.assertRaises(Blocked):
                trial.verify(self.prepared, changed)

    def test_malformed_corpus_json_is_structured_refusal(self):
        for invalid in ([], None, True, 'not a corpus'):
            changed = self.clone(self.prepared, sha(canonical(invalid))[:12])
            write(changed / 'corpora/candidate.json', canonical(invalid)); repack(changed)
            with self.assertRaises(Blocked):
                trial.verify(changed, self.result)

    def test_invalid_time_order_cannot_be_complete_evidence(self):
        out = self.root / 'invalid-time'
        def old_worker(*args, **kwargs):
            request = loads(kwargs['input']); worker = copy.deepcopy(self.raw)
            for key in ('run_id', 'plan_sha256', 'contract_sha256', 'start_sha256'):
                worker[key] = request[key]
            worker['request_sha256'] = sha(canonical(request))
            worker['flags']['pycache_prefix'] = request['cache_prefix']
            worker['started_utc'] = '2000-01-01T00:00:00+00:00'
            worker['finished_utc'] = '1999-01-01T00:00:00+00:00'
            return canonical(worker)
        with patch.object(trial, 'run_bounded', side_effect=old_worker):
            summary = trial.run(self.workbench, self.prepared, out)
        self.assertEqual(summary['status'], 'REVIEWED_TRIAL_EXECUTION_FAILED')
        self.assertEqual(trial.verify(self.prepared, out)['qualification'], 'EXECUTION_FAILED')

    def test_nonempty_worker_cache_keeps_failure_evidence(self):
        out = self.root / 'cache-failure'
        def failed_worker(*args, **kwargs):
            write(out / 'worker-cache/unexpected.bin', b'bounded unexpected cache bytes', new=True)
            raise Blocked('TOOL_TIMEOUT')
        with patch.object(trial, 'run_bounded', side_effect=failed_worker):
            summary = trial.run(self.workbench, self.prepared, out)
        self.assertEqual(summary['status'], 'REVIEWED_TRIAL_EXECUTION_FAILED')
        receipt = loads(read(out / 'receipt.json'))
        self.assertEqual(receipt['cache']['status'], 'NOT_EMPTY')
        self.assertEqual(receipt['supervision_error']['reason'], 'TOOL_TIMEOUT')
        self.assertTrue((out / 'worker-cache/unexpected.bin').is_file())
        self.assertEqual(trial.verify(self.prepared, out)['qualification'], 'EXECUTION_FAILED')

    def test_unbounded_cache_preserves_raw_and_incomplete_packet_record(self):
        out = self.root / 'unbounded-cache'
        def failed_worker(*args, **kwargs):
            write(out / 'worker-cache/oversize.bin', b'x' * (trial.MAX_BYTES + 1), new=True)
            return b'captured incomplete worker output'
        with patch.object(trial, 'run_bounded', side_effect=failed_worker):
            with self.assertRaisesRegex(Blocked, 'REVIEWED_TRIAL_PACKET_INCOMPLETE'):
                trial.run(self.workbench, self.prepared, out)
        self.assertEqual(read(out / 'artifacts/raw-report.json'), b'captured incomplete worker output')
        self.assertTrue((out / 'receipt.json').is_file())
        self.assertEqual(loads(read(out / 'summary.json'))['status'], 'REVIEWED_TRIAL_EXECUTION_FAILED')
        self.assertEqual(loads(read(out / 'packet-incomplete.json'))['status'], 'REVIEWED_TRIAL_PACKET_INCOMPLETE')
        self.assertFalse((out / 'packet.json').exists())

    def test_archived_prefix_must_be_absolute_normalized_spelling(self):
        for invalid in ('relative/cache', '/absolute/../cache', '/absolute//cache'):
            with self.subTest(path=invalid), self.assertRaises(Blocked):
                trial._recorded_absolute(invalid)

    def test_nondirectory_output_ancestor_refused_before_writes(self):
        parent = self.root / 'ordinary-file'; write(parent, b'data', new=True)
        home = self.root / 'not-created-home'
        with self.assertRaises(Blocked):
            trial.preflight(home, parent / 'child')
        self.assertFalse(home.exists())

    def test_duplicate_json_and_deep_packets_are_refused(self):
        for raw in (b'{"schema":1,"schema":1}', b'[' * 2000 + b'0' + b']' * 2000):
            changed = self.clone(self.result, sha(raw)[:12])
            (changed / 'summary.json').write_bytes(raw); repack(changed)
            with self.assertRaises(Blocked):
                trial.verify(self.prepared, changed)

    def test_no_arbitrary_executable_protocol_options(self):
        for name, value in [('command', ['echo', 'x']), ('source_root', '/arbitrary'),
                            ('module', 'unreviewed'), ('function', 'run'), ('harness', 'other.py')]:
            changed = self.clone(self.prepared, name)
            plan = loads(read(changed / 'plan.json')); plan[name] = value
            write(changed / 'plan.json', canonical(plan)); repack(changed)
            with self.assertRaises(Blocked):
                trial.preflight(self.workbench.home, self.root / ('refused-' + name), changed)

    def test_unsafe_paths_refused_without_writes(self):
        before = inventory(self.root)
        for out in (self.result, self.prepared / 'nested', self.workbench.home / 'nested',
                    trial.ROOT / 'new-result', self.root):
            with self.subTest(out=str(out)), self.assertRaises(Blocked):
                trial.preflight(self.workbench.home, out, self.prepared)
        self.assertEqual(inventory(self.root), before)

    def test_symlinks_refused(self):
        alias = self.root / 'alias'; alias.symlink_to(self.prepared, target_is_directory=True)
        with self.assertRaises(Blocked):
            trial.preflight(self.workbench.home, self.root / 'link-result', alias)
        changed = self.clone(self.result, 'symlink')
        (changed / 'summary.json').unlink()
        (changed / 'summary.json').symlink_to(self.result / 'summary.json')
        with self.assertRaises(Blocked):
            trial.verify(self.prepared, changed)


if __name__ == '__main__':
    unittest.main()
