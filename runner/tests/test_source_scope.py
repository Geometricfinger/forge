"""Module evidence must survive research without becoming a fake function."""
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from research_runner import bridge, core, runner
from research_runner.supervise import Supervisor

ROOT = Path(__file__).resolve().parents[2]


class SourceScopeResearch(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.sources = self.directory / 'sources'
        self.sources.mkdir()
        self.canary = self.directory / 'unexpected-execution'
        self.sequence = 0

    def source(self, data, path='sample.py'):
        (self.sources / path).write_bytes(data)
        return {'id': 'sample', 'path': path, 'kind': 'implementation',
                'origin': 'fixture://source-scope', 'capture': 'synthetic_fixture',
                'family': 'synthetic', 'size': len(data), 'sha256': core.sha(data)}

    def inspect(self, data, path='sample.py'):
        self.sequence += 1
        work = self.directory / ('inspection-' + str(self.sequence))
        work.mkdir()
        result = bridge.inspect_source(self.source(data, path), self.sources, work, ROOT)
        self.assertEqual((self.sources / path).read_bytes(), data)
        self.assertFalse(self.canary.exists())
        return result

    def mission(self, data, query='hashlib', required_api='hashlib.sha256', path='sample.py'):
        question = {'id': 'initial', 'query': query, 'intent': 'code', 'sources': ['sample']}
        if required_api:
            question['required_api'] = required_api
        return {'schema': 1, 'objective': 'Review synthetic non-callable source evidence',
                'sources': [self.source(data, path)], 'questions': [question],
                'budgets': {'max_sources': 1, 'max_source_bytes': 8_000_000,
                            'max_total_bytes': 8_000_000, 'max_questions': 6, 'packet_bytes': 16_384}}

    def start(self, mission):
        work = self.directory / 'mission-work'
        manifest = self.directory / 'mission.json'
        manifest.write_bytes(core.canonical(mission))
        receipt = runner.run(mission, self.sources, work, ROOT)
        self.assertEqual(receipt['status'], 'READY_FOR_SUPERVISOR_REVIEW')
        self.assertFalse(receipt['source_code_executed'])
        report = json.loads((work / 'report.json').read_bytes())
        return work, report, Supervisor(manifest, self.sources, work, ROOT)

    def question(self, query='hashlib', rid='followup', required_api='hashlib.sha256'):
        q = {'id': rid, 'query': query, 'intent': 'code', 'sources': ['sample']}
        if required_api:
            q['required_api'] = required_api
        return {'action': 'ask', 'request_id': rid, 'question': q}

    def assert_module(self, record, expected):
        raw = expected.encode('utf-8')
        self.assertEqual(record['record_kind'], 'source_scope')
        self.assertEqual(record['name'], '<module>')
        self.assertTrue(record['id'].endswith(':source'))
        self.assertFalse(record['id'].startswith('definition_'))
        self.assertNotIn('definition_id', record)
        self.assertEqual(record['text'], expected)
        self.assertTrue(record['text_complete'])
        self.assertEqual(record['text_sha256'], core.sha(raw))
        self.assertEqual(record['source_window'], {'status': 'AVAILABLE', 'byte_start': 0,
            'byte_end': len(raw), 'bytes': len(raw), 'sha256': core.sha(raw),
            'module_sha256': core.sha(raw), 'byte_basis': 'UTF8_SEGMENT_SOURCE', 'complete': True})
        self.assertEqual(record['runtime_validation'], 'NOT_RUN')

    def test_module_api_survives_initial_continuation_inspection_and_replay(self):
        text = ('from pathlib import Path\nPath(' + repr(str(self.canary)) + ').write_text("unexpected")\n'
                'import hashlib\nfingerprint = hashlib.sha256(b"payload").hexdigest()\n')
        data = text.encode()
        work, report, session = self.start(self.mission(data))
        self.assertEqual(report['sources'][0]['definition_count'], 0)
        self.assertEqual(report['sources'][0]['source_scope_count'], 1)
        initial = report['queries'][0]
        self.assertEqual(initial['status'], 'STATIC_LEADS_REVIEW_REQUIRED')
        for field in ('results', 'lexical_results', 'forge_results'):
            self.assertEqual([r['record_kind'] for r in initial[field]], ['source_scope'])
            self.assertEqual(initial[field][0]['observed_apis'], ['hashlib.sha256'])
        packet = json.loads((work / 'supervisor_packet.json').read_bytes())
        self.assertEqual(packet['queries'][0]['candidates'][0]['record_kind'], 'source_scope')
        before = {p.name: core.sha(p.read_bytes()) for p in (work / 'checkpoints').glob('*.json')}
        request = self.question()
        with patch.object(bridge, 'inspect_source', side_effect=AssertionError('unexpected reanalysis')):
            followup = session.execute(request)
            row = followup['result']['results'][0]
            self.assertEqual(row['record_kind'], 'source_scope')
            self.assertEqual(row['id'], initial['results'][0]['id'])
            inspected = session.execute({'action': 'inspect', 'request_id': 'inspect-module',
                                         'source_id': 'sample', 'evidence_id': row['id'], 'max_chars': 4096})
        self.assertEqual(inspected['result']['text'], text)
        self.assertEqual(inspected['result']['record_kind'], 'source_scope')
        self.assertEqual(inspected['result']['source_window']['sha256'], core.sha(data))
        self.assertFalse(inspected['result']['truncated'])
        with patch.object(bridge, 'investigate', side_effect=AssertionError('unexpected replay query')):
            replay = session.execute(request)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['result'], followup['result'])
        budget = session.execute({'action': 'status'})
        self.assertEqual((budget['reserved_followups'], budget['remaining_questions']), (1, 4))
        self.assertEqual(before, {p.name: core.sha(p.read_bytes()) for p in (work / 'checkpoints').glob('*.json')})
        self.assertFalse(self.canary.exists())
        self.assertEqual((self.sources / 'sample.py').read_bytes(), data)

    def test_mixed_module_call_and_callable_have_distinct_evidence_and_counts(self):
        data = (b'import hashlib\nmodule_digest = hashlib.sha256(b"module")\n'
                b'def function_digest(value):\n    return hashlib.sha256(value)\n')
        _, report, _ = self.start(self.mission(data))
        rows = report['queries'][0]['results']
        self.assertEqual({r['record_kind'] for r in rows}, {'source_scope', 'function'})
        self.assertEqual(len({r['id'] for r in rows}), 2)
        self.assertEqual(report['sources'][0]['definition_count'], 1)
        self.assertEqual(report['sources'][0]['source_scope_count'], 1)
        self.assertTrue(all(r['observed_apis'] == ['hashlib.sha256'] for r in rows))

    def test_function_call_does_not_become_module_observation(self):
        data = b'import hashlib\nLABEL = "context"\ndef digest(value):\n    return hashlib.sha256(value)\n'
        result = self.inspect(data)
        self.assertEqual([r['record_kind'] for r in result['records']], ['function'])
        found = bridge.investigate(self.question()['question'], [result], ROOT)
        self.assertEqual([r['name'] for r in found['results']], ['digest'])

    def test_import_and_text_mentions_do_not_satisfy_required_api(self):
        data = b'import hashlib\nfingerprint = "hashlib.sha256 is a textual reference"\n'
        result = self.inspect(data)
        self.assertEqual(result['records'][0]['record_kind'], 'source_scope')
        self.assertEqual(result['records'][0]['observed_apis'], [])
        found = bridge.investigate(self.question()['question'], [result], ROOT)
        for field in ('results', 'lexical_results', 'forge_results'):
            self.assertEqual(found[field], [])
        self.assertEqual(found['status'], 'NO_EVIDENCE_IN_SELECTED_SOURCES')
        unconstrained = bridge.investigate(self.question(required_api=None)['question'], [result], ROOT)
        self.assertEqual(unconstrained['results'][0]['record_kind'], 'source_scope')
        self.assertEqual(unconstrained['results'][0]['observed_apis'], [])

    def test_lexical_module_symbol_keeps_actual_api_evidence_without_metadata_hit(self):
        data = b'import hashlib\nunique_fingerprint_marker = hashlib.sha256(b"payload")\n'
        work, report, session = self.start(self.mission(data, query='unique_fingerprint_marker'))
        initial = report['queries'][0]
        self.assertEqual(initial['forge_results'], [])
        self.assertEqual(initial['status'], 'STATIC_LEADS_REVIEW_REQUIRED')
        self.assertEqual(initial['results'][0]['record_kind'], 'source_scope')
        self.assertEqual(initial['results'][0]['observed_apis'], ['hashlib.sha256'])
        self.assertEqual(initial['results'][0]['channels'], ['lexical'])
        followup = session.execute(self.question(query='unique_fingerprint_marker'))
        self.assertEqual(followup['result']['results'][0]['id'], initial['results'][0]['id'])
        full = json.loads((work / 'supervision/followup.json').read_bytes())['result']
        self.assertEqual(full['results'][0]['observed_apis'], ['hashlib.sha256'])

    def test_lexical_record_api_claim_cannot_override_bound_corpus_observations(self):
        data = b'import hashlib\nunique_fingerprint_marker = "hashlib.sha256 is a mention"\n'
        result = self.inspect(data)
        result['records'][0]['observed_apis'] = ['hashlib.sha256']
        found = bridge.investigate(self.question(query='unique_fingerprint_marker')['question'], [result], ROOT)
        self.assertEqual(found['results'], [])
        self.assertEqual(found['lexical_results'], [])
        self.assertEqual(found['forge_results'], [])
        unconstrained = bridge.investigate(self.question(query='unique_fingerprint_marker', required_api=None)['question'], [result], ROOT)
        self.assertEqual(unconstrained['results'][0]['observed_apis'], [])
        self.assertEqual(unconstrained['lexical_results'][0]['observed_apis'], [])

    def test_changed_record_identity_cannot_inherit_corpus_api_observations(self):
        data = b'import hashlib\nvalue = hashlib.sha256(b"payload")\n'
        for field, value in [('source_sha256', '0' * 64), ('record_kind', 'function')]:
            with self.subTest(field=field):
                result = self.inspect(data)
                result['records'][0][field] = value
                with self.assertRaisesRegex(core.Blocked, '^BRIDGE_EVIDENCE_BINDING$'):
                    bridge.investigate(self.question()['question'], [result], ROOT)

    def test_module_api_filter_applies_before_top_k_cutoffs(self):
        text = 'import hashlib\n' + '\n'.join('def hashlib_%02d(value):\n    return value\n' % i for i in range(20))
        text += '\nmodule_digest = hashlib.sha256(b"payload")\n'
        result = self.inspect(text.encode())
        found = bridge.investigate(self.question()['question'], [result], ROOT)
        for field in ('results', 'lexical_results', 'forge_results'):
            self.assertEqual([r['record_kind'] for r in found[field]], ['source_scope'])

    def test_large_source_scope_is_an_explicit_omission_not_a_partial_record(self):
        data = b'import hashlib\nvalue = hashlib.sha256(b"payload")\n#' + b'x' * 250_000
        result = self.inspect(data)
        self.assertEqual(result['status'], 'CODE_INDEXED_WITH_GAPS')
        self.assertEqual(result['records'], [])
        gaps = [g for g in result['gaps'] if g.get('status') == 'SOURCE_SCOPE_SIZE_LIMIT']
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]['source_bytes'], len(data))
        self.assertEqual(gaps[0]['max_source_bytes'], 250_000)
        self.assertTrue(gaps[0]['source_scope_id'].endswith(':source'))
        found = bridge.investigate(self.question()['question'], [result], ROOT)
        self.assertEqual(found['status'], 'EVALUATION_BLOCKED')
        self.assertTrue(found['source_gaps'])

    def test_exact_source_scope_size_limit_retains_complete_bytes(self):
        prefix = b'import hashlib\nvalue = hashlib.sha256(b"payload")\n#'
        data = prefix + b'x' * (250_000 - len(prefix))
        result = self.inspect(data)
        self.assertEqual(len(result['records']), 1)
        self.assert_module(result['records'][0], data.decode())
        self.assertFalse(any(g.get('status') == 'SOURCE_SCOPE_SIZE_LIMIT' for g in result['gaps']))

    def test_module_context_does_not_evict_functions_at_cumulative_retention_limit(self):
        body = 'def useful(value):\n    return value + 1\n'
        text = 'import hashlib\nvalue = hashlib.sha256(b"x")\n' + body
        with patch.object(bridge, 'MAX_LEXICAL_CHARS', len(body)):
            result = self.inspect(text.encode())
        self.assertEqual([(r['record_kind'], r['name']) for r in result['records']], [('function', 'useful')])
        self.assertEqual(result['records'][0]['text'], body)
        gaps = [g for g in result['gaps'] if g.get('status') == 'SOURCE_SCOPE_RETENTION_LIMIT']
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]['max_retained_chars'], len(body))
        self.assertEqual(result['status'], 'CODE_INDEXED_WITH_GAPS')

    def test_unsupported_language_stays_text_only_and_not_callable_evidence(self):
        data = b'import crypto from "crypto";\nconst value = crypto.createHash("sha256");\n'
        result = self.inspect(data, 'sample.ts')
        self.assertEqual(result['status'], 'TEXT_ONLY_UNSUPPORTED_LANGUAGE')
        self.assertEqual(result['records'], [])
        self.assertEqual(result['gaps'], ['LANGUAGE_NOT_ANALYZED'])
        found = bridge.investigate(self.question(query='crypto', required_api=None)['question'], [result], ROOT)
        self.assertEqual(found['results'], [])
        self.assertEqual(found['status'], 'REFERENCE_OR_TEXT_ONLY')
        self.assertTrue(all(r['kind'] == 'code_text_only' for r in found['references']))

    def test_source_scope_preserves_crlf_unicode_and_declared_encoding(self):
        crlf = 'LABEL = "before\u2028after"\r\nimport hashlib\r\nvalue = hashlib.sha256(b"x")\r\n'
        latin = '# coding: latin-1\nLABEL = "caf\u00e9"\nimport hashlib\nvalue = hashlib.sha256(b"x")\n'
        for data, expected in [(crlf.encode(), crlf), (latin.encode('latin-1'), latin)]:
            with self.subTest(encoding='utf8' if data == crlf.encode() else 'latin1'):
                result = self.inspect(data)
                self.assert_module(result['records'][0], expected)
                self.assertEqual(result['records'][0]['container_sha256'], core.sha(data))

    def test_fences_cells_and_nested_archives_preserve_distinct_module_identities(self):
        body = 'import hashlib\r\nvalue = hashlib.sha256(b"x")\r\n'
        markdown = ('# sample\r\n```python\r\n' + body + '```\r\n').encode()
        notebook = json.dumps({'nbformat': 4, 'nbformat_minor': 5,
            'metadata': {'language_info': {'name': 'python'}},
            'cells': [{'cell_type': 'code', 'metadata': {}, 'source': body, 'outputs': [], 'execution_count': None} for _ in range(2)]}).encode()
        def archive(entries):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as z:
                for path, data in entries: z.writestr(path, data)
            return stream.getvalue()
        child = archive([('same.py', body.encode())])
        nested = archive([('left.zip', child), ('right.zip', child)])
        for data, path, count in [(markdown, 'sample.md', 1), (notebook, 'sample.ipynb', 2), (nested, 'sample.zip', 2)]:
            with self.subTest(path=path):
                result = self.inspect(data, path)
                self.assertEqual(len(result['records']), count)
                self.assertEqual(len({r['id'] for r in result['records']}), count)
                for record in result['records']:
                    self.assert_module(record, body)
                    self.assertEqual(record['container_sha256'], core.sha(data))
                if path.endswith('.md'):
                    self.assertEqual((result['records'][0]['start_line'], result['records'][0]['end_line']), (3, 4))
                elif path.endswith('.ipynb'):
                    self.assertEqual({r['cell_index'] for r in result['records']}, {0, 1})

    def test_second_parse_cannot_change_module_identity_or_location(self):
        corpus, _, _ = bridge.load_base(ROOT)
        parser = corpus._parser
        data = b'import hashlib\nvalue = hashlib.sha256(b"one")\n'
        for tamper in ('bytes', 'location'):
            with self.subTest(tamper=tamper):
                calls = 0
                def inconsistent(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    parsed = parser(*args, **kwargs)
                    if calls == 2:
                        seg = parsed['segments'][0]
                        if tamper == 'bytes':
                            raw = bytes.fromhex(seg['source_hex']).replace(b'one', b'two')
                            seg['source_hex'] = raw.hex()
                            seg['source_sha256'] = core.sha(raw)
                        else:
                            seg['start_line'] += 1
                    return parsed
                with patch.object(corpus, '_parser', side_effect=inconsistent):
                    with self.assertRaisesRegex(core.Blocked, '^BRIDGE_SOURCE_SCOPE_BINDING$'):
                        self.inspect(data)
                self.assertEqual(calls, 2)

    def test_module_source_change_blocks_completed_request_replay(self):
        data = b'import hashlib\nvalue = hashlib.sha256(b"one")\n'
        _, _, session = self.start(self.mission(data))
        request = self.question()
        self.assertEqual(session.execute(request)['state'], 'COMPLETED')
        (self.sources / 'sample.py').write_bytes(data.replace(b'one', b'two'))
        with self.assertRaisesRegex(core.Blocked, '^SOURCE_CHANGED$'):
            session.execute(request)

    def test_compact_inspection_marks_excerpt_truncation_without_changing_bound_window(self):
        data = b'import hashlib\nvalue = hashlib.sha256(b"payload")\n'
        _, report, session = self.start(self.mission(data))
        evidence = report['queries'][0]['results'][0]
        result = session.execute({'action': 'inspect', 'request_id': 'short-view', 'source_id': 'sample',
                                  'evidence_id': evidence['id'], 'max_chars': 16})['result']
        self.assertEqual(result['text'], data.decode()[:16])
        self.assertTrue(result['truncated'])
        self.assertEqual(result['total_chars'], len(data))
        self.assertEqual(result['record_kind'], 'source_scope')
        self.assertEqual(result['source_window']['bytes'], len(data))
        self.assertEqual(result['source_window']['sha256'], core.sha(data))


if __name__ == '__main__':
    unittest.main()
