"""Preserve refusal codes while identifying fields without disclosing values."""
import copy
import unittest

from forge_core.common import Blocked, canonical, sha
from forge_core.quest_assessment import validate_plan
from forge_core.quest_diagnostics import DiagnosticBlocked, require_text


class QuestDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.recipe = {'acceptance': ['A fixed result.'], 'evaluation_plan': {
            'schema': 1, 'cases': [{'id': 'case-1', 'acceptance': 0, 'needs': [], 'expected': {}}]}}

    def rejected(self, field, reason='QUEST_EVALUATION_PLAN'):
        before = canonical(self.recipe)
        with self.assertRaises(DiagnosticBlocked) as caught:
            validate_plan(self.recipe)
        error = caught.exception
        self.assertIsInstance(error, Blocked)
        self.assertEqual(str(error), reason)
        self.assertEqual(error.detail['field'], field)
        self.assertTrue(error.detail['hint'])
        self.assertEqual(canonical(self.recipe), before)
        return error

    def test_identifier_guidance_does_not_rewrite_or_echo_record_identifier(self):
        self.recipe['evaluation_plan']['cases'][0]['id'] = 'private-record-label '
        error = self.rejected('evaluation_plan.cases[0].id')
        self.assertNotIn('private-record-label', canonical(error.detail).decode())
        self.assertIn('fixture unchanged', error.detail['hint'])

    def test_second_case_duplicate_is_located(self):
        self.recipe['evaluation_plan']['cases'] *= 2
        self.rejected('evaluation_plan.cases[1].id')

    def test_schema_list_and_case_fields(self):
        valid = copy.deepcopy(self.recipe)
        variants = [(None, 'evaluation_plan'),
                    ({'schema': True, 'cases': []}, 'evaluation_plan.schema'),
                    ({'schema': 1, 'cases': []}, 'evaluation_plan.cases'),
                    ({'schema': 1, 'cases': [{}]}, 'evaluation_plan.cases[0]')]
        for plan, field in variants:
            with self.subTest(field=field):
                self.recipe = copy.deepcopy(valid)
                self.recipe['evaluation_plan'] = plan
                self.rejected(field)

    def test_boolean_acceptance_unknown_need_and_oversized_expectation(self):
        case = self.recipe['evaluation_plan']['cases'][0]
        for name, value in [('acceptance', True), ('needs', ['unknown']), ('expected', 'x' * 64001)]:
            with self.subTest(field=name):
                self.recipe['evaluation_plan']['cases'][0] = dict(case, **{name: value})
                self.rejected('evaluation_plan.cases[0].' + name)

    def test_oversized_input_has_a_numeric_budget_and_no_values(self):
        self.recipe['evaluation_plan']['cases'][0]['inputs'] = [
            {'path': 'private-source.json', 'bytes': 1_000_001, 'sha256': sha(b'') }]
        error = self.rejected('evaluation_plan.cases[0].inputs[0]', 'QUEST_EVALUATION_INPUTS')
        self.assertIn('1000000', error.detail['hint'])
        self.assertNotIn('private-source.json', canonical(error.detail).decode())

    def test_path_refusal_keeps_original_code(self):
        self.recipe['evaluation_plan']['cases'][0]['inputs'] = [
            {'path': '../private-source.json', 'bytes': 0, 'sha256': sha(b'')}]
        self.rejected('evaluation_plan.cases[0].inputs[0]', 'REPOSITORY_PATH')

    def test_conflicting_input_binding_and_total_budget(self):
        reference = {'path': 'input.json', 'bytes': 1, 'sha256': sha(b'0')}
        first = self.recipe['evaluation_plan']['cases'][0]
        first['inputs'] = [reference]
        self.recipe['evaluation_plan']['cases'].append(dict(first, id='case-2', inputs=[dict(reference, bytes=2)]))
        self.rejected('evaluation_plan.cases[1].inputs[0]', 'QUEST_EVALUATION_INPUTS')
        self.recipe['evaluation_plan']['cases'] = [dict(first, inputs=[
            dict(reference, path=f'input-{i}.json', bytes=1_000_000) for i in range(9)])]
        self.rejected('evaluation_plan.cases[0].inputs', 'QUEST_EVALUATION_INPUTS')

    def test_no_plan_and_valid_plan_are_unchanged(self):
        self.assertIsNone(validate_plan({'acceptance': ['A check.']}))
        plan = self.recipe['evaluation_plan']
        self.assertIs(validate_plan(self.recipe), plan)

    def test_text_error_retains_reason_and_declared_limit(self):
        with self.assertRaises(DiagnosticBlocked) as caught:
            require_text('private-value' * 20, 'idea', 200)
        self.assertEqual(str(caught.exception), 'TEXT_BOUNDS')
        self.assertEqual(caught.exception.detail['field'], 'idea')
        self.assertIn('200', caught.exception.detail['hint'])
        self.assertNotIn('private-value', canonical(caught.exception.detail).decode())


if __name__ == '__main__':
    unittest.main()
