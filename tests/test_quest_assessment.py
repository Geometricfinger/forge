"""Assess exact packaged programs from frozen checks and retained observations."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from forge_core.common import Blocked, canonical, sha
from forge_core.engine import ROOT
from forge_core.opportunity_examples import cite, source
from forge_core.quest_assessment import validate_plan


class QuestAssessmentTests(unittest.TestCase):
    @classmethod
    def cli(cls, *args):
        completed = subprocess.run(
            [sys.executable, '-B', str(ROOT/'forge.py'), '--home', str(cls.home), *map(str, args)],
            capture_output=True, timeout=90, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        if completed.stderr:
            raise AssertionError(completed.stderr.decode(errors='replace'))
        return completed.returncode, json.loads(completed.stdout)

    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name).resolve()
        cls.home = cls.root/'home'
        cls.pool = cls.root/'pool'
        cls.pool.mkdir()
        (cls.pool/'amounts.py').write_text('def double(value):\n    return value * 2\n')
        code, report = cls.cli('corpus-run', '--source-root', cls.pool, '--run-id', 'parts')
        if code:
            raise AssertionError(report)
        note = source('observed', 'A synthetic operator needs an amount and a label.', kind='synthetic')
        evidence = [cite(note)]
        bundle = {
            'schema': 1, 'title': 'Prepare a package', 'as_of': '2026-09-21', 'max_age_days': 365,
            'public_brief': 'Prepare a package for the next operator.', 'sources': [note],
            'workflows': [{
                'id': 'prepare', 'title': 'Package preparation', 'actor': 'Receiving operator',
                'domain': 'operations', 'purpose': 'Prepare an amount and label for the next operator.',
                'initial_fields': ['value'],
                'steps': [{'id': 'count', 'action': 'Prepare the package',
                           'requires': ['value', 'amount', 'label'], 'produces': ['package'],
                           'evidence': evidence}],
                'needs': [
                    {'id': 'amount', 'step': 'count', 'field': 'amount', 'pattern': 'information_gap',
                     'why': 'The next operator needs the amount.', 'basis': 'inferred', 'evidence': evidence},
                    {'id': 'label', 'step': 'count', 'field': 'label', 'pattern': 'identity_loss',
                     'why': 'The next operator needs a label.', 'basis': 'inferred', 'evidence': evidence}],
            }],
            'observations': [],
            'mechanisms': [{
                'id': 'convert', 'title': 'Convert a quantity', 'domain': 'arithmetic',
                'purpose': 'Produce an amount.', 'mechanism': 'Multiply the value by two.',
                'addresses': ['information_gap'], 'requires': ['value'], 'provides': ['amount'],
                'limits': ['The factor must suit the task.'], 'evidence': evidence,
                'code_queries': ['symbol:double']}], 'preferences': {},
        }
        bundle_file = cls.root/'bundle.json'
        bundle_file.write_bytes(canonical(bundle))
        code, report = cls.cli('opportunity-run', '--bundle', bundle_file, '--corpus-run', 'parts')
        if code:
            raise AssertionError(report)
        cls.quest = cls.root/'quest'/'quest.json'
        code, report = cls.cli('quest-start', '--run-id', 'parts', '--opportunity-run', report['id'],
                               '--workflow', 'prepare', '--out', cls.quest.parent)
        if code:
            raise AssertionError(report)
        cls.template = json.loads((cls.quest.parent/'recipe.template.json').read_bytes())
        cls.component = json.loads(cls.quest.read_bytes())['leads'][0]['definition_id']

    def setUp(self):
        self.work = self.root/self._testMethodName
        self.work.mkdir()
        self.entry = self.work/'main.py'
        self.entry.write_text('from amounts import double\nimport json,sys\n'
                              "print(json.dumps({'amount': double(int(sys.argv[1]))}))\n")
        self.recipe = copy.deepcopy(self.template)
        self.recipe.update({
            'acceptance': ['Input 6 returns amount 12.', 'Input 0 returns amount 0.'],
            'components': [self.component],
            'need_responses': [{'need': 'amount', 'outcome': 'Provide the correct package amount.',
                                'components': [self.component]}],
            'evaluation_plan': {'schema': 1, 'cases': [
                {'id': 'routine', 'acceptance': 0, 'needs': ['amount'], 'expected': {'amount': 12}},
                {'id': 'zero', 'acceptance': 1, 'needs': ['amount'], 'expected': {'amount': 0}}]},
        })
        self.recipe_file = self.work/'recipe.json'
        self.checker = self.work/'check.txt'
        self.checker.write_bytes(b'Check the exact archive with independent inputs 6 and 0.\n')
        self.build_dir = self.work/'build'
        self.artifacts = self.work/'observations'
        self.artifacts.mkdir()
        self.results_file = self.work/'results.json'
        self.out = self.work/'assessment'
        self.use_checker = True

    def build(self, quest=None):
        self.recipe_file.write_bytes(canonical(self.recipe))
        extra = ['--check-file', self.checker] if self.use_checker else []
        code, report = self.cli('quest-build', '--quest', quest or self.quest,
                                '--recipe', self.recipe_file, '--entry', self.entry,
                                '--source-root', self.pool, '--out', self.build_dir, *extra)
        self.assertEqual(code, 0, report)
        self.receipt_file = self.build_dir/'receipt.json'
        self.product = self.build_dir/'product.pyz'
        self.receipt = json.loads(self.receipt_file.read_bytes())

    def artifact(self, name, data):
        path = self.artifacts/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {'path': name, 'bytes': len(data), 'sha256': sha(data)}

    def results(self, rows=None):
        if rows is None:
            rows = [{'id': case['id'], 'status': 'OBSERVED',
                     'artifact': self.artifact(case['id']+'.json', canonical(case['expected']))}
                    for case in self.recipe.get('evaluation_plan', {}).get('cases', [])]
        plan = self.receipt.get('evaluation_plan')
        return {'schema': 1, 'receipt_sha256': sha(self.receipt_file.read_bytes()),
                'product_sha256': self.receipt['product_sha256'],
                'evaluation_plan_sha256': sha(canonical(plan)) if plan is not None else None,
                'checker_sha256': self.receipt['acceptance_file']['sha256'] if self.use_checker else None,
                'cases': rows}

    def assess(self, results, out=None, checker=None):
        self.results_file.write_bytes(canonical(results))
        extra = ['--check-file', checker or self.checker] if self.use_checker else []
        return self.cli('quest-assess', '--receipt', self.receipt_file, '--recipe', self.recipe_file,
                        '--product', self.product, '--results', self.results_file,
                        '--artifact-root', self.artifacts, '--out', out or self.out, *extra)

    def load(self):
        return json.loads((self.out/'assessment.json').read_bytes())

    def test_end_to_end_exact_archive_and_original_purpose_retained(self):
        self.build()
        original_receipt = self.receipt_file.read_bytes()
        rows = []
        for name, value in [('routine', '6'), ('zero', '0')]:
            completed = subprocess.run([sys.executable, '-I', str(self.product), value],
                                       cwd=self.work, capture_output=True, check=True, timeout=10)
            rows.append({'id': name, 'status': 'OBSERVED',
                         'artifact': self.artifact(name+'.json', completed.stdout)})
        code, result = self.assess(self.results(rows))
        self.assertEqual((code, result['outcome_status'], result['next_step']),
                         (0, 'REPORTED_PASS', 'REVIEW_UNADDRESSED_NEEDS'))
        report = self.load()
        self.assertEqual(report['purpose_evidence'], self.receipt['purpose_evidence'])
        self.assertEqual(report['unaddressed_needs'], ['label'])
        self.assertEqual(report['need_results'][0]['status'], 'REPORTED_PASS')
        self.assertEqual(self.receipt_file.read_bytes(), original_receipt)
        self.assertEqual(self.receipt['runtime_validation'], 'NOT_RUN')
        self.assertFalse(report['source_code_executed'])
        self.assertFalse(report['runtime_execution_authenticated'])
        self.assertFalse(report['execution_input_usage_authenticated'])
        self.assertTrue(all(case['input_status'] == 'NOT_DECLARED' for case in report['case_results']))
        self.assertFalse(report['release_approved'])
        self.assertEqual((self.out/'build-receipt.json').read_bytes(), original_receipt)
        self.assertEqual((self.out/'recorded-results.json').read_bytes(), self.results_file.read_bytes())
        self.assertEqual((self.out/'artifacts/routine.json').read_bytes(), (self.artifacts/'routine.json').read_bytes())
        self.assertEqual((self.out/'check-file').read_bytes(), self.checker.read_bytes())

    def test_failure_cannot_be_erased_by_a_passing_case(self):
        self.build()
        results = self.results()
        results['cases'][1]['artifact'] = self.artifact('zero.json', b'{"amount": 5}\n')
        code, result = self.assess(results)
        self.assertEqual((code, result['outcome_status'], result['next_step']), (0, 'REPORTED_FAIL', 'REVISE'))
        report = self.load()
        self.assertEqual(report['need_results'][0]['status'], 'REPORTED_FAIL')
        self.assertEqual([r['status'] for r in report['acceptance_results']], ['REPORTED_PASS', 'REPORTED_FAIL'])
        self.assertEqual(report['case_results'][1]['observed'], {'amount': 5})

    def test_missing_skipped_and_errored_cases_remain_incomplete(self):
        self.build()
        base = self.results()
        variants = [base['cases'][:1], [base['cases'][0], {'id': 'zero', 'status': 'NOT_RUN', 'artifact': None}],
                    [base['cases'][0], {'id': 'zero', 'status': 'ERROR',
                                       'artifact': self.artifact('error.txt', b'External runner failed.\n')}]]
        for index, rows in enumerate(variants):
            out = self.work/('assessment-'+str(index))
            code, result = self.assess(self.results(rows), out=out)
            self.assertEqual((code, result['outcome_status'], result['next_step']),
                             (0, 'REPORTED_INCOMPLETE', 'COMPLETE_EVALUATION'))
            report = json.loads((out/'assessment.json').read_bytes())
            self.assertEqual(report['need_results'][0]['status'], 'REPORTED_INCOMPLETE')
            self.assertNotIn('observed', report['case_results'][1])
            if index == 2:
                self.assertEqual((out/'artifacts/error.txt').read_bytes(), b'External runner failed.\n')

    def test_unknown_duplicate_and_claimed_pass_rows_are_rejected(self):
        self.build()
        base = self.results()
        variants = [[base['cases'][0], base['cases'][0]],
                    [base['cases'][0] | {'id': 'invented'}],
                    [base['cases'][0] | {'status': 'PASS'}],
                    [base['cases'][0] | {'expected': {'amount': 12}}],
                    [base['cases'][0] | {'observed': {'amount': 12}}]]
        for rows in variants:
            code, result = self.assess(base | {'cases': rows})
            self.assertEqual((code, result['reason']), (2, 'QUEST_ASSESS_RESULTS'))
            self.assertFalse(self.out.exists())

    def test_product_checker_recipe_and_result_bindings(self):
        self.build()
        results = self.results()
        for path, reason in [(self.product, 'QUEST_ASSESS_PRODUCT_BINDING'),
                             (self.checker, 'QUEST_ASSESS_CHECKER_BINDING'),
                             (self.recipe_file, 'QUEST_ASSESS_RECIPE_BINDING')]:
            original = path.read_bytes()
            path.write_bytes(original+b' ')
            try:
                code, result = self.assess(results)
            finally:
                path.write_bytes(original)
            self.assertEqual((code, result['reason']), (2, reason))
            self.assertFalse(self.out.exists())
        for key in ('receipt_sha256', 'product_sha256', 'evaluation_plan_sha256', 'checker_sha256'):
            code, result = self.assess(results | {key: '0'*64})
            self.assertEqual((code, result['reason']), (2, 'QUEST_ASSESS_RESULT_BINDING'))
            self.assertFalse(self.out.exists())

    def test_rehashed_receipt_cannot_redefine_original_expectations(self):
        self.build()
        changed = copy.deepcopy(self.receipt)
        changed['evaluation_plan']['cases'][0]['expected'] = {'amount': 99}
        self.receipt_file.write_bytes(canonical(changed))
        results = self.results()
        results['evaluation_plan_sha256'] = sha(canonical(changed['evaluation_plan']))
        code, result = self.assess(results)
        self.assertEqual((code, result['reason']), (2, 'QUEST_ASSESS_RECIPE_BINDING'))
        self.assertFalse(self.out.exists())

    def test_changed_missing_and_unsafe_artifacts_block_before_output(self):
        self.build()
        results = self.results()
        path = self.artifacts/'routine.json'
        original = path.read_bytes()
        path.write_bytes(b'{"amount": 99}')
        self.assertEqual(self.assess(results)[1]['reason'], 'QUEST_ASSESS_ARTIFACT')
        path.unlink()
        self.assertEqual(self.assess(results)[1]['reason'], 'QUEST_ASSESS_ARTIFACT')
        target = self.work/'outside.json'
        target.write_bytes(original)
        path.symlink_to(target)
        self.assertEqual(self.assess(results)[1]['reason'], 'SYMLINK_PATH')
        path.unlink()
        path.write_bytes(original)
        results['cases'][0]['artifact']['path'] = '../outside.json'
        self.assertEqual(self.assess(results)[1]['reason'], 'REPOSITORY_PATH')
        self.assertFalse(self.out.exists())

    def test_unmapped_acceptance_item_prevents_overall_pass(self):
        self.recipe['evaluation_plan']['cases'].pop()
        self.build()
        code, result = self.assess(self.results())
        self.assertEqual((code, result['outcome_status']), (0, 'REPORTED_INCOMPLETE'))
        self.assertEqual(self.load()['acceptance_results'][1],
                         {'acceptance': 1, 'description': self.recipe['acceptance'][1],
                          'cases': [], 'status': 'REPORTED_INCOMPLETE'})

    def test_unmapped_promised_need_prevents_overall_pass(self):
        for case in self.recipe['evaluation_plan']['cases']:
            case['needs'] = []
        self.build()
        code, result = self.assess(self.results())
        self.assertEqual((code, result['outcome_status']), (0, 'REPORTED_INCOMPLETE'))
        self.assertEqual(self.load()['need_results'], [{'need': 'amount', 'cases': [], 'status': 'REPORTED_INCOMPLETE'}])

    def test_legacy_recipe_without_a_plan_is_unassessed(self):
        del self.recipe['evaluation_plan']
        self.build()
        code, result = self.assess(self.results())
        self.assertEqual((code, result['outcome_status'], result['next_step']),
                         (0, 'REPORTED_INCOMPLETE', 'COMPLETE_EVALUATION'))
        self.assertIsNone(self.load()['evaluation_plan'])
        self.assertEqual(self.load()['case_results'], [])
        self.assertTrue(all(row['status'] == 'REPORTED_INCOMPLETE' for row in self.load()['acceptance_results']))

    def test_idea_quest_can_finish_selected_checks_without_workflow_needs(self):
        quest = self.work/'idea'/'quest.json'
        code, result = self.cli('quest-start', '--run-id', 'parts', '--idea', 'double', '--out', quest.parent)
        self.assertEqual(code, 0, result)
        self.recipe.pop('opportunity')
        self.recipe.pop('need_responses')
        for case in self.recipe['evaluation_plan']['cases']:
            case['needs'] = []
        self.use_checker = False
        self.build(quest=quest)
        code, result = self.assess(self.results())
        self.assertEqual((code, result['outcome_status'], result['next_step']),
                         (0, 'REPORTED_PASS', 'READY_FOR_FIELD_TRIAL'))
        self.assertNotIn('purpose_evidence', self.load())
        self.assertIsNone(self.load()['checker_sha256'])

    def test_json_null_is_an_observation_and_boolean_is_not_a_number(self):
        self.recipe['evaluation_plan']['cases'][0]['expected'] = None
        self.recipe['evaluation_plan']['cases'][1]['expected'] = 1
        self.build()
        results = self.results()
        results['cases'][1]['artifact'] = self.artifact('zero.json', b'true\n')
        code, result = self.assess(results)
        self.assertEqual((code, result['outcome_status']), (0, 'REPORTED_FAIL'))
        self.assertEqual(self.load()['case_results'][0]['status'], 'REPORTED_PASS')
        self.assertIn('observed', self.load()['case_results'][0])
        self.assertIsNone(self.load()['case_results'][0]['observed'])

    def test_nonfinite_and_duplicate_json_keys_are_rejected(self):
        self.build()
        results = self.results()
        for data, reason in [(b'NaN', 'NONFINITE_JSON'), (b'{"amount":1,"amount":12}', 'DUPLICATE_JSON_KEY')]:
            results['cases'][0]['artifact'] = self.artifact('routine.json', data)
            self.assertEqual(self.assess(results)[1]['reason'], reason)
            self.assertFalse(self.out.exists())

    def test_invalid_plan_never_publishes_a_product(self):
        original = copy.deepcopy(self.recipe)
        first = original['evaluation_plan']['cases'][0]
        invalid = [[first, first], [], [first | {'needs': ['unknown']}],
                   [first | {'needs': ['amount', 'amount']}], [first | {'acceptance': 2}],
                   [first | {'acceptance': True}], [first | {'expected': 'x'*64001}]]
        for cases in invalid:
            self.recipe_file.write_bytes(canonical(original | {'evaluation_plan': {'schema': 1, 'cases': cases}}))
            code, result = self.cli('quest-build', '--quest', self.quest, '--recipe', self.recipe_file,
                                    '--entry', self.entry, '--source-root', self.pool, '--out', self.build_dir)
            self.assertEqual((code, result['reason']), (2, 'QUEST_EVALUATION_PLAN'))
            self.assertFalse(self.build_dir.exists())

    def test_cli_reports_invalid_case_location_without_echoing_its_label(self):
        self.recipe['evaluation_plan']['cases'][0]['id'] = 'private-case-label '
        self.recipe_file.write_bytes(canonical(self.recipe))
        code, result = self.cli('quest-build', '--quest', self.quest, '--recipe', self.recipe_file,
                                '--entry', self.entry, '--source-root', self.pool, '--out', self.build_dir)
        self.assertEqual((code, result['reason']), (2, 'QUEST_EVALUATION_PLAN'))
        self.assertEqual(result['detail']['field'], 'evaluation_plan.cases[0].id')
        self.assertNotIn('private-case-label', canonical(result).decode())
        self.assertFalse(self.build_dir.exists())

    def test_malformed_receipt_has_a_structured_refusal(self):
        self.build()
        for mode in ([], {}, None):
            self.receipt_file.write_bytes(canonical(self.receipt | {'mode': mode}))
            code, result = self.assess(self.results())
            self.assertEqual((code, result['reason']), (2, 'QUEST_ASSESS_RECEIPT'))
            self.assertFalse(self.out.exists())

    def test_preflight_summary_is_validated_and_legacy_receipts_still_work(self):
        from forge_core.quest_assessment import _receipt
        self.build()
        raw_recipe = self.recipe_file.read_bytes()
        legacy = copy.deepcopy(self.receipt)
        del legacy['preflight']
        _receipt(canonical(legacy), raw_recipe, self.recipe)
        for update in ({'status': []}, {'status': 'EXECUTED_PASS'}, {'files': True}, {'files': 0},
                       {'issues': -1}, {'issues': 513}, {'issues': 1}, {'omitted_issues': 1},
                       {'report_sha256': 'bad'}, {'source_code_executed': True},
                       {'runtime_validation': 'PASSED'}):
            with self.subTest(update=update):
                changed = copy.deepcopy(self.receipt)
                changed['preflight'].update(update)
                with self.assertRaisesRegex(Blocked, '^QUEST_ASSESS_RECEIPT$'):
                    _receipt(canonical(changed), raw_recipe, self.recipe)

    def test_no_assessment_written_into_artifacts_or_source(self):
        self.build()
        results = self.results()
        for out in (self.artifacts/'report', self.pool/'assessment', self.home/'assessment'):
            code, result = self.assess(results, out=out)
            self.assertEqual(code, 2, result)
            self.assertFalse(out.exists())

    def test_declared_inputs_retained_for_exact_archive_observations(self):
        self.entry.write_text('from amounts import double\nimport json,sys\nfrom pathlib import Path\n'
                              "print(json.dumps({'amount': double(int(Path(sys.argv[1]).read_text()))}))\n")
        for case, value in zip(self.recipe['evaluation_plan']['cases'], (6, 0)):
            case['inputs'] = [self.artifact('inputs/'+case['id']+'.txt', (str(value)+'\n').encode())]
        self.build()
        rows = []
        for case in self.recipe['evaluation_plan']['cases']:
            path = self.artifacts/case['inputs'][0]['path']
            completed = subprocess.run([sys.executable, '-I', str(self.product), str(path)],
                                       cwd=self.work, capture_output=True, check=True, timeout=10)
            rows.append({'id': case['id'], 'status': 'OBSERVED',
                         'artifact': self.artifact(case['id']+'.json', completed.stdout)})
        original_receipt = self.receipt_file.read_bytes()
        code, result = self.assess(self.results(rows))
        self.assertEqual((code, result['outcome_status']), (0, 'REPORTED_PASS'))
        report = self.load()
        self.assertFalse(report['execution_input_usage_authenticated'])
        self.assertEqual(self.receipt_file.read_bytes(), original_receipt)
        for planned, observed in zip(self.recipe['evaluation_plan']['cases'], report['case_results']):
            self.assertEqual(observed['input_status'], 'VERIFIED_DECLARED_INPUT_BYTES')
            self.assertEqual(observed['inputs'], planned['inputs'])
            reference = observed['inputs'][0]
            self.assertEqual((self.out/'artifacts'/reference['path']).read_bytes(),
                             (self.artifacts/reference['path']).read_bytes())

    def test_unobserved_cases_still_verify_and_retain_declared_inputs(self):
        self.recipe['evaluation_plan']['cases'][1]['inputs'] = [self.artifact('inputs/zero.txt', b'0\n')]
        self.build()
        base = self.results()
        variants = [base['cases'][:1], [base['cases'][0], {'id': 'zero', 'status': 'NOT_RUN', 'artifact': None}],
                    [base['cases'][0], {'id': 'zero', 'status': 'ERROR', 'artifact': None}]]
        for index, rows in enumerate(variants):
            out = self.work/('unobserved-'+str(index))
            code, result = self.assess(self.results(rows), out=out)
            self.assertEqual((code, result['outcome_status']), (0, 'REPORTED_INCOMPLETE'))
            report = json.loads((out/'assessment.json').read_bytes())
            self.assertEqual(report['case_results'][0]['input_status'], 'NOT_DECLARED')
            self.assertEqual(report['case_results'][1]['input_status'], 'VERIFIED_DECLARED_INPUT_BYTES')
            self.assertEqual((out/'artifacts/inputs/zero.txt').read_bytes(), b'0\n')

    def test_changed_or_missing_inputs_block_even_without_observations(self):
        reference = self.artifact('inputs/value.txt', b'6\n')
        self.recipe['evaluation_plan']['cases'][0]['inputs'] = [reference]
        self.build()
        results = self.results(rows=[])
        path = self.artifacts/reference['path']
        for content in (b'7\n', b'6\n '):
            path.write_bytes(content)
            code, result = self.assess(results)
            self.assertEqual((code, result['reason']), (2, 'QUEST_ASSESS_ARTIFACT'))
            self.assertFalse(self.out.exists())
        path.unlink()
        code, result = self.assess(results)
        self.assertEqual((code, result['reason']), (2, 'QUEST_ASSESS_ARTIFACT'))
        self.assertFalse(self.out.exists())

    def test_declared_input_symlinks_are_refused(self):
        reference = self.artifact('inputs/value.txt', b'6\n')
        self.recipe['evaluation_plan']['cases'][0]['inputs'] = [reference]
        self.build()
        path = self.artifacts/reference['path']
        target = self.work/'outside.txt'
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
        code, result = self.assess(self.results())
        self.assertEqual((code, result['reason']), (2, 'SYMLINK_PATH'))
        self.assertFalse(self.out.exists())

    def test_invalid_input_references_prevent_a_build(self):
        reference = {'path': 'inputs/value.txt', 'bytes': 2, 'sha256': sha(b'6\n')}
        variants = [None, {}, [], [reference]*17, [reference, reference],
                    [reference | {'bytes': True}], [reference | {'bytes': -1}],
                    [reference | {'bytes': 1_000_001}], [reference | {'sha256': 'invalid'}],
                    [reference | {'extra': True}], [{'path': reference['path']}]]
        for inputs in variants:
            self.recipe['evaluation_plan']['cases'][0]['inputs'] = inputs
            with self.subTest(inputs=inputs), self.assertRaisesRegex(Blocked, '^QUEST_EVALUATION_INPUTS$'):
                validate_plan(self.recipe)
        for path in ('../outside', '/absolute', 'inputs//value.txt', 'inputs/./value.txt',
                     'inputs\\value.txt', 'inputs:value.txt'):
            self.recipe['evaluation_plan']['cases'][0]['inputs'] = [reference | {'path': path}]
            with self.subTest(path=path), self.assertRaisesRegex(Blocked, '^REPOSITORY_PATH$'):
                validate_plan(self.recipe)
        # Exercise structural variants directly, then verify the public builder
        # uses that validator and publishes no product on representative errors.
        for inputs, reason in (([], 'QUEST_EVALUATION_INPUTS'),
                               ([reference | {'path': '../outside'}], 'REPOSITORY_PATH')):
            self.recipe['evaluation_plan']['cases'][0]['inputs'] = inputs
            self.recipe_file.write_bytes(canonical(self.recipe))
            code, result = self.cli('quest-build', '--quest', self.quest, '--recipe', self.recipe_file,
                                    '--entry', self.entry, '--source-root', self.pool, '--out', self.build_dir)
            self.assertEqual((code, result['reason']), (2, reason))
            self.assertFalse(self.build_dir.exists())

    def test_surrogate_input_paths_are_refused_with_field_diagnostics(self):
        for point in (0xd800, 0xdbff, 0xdc00, 0xdc80, 0xdfff):
            reference = {'path': 'inputs/' + chr(point) + '.json', 'bytes': 2, 'sha256': sha(b'{}')}
            self.recipe['evaluation_plan']['cases'][0]['inputs'] = [reference]
            with self.subTest(point=point), self.assertRaisesRegex(Blocked, '^QUEST_EVALUATION_INPUTS$') as caught:
                validate_plan(self.recipe)
            self.assertEqual(caught.exception.detail['field'], 'evaluation_plan.cases[0].inputs[0]')
            self.assertNotIn(reference['path'], str(caught.exception.detail))

    def test_surrogate_observation_paths_have_a_structured_cli_refusal(self):
        self.build()
        results = self.results()
        for point in (0xd800, 0xdbff, 0xdc00, 0xdc80, 0xdfff):
            results['cases'][0]['artifact']['path'] = 'observed/' + chr(point) + '.json'
            with self.subTest(point=point):
                code, result = self.assess(results)
                self.assertEqual((code, result['reason']), (2, 'QUEST_ASSESS_ARTIFACT'))
                self.assertFalse(self.out.exists())

    def test_valid_unicode_input_and_observation_paths_preserve_bytes(self):
        reference = self.artifact('資料/café-𐐀.txt', b'6\r\n')
        self.recipe['evaluation_plan']['cases'][0]['inputs'] = [reference]
        self.build()
        results = self.results()
        data = canonical(self.recipe['evaluation_plan']['cases'][0]['expected'])
        results['cases'][0]['artifact'] = self.artifact('観測/Δ-é.json', data)
        code, result = self.assess(results)
        self.assertEqual((code, result['outcome_status']), (0, 'REPORTED_PASS'))
        self.assertEqual((self.out / 'artifacts' / reference['path']).read_bytes(), b'6\r\n')
        self.assertEqual((self.out / 'artifacts/観測/Δ-é.json').read_bytes(), data)

    def test_result_or_rehashed_receipt_cannot_replace_fixed_inputs(self):
        reference = self.artifact('inputs/value.txt', b'6\n')
        self.recipe['evaluation_plan']['cases'][0]['inputs'] = [reference]
        self.build()
        results = self.results()
        results['cases'][0]['inputs'] = [reference | {'sha256': sha(b'7\n')}]
        code, result = self.assess(results)
        self.assertEqual((code, result['reason']), (2, 'QUEST_ASSESS_RESULTS'))
        self.assertFalse(self.out.exists())
        changed = copy.deepcopy(self.receipt)
        changed['evaluation_plan']['cases'][0]['inputs'][0]['sha256'] = sha(b'7\n')
        self.receipt_file.write_bytes(canonical(changed))
        results = self.results()
        results['evaluation_plan_sha256'] = sha(canonical(changed['evaluation_plan']))
        code, result = self.assess(results)
        self.assertEqual((code, result['reason']), (2, 'QUEST_ASSESS_RECIPE_BINDING'))
        self.assertFalse(self.out.exists())

    def test_shared_inputs_count_once_in_artifact_budget(self):
        references = [self.artifact('inputs/part-'+str(i), b'x'*1_000_000) for i in range(4)]
        for case in self.recipe['evaluation_plan']['cases']:
            case['inputs'] = references
        self.build()
        code, result = self.assess(self.results())
        self.assertEqual((code, result['outcome_status']), (0, 'REPORTED_PASS'))
        self.assertEqual(len(list((self.out/'artifacts/inputs').iterdir())), 4)
        self.assertTrue(all(case['input_status'] == 'VERIFIED_DECLARED_INPUT_BYTES'
                            for case in self.load()['case_results']))

    def test_declared_inputs_and_observations_share_total_budget(self):
        self.recipe['evaluation_plan']['cases'][0]['inputs'] = [
            self.artifact('inputs/part-'+str(i), b'x'*1_000_000) for i in range(8)]
        self.build()
        code, result = self.assess(self.results())
        self.assertEqual((code, result['reason']), (2, 'QUEST_ASSESS_ARTIFACT'))
        self.assertFalse(self.out.exists())

    def test_conflicting_or_over_budget_inputs_prevent_build(self):
        reference = {'path': 'inputs/value.txt', 'bytes': 2, 'sha256': sha(b'6\n')}
        self.recipe['evaluation_plan']['cases'][0]['inputs'] = [reference]
        self.recipe['evaluation_plan']['cases'][1]['inputs'] = [reference | {'sha256': sha(b'7\n')}]
        for phase in ('conflict', 'budget'):
            if phase == 'budget':
                self.recipe['evaluation_plan']['cases'][0]['inputs'] = [
                    reference | {'path': 'inputs/part-'+str(i), 'bytes': 1_000_000} for i in range(9)]
                del self.recipe['evaluation_plan']['cases'][1]['inputs']
            self.recipe_file.write_bytes(canonical(self.recipe))
            code, result = self.cli('quest-build', '--quest', self.quest, '--recipe', self.recipe_file,
                                    '--entry', self.entry, '--source-root', self.pool, '--out', self.build_dir)
            self.assertEqual((code, result['reason']), (2, 'QUEST_EVALUATION_INPUTS'))
            self.assertFalse(self.build_dir.exists())

    def test_input_artifacts_are_opaque_bytes_and_never_executed(self):
        payload = b"raise AssertionError('input must never execute')\n"
        references = [self.artifact('inputs/program.py', payload),
                      self.artifact('inputs/binary.dat', b'\x00\xff\xfe\n')]
        self.recipe['evaluation_plan']['cases'][0]['inputs'] = references
        self.build()
        code, result = self.assess(self.results())
        self.assertEqual((code, result['outcome_status']), (0, 'REPORTED_PASS'))
        for reference in references:
            self.assertEqual((self.out/'artifacts'/reference['path']).read_bytes(),
                             (self.artifacts/reference['path']).read_bytes())
        self.assertFalse(self.load()['source_code_executed'])
        self.assertFalse(self.load()['execution_input_usage_authenticated'])


if __name__ == '__main__':
    unittest.main()
