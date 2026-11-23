"""Carry workflow evidence into the real creation CLI and executable package."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from forge_core.common import canonical, sha
from forge_core.engine import ROOT
from forge_core.opportunity_examples import cite, source


class OpportunityQuestTests(unittest.TestCase):
    @classmethod
    def cli(cls, *args):
        result = subprocess.run(
            [sys.executable, '-B', str(ROOT/'forge.py'), '--home', str(cls.home), *map(str, args)],
            capture_output=True, timeout=90, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
        )
        if result.stderr:
            raise AssertionError(result.stderr.decode(errors='replace'))
        return result.returncode, json.loads(result.stdout)

    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.root = Path(temp.name).resolve()
        cls.home = cls.root/'home'
        cls.pool = cls.root/'pool'
        cls.pool.mkdir()
        (cls.pool/'amounts.py').write_text('def double(value):\n    return value * 2\n')
        (cls.pool/'labels.py').write_text('def label(value):\n    return str(value)\n')
        code, _ = cls.cli('corpus-run', '--source-root', cls.pool, '--run-id', 'parts')
        if code:
            raise AssertionError('Source indexing failed')
        note = source('observed', 'A synthetic operator needs package amounts and a readable label.',
                      kind='synthetic')
        existing = source('existing', 'The current local labeling tool already makes readable labels.',
                          kind='product_docs')
        evidence = [cite(note)]
        cls.bundle = {
            'schema': 1, 'title': 'Prepare a package', 'as_of': '2026-09-21', 'max_age_days': 365,
            'public_brief': 'Prepare a package for the next team.',
            'sources': [note, existing],
            'workflows': [{
                'id': 'prepare', 'title': 'Package preparation', 'actor': 'Receiving operator',
                'domain': 'operations', 'purpose': 'Prepare the amount and label for the next team.',
                'initial_fields': ['value'],
                'steps': [
                    {'id': 'count', 'action': 'Determine package amount', 'requires': ['value', 'amount'],
                     'produces': ['counted'], 'evidence': evidence},
                    {'id': 'tag', 'action': 'Print the package label', 'requires': ['value', 'label'],
                     'produces': ['tagged'], 'evidence': evidence},
                ],
                'needs': [
                    {'id': 'amount', 'step': 'count', 'field': 'amount', 'pattern': 'information_gap',
                     'why': 'The amount must be available for the next team.', 'basis': 'inferred',
                     'evidence': evidence},
                    {'id': 'label', 'step': 'tag', 'field': 'label', 'pattern': 'identity_loss',
                     'why': 'The next team needs a readable label.', 'basis': 'inferred',
                     'evidence': evidence},
                ],
            }],
            'observations': [{
                'id': 'existing-label', 'workflow': 'prepare', 'need': 'label', 'kind': 'supported',
                'scope': 'target', 'statement': 'Evaluate the current labeling tool before replacing it.',
                'evidence': [cite(existing)],
            }],
            'mechanisms': [{
                'id': 'amount-conversion', 'title': 'Convert a quantity', 'domain': 'arithmetic',
                'purpose': 'Produce the declared package amount.', 'mechanism': 'Multiply input by two.',
                'addresses': ['information_gap'], 'requires': ['value'], 'provides': ['amount'],
                'limits': ['The factor must fit the target task.'], 'evidence': evidence,
                'code_queries': ['symbol:double'],
            }],
            'preferences': {},
        }
        bundle_file = cls.root/'bundle.json'
        bundle_file.write_bytes(canonical(cls.bundle))
        code, report = cls.cli('opportunity-run', '--bundle', bundle_file, '--corpus-run', 'parts')
        if code:
            raise AssertionError(report)
        cls.opportunity = report
        cls.quest_dir = cls.root/'quest'
        code, result = cls.cli('quest-start', '--run-id', 'parts', '--opportunity-run', report['id'],
                               '--workflow', 'prepare', '--out', cls.quest_dir)
        if code:
            raise AssertionError(result)
        cls.quest = json.loads((cls.quest_dir/'quest.json').read_bytes())
        cls.component = cls.quest['leads'][0]['definition_id']

    def setUp(self):
        self.work = self.root/self._testMethodName
        self.work.mkdir()
        self.entry = self.work/'main.py'
        self.entry.write_text('from amounts import double\nimport sys\nprint(double(int(sys.argv[1])))\n')
        self.recipe = json.loads((self.quest_dir/'recipe.template.json').read_bytes())
        self.recipe.update({
            'acceptance': ['Input 6 produces amount 12.'],
            'components': [self.component],
            'need_responses': [{'need': 'amount', 'outcome': 'Input 6 produces the amount 12.',
                                'components': [self.component]}],
        })
        self.recipe_file = self.work/'recipe.json'
        self.out = self.work/'product'

    def build(self, quest=None):
        self.recipe_file.write_bytes(canonical(self.recipe))
        return self.cli('quest-build', '--quest', quest or self.quest_dir/'quest.json',
                        '--recipe', self.recipe_file, '--entry', self.entry,
                        '--source-root', self.pool, '--out', self.out)

    def test_whole_workflow_survives_into_a_runnable_product(self):
        self.assertEqual(self.quest['mode'], 'opportunity')
        origin = self.quest['opportunity']
        self.assertEqual(origin['workflow'], self.bundle['workflows'][0])
        self.assertEqual(len(origin['hypotheses']), 2)
        self.assertEqual(origin['sources'], self.bundle['sources'])
        existing = next(h for h in origin['hypotheses'] if h['need']['id'] == 'label')
        self.assertEqual(existing['state'], 'EXISTING_OPTION_TO_VERIFY')
        self.assertTrue(existing['counter_searches'])
        self.assertTrue(existing['evidence']['support_observations'])
        code, result = self.build()
        self.assertEqual(code, 0, result)
        receipt = json.loads((self.out/'receipt.json').read_bytes())
        purpose = receipt['purpose_evidence']
        self.assertEqual(purpose['opportunity'], origin)
        self.assertEqual(purpose['need_responses'], self.recipe['need_responses'])
        self.assertEqual(purpose['unaddressed_needs'], ['label'])
        self.assertEqual(purpose['market_validation'], 'NOT_ESTABLISHED')
        self.assertEqual(receipt['runtime_validation'], 'NOT_RUN')
        result = subprocess.run([sys.executable, '-I', str(self.out/'product.pyz'), '6'],
                                cwd=self.work, capture_output=True, check=True, timeout=10)
        self.assertEqual(result.stdout, b'12\n')

    def test_invalid_workflow_or_mode_arguments_leave_no_output(self):
        for extra in (['--opportunity-run', self.opportunity['id']],
                      ['--opportunity-run', self.opportunity['id'], '--workflow', 'missing'],
                      ['--scout', '--workflow', 'prepare']):
            code, result = self.cli('quest-start', '--run-id', 'parts', '--out', self.out, *extra)
            self.assertEqual(code, 2, result)
            self.assertFalse(self.out.exists())

    def test_corpus_must_belong_to_the_opportunity_run(self):
        code, report = self.cli('corpus-run', '--source-root', self.pool, '--run-id', 'unselected')
        self.assertEqual(code, 0, report)
        code, result = self.cli('quest-start', '--run-id', 'unselected',
                               '--opportunity-run', self.opportunity['id'],
                               '--workflow', 'prepare', '--out', self.out)
        self.assertEqual((code, result['reason']), (2, 'QUEST_OPPORTUNITY_CORPUS'))
        self.assertFalse(self.out.exists())

    def test_recipe_must_answer_a_declared_need_with_its_selected_lead(self):
        original = copy.deepcopy(self.recipe)
        wrong = 'definition_' + '0'*64
        updates = [
            {'opportunity': {'run_id': self.opportunity['id'], 'workflow_id': 'different'}},
            {'need_responses': []},
            {'need_responses': [{'need': 'invented', 'outcome': 'Useful', 'components': [self.component]}]},
            {'need_responses': [{'need': 'label', 'outcome': 'Useful', 'components': [self.component]}]},
            {'need_responses': [{'need': 'amount', 'outcome': 'Useful', 'components': [wrong]}]},
        ]
        for update in updates:
            self.recipe = original | update
            code, result = self.build()
            self.assertEqual(code, 2, result)
            self.assertFalse(self.out.exists())

    def test_changed_quest_goal_is_rejected(self):
        changed = copy.deepcopy(self.quest)
        changed['opportunity']['workflow']['purpose'] = 'A different product goal'
        path = self.work/'changed.json'
        path.write_bytes(canonical(changed))
        code, result = self.build(quest=path)
        self.assertEqual((code, result['reason']), (2, 'QUEST_BINDING'))
        self.assertFalse(self.out.exists())

    def test_rehashed_stored_report_is_recomputed_against_source_annotations(self):
        path = self.home/'opportunities'/self.opportunity['id']/'record.json'
        original = path.read_bytes()
        changed = json.loads(original)
        changed['payload']['report']['hypotheses'][0]['concept']['user'] = 'A different operator'
        changed['sha256'] = sha(canonical(changed['payload']))
        path.write_bytes(canonical(changed))
        try:
            code, result = self.build()
        finally:
            path.write_bytes(original)
        self.assertEqual((code, result['reason']), (2, 'QUEST_OPPORTUNITY_BINDING'))
        self.assertFalse(self.out.exists())

    def test_changed_saved_corpus_is_detected_before_build(self):
        report_file = self.home/'corpora'/'parts'/'corpus.json'
        receipt_file = report_file.parent/'receipt.json'
        original_report, original_receipt = report_file.read_bytes(), receipt_file.read_bytes()
        changed = json.loads(original_report)
        changed['status'] = 'CHANGED'
        data = canonical(changed)
        receipt = json.loads(original_receipt)
        receipt['report_sha256'] = sha(data)
        report_file.write_bytes(data)
        receipt_file.write_bytes(canonical(receipt))
        try:
            code, result = self.build()
        finally:
            report_file.write_bytes(original_report)
            receipt_file.write_bytes(original_receipt)
        self.assertEqual((code, result['reason']), (2, 'QUEST_OPPORTUNITY_CORPUS'))
        self.assertFalse(self.out.exists())


if __name__ == '__main__':
    unittest.main()
