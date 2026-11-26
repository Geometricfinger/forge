"""Review rendering preserves data and never turns annotations into markup."""
import copy
import html
from html.parser import HTMLParser
import unittest

from forge_core import opportunities as op
from forge_core.invention_view import render_build_options
from forge_core.opportunity_examples import example_bundle


def fixture():
    bundle = example_bundle()
    evidence = copy.deepcopy(bundle['workflows'][0]['needs'][0]['evidence'])
    bundle['mechanisms'] = [
        {'id': 'parse', 'title': 'Read declared records', 'domain': 'synthetic',
         'purpose': 'Read records.', 'mechanism': 'Inspect the declared input.',
         'addresses': ['format_gap'], 'requires': ['file'], 'provides': ['records'],
         'limits': ['Synthetic declaration.'], 'evidence': evidence, 'code_queries': [],
         'role': 'source_component'},
        {'id': 'resolve', 'title': 'Review declared identity', 'domain': 'synthetic',
         'purpose': 'Review evidence.', 'mechanism': 'Preserve unknown identity.',
         'addresses': ['identity_loss'], 'requires': ['records'], 'provides': ['identity'],
         'limits': ['No factual identity guarantee.'], 'evidence': evidence, 'code_queries': [],
         'role': 'new_work'},
    ]
    return bundle


def hypothesis(bundle):
    return next(h for h in op.evaluate(bundle)['hypotheses'] if h['need']['id'] == 'identity')


def visible_text(markup):
    class Reader(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)

    reader = Reader()
    reader.feed(markup)
    return ''.join(reader.parts)


class InventionViewTests(unittest.TestCase):
    def test_annotation_text_is_escaped_with_input_order_and_limits_retained(self):
        bundle = fixture()
        value = '<img src=x onerror="alert(1)"> & payload'
        bundle['mechanisms'][0].update({'title': value, 'mechanism': value,
                                       'provides': [value], 'limits': [value], 'code_queries': [value]})
        bundle['mechanisms'][1]['requires'] = [value]
        result = render_build_options(hypothesis(bundle))
        self.assertNotIn('<img', result)
        self.assertIn(html.escape(value, quote=True), result)
        self.assertIn('step parse', result)
        self.assertLess(result.index(html.escape(value, quote=True)), result.index('Review declared identity'))
        self.assertIn('New work requiring implementation and evidence', result)
        self.assertIn('Source queries:', result)

    def test_unknown_conditions_and_catalog_blockers_remain_visible(self):
        bundle = fixture()
        bundle['workflows'][0]['required_conditions'] = ['local_only']
        blocked = copy.deepcopy(bundle['mechanisms'][1])
        blocked.update({'id': 'alternate', 'requires': ['missing_signal']})
        bundle['mechanisms'].append(blocked)
        result = render_build_options(hypothesis(bundle))
        self.assertIn('conditions unconfirmed', result)
        self.assertIn('local_only: Unknown', visible_text(result))
        self.assertIn('No condition declaration retained.', result)
        self.assertIn('Inputs with no declared provider', result)
        self.assertIn('missing_signal', result)
        self.assertIn('catalog branches may be unused', result)
        self.assertIn('Transitions omitted by depth limit', result)

    def test_existing_option_gate_shows_next_decision_and_unassessed_search(self):
        bundle = fixture()
        bundle['observations'][0]['kind'] = 'supported'
        result = render_build_options(hypothesis(bundle))
        self.assertIn('Review the cited existing option', result)
        self.assertIn('Verify existing option', result)
        self.assertIn('Not assessed', result)
        self.assertNotIn('<summary>Option 1', result)

    def test_historical_report_has_no_invented_options(self):
        self.assertEqual(render_build_options({'state': 'INFERRED_HANDOFF_GAP'}), '')


if __name__ == '__main__':
    unittest.main()
