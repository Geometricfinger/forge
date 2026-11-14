"""Research evidence keeps the identity of interpreted Markdown source."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_runner import bridge, core, runner
from research_runner.supervise import Supervisor

ROOT = Path(__file__).resolve().parents[2]
CODE = ('import hashlib\nmodule_digest = hashlib.sha256(b"module")\n'
        'raise RuntimeError("FENCE_VIEW_SOURCE_MUST_NOT_RUN")\n'
        '@(\n marker\n)\ndef digest(value):\n return hashlib.sha256(value)\n')


class AnalysisViewResearch(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.sources = self.directory / 'sources'
        self.sources.mkdir()
        self.raw = ''.join('  ' + line for line in CODE.splitlines(keepends=True)).replace('\n', '\r\n').encode()
        self.document = b'# Synthetic\r\n  ```python\r\n' + self.raw + b'```\r\n'
        (self.sources / 'sample.md').write_bytes(self.document)
        self.source = {'id': 'sample', 'path': 'sample.md', 'kind': 'implementation',
                       'origin': 'fixture://analysis-view', 'capture': 'synthetic_fixture',
                       'family': 'synthetic', 'size': len(self.document), 'sha256': core.sha(self.document)}
        self.sequence = 0

    def inspect(self):
        self.sequence += 1
        work = self.directory / ('inspection-' + str(self.sequence))
        work.mkdir()
        return bridge.inspect_source(self.source, self.sources, work, ROOT)

    def question(self, rid='initial'):
        return {'id': rid, 'query': 'hashlib', 'intent': 'code', 'sources': ['sample'],
                'required_api': 'hashlib.sha256'}

    def test_view_survives_mission_packet_continuation_inspection_and_replay(self):
        mission = {'schema': 1, 'objective': 'Review an indented synthetic Python fence',
                   'sources': [self.source], 'questions': [self.question()],
                   'budgets': {'max_sources': 1, 'max_source_bytes': 8_000_000,
                               'max_total_bytes': 8_000_000, 'max_questions': 6, 'packet_bytes': 16_384}}
        manifest = self.directory / 'mission.json'
        manifest.write_bytes(core.canonical(mission))
        work = self.directory / 'mission-work'
        receipt = runner.run(mission, self.sources, work, ROOT)
        self.assertEqual(receipt['status'], 'READY_FOR_SUPERVISOR_REVIEW')
        self.assertFalse(receipt['source_code_executed'])
        report = json.loads((work / 'report.json').read_bytes())
        initial = report['queries'][0]
        rows = initial['results']
        self.assertEqual({r['record_kind'] for r in rows}, {'function', 'source_scope'})
        view = rows[0]['analysis_view']
        self.assertEqual(view['source_sha256'], core.sha(self.raw))
        self.assertEqual(view['analysis_sha256'], core.sha(CODE.replace('\n', '\r\n').encode()))
        self.assertNotEqual(view['source_sha256'], view['analysis_sha256'])
        for field in ('results', 'lexical_results', 'forge_results'):
            self.assertEqual(len(initial[field]), 2)
            for row in initial[field]:
                self.assertEqual(row['analysis_view'], view)
                self.assertEqual(row['source_sha256'], core.sha(self.raw))
                self.assertEqual(row['observed_apis'], ['hashlib.sha256'])
        packet = json.loads((work / 'supervisor_packet.json').read_bytes())
        self.assertEqual([r['analysis_view'] for r in packet['queries'][0]['candidates']], [view, view])
        session = Supervisor(manifest, self.sources, work, ROOT)
        request = {'action': 'ask', 'request_id': 'followup', 'question': self.question('followup')}
        before = {p.name: core.sha(p.read_bytes()) for p in (work / 'checkpoints').glob('*.json')}
        with patch.object(bridge, 'inspect_source', side_effect=AssertionError('Unexpected reanalysis')):
            followup = session.execute(request)
            self.assertEqual([r['analysis_view'] for r in followup['result']['results']], [view, view])
            for row in rows:
                inspected = session.execute({'action': 'inspect', 'request_id': 'inspect-' + row['record_kind'],
                                             'source_id': 'sample', 'evidence_id': row['id'], 'max_chars': 4096})
                result = inspected['result']
                expected = self.raw if row['record_kind'] == 'source_scope' else b''.join(self.raw.splitlines(keepends=True)[3:])
                self.assertEqual(result['analysis_view'], view)
                self.assertEqual(result['text'].encode(), expected)
                self.assertEqual(result['text_sha256'], core.sha(expected))
                self.assertEqual(result['source_window']['sha256'], core.sha(expected))
                self.assertEqual(result['source_window']['module_sha256'], core.sha(self.raw))
                self.assertEqual(result['source_sha256'], core.sha(self.raw))
                self.assertFalse(result['truncated'])
        with patch.object(bridge, 'investigate', side_effect=AssertionError('Unexpected replay query')):
            replay = session.execute(request)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['result'], followup['result'])
        self.assertEqual(before, {p.name: core.sha(p.read_bytes()) for p in (work / 'checkpoints').glob('*.json')})
        self.assertEqual((self.sources / 'sample.md').read_bytes(), self.document)

    def test_modified_or_missing_record_view_cannot_inherit_api_evidence(self):
        result = self.inspect()
        for kind in ('function', 'source_scope'):
            for mutation in ('policy', 'remove', 'type'):
                changed = copy.deepcopy(result)
                record = next(r for r in changed['records'] if r['record_kind'] == kind)
                if mutation == 'remove':
                    record.pop('analysis_view')
                elif mutation == 'type':
                    record['analysis_view']['schema'] = True
                else:
                    record['analysis_view']['policy'] = 'different-policy'
                with self.subTest(kind=kind, mutation=mutation):
                    with self.assertRaisesRegex(core.Blocked, '^BRIDGE_EVIDENCE_BINDING$'):
                        bridge.investigate(self.question(), [changed], ROOT)

    def test_second_parse_view_must_match_bound_corpus_metadata(self):
        corpus, _, _ = bridge.load_base(ROOT)
        original = corpus._parser
        for mutation in ('policy', 'remove'):
            count = 0
            def changed_parser(*args, **kwargs):
                nonlocal count
                count += 1
                result = original(*args, **kwargs)
                if count == 2:
                    segment = result['segments'][0]
                    if mutation == 'remove':
                        segment.pop('analysis_view')
                    else:
                        segment['analysis_view']['policy'] = 'different-policy'
                return result
            with self.subTest(mutation=mutation), patch.object(corpus, '_parser', side_effect=changed_parser):
                with self.assertRaisesRegex(core.Blocked, '^BRIDGE_ANALYSIS_VIEW_BINDING$'):
                    self.inspect()
            self.assertEqual(count, 2)

    def test_compact_budget_omits_whole_evidence_instead_of_dropping_view(self):
        result = self.inspect()
        query = bridge.investigate(self.question(), [result], ROOT)
        report = {'objective': 'Bound the synthetic review packet',
                  'queries': [dict(query, id='query-%02d' % i) for i in range(12)]}
        packet = core.compact_packet(report, 4096)
        self.assertLessEqual(len(core.canonical(packet)), 4096)
        self.assertGreater(packet['omitted_evidence'], 0)
        kept = [r for q in packet['queries'] for r in q['candidates']]
        self.assertTrue(kept)
        self.assertTrue(all(r['analysis_view']['source_sha256'] == r['source_sha256'] for r in kept))


if __name__ == '__main__':
    unittest.main()
