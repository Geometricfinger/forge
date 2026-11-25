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

    def test_single_word_overlap_survives_report_packet_and_supervisor_projection(self):
        data = b'def data_archive():\n    """history known saved"""\n    return 1\n'
        query = 'archive weather charts'
        work, _, session = self.start(self.mission(data, query=query))
        full = json.loads((work / 'report.json').read_bytes())
        item = full['queries'][0]
        evidence = item['query_evidence']
        self.assertEqual(item['results'][0]['name'], 'data_archive')
        self.assertEqual(item['status'], 'PARTIAL_QUERY_ONLY')
        self.assertEqual(evidence['term_coverage'], 'SINGLE_TERM_OVERLAP')
        self.assertEqual(evidence['matched_terms'], ['archive'])
        self.assertEqual(evidence['unmatched_terms'], ['charts', 'weather'])
        self.assertEqual(item['next_action'], 'INSPECT_SOURCE_OR_REFINE_QUERY')
        packet = core.compact_packet(full, 4096)
        self.assertEqual(packet['queries'][0]['term_coverage'], 'SINGLE_TERM_OVERLAP')
        request = self.request(query)
        followup = session.execute(request)
        self.assertEqual(followup['result']['query_evidence'], evidence)
        self.assertEqual(session.execute(request)['result'], followup['result'])
        for i, (query, n, total, action) in enumerate((
            ('data archive weather charts river stone field', 2, 7, 'INSPECT_SOURCE_OR_REFINE_QUERY'),
            ('data archive history known saved river stone', 5, 7, 'INSPECT_SOURCE'),
            ('data archive weather charts', 2, 4, 'INSPECT_SOURCE'),
            ('archive', 1, 1, 'INSPECT_SOURCE'),
        )):
            with self.subTest(query=query):
                result = session.execute(self.request(query, rid='coverage' + str(i)))['result']
                self.assertEqual(result['results'][0]['name'], 'data_archive')
                evidence = result['query_evidence']
                self.assertEqual((evidence['matched_term_count'], evidence['query_term_count'],
                                  evidence['next_action']), (n, total, action))
                self.assertEqual(result['next_action'], action if n < total - n else
                                 'REVIEW_EVIDENCE_AND_DECLARE_BEHAVIOR_TEST')

    def budgeted_result(self, limit):
        doc = 'Use example records and archive data. ' * 8
        data = ''.join(f'def archive_{letter}_{"x" * 80}(data):\n    """{doc}"""\n    return data\n\n'
                       for letter in 'abcde')
        mission = self.mission(data.encode(), query='archive')
        mission['budgets']['packet_bytes'] = limit
        mission['sources'][0]['origin'] = 'fixture://' + 'e' * 90
        work, _, session = self.start(mission)
        request = self.request('archive')
        response = session.execute(request)
        self.assertEqual(session.execute(request)['result'], response['result'])
        return work, response

    def test_tight_followup_preserves_fitting_source_results_and_full_evidence(self):
        work, response = self.budgeted_result(4700)
        self.assertEqual(response['state'], 'COMPLETED')
        self.assertLessEqual(len(core.canonical(response)), 4700)
        self.assertEqual(len(response['result']['results']), 5)
        saved = json.loads((work / 'supervision' / 'followup.json').read_bytes())
        self.assertEqual(saved['result']['query_evidence']['matched_terms'], ['archive'])

    def test_oversized_followup_still_enforces_existing_limit(self):
        _, response = self.budgeted_result(4096)
        self.assertEqual((response['state'], response['reason']), ('FAILED', 'SUPERVISOR_RESPONSE_LIMIT'))

    def test_conversion_direction_reaches_fused_result_and_budgeted_supervisor(self):
        source = (b'def amber(value):\n    """Convert copper to silver."""\n    return value\n'
                  b'def blue(value):\n    """Convert silver to copper."""\n    return value\n')
        work, _, session = self.start(self.mission(source, query='convert copper to silver'))
        first = json.loads((work / 'report.json').read_bytes())['queries'][0]
        self.assertEqual(first['results'][0]['name'], 'amber')
        second = session.execute(self.request('convert silver to copper'))['result']
        self.assertEqual(second['results'][0]['name'], 'blue')
        self.assertEqual(second['query_evidence']['requested_direction']['top_result_declaration'],
                         {'basis': 'DOCSTRING_SUMMARY', 'input': ['silver'], 'output': ['copper']})

    def test_formatted_direction_and_reverse_conflict_reach_both_channels(self):
        source = (b'def reverse(value):\n    """Convert hexadecimal values to integers."""\n    return value\n'
                  b'def forward(value):\n    """Convert :class:`int` to hexadecimal."""\n    return value\n')
        work, _, session = self.start(self.mission(source, query='convert integers to hexadecimal values'))
        first = json.loads((work / 'report.json').read_bytes())['queries'][0]
        self.assertEqual(first['results'][0]['name'], 'forward')
        self.assertEqual(first['results'][0]['directional_match']['basis'], 'DOCSTRING_SUMMARY')
        self.assertTrue(any('directional_conflict' in r for r in first['results']))
        followup = session.execute(self.request('convert hexadecimal to integers'))['result']
        self.assertEqual(followup['results'][0]['name'], 'reverse')
        self.assertEqual(followup['runtime_validation'], 'NOT_RUN')

    def test_subject_matching_predicate_reaches_supervisor_without_execution(self):
        source = (b'def titleize(value):\n    """Convert a string to title case."""\n    return value\n'
                  b'def is_titlecase(value):\n    """Determine whether a string already has the requested form."""\n    return bool(value)\n')
        query = 'Determine whether a string uses title case without converting it'
        work, _, session = self.start(self.mission(source, query=query))
        first = json.loads((work / 'report.json').read_bytes())['queries'][0]
        self.assertEqual(first['results'][0]['name'], 'is_titlecase')
        self.assertEqual(first['results'][0]['predicate_match']['matched_subject_terms'], ['case', 'title'])
        second = session.execute(self.request(query))['result']
        self.assertEqual(second['results'][0]['name'], 'is_titlecase')
        self.assertEqual(second['query_evidence']['requested_predicate']['evidence'],
                         'PREDICATE_DECLARATION_NOT_BEHAVIOR_PROOF')
        self.assertFalse(self.canary.exists())

    def test_literal_only_query_is_admitted_and_preserves_lexical_evidence(self):
        source = (b'def yes(value):\n    """Return True."""\n    return value\n'
                  b'def no(value):\n    """Return False."""\n    return value\n')
        work, _, session = self.start(self.mission(source, query='True'))
        first = json.loads((work / 'report.json').read_bytes())['queries'][0]
        self.assertEqual(first['results'][0]['name'], 'yes')
        self.assertEqual(first['query_evidence']['matched_terms'], ['true'])
        second = session.execute(self.request('False'))['result']
        self.assertEqual(second['results'][0]['name'], 'no')
        self.assertEqual(second['query_evidence']['matched_terms'], ['false'])

    def test_return_declarations_reach_runner_and_supervisor_without_type_guarantee(self):
        source = (b'def other(value) -> Stone:\n    """Parse text into a Jewel."""\n    return value\n'
                  b'def matching(value) -> Jewel:\n    """Parse text."""\n    return None\n')
        query = 'Parse text into a Jewel'
        work, _, session = self.start(self.mission(source, query=query))
        first = json.loads((work / 'report.json').read_bytes())['queries'][0]
        self.assertEqual(first['results'][0]['name'], 'matching')
        self.assertTrue(first['results'][0]['declared_output']['name_match'])
        self.assertIn('other', [r['name'] for r in first['results']])
        second = session.execute(self.request(query))['result']
        output = second['query_evidence']['requested_output']
        self.assertEqual(second['results'][0]['name'], 'matching')
        self.assertEqual(output['runtime_type_relationship'], 'NOT_ESTABLISHED')
        self.assertEqual(second['runtime_validation'], 'NOT_RUN')

    def test_optional_return_annotation_does_not_become_an_exact_name(self):
        source = b'def matching(value) -> Jewel | None:\n    """Parse text into a Jewel."""\n    return value\n'
        work, _, _ = self.start(self.mission(source, query='Parse text into a Jewel'))
        first = json.loads((work / 'report.json').read_bytes())['queries'][0]
        self.assertNotIn('declared_output', first['results'][0])
        self.assertIsNone(first['query_evidence']['requested_output']['top_result_declaration'])

    def test_compound_symbols_do_not_become_source_body_or_call_matches(self):
        source = (b'def lookup(value):\n    return is_titlecase(value)\n'
                  b'def is_titlecase(value):\n    return bool(value)\n')
        work, _, _ = self.start(self.mission(source, query='title case'))
        first = json.loads((work / 'report.json').read_bytes())['queries'][0]
        for field in ('results', 'lexical_results', 'forge_results'):
            self.assertEqual([r['name'] for r in first[field]], ['is_titlecase'])

    def test_mixed_legacy_report_does_not_gain_predicate_or_return_hints(self):
        source = (b'def is_copper(value) -> Jewel:\n    """Check whether copper. Parse text into a Jewel."""\n    return value\n'
                  b'def older(value):\n    return value\n')
        mission = self.mission(source, query='check whether copper')
        group = bridge.inspect_source(mission['sources'][0], self.sources, self.directory / 'intake', ROOT)
        report_path = Path(group['forge_report_path'])
        report = json.loads(report_path.read_bytes())
        report['sources'][0]['segments'][0]['definitions'][1].pop('structure')
        report_path.write_bytes(core.canonical(report))
        receipt_path = report_path.with_name('receipt.json')
        receipt = json.loads(receipt_path.read_bytes())
        receipt['report_sha256'] = core.sha(report_path.read_bytes())
        receipt_path.write_bytes(core.canonical(receipt))
        for query in ('check whether copper', 'Parse text into a Jewel'):
            question = dict(mission['questions'][0], query=query)
            result = bridge.investigate(question, [group], ROOT)
            self.assertTrue(result['results'])
            for row in result['results']:
                self.assertNotIn('predicate_match', row)
                self.assertNotIn('declared_output', row)

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
