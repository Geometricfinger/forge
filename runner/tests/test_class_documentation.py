"""Source-bound class descriptions must survive both research search channels."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_runner import bridge, core, runner
from research_runner.supervise import Supervisor

ROOT = Path(__file__).resolve().parents[2]
DESCRIPTION = '''class Deadline:
    """Stop before a cumulative deadline with contention safeguards."""
    def __init__(self, maximum):
        self.maximum = maximum
    def __call__(self, state):
        return state.elapsed_seconds + state.upcoming_sleep >= self.maximum
'''
DECOYS = '\n'.join(f'def annotation_{i}(value):\n    """contention"""\n    return value\n' for i in range(10))


class ClassDocumentationResearch(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.sources = self.directory / 'sources'
        self.sources.mkdir()
        self.sequence = 0

    def source(self, text):
        data = text.encode()
        (self.sources / 'sample.py').write_bytes(data)
        return {'id': 'sample', 'path': 'sample.py', 'kind': 'implementation',
                'origin': 'fixture://class-description', 'capture': 'synthetic_fixture',
                'family': 'synthetic', 'size': len(data), 'sha256': core.sha(data)}

    def inspect(self, text=DESCRIPTION + DECOYS):
        self.sequence += 1
        work = self.directory / f'inspection-{self.sequence}'
        work.mkdir()
        return bridge.inspect_source(self.source(text), self.sources, work, ROOT)

    def query(self, inspected, text='contention safeguards'):
        return bridge.investigate({'id': 'q', 'query': text, 'intent': 'code',
                                   'sources': ['sample']}, [inspected], ROOT)

    def test_real_mission_followup_and_packet_keep_declared_context(self):
        canary = self.directory / 'never-created'
        text = ('from pathlib import Path\nPath(' + repr(str(canary)) + ').touch()\n'
                + DESCRIPTION + DECOYS)
        mission = {'schema': 1, 'objective': 'Find a declared class mechanism among partial matches',
                   'sources': [self.source(text)],
                   'questions': [{'id': 'initial', 'query': 'contention safeguards',
                                  'intent': 'code', 'sources': ['sample']}],
                   'budgets': {'max_sources': 1, 'max_source_bytes': 8_000_000,
                               'max_total_bytes': 8_000_000, 'max_questions': 3,
                               'packet_bytes': 16_384}}
        manifest = self.directory / 'mission.json'
        manifest.write_bytes(core.canonical(mission))
        work = self.directory / 'mission'
        receipt = runner.run(mission, self.sources, work, ROOT)
        self.assertEqual(receipt['status'], 'READY_FOR_SUPERVISOR_REVIEW')
        report = json.loads((work / 'report.json').read_bytes())
        result = report['queries'][0]
        self.assertEqual(result['status'], 'STATIC_LEADS_REVIEW_REQUIRED')
        self.assertEqual({r['name'] for r in result['results'][:2]}, {'Deadline.__init__', 'Deadline.__call__'})
        for channel in ('results', 'lexical_results', 'forge_results'):
            first = result[channel][0]
            self.assertEqual(first['matched_terms'], ['contention', 'safeguards'])
            self.assertEqual(first['class_documentation_match']['class_name'], 'Deadline')
            self.assertEqual(first['class_documentation_match']['evidence'], 'ENCLOSING_CLASS_DOCUMENTATION_NOT_BEHAVIOR_PROOF')
            self.assertEqual(first['class_documentation_match']['line_basis'], 'SEGMENT_LOCAL')
            self.assertNotIn('safeguards', first['snippet'])
            self.assertEqual(first['runtime_validation'], 'NOT_RUN')
        packet = json.loads((work / 'supervisor_packet.json').read_bytes())
        self.assertEqual(packet['queries'][0]['candidates'][0]['class_documentation_match'],
                         result['results'][0]['class_documentation_match'])
        supervisor = Supervisor(manifest, self.sources, work, ROOT)
        with patch.object(bridge, 'inspect_source', side_effect=AssertionError('unexpected reanalysis')):
            answer = supervisor.execute({'action': 'ask', 'request_id': 'again',
                                         'question': {'id': 'again', 'query': 'CONTENTION Safeguards',
                                                      'intent': 'code', 'sources': ['sample']}})
        self.assertEqual(answer['state'], 'COMPLETED')
        self.assertEqual(answer['result']['results'][0]['class_documentation_match'],
                         result['results'][0]['class_documentation_match'])
        self.assertFalse(canary.exists())
        self.assertEqual((self.sources / 'sample.py').read_text(), text)

    def test_only_direct_class_description_is_attributed(self):
        source = '''class Parent:
    """ambercontext"""
    def call(self): return 1
    def wrapper(self):
        def nested(): return 2
        return nested
class Child(Parent):
    """jadecontext"""
    def call(self): return 3
class Outer:
    """outercontext"""
    class Inner:
        """innercontext"""
        def call(self): return 4
'''
        inspected = self.inspect(source)
        cases = [('ambercontext', {'Parent.call', 'Parent.wrapper'}),
                 ('jadecontext', {'Child.call'}), ('innercontext', {'Outer.Inner.call'})]
        for query, names in cases:
            with self.subTest(query=query):
                result = self.query(inspected, query)
                self.assertEqual({r['name'] for r in result['results']}, names)
        self.assertEqual(self.query(inspected, 'absentcontext')['results'], [])
        for row in inspected['records']:
            self.assertEqual(row['class_documentation'], '')
            if row['name'] == 'Parent.wrapper.nested':
                self.assertIsNone(row['class_documentation_context'])

    def test_changed_saved_terms_or_attribution_block_query(self):
        inspected = self.inspect(DESCRIPTION)
        for field, value in [('terms', {'inventedcontext': 1}), ('class_name', 'Other'), ('start_line', 99)]:
            changed = copy.deepcopy(inspected)
            changed['records'][0]['class_documentation_context'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(core.Blocked, 'BRIDGE_CLASS_DOCUMENTATION_BINDING'):
                self.query(changed, 'inventedcontext')

    def test_unbound_class_record_cannot_supply_lexical_evidence(self):
        inspected = self.inspect(DESCRIPTION)
        for mutation in ('missing_report', 'changed_id', 'duplicate'):
            changed = copy.deepcopy(inspected)
            if mutation == 'missing_report':
                changed.pop('forge_report_path')
            elif mutation == 'changed_id':
                changed['records'][0]['id'] = 'unknown'
            else:
                extra = copy.deepcopy(changed['records'][0])
                extra['class_documentation_context']['terms']['inventedcontext'] = 1
                changed['records'].insert(0, extra)
            with self.subTest(mutation=mutation), self.assertRaisesRegex(core.Blocked, 'BRIDGE_CLASS_DOCUMENTATION_BINDING'):
                self.query(changed, 'inventedcontext')

    def test_second_parse_disagreement_blocks_intake(self):
        corpus, _, _ = bridge.load_base(ROOT)
        real = corpus._parser
        calls = []
        def altered(*args, **kwargs):
            parsed = real(*args, **kwargs)
            calls.append(1)
            if len(calls) == 2:
                parsed = copy.deepcopy(parsed)
                owner = parsed['segments'][0]['definitions'][0]['structure']['class_context']
                owner['documentation_terms']['inventedcontext'] = 1
            return parsed
        with patch.object(corpus, '_parser', side_effect=altered):
            with self.assertRaisesRegex(core.Blocked, 'BRIDGE_CLASS_DOCUMENTATION_BINDING'):
                self.inspect(DESCRIPTION)
        self.assertEqual(len(calls), 2)

    def test_missing_legacy_context_adds_no_class_term_claims(self):
        inspected = self.inspect(DESCRIPTION)
        for row in inspected['records']:
            row.pop('class_documentation_context')
            row['class_documentation'] = 'contention safeguards'
        result = self.query(inspected)
        self.assertEqual(result['lexical_results'], [])
        self.assertTrue(all(not row['matched_terms'] for row in result['results']))
        self.assertTrue(all('class_documentation_match' not in row for row in result['results']))
        self.assertEqual(result['query_evidence']['term_coverage'], 'NO_TERM_OVERLAP')

    def test_class_words_count_toward_retained_search_budget(self):
        inspected = self.inspect(DESCRIPTION)
        body_characters = sum(len(row['text']) for row in inspected['records'])
        with patch.object(bridge, 'MAX_LEXICAL_CHARS', body_characters):
            with self.assertRaisesRegex(core.Blocked, 'LEXICAL_EXPANSION_LIMIT'):
                self.inspect(DESCRIPTION)


if __name__ == '__main__':
    unittest.main()
