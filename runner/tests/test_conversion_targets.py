"""Source-bound conversion destination cues through runner entry points."""
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
QUERY = 'decode percent escapes into bytes'
HELPER = ('def blob_to_bytes(value):\n'
          '    """blob_to_bytes(\'sample\') -> b\'sample\'."""\n'
          '    return value\n')
DISTRACTOR = 'def decode_percent(value):\n    return value\n'


class ConversionTargets(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.sources = self.directory / 'sources'
        self.sources.mkdir()
        self.sequence = 0

    def source(self, text, sid='sample'):
        data = text.encode()
        (self.sources / (sid + '.py')).write_bytes(data)
        return {'id': sid, 'path': sid + '.py', 'kind': 'implementation',
                'origin': 'fixture://conversion-destinations', 'capture': 'synthetic_fixture',
                'family': 'synthetic', 'size': len(data), 'sha256': core.sha(data)}

    def inspect(self, text, sid='sample'):
        self.sequence += 1
        work = self.directory / ('inspection-' + str(self.sequence))
        work.mkdir()
        return bridge.inspect_source(self.source(text, sid), self.sources, work, ROOT)

    def query(self, groups, query=QUERY, **extra):
        question = {'id': 'q', 'query': query, 'intent': 'code',
                    'sources': [group['source']['id'] for group in groups], **extra}
        return bridge.investigate(question, groups, ROOT)

    def make_legacy(self, group):
        path = Path(group['forge_report_path'])
        report = json.loads(path.read_bytes())
        for source in report['sources']:
            for segment in source['segments']:
                for definition in segment['definitions']:
                    definition.pop('structure', None)
        path.write_bytes(core.canonical(report))
        receipt_path = path.with_name('receipt.json')
        receipt = json.loads(receipt_path.read_bytes())
        receipt['report_sha256'] = core.sha(path.read_bytes())
        receipt_path.write_bytes(core.canonical(receipt))

    def assert_no_target(self, result):
        self.assertTrue(result['results'])
        for channel in ('results', 'lexical_results', 'forge_results'):
            self.assertTrue(all('conversion_target_match' not in row for row in result[channel]))

    def test_real_mission_followup_and_replay_preserve_truthful_target_only_evidence(self):
        canary = self.directory / 'source-must-not-run'
        text = ('from pathlib import Path\nPath(' + repr(str(canary)) + ').touch()\n'
                + DISTRACTOR + HELPER)
        mission = {'schema': 1, 'objective': 'Review a sparse declared destination without executing source',
                   'sources': [self.source(text)],
                   'questions': [{'id': 'initial', 'query': QUERY, 'intent': 'code', 'sources': ['sample']}],
                   'budgets': {'max_sources': 1, 'max_source_bytes': 8_000_000,
                               'max_total_bytes': 8_000_000, 'max_questions': 3, 'packet_bytes': 16_384}}
        manifest = self.directory / 'mission.json'
        manifest.write_bytes(core.canonical(mission))
        work = self.directory / 'mission'
        runner.run(mission, self.sources, work, ROOT)
        initial = json.loads((work / 'report.json').read_bytes())['queries'][0]
        first = initial['results'][0]
        self.assertEqual(first['name'], 'blob_to_bytes')
        self.assertEqual(first['matched_terms'], ['bytes'])
        self.assertNotIn('directional_match', first)
        self.assertNotIn('declared_output', first)
        cue = first['conversion_target_match']
        self.assertEqual(cue['declaration'], {'basis': 'FUNCTION_NAME', 'input': ['blob'], 'output': ['bytes']})
        self.assertEqual(cue['input_match'], 'NOT_ESTABLISHED')
        self.assertEqual(cue['evidence'], 'CONVERSION_DESTINATION_SPELLING_ONLY_NOT_RETURN_TYPE_OR_BEHAVIOR_PROOF')
        self.assertEqual(initial['query_evidence']['requested_direction']['top_result_destination_only_declaration'], cue)
        self.assertIsNone(initial['query_evidence']['requested_direction']['top_result_declaration'])
        self.assertIsNone(initial['query_evidence']['requested_output']['top_result_declaration'])
        self.assertEqual(initial['status'], 'PARTIAL_QUERY_ONLY')
        request = {'action': 'ask', 'request_id': 'again',
                   'question': {'id': 'again', 'query': QUERY, 'intent': 'code', 'sources': ['sample']}}
        session = Supervisor(manifest, self.sources, work, ROOT)
        with patch.object(bridge, 'inspect_source', side_effect=AssertionError('unexpected source intake')):
            answer = session.execute(request)
        self.assertEqual(answer['state'], 'COMPLETED')
        self.assertEqual(answer['result']['results'][0]['name'], 'blob_to_bytes')
        self.assertEqual(answer['result']['query_evidence']['requested_direction']['top_result_destination_only_declaration'], cue)
        saved = json.loads((work / 'supervision' / 'again.json').read_bytes())['result']
        self.assertEqual(saved['results'][0]['conversion_target_match'], cue)
        self.assertEqual(saved['results'][0]['matched_terms'], ['bytes'])
        with patch.object(bridge, 'investigate', side_effect=AssertionError('unexpected replay search')):
            replay = session.execute(request)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['result'], answer['result'])
        self.assertFalse(canary.exists())
        self.assertEqual((self.sources / 'sample.py').read_text(), text)

    def test_complete_coverage_and_literal_identifiers_keep_priority(self):
        group = self.inspect(HELPER + 'def complete(value):\n    """Decode percent escapes into bytes."""\n    return value\n')
        result = self.query([group])
        self.assertEqual(result['results'][0]['name'], 'complete')
        helper = next(row for row in result['results'] if row['name'] == 'blob_to_bytes')
        self.assertIn('conversion_target_match', helper)
        self.assertEqual(helper['matched_terms'], ['bytes'])
        literal = self.query([group], 'blob_to_bytes')['results'][0]
        self.assertEqual(literal['name'], 'blob_to_bytes')
        self.assertTrue(literal['literal_symbol_match'])
        self.assertNotIn('conversion_target_match', literal)

    def test_target_cue_does_not_stack_with_other_partial_allowances(self):
        def row(rid, matched):
            return {'id': rid, 'kind': 'implementation', 'name': rid,
                    'matched_terms': matched, 'term_count': 5}
        cue = {'input_match': 'NOT_ESTABLISHED'}
        one = row('one', ['bytes'])
        two = row('two', ['decode', 'percent'])
        got = core.rank_fusion([one, two], [], conversion_targets={'one': cue},
                               predicate_matches={'one': {'evidence': 'DECLARATION'}},
                               directional_matches={'one': {'basis': 'SYNTHETIC_POLICY_CONTROL'}})
        # One point offsets one missing word; it cannot outweigh three terms.
        three = row('three', ['decode', 'percent', 'escapes'])
        combined = core.rank_fusion([one, two, three], [], conversion_targets={'one': cue},
                                    predicate_matches={'one': {'evidence': 'DECLARATION'}},
                                    directional_matches={'one': {'basis': 'SYNTHETIC_POLICY_CONTROL'}})
        self.assertEqual(got[0]['id'], 'one')
        self.assertEqual(combined[0]['id'], 'three')
        self.assertEqual(combined[1]['matched_terms'], ['bytes'])

    def test_qualified_destination_cannot_promote_a_namespace_only_match(self):
        group = self.inspect(DISTRACTOR + HELPER +
                             'def peel_to_models(value):\n    return value\n')
        result = self.query([group], 'decode percent escapes into models.Bytes')
        self.assert_no_target(result)
        self.assertEqual(result['results'][0]['name'], 'decode_percent')

    def test_forged_lexical_target_field_is_not_authority(self):
        group = self.inspect(DISTRACTOR)
        group['records'][0]['conversion_target_match'] = {'requested_output': 'bytes'}
        self.assert_no_target(self.query([group]))
        projected = {'id': 'fake', 'kind': 'implementation', 'name': 'fake',
                     'matched_terms': ['bytes'], 'term_count': 5,
                     'conversion_target_match': {'requested_output': 'bytes'}}
        self.assertNotIn('conversion_target_match', core.rank_fusion([projected], [projected])[0])

    def test_changed_retained_text_or_location_cannot_earn_target_cue(self):
        group = self.inspect(HELPER)
        self.assertIn('conversion_target_match', self.query([group])['results'][0])
        for field, value in [('name', 'different'), ('path', 'different.py'),
                             ('start_line', 2), ('end_line', 99), ('text_complete', False),
                             ('text_sha256', '0' * 64), ('text', HELPER + '# changed\n')]:
            with self.subTest(field=field):
                changed = copy.deepcopy(group)
                row = next(row for row in changed['records'] if row['name'] == 'blob_to_bytes')
                row[field] = value
                if field == 'text':
                    row['text_sha256'] = core.sha(value.encode())
                self.assert_no_target(self.query([changed]))

    def test_changed_source_hash_is_refused_before_target_evidence(self):
        group = self.inspect(HELPER)
        group['records'][0]['source_sha256'] = '0' * 64
        from forge_core import retrieval
        with patch.object(retrieval, 'conversion_target_match', side_effect=AssertionError('unbound cue')):
            with self.assertRaisesRegex(core.Blocked, '^BRIDGE_EVIDENCE_BINDING$'):
                self.query([group])

    def test_separate_legacy_source_does_not_erase_current_bound_target(self):
        modern = self.inspect(HELPER, 'modern')
        legacy = self.inspect(HELPER.replace('blob_to_bytes', 'old_to_bytes'), 'legacy')
        self.make_legacy(legacy)
        for groups in ([modern, legacy], [legacy, modern]):
            with self.subTest(order=[group['source']['id'] for group in groups]):
                result = self.query(groups)
                current = next(row for row in result['results'] if row['source_id'] == 'modern')
                older = next(row for row in result['results'] if row['source_id'] == 'legacy')
                self.assertIn('conversion_target_match', current)
                self.assertNotIn('conversion_target_match', older)
                self.assertNotIn('direct_matched_terms', current)
        self.assert_no_target(self.query([legacy]))

    def test_external_workbench_without_helper_keeps_compatible_results(self):
        group = self.inspect(HELPER)
        corpus, _, _ = bridge.load_base(ROOT)
        backend = SimpleNamespace(read_report=corpus.read_report, all_rows=corpus.all_rows,
                                  search=lambda *args, **kwargs: {'results': []})
        from forge_core import retrieval
        with patch.object(bridge, 'validate_questions', return_value=backend), patch.object(retrieval, 'conversion_target_match', None):
            result = self.query([group])
        self.assert_no_target(result)
        self.assertNotIn('top_result_destination_only_declaration', result['query_evidence']['requested_direction'])

    def test_api_eligibility_is_applied_before_target_ranking_and_top_k(self):
        source = 'import hashlib\n' + '\n'.join(
            'def decode_percent_%02d(value):\n    """Decode percent escapes into bytes."""\n    return value\n' % index
            for index in range(20))
        source += 'def blob_to_bytes(value):\n    return hashlib.sha256(value).digest()\n'
        result = self.query([self.inspect(source)], required_api='hashlib.sha256')
        for channel in ('results', 'lexical_results', 'forge_results'):
            self.assertEqual([row['name'] for row in result[channel]], ['blob_to_bytes'])
            self.assertIn('hashlib.sha256', result[channel][0]['observed_apis'])
        self.assertIn('conversion_target_match', result['results'][0])
        mentions = self.inspect('def blob_to_bytes(value):\n    """hashlib.sha256 is mentioned only."""\n    return value\n')
        absent = self.query([mentions], required_api='hashlib.sha256')
        for channel in ('results', 'lexical_results', 'forge_results'):
            self.assertEqual(absent[channel], [])


if __name__ == '__main__':
    unittest.main()
