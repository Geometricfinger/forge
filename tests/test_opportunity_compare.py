"""Compare exact citations across saved synthetic Opportunity Lab runs."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from forge_core import opportunities
from forge_core.common import Blocked
from forge_core.opportunities import OpportunityStore
from forge_core.opportunity_examples import cite, example_bundle, source
from forge_core.workspace import ROOT, initialize


def annotated(text, *quotes, owner='manual', uri='fixture://field-note'):
    bundle = example_bundle()
    note = source('note', text, kind='synthetic', uri=uri)
    bundle['sources'].append(note)
    observation = bundle['observations'][0]
    observation['id'] = owner
    observation['evidence'] = [cite(note, quote) for quote in quotes]
    return bundle


def note_citations(row):
    return [citation for citation in row['citations'] if citation['source'] == 'note']


def snapshot(folder):
    return {p.relative_to(folder): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in folder.rglob('*') if p.is_file()}


class OpportunityComparisonTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / 'home'
        self.store = OpportunityStore(SimpleNamespace(home=self.home, guard=lambda: None))

    def compare(self, before, after):
        first = self.store.run(before)['id']
        second = self.store.run(after)['id']
        return self.store.compare(first, second)['hypotheses']

    def test_unrelated_replacement_in_same_source_requires_review(self):
        before = annotated('The crate arrived unsigned.', 'unsigned')
        after = annotated('The shelf is blue.', 'blue')
        row, = self.compare(before, after)
        self.assertEqual(row['status'], 'REVIEW_REQUIRED')
        self.assertCountEqual([c['status'] for c in note_citations(row)],
                              ['PASSAGE_MISSING', 'NEW_CITATION'])

    def test_unique_quote_moved_to_a_new_offset(self):
        before = annotated('The receipt needs a label.', 'needs a label')
        after = annotated('Today the receipt needs a label.', 'needs a label')
        row, = self.compare(before, after)
        citation, = note_citations(row)
        self.assertEqual(row['status'], 'CITATIONS_RETAINED')
        self.assertEqual(citation['status'], 'MOVED')
        self.assertEqual(citation['before']['start'], 12)
        self.assertEqual(citation['after']['start'], 18)

    def test_identical_sibling_cannot_absorb_a_dropped_reference(self):
        text = 'Label. Shelf. Label.'
        before = annotated(text, 'Label')
        first = before['observations'][0]['evidence'][0]
        second = dict(first, start=text.rindex('Label'), end=len(text) - 1)
        before['observations'][0]['evidence'].append(second)
        after = copy.deepcopy(before)
        after['observations'][0]['evidence'].pop(0)
        row, = self.compare(before, after)
        self.assertEqual(row['status'], 'REVIEW_REQUIRED')
        citations = note_citations(row)
        self.assertEqual(len(citations), 2)
        kept, = [c for c in citations if c['status'] == 'RETAINED']
        self.assertEqual(kept['before']['start'], second['start'])
        self.assertEqual(kept['after']['start'], second['start'])
        self.assertNotIn('MOVED', [c['status'] for c in citations])

    def test_moved_annotation_with_two_possible_occurrences_is_uncertain(self):
        before = annotated('Mark the shelf.', 'shelf')
        after = annotated('A shelf beside a shelf.', 'shelf')
        row, = self.compare(before, after)
        self.assertEqual(row['status'], 'REVIEW_REQUIRED')
        self.assertIn('OCCURRENCE_UNCERTAIN', [c['status'] for c in note_citations(row)])

    def test_dropped_reference_distinguishes_uncited_missing_and_removed_source(self):
        before = annotated('The cart is empty.', 'cart')
        for status, body in [('NO_LONGER_CITED', 'The cart is empty.'),
                             ('PASSAGE_MISSING', 'The shelf is empty.'),
                             ('SOURCE_REMOVED', None)]:
            with self.subTest(status=status):
                after = copy.deepcopy(before)
                after['observations'] = []
                if body is None:
                    after['sources'].pop()
                else:
                    after['sources'][-1]['text'] = body
                row, = self.compare(before, after)
                citation, = note_citations(row)
                self.assertEqual(row['status'], 'REVIEW_REQUIRED')
                self.assertEqual(citation['status'], status)

    def test_reassigned_quote_belongs_to_the_new_observation(self):
        before = annotated('Check the label.', 'label', owner='start')
        after = annotated('Check the label.', 'label', owner='finish')
        row, = self.compare(before, after)
        self.assertEqual(row['status'], 'REVIEW_REQUIRED')
        citations = note_citations(row)
        self.assertCountEqual([c['status'] for c in citations], ['NO_LONGER_CITED', 'NEW_CITATION'])
        self.assertEqual(len({c['owner'] for c in citations}), 2)

    def test_explicit_need_and_step_track_their_own_identical_citations(self):
        before = example_bundle()
        workflow = before['workflows'][0]
        need = workflow['needs'][0]
        step = workflow['steps'][-1]
        old_source = before['sources'][0]['id']
        self.assertEqual(need['evidence'], step['evidence'])
        after = copy.deepcopy(before)
        note = source('additional', 'Review the shelf label.', kind='synthetic')
        after['sources'].append(note)
        after['workflows'][0]['steps'][-1]['evidence'] = [cite(note)]
        row, = self.compare(before, after)
        self.assertEqual(row['status'], 'REVIEW_REQUIRED')
        statuses = {(c['role'], c['owner'], c['source']): c['status'] for c in row['citations']}
        self.assertEqual(statuses['need', need['id'], old_source], 'RETAINED')
        self.assertEqual(statuses['step', step['id'], old_source], 'NO_LONGER_CITED')
        self.assertEqual(statuses['step', step['id'], note['id']], 'NEW_CITATION')

    def test_source_key_and_matching_words_do_not_override_changed_uri(self):
        before = annotated('Check the label.', 'label', uri='fixture://register-a')
        after = annotated('Check the label.', 'label', uri='fixture://register-b')
        row, = self.compare(before, after)
        self.assertEqual(row['status'], 'REVIEW_REQUIRED')
        statuses = [c['status'] for c in note_citations(row)]
        self.assertIn('SOURCE_REPLACED', statuses)
        self.assertNotIn('RETAINED', statuses)

    def test_renamed_need_reports_both_removed_and_added_hypotheses(self):
        before = example_bundle()
        after = copy.deepcopy(before)
        after['workflows'][0]['needs'][0]['id'] = 'new-need'
        after['observations'][0]['need'] = 'new-need'
        rows = self.compare(before, after)
        self.assertCountEqual([row['status'] for row in rows], ['REMOVED', 'ADDED'])

    def test_colliding_explicit_and_inferred_hypotheses_block_comparison(self):
        bundle = example_bundle()
        bundle['observations'] = []
        workflow = bundle['workflows'][0]
        explicit_need = workflow['needs'].pop()
        original = self.store.run(bundle)
        inferred, = original['hypotheses']
        second_step = dict(workflow['steps'][-1], id='second-review')
        workflow['steps'].append(second_step)
        explicit_need.update(id=inferred['need']['id'], step=second_step['id'])
        workflow['needs'].append(explicit_need)
        colliding = self.store.run(bundle)
        first, second = colliding['hypotheses']
        self.assertEqual(first['id'], second['id'])
        for pair in [(original, colliding), (colliding, original)]:
            with self.subTest(before=pair[0]['id']), self.assertRaisesRegex(
                    Blocked, 'OPPORTUNITY_COMPARE_AMBIGUOUS_ID'):
                self.store.compare(pair[0]['id'], pair[1]['id'])

    def test_changed_receipt_on_either_side_blocks_comparison(self):
        first = self.store.run(example_bundle())['id']
        second = self.store.run(annotated('Check the label.', 'label'))['id']
        path = self.store.path(first) / 'record.json'
        record = json.loads(path.read_bytes())
        record['payload']['bundle']['title'] = 'Modified'
        path.write_text(json.dumps(record), encoding='utf-8')
        for pair in [(first, second), (second, first)]:
            with self.subTest(pair=pair), self.assertRaises(Blocked):
                self.store.compare(*pair)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'forge.py'), '--home', str(self.home),
                               'opportunity-compare', *args], capture_output=True, timeout=60)

    def test_cli_reads_existing_run_id_without_rewriting_either_run(self):
        store = OpportunityStore(initialize(self.home))
        # This fixture's ID was captured before the comparison command existed.
        # Retain that historical identity explicitly, then compare it under the
        # current algorithm. No source corpus is part of this synthetic fixture.
        original_id = 'opp_3586a50ea9e12411df39c87cecb5a56a62e09e7af5220151904907b350982b1f'
        historical_version = 'opportunity-1.1-inventor-audit'
        current_version = opportunities.VERSION
        with mock.patch.object(opportunities, 'VERSION', historical_version):
            original = store.run(example_bundle())
        self.assertEqual(original['id'], original_id)
        self.assertEqual(original['version'], historical_version)
        self.assertEqual(opportunities.VERSION, current_version)
        self.assertNotEqual(opportunities.evaluate(example_bundle())['id'], original_id)
        updated = store.run(annotated('Check the label.', 'label'))
        updated_id = updated['id']
        self.assertEqual(updated['version'], current_version)
        saved = snapshot(store.root)
        expected = store.compare(original_id, updated_id)
        result = self.cli(original_id, updated_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), expected)
        self.assertEqual(snapshot(store.root), saved)
        self.assertEqual(store.get(original_id)['version'], historical_version)

    def test_cli_with_missing_home_leaves_no_new_workspace(self):
        unknown_id = 'opp_' + '0' * 64
        self.assertNotEqual(self.cli(unknown_id, unknown_id).returncode, 0)
        self.assertFalse(self.home.exists())
