"""Bounded regular spellings and source-bound ranking through both channels."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from research_runner import bridge, core, runner
from research_runner.supervise import Supervisor

ROOT = Path(__file__).resolve().parents[2]


class InflectionQueries(unittest.TestCase):
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
                'origin': 'fixture://regular-spellings', 'capture': 'synthetic_fixture',
                'family': 'synthetic', 'size': len(data), 'sha256': core.sha(data)}

    def inspect(self, text):
        self.sequence += 1
        work = self.directory / ('inspection-' + str(self.sequence))
        work.mkdir()
        return bridge.inspect_source(self.source(text), self.sources, work, ROOT)

    def query(self, group, query, **extra):
        question = {'id': 'q', 'query': query, 'intent': 'code', 'sources': ['sample'], **extra}
        return bridge.investigate(question, [group], ROOT)

    def test_real_mission_and_followup_keep_shared_spelling_evidence(self):
        canary = self.directory / 'source-must-not-run'
        source = ('from pathlib import Path\nPath(' + repr(str(canary)) + ').touch()\n'
                  'def candidate(value):\n    """Alternating iterables yields items."""\n    return value\n')
        mission = {'schema': 1, 'objective': 'Review regular lexical forms without execution',
                   'sources': [self.source(source)],
                   'questions': [{'id': 'initial', 'query': 'alternate iterable item',
                                  'intent': 'code', 'sources': ['sample']}],
                   'budgets': {'max_sources': 1, 'max_source_bytes': 8_000_000,
                               'max_total_bytes': 8_000_000, 'max_questions': 3, 'packet_bytes': 16_384}}
        manifest = self.directory / 'mission.json'
        manifest.write_bytes(core.canonical(mission))
        work = self.directory / 'mission'
        runner.run(mission, self.sources, work, ROOT)
        first = json.loads((work / 'report.json').read_bytes())['queries'][0]
        self.assertIn('alternating', first['query_expansion']['alternate'])
        for channel in ('results', 'lexical_results', 'forge_results'):
            self.assertEqual(first[channel][0]['name'], 'candidate')
            self.assertEqual(first[channel][0]['matched_terms'], ['alternate', 'item', 'iterable'])
            self.assertEqual(first[channel][0]['direct_matched_terms'], ['alternate', 'item', 'iterable'])
        answer = Supervisor(manifest, self.sources, work, ROOT).execute({
            'action': 'ask', 'request_id': 'again',
            'question': {'id': 'again', 'query': 'alternate items', 'intent': 'code', 'sources': ['sample']}})
        self.assertEqual(answer['state'], 'COMPLETED')
        self.assertEqual(answer['result']['results'][0]['name'], 'candidate')
        self.assertFalse(canary.exists())
        self.assertEqual((self.sources / 'sample.py').read_text(), source)

    def test_body_only_words_remain_lexical_evidence(self):
        group = self.inspect('def candidate():\n    return "Scheduled batches"\n')
        result = self.query(group, 'schedule batch')
        self.assertEqual(result['forge_results'], [])
        self.assertEqual(result['results'][0]['channels'], ['lexical'])
        self.assertEqual(result['results'][0]['matched_terms'], ['batch', 'schedule'])
        self.assertEqual(result['results'][0]['observed_apis'], [])
        self.assertEqual(result['runtime_validation'], 'NOT_RUN')

    def test_class_terms_stay_attributed_and_direct_words_break_equal_coverage_ties(self):
        source = ('class Holder:\n    """Schedules batches."""\n    def __call__(self): return 1\n'
                  'def candidate():\n    """Scheduled batches."""\n    return 2\n')
        result = self.query(self.inspect(source), 'schedule batch')
        self.assertEqual(result['results'][0]['name'], 'candidate')
        for channel in ('results', 'lexical_results', 'forge_results'):
            inherited = next(row for row in result[channel] if row['name'] == 'Holder.__call__')
            self.assertEqual(inherited['matched_terms'], ['batch', 'schedule'])
            self.assertEqual(inherited['direct_matched_terms'], [])
            self.assertEqual(inherited['class_documentation_match']['class_name'], 'Holder')

    def test_tampered_class_metadata_is_refused_before_lexical_expansion(self):
        group = self.inspect('class Holder:\n    """Schedules batches."""\n    def __call__(self): return 1\n')
        group['records'][0]['class_documentation_context']['terms']['invented'] = 1
        with patch.object(bridge, 'Lexical', side_effect=AssertionError('unbound vocabulary used')):
            with self.assertRaisesRegex(core.Blocked, '^BRIDGE_CLASS_DOCUMENTATION_BINDING$'):
                self.query(group, 'schedule batch')

    def test_changed_source_identity_is_refused_before_lexical_expansion(self):
        group = self.inspect('def candidate():\n    """Schedules batches."""\n    return 1\n')
        group['records'][0]['source_sha256'] = '0' * 64
        with patch.object(bridge, 'Lexical', side_effect=AssertionError('unbound vocabulary used')):
            with self.assertRaisesRegex(core.Blocked, '^BRIDGE_EVIDENCE_BINDING$'):
                self.query(group, 'schedule batch')

    def test_word_forms_cannot_satisfy_an_observed_api_filter(self):
        group = self.inspect('def candidate():\n    """Writes batches."""\n    return 1\n')
        result = self.query(group, 'write batch', required_api='os.write')
        for channel in ('results', 'lexical_results', 'forge_results'):
            self.assertEqual(result[channel], [])

    def test_external_backend_without_helper_keeps_prior_matching(self):
        group = self.inspect('def candidate():\n    """Alternating."""\n    return 1\n')
        corpus, _, _ = bridge.load_base(ROOT)
        backend = SimpleNamespace(read_report=corpus.read_report, all_rows=corpus.all_rows,
                                  search=lambda *args, **kwargs: {'results': []})
        from forge_core import retrieval
        with patch.object(bridge, 'validate_questions', return_value=backend), patch.object(retrieval, 'expand_inflections', None):
            result = self.query(group, 'alternate')
            self.assertEqual(result['results'], [])
            self.assertNotIn('query_expansion', result)
            literal = self.query(group, 'candidate')['results'][0]
            self.assertNotIn('direct_matched_terms', literal)
            self.assertNotIn('literal_symbol_match', literal)

    def test_legacy_snapshot_does_not_gain_new_spelling_or_direct_priority(self):
        group = self.inspect('def candidate():\n    """Scheduled batches."""\n    return 1\n')
        path = Path(group['forge_report_path'])
        report = json.loads(path.read_bytes())
        report['sources'][0]['segments'][0]['definitions'][0].pop('structure')
        path.write_bytes(core.canonical(report))
        receipt_path = path.with_name('receipt.json')
        receipt = json.loads(receipt_path.read_bytes())
        receipt['report_sha256'] = core.sha(path.read_bytes())
        receipt_path.write_bytes(core.canonical(receipt))
        result = self.query(group, 'schedule batch')
        self.assertEqual(result['results'], [])
        self.assertNotIn('query_expansion', result)

    def test_mixed_snapshots_keep_each_sources_spelling_evidence(self):
        groups = []
        for sid in ('modern', 'legacy'):
            raw = ('def ' + sid + '_candidate():\n    """Alternating."""\n    return 1\n').encode()
            (self.sources / (sid + '.py')).write_bytes(raw)
            source = {'id': sid, 'path': sid + '.py', 'kind': 'implementation',
                      'origin': 'fixture://mixed-spellings', 'capture': 'synthetic_fixture',
                      'family': 'synthetic', 'size': len(raw), 'sha256': core.sha(raw)}
            work = self.directory / sid
            work.mkdir()
            groups.append(bridge.inspect_source(source, self.sources, work, ROOT))
        modern, legacy = groups
        path = Path(legacy['forge_report_path'])
        report = json.loads(path.read_bytes())
        report['sources'][0]['segments'][0]['definitions'][0].pop('structure')
        path.write_bytes(core.canonical(report))
        receipt_path = path.with_name('receipt.json')
        receipt = json.loads(receipt_path.read_bytes())
        receipt['report_sha256'] = core.sha(path.read_bytes())
        receipt_path.write_bytes(core.canonical(receipt))
        question = {'id': 'q', 'query': 'alternate', 'intent': 'code', 'sources': ['modern']}
        alone = bridge.investigate(question, [modern], ROOT)
        for ordered in (groups, list(reversed(groups))):
            with self.subTest(order=[group['source']['id'] for group in ordered]):
                mixed = bridge.investigate({**question, 'sources': ['modern', 'legacy']}, ordered, ROOT)
                self.assertEqual(mixed['query_expansion_scope'], 'STRUCTURED_CORPUS_RECORDS_ONLY')
                self.assertIn('alternating', mixed['query_expansion']['alternate'])
                for channel in ('results', 'lexical_results', 'forge_results'):
                    self.assertEqual([row['id'] for row in mixed[channel]], [row['id'] for row in alone[channel]])
                    self.assertTrue(mixed[channel])
                    for row in mixed[channel]:
                        self.assertEqual(row['source_id'], 'modern')
                        self.assertEqual(row['matched_terms'], ['alternate'])
                        self.assertNotIn('direct_matched_terms', row)
                        self.assertNotIn('literal_symbol_match', row)
        literal = bridge.investigate({**question, 'sources': ['modern', 'legacy'],
                                     'query': 'legacy_candidate'}, groups, ROOT)
        self.assertEqual(literal['results'][0]['source_id'], 'legacy')
        self.assertTrue(all(row['matched_terms'] for channel in ('results', 'lexical_results', 'forge_results')
                            for row in literal[channel]))

    def test_declared_type_names_remain_exact(self):
        group = self.inspect('def candidate() -> Plan:\n    """Plans routes."""\n    return None\n')
        result = self.query(group, 'parse text into a Plans')
        output = result['results'][0]['declared_output']
        self.assertFalse(output['name_match'])
        self.assertEqual(output['runtime_type_relationship'], 'NOT_ESTABLISHED')

    def test_multiple_spellings_never_create_extra_query_votes(self):
        group = self.inspect('def candidate():\n    """Plan plans planned planning."""\n    return 1\n')
        result = self.query(group, 'plan')
        for channel in ('results', 'lexical_results', 'forge_results'):
            self.assertEqual(result[channel][0]['matched_terms'], ['plan'])
            self.assertEqual(result[channel][0]['term_count'], 1)

    def test_literal_identifier_precedes_conflicting_declarations_but_prose_keeps_direction(self):
        group = self.inspect('def copper_to_silver(x):\n    """Convert silver to copper."""\n    return x\n'
                             'def declared_forward(x):\n    """Convert copper to silver."""\n    return x\n')
        literal = self.query(group, 'copper_to_silver')['results'][0]
        self.assertEqual(literal['name'], 'copper_to_silver')
        self.assertTrue(literal['literal_symbol_match'])
        natural = self.query(group, 'convert copper to silver')['results'][0]
        self.assertEqual(natural['name'], 'declared_forward')
        self.assertNotIn('literal_symbol_match', natural)

    def test_unbound_text_cannot_acquire_new_direct_priority(self):
        group = self.inspect('def candidate():\n    """Schedules batches."""\n    return 1\n')
        group['records'][0]['text'] += '\n# altered retained text\n'
        group['records'][0]['text_sha256'] = core.sha(group['records'][0]['text'].encode())
        result = self.query(group, 'schedule batch')
        for channel in ('results', 'lexical_results', 'forge_results'):
            self.assertNotIn('direct_matched_terms', result[channel][0])
            self.assertNotIn('literal_symbol_match', result[channel][0])


if __name__ == '__main__':
    unittest.main()
