"""Independent consistency challenges for both modes of the handoff archive."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from examples.handoff_bench import burden, run_bench, verify_run


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode() + b'\n'


class HandoffVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='forge-handoff-verifier-')
        cls.root = Path(cls.temporary.name)
        cls.original = cls.root / 'authentic'
        cls.benchmark = run_bench.run(cls.original, execute_reviewed=True)
        cls.anchor = cls.benchmark['freeze_sha256']

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.copy = self.root / self._testMethodName
        shutil.copytree(self.original, self.copy)

    def verify(self):
        return verify_run.verify_run(self.copy / 'frozen/binding.json',
                                     self.copy / 'execution',
                                     trusted_freeze_sha256=self.anchor)

    def read(self, path):
        return json.loads(path.read_bytes())

    def write(self, path, value):
        path.write_bytes(canonical(value))

    def rewrite_execution(self, mutate, case_id='legacy-24'):
        record_path = self.copy / 'execution/run.json'
        record = self.read(record_path)
        row = next(row for row in record['results'] if row['id'] == case_id)
        execution_path = self.copy / 'execution' / row['execution']['path']
        execution = self.read(execution_path)
        mutate(execution)
        self.write(execution_path, execution)
        raw = execution_path.read_bytes()
        row['execution']['bytes'] = len(raw)
        row['execution']['sha256'] = hashlib.sha256(raw).hexdigest()
        self.write(record_path, record)

    def rewrite_handoff_payload(self, mutate, case_id='H01'):
        folder = self.copy / 'execution/cases' / case_id
        output = self.read(folder / 'stdout.bin')
        mutate(output)
        self.write(folder / 'stdout.bin', output)
        self.write(folder / 'work/result/plan.json', output['plan'])
        self.write(folder / 'work/result/burden.json', output['burden'])
        receipt_path = folder / 'work/result/receipt.json'
        receipt = self.read(receipt_path)
        raw = (folder / 'stdout.bin').read_bytes()
        receipt['stdout'] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        for ref in receipt['files']:
            value = (folder / 'work/result' / ref['path']).read_bytes()
            ref.update(bytes=len(value), sha256=hashlib.sha256(value).hexdigest())
        self.write(receipt_path, receipt)

        def rewrite(e):
            e['stdout'].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            e['stream_bytes_seen_at_least']['stdout'] = len(raw)
            for name in ['result/plan.json', 'result/burden.json', 'result/receipt.json']:
                value = (folder / 'work' / name).read_bytes()
                e['files_after'][name] = {'kind': 'REGULAR', 'bytes': len(value),
                    'sha256': hashlib.sha256(value).hexdigest(), 'utf8': True}
        self.rewrite_execution(rewrite, case_id)

    def test_authentic_same_archive_has_24_original_and_five_handoffs(self):
        result = self.verify()
        self.assertEqual('PASS', result['status'])
        self.assertEqual(24, result['legacy']['exact_cases'])
        self.assertEqual(24, result['legacy']['total_cases'])
        self.assertEqual(5, len(result['cases']))
        self.assertTrue(all(row['checks']['passed'] for row in result['cases']))

    def test_recorded_product_identity_change_refused_even_with_same_math(self):
        self.rewrite_execution(lambda e: e['product_before'].__setitem__('sha256', '0' * 64))
        with self.assertRaises(ValueError):
            self.verify()

    def test_recorded_limits_change_refused_even_with_same_math(self):
        self.rewrite_execution(lambda e: e['limits'].__setitem__('wall_seconds', 29))
        with self.assertRaises(ValueError):
            self.verify()

    def test_pinned_original_spec_change_refused(self):
        (self.copy / 'frozen/SPEC.md').write_bytes(b'Changed contract without changed math.\n')
        with self.assertRaises(ValueError):
            self.verify()

    def test_pinned_handoff_spec_change_refused(self):
        (self.copy / 'frozen/HANDOFF_SPEC.md').write_bytes(b'Changed handoff contract.\n')
        with self.assertRaises(ValueError):
            self.verify()

    def test_raw_artifact_change_refused(self):
        record = self.read(self.copy / 'execution/run.json')
        row = next(row for row in record['results'] if row['id'] == 'H01')
        e = self.read(self.copy / 'execution' / row['execution']['path'])
        artifact = next(name for name in e['inputs_before'] if name.startswith('artifacts/'))
        (self.copy / 'execution/cases/H01/work' / artifact).write_bytes(b'changed raw export')
        with self.assertRaises(ValueError):
            self.verify()

    def test_profile_change_refused(self):
        path = self.copy / 'execution/profile.json'
        profile = self.read(path)
        profile['limits']['wall_seconds'] = 29
        self.write(path, profile)
        with self.assertRaises(ValueError):
            self.verify()

    def test_changed_core_in_product_refused(self):
        path = self.copy / 'execution/cases/legacy-24/product.pyz'
        path.write_bytes(path.read_bytes() + b'changed product identity')
        with self.assertRaises(ValueError):
            self.verify()

    def test_changed_burden_sidecar_refused(self):
        path = self.copy / 'execution/cases/H01/work/result/burden.json'
        value = self.read(path)
        value['invented_follow_up'] = 1
        self.write(path, value)
        with self.assertRaises(ValueError):
            self.verify()

    def test_coherent_ledger_arithmetic_change_refused(self):
        def mutate(value):
            value['burden']['totals']['known_wall_time_ns'] += 500
        self.rewrite_handoff_payload(mutate)
        with self.assertRaises(ValueError):
            self.verify()

    def test_coherent_plan_core_identity_change_refused(self):
        self.rewrite_handoff_payload(lambda value: value['plan'].__setitem__('planner_core_sha256', '0' * 64))
        with self.assertRaises(ValueError):
            self.verify()

    def test_coherent_extraction_source_identity_change_refused(self):
        def mutate(value):
            proof = next(field['provenance'][0] for field in value['plan']['extraction']['fields'].values()
                         if field['provenance'])
            proof['sha256'] = '0' * 64
        self.rewrite_handoff_payload(mutate)
        with self.assertRaises(ValueError):
            self.verify()

    def test_coherent_native_manifest_identity_change_refused(self):
        def mutate(value):
            proof = next(field['provenance'][0] for field in value['plan']['extraction']['fields'].values()
                         if field['provenance'])
            proof['manifest_sha256'] = '0' * 64
        self.rewrite_handoff_payload(mutate, 'H03')
        with self.assertRaises(ValueError):
            self.verify()

    def test_coherent_ledger_case_identity_change_refused(self):
        self.rewrite_handoff_payload(lambda value: value['burden'].__setitem__('case_id', 'unrelated_case'))
        with self.assertRaises(ValueError):
            self.verify()

    def test_missing_human_stage_for_a_route_refused(self):
        supplied = {'burden_events': [
            {'id': stage, 'stage': stage, 'actor': 'human', 'measurement': 'unknown',
             'wall_time_ns': None, 'step_count': 1, 'source': 'Unmeasured declared work',
             'routes': ['manual_model']}
            for stage in ['acquisition', 'interpretation', 'model_preparation']], 'follow_up': []}
        plan = {'case_id': 'coverage', 'planner_result': None, 'comparison': {
            route: {'unresolved_count': 1, 'recorded_questions_asked': 0, 'synthetic_questions_asked': 0}
            for route in verify_run.ROUTES}}
        ledger = burden.make_ledger(supplied['burden_events'], [], plan['comparison'], None)
        ledger['case_id'] = 'coverage'
        # Remove only the placeholders for the unmeasured export routes, then
        # keep arithmetic internally coherent. Global human-stage presence is
        # insufficient because these events apply only to manual_model.
        ledger['events'] = supplied['burden_events']
        ledger['totals'] = verify_run.aggregate(ledger['events'])
        for stage in verify_run.STAGES:
            ledger['phases'][stage] = verify_run.aggregate([e for e in ledger['events'] if e['stage'] == stage])
        for route in verify_run.ROUTES:
            ledger['routes'][route] = verify_run.aggregate([e for e in ledger['events'] if route in e['routes']])
        with self.assertRaises(ValueError):
            verify_run.verify_burden(ledger, plan, supplied, 1)

    def test_coherent_invented_history_is_semantic_failure(self):
        def mutate(value):
            world = value['plan']['model_input']['cases'][0]['worlds'][0].copy()
            world['id'] = 'invented_by_adapter'
            value['plan']['model_input']['cases'][0]['worlds'].append(world)
        self.rewrite_handoff_payload(mutate)
        result = self.verify()
        self.assertEqual('FAIL', result['status'])
        self.assertEqual('H01', result['failures'][0]['case_id'])

    def test_changed_trusted_freeze_refused(self):
        path = self.copy / 'frozen/binding.json'
        value = self.read(path)
        value['invented_claim'] = 'self asserted trusted evidence'
        self.write(path, value)
        with self.assertRaises(ValueError):
            self.verify()

    def test_duplicate_execution_key_refused(self):
        record_path = self.copy / 'execution/run.json'
        record = self.read(record_path)
        row = record['results'][0]
        path = self.copy / 'execution' / row['execution']['path']
        raw = path.read_bytes().rstrip()
        raw = raw[:-1] + b',"case_id":"legacy-24"}'
        path.write_bytes(raw)
        row['execution']['bytes'] = len(raw)
        row['execution']['sha256'] = hashlib.sha256(raw).hexdigest()
        self.write(record_path, record)
        with self.assertRaises(ValueError):
            self.verify()

    def test_oversized_saved_stdout_refused(self):
        path = self.copy / 'execution/cases/legacy-24/stdout.bin'
        path.write_bytes(b' ' * 1_000_001)
        with self.assertRaises(ValueError):
            self.verify()


if __name__ == '__main__':
    unittest.main()
