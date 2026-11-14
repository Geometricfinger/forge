"""Query admission and required-API recall through real research entry points."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_runner import bridge, core, runner
from research_runner.supervise import Supervisor

ROOT = Path(__file__).resolve().parents[2]


def term_query(count):
    return ' '.join('q' + chr(97 + i // 26) + chr(97 + i % 26) for i in range(count))


class QueryContracts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.sources = self.directory / 'sources'
        self.sources.mkdir()
        self.canary = self.directory / 'source-was-executed'

    def mission(self, data, *, kind='implementation', query='digest', intent='code', required_api=None):
        path = 'fixture.py' if kind == 'implementation' else 'reference.txt'
        (self.sources / path).write_bytes(data)
        source = {'id': 'sample', 'path': path, 'kind': kind, 'origin': 'fixture://query-contract',
                  'capture': 'synthetic_fixture', 'family': 'synthetic',
                  'size': len(data), 'sha256': core.sha(data)}
        question = {'id': 'initial', 'query': query, 'intent': intent, 'sources': ['sample']}
        if required_api is not None:
            question['required_api'] = required_api
        return {'schema': 1, 'objective': 'Test source-bound query admission', 'sources': [source],
                'questions': [question], 'budgets': {'max_sources': 1, 'max_source_bytes': 8_000_000,
                    'max_total_bytes': 8_000_000, 'max_questions': 8, 'packet_bytes': 16_384}}

    def simple_mission(self):
        return self.mission(b'def digest(value):\n    return value\n')

    def start(self, mission):
        manifest = self.directory / 'mission.json'
        manifest.write_bytes(core.canonical(mission))
        work = self.directory / 'work'
        receipt = runner.run(mission, self.sources, work, ROOT)
        self.assertEqual(receipt['status'], 'READY_FOR_SUPERVISOR_REVIEW')
        return work, manifest, Supervisor(manifest, self.sources, work, ROOT)

    def request(self, query, *, rid='followup', intent='code', required_api=None):
        question = {'id': rid, 'query': query, 'intent': intent, 'sources': ['sample']}
        if required_api is not None:
            question['required_api'] = required_api
        return {'action': 'ask', 'request_id': rid, 'question': question}

    def snapshot(self, root):
        return {str(p.relative_to(root)): core.sha(p.read_bytes())
                for p in root.rglob('*') if p.is_file()}

    def invalid_queries(self):
        return [
            ('digest ' * 35, 'RESEARCH_QUERY_TEXT_BOUNDS'),
            (term_query(33), 'RESEARCH_QUERY_QUERY_TERM_LIMIT'),
            ('digest\nhelper', 'RESEARCH_QUERY_TEXT_BOUNDS'),
            ('return class', 'RESEARCH_QUERY_QUERY_TERMS_REQUIRED'),
        ]

    def test_required_api_retrieved_before_both_channel_cutoffs_and_replayed(self):
        # Twenty stronger unfiltered symbol matches precede the only actual API
        # observation. It must still be found by the constrained query.
        text = ('from pathlib import Path\nPath(' + repr(str(self.canary)) + ').write_text("unexpected")\n'
                'import hashlib\n\n')
        text += '\n'.join('def digest_%02d(value):\n    return value\n' % i for i in range(20))
        text += '\ndef utility(value):\n    """digest"""\n    return hashlib.sha256(value).hexdigest()\n'
        data = text.encode()
        work, _, session = self.start(self.mission(data, required_api='hashlib.sha256'))
        initial = json.loads((work / 'report.json').read_bytes())['queries'][0]
        self.assertEqual(initial['status'], 'STATIC_LEADS_REVIEW_REQUIRED')
        for field in ('results', 'lexical_results', 'forge_results'):
            self.assertEqual([r['name'] for r in initial[field]], ['utility'])
        request = self.request('digest', required_api='hashlib.sha256')
        with patch.object(bridge, 'inspect_source', side_effect=AssertionError('unexpected intake')):
            result = session.execute(request)
        self.assertEqual(result['state'], 'COMPLETED')
        self.assertEqual([r['name'] for r in result['result']['results']], ['utility'])
        with patch.object(bridge, 'investigate', side_effect=AssertionError('unexpected replay search')):
            replay = session.execute(request)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['result'], result['result'])
        self.assertFalse(self.canary.exists())
        self.assertEqual((self.sources / 'fixture.py').read_bytes(), data)

    def test_required_api_is_not_inferred_from_text_mentions(self):
        data = b'def digest(value):\n    """hashlib.sha256 is mentioned but never called."""\n    return value\n'
        work, _, _ = self.start(self.mission(data, required_api='hashlib.sha256'))
        query = json.loads((work / 'report.json').read_bytes())['queries'][0]
        for field in ('results', 'lexical_results', 'forge_results'):
            self.assertEqual(query[field], [])
        self.assertEqual(query['status'], 'NO_EVIDENCE_IN_SELECTED_SOURCES')

    def test_invalid_initial_code_and_mixed_queries_create_no_workspace(self):
        mission = self.simple_mission()
        for intent in ('code', 'mixed'):
            for index, (query, reason) in enumerate(self.invalid_queries()):
                with self.subTest(intent=intent, reason=reason):
                    candidate = copy.deepcopy(mission)
                    candidate['questions'][0].update(query=query, intent=intent)
                    work = self.directory / (intent + str(index))
                    with self.assertRaisesRegex(core.Blocked, '^' + reason + '$'):
                        runner.run(candidate, self.sources, work, ROOT)
                    self.assertFalse(work.exists())

    def test_invalid_followup_creates_no_supervision_ledger_or_changed_report(self):
        work, _, session = self.start(self.simple_mission())
        before = self.snapshot(work)
        for index, (query, reason) in enumerate(self.invalid_queries()):
            with self.subTest(reason=reason):
                with patch.object(bridge, 'investigate', side_effect=AssertionError('unexpected query')):
                    with self.assertRaisesRegex(core.Blocked, '^' + reason + '$'):
                        session.execute(self.request(query, rid='invalid' + str(index)))
                self.assertFalse((work / 'supervision').exists())
                self.assertEqual(self.snapshot(work), before)

    def test_invalid_followup_preserves_existing_ledger_and_question_budget(self):
        work, _, session = self.start(self.simple_mission())
        completed = session.execute(self.request('digest', rid='valid'))
        self.assertEqual(completed['state'], 'COMPLETED')
        before_budget = session.execute({'action': 'status'})
        before = self.snapshot(work)
        for index, (query, reason) in enumerate(self.invalid_queries()):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(core.Blocked, '^' + reason + '$'):
                    session.execute(self.request(query, rid='bad' + str(index)))
                self.assertEqual(session.execute({'action': 'status'}), before_budget)
                self.assertEqual(self.snapshot(work), before)
        self.assertTrue(session.execute(self.request('digest', rid='valid'))['replayed'])

    def test_invalid_api_filter_is_refused_before_workspace_or_ledger(self):
        mission = self.simple_mission()
        malformed = copy.deepcopy(mission)
        malformed['questions'][0]['required_api'] = 'hashlib.sha256()'
        with self.assertRaisesRegex(core.Blocked, '^RESEARCH_QUERY_SEARCH_API_FILTER$'):
            runner.run(malformed, self.sources, self.directory / 'invalid', ROOT)
        self.assertFalse((self.directory / 'invalid').exists())
        work, _, session = self.start(mission)
        before = self.snapshot(work)
        with self.assertRaisesRegex(core.Blocked, '^RESEARCH_QUERY_SEARCH_API_FILTER$'):
            session.execute(self.request('digest', required_api='hashlib.sha256()'))
        self.assertEqual(self.snapshot(work), before)

    def test_actual_backend_tokenizer_controls_admission(self):
        # These tokens are ignored by the Workbench but not by the runner's
        # separate reference tokenizer. No duplicated tokenizer policy is used.
        question = self.request('return class')['question']
        self.assertTrue(core.terms(question['query']))
        with self.assertRaisesRegex(core.Blocked, '^RESEARCH_QUERY_QUERY_TERMS_REQUIRED$'):
            bridge.validate_questions([question], ROOT)
        for query in ('digest' + ' ' * 194, term_query(32)):
            self.assertIsNotNone(bridge.validate_questions([self.request(query)['question']], ROOT))

    def test_reference_only_long_many_term_and_multiline_queries_remain_supported(self):
        queries = ['reference ' + 'x' * 390, term_query(33), 'reference\nnotes']
        mission = self.mission(b'Reference notes for synthetic research.\n', kind='documentation',
                               query=queries[0], intent='reference')
        work, _, session = self.start(mission)
        for index, query in enumerate(queries):
            with self.subTest(query_length=len(query)):
                with patch.object(bridge, 'load_base', side_effect=AssertionError('unexpected code backend')):
                    result = session.execute(self.request(query, rid='reference' + str(index), intent='reference'))
                self.assertEqual(result['state'], 'COMPLETED')
                self.assertEqual(result['result']['results'], [])
        self.assertEqual(json.loads((work / 'report.json').read_bytes())['queries'][0]['intent'], 'reference')

    def test_cli_invalid_query_returns_reason_without_creating_ledger(self):
        work, manifest, _ = self.start(self.simple_mission())
        request = self.directory / 'request.json'
        request.write_bytes(core.canonical(self.request(term_query(33))))
        before = self.snapshot(work)
        command = [sys.executable, '-B', str(ROOT / 'runner/forge_supervise.py'),
                   '--manifest', str(manifest), '--source-root', str(self.sources),
                   '--work', str(work), '--forge-root', str(ROOT), '--request', str(request)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output['state'], 'BLOCKED')
        self.assertEqual(output['reason'], 'RESEARCH_QUERY_QUERY_TERM_LIMIT')
        self.assertEqual(self.snapshot(work), before)


if __name__ == '__main__':
    unittest.main()
