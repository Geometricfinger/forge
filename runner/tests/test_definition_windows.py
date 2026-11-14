"""Runner evidence must retain the core's complete, source-bound byte windows."""
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


class DefinitionWindows(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.sources = self.directory / 'sources'
        self.sources.mkdir()
        self.sequence = 0

    def source(self, data, path='module.py'):
        (self.sources / path).write_bytes(data)
        return {'id': 'sample', 'path': path, 'kind': 'implementation',
                'origin': 'fixture://sample', 'capture': 'synthetic_fixture',
                'family': 'synthetic', 'size': len(data), 'sha256': core.sha(data)}

    def inspect(self, data, path='module.py'):
        self.sequence += 1
        work = self.directory / ('work-' + str(self.sequence))
        work.mkdir()
        result = bridge.inspect_source(self.source(data, path), self.sources, work, ROOT)
        self.assertEqual((self.sources / path).read_bytes(), data)
        self.assertIs(result['source_code_executed'], False)
        return result

    def assert_complete(self, record, expected, module):
        encoded = expected.encode('utf-8')
        self.assertEqual(record['text'], expected)
        self.assertIs(record['text_complete'], True)
        self.assertEqual(record['text_sha256'], core.sha(encoded))
        window = record['source_window']
        self.assertEqual(window['status'], 'AVAILABLE')
        self.assertIs(window['complete'], True)
        self.assertEqual(window['bytes'], len(encoded))
        self.assertEqual(window['sha256'], core.sha(encoded))
        self.assertEqual(window['module_sha256'], core.sha(module))
        self.assertEqual(record['source_sha256'], core.sha(module))
        self.assertEqual(module[window['byte_start']:window['byte_end']], encoded)

    def test_large_module_unicode_separator_survives_mission_and_continuation(self):
        # U+2028 in a string is not a physical Python newline. A line-based
        # reconstruction previously returned 'after"\ndef useful(value):'.
        body = 'def useful(value):\n    return value + 1\n'
        data = b'#' + b'x' * 250_000 + b'\n' + ('PREFIX = "before\u2028after"\n' + body).encode()
        source = self.source(data)
        mission = {'schema': 1, 'objective': 'Inspect a synthetic large-module helper',
                   'sources': [source],
                   'questions': [{'id': 'initial', 'query': 'useful', 'intent': 'code', 'sources': ['sample']}],
                   'budgets': {'max_sources': 1, 'max_source_bytes': 8_000_000,
                               'max_total_bytes': 8_000_000, 'max_questions': 3, 'packet_bytes': 16_384}}
        manifest = self.directory / 'mission.json'
        manifest.write_bytes(core.canonical(mission))
        work = self.directory / 'mission-work'
        receipt = runner.run(mission, self.sources, work, ROOT)
        self.assertEqual(receipt['status'], 'READY_FOR_SUPERVISOR_REVIEW')
        self.assertIs(receipt['source_code_executed'], False)
        report = json.loads((work / 'report.json').read_bytes())
        self.assertEqual(report['sources'][0]['status'], 'CODE_INDEXED_WITH_GAPS')
        self.assertIn('SOURCE_SIZE_LIMIT', {gap.get('status') for gap in report['sources'][0]['gaps']})
        initial = report['queries'][0]['results'][0]
        self.assertEqual(initial['snippet'], body)
        supervisor = Supervisor(manifest, self.sources, work, ROOT)
        with patch.object(bridge, 'inspect_source', side_effect=AssertionError('unexpected reanalysis')):
            followup = supervisor.execute({'action': 'ask', 'request_id': 'followup',
                                          'question': {'id': 'followup', 'query': 'useful',
                                                       'intent': 'code', 'sources': ['sample']}})
            self.assertEqual(followup['state'], 'COMPLETED')
            evidence = followup['result']['results'][0]
            self.assertEqual(evidence['id'], initial['id'])
            inspected = supervisor.execute({'action': 'inspect', 'request_id': 'view',
                                            'source_id': 'sample', 'evidence_id': evidence['id'],
                                            'max_chars': 1024})
        self.assertEqual(inspected['state'], 'COMPLETED')
        self.assertEqual(inspected['result']['text'], body)
        self.assertFalse(inspected['result']['truncated'])
        self.assertEqual((inspected['result']['start_line'], inspected['result']['end_line']), (3, 4))
        self.assertEqual((self.sources / 'module.py').read_bytes(), data)

    def test_crlf_decorators_async_and_nested_definitions_preserve_exact_bytes(self):
        inner = '    @mark\r\n    def inner():\r\n        return "na\u00efve"\r\n'
        outer = '@decorate("caf\u00e9")\r\nasync def outer(value):\r\n' + inner + '    return inner()\r\n'
        data = ('LABEL = "before\u2028after"\r\n' + outer).encode()
        result = self.inspect(data)
        records = {record['name']: record for record in result['records']}
        self.assertEqual(set(records), {'outer', 'outer.inner'})
        self.assert_complete(records['outer'], outer, data)
        self.assert_complete(records['outer.inner'], inner, data)
        self.assertEqual((records['outer']['start_line'], records['outer']['end_line']), (2, 7))
        self.assertEqual((records['outer.inner']['start_line'], records['outer.inner']['end_line']), (4, 6))

    def test_oversized_definition_is_omitted_with_gap_and_small_peer_remains_complete(self):
        huge = b'def enormous():\n    value = "' + b'x' * 250_000 + b'"\n    return value\n\n'
        peer = 'def useful(value):\n    return value + 1\n'
        data = huge + peer.encode()
        result = self.inspect(data)
        self.assertEqual(result['status'], 'CODE_INDEXED_WITH_GAPS')
        self.assertEqual([record['name'] for record in result['records']], ['useful'])
        self.assert_complete(result['records'][0], peer, data)
        omitted = [gap for gap in result['gaps'] if gap.get('status') == 'DEFINITION_SIZE_LIMIT']
        self.assertEqual(len(omitted), 1)
        self.assertEqual(omitted[0]['name'], 'enormous')
        report = json.loads(Path(result['forge_report_path']).read_bytes())
        self.assertEqual(report['coverage']['definitions']['discovered'], 2)
        self.assertEqual(report['coverage']['definitions']['inspection_omitted'], 1)

    def test_parenthesized_decorators_keep_complete_outer_and_nested_windows(self):
        inner = '    @(\r\n        mark\r\n    )\r\n    def inner():\r\n        return 1\r\n'
        outer = '@(\r\n    decorate\r\n)\r\nasync def outer():\r\n' + inner + '    return inner\r\n'
        data = ('LABEL = "before\u2028after"\r\n' + outer).encode()
        result = self.inspect(data)
        records = {record['name']: record for record in result['records']}
        self.assert_complete(records['outer'], outer, data)
        self.assert_complete(records['outer.inner'], inner, data)
        self.assertEqual((records['outer']['start_line'], records['outer']['end_line']), (2, 11))
        self.assertEqual((records['outer.inner']['start_line'], records['outer.inner']['end_line']), (6, 10))

    def test_changed_original_during_inspection_cannot_return_bound_records(self):
        data = b'def useful():\n    return 1\n'
        source = self.source(data)
        work = self.directory / 'work'
        work.mkdir()
        original_read = bridge.read_source
        calls = 0

        def changed(root, descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.sources / 'module.py').write_bytes(data.replace(b'return 1', b'return 2'))
            return original_read(root, descriptor)

        with patch.object(bridge, 'read_source', side_effect=changed):
            with self.assertRaisesRegex(core.Blocked, '^SOURCE_CHANGED$'):
                bridge.inspect_source(source, self.sources, work, ROOT)
        self.assertEqual(calls, 2)

    def test_second_parse_must_match_container_source_and_definition_window(self):
        corpus, _, _ = bridge.load_base(ROOT)
        original_parser = corpus._parser
        data = b'def useful():\n    return 1\n'
        for tamper, expected in [('container', 'BRIDGE_CORPUS_BINDING'),
                                 ('source', 'BRIDGE_SOURCE_BINDING'),
                                 ('window', 'BRIDGE_DEFINITION_WINDOW_BINDING')]:
            with self.subTest(tamper=tamper):
                calls = 0

                def inconsistent(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    parsed = original_parser(*args, **kwargs)
                    if calls == 2:
                        if tamper == 'container':
                            parsed['container_sha256'] = '0' * 64
                        elif tamper == 'source':
                            segment = parsed['segments'][0]
                            segment['source_hex'] = bytes.fromhex(segment['source_hex']).replace(b'return 1', b'return 2').hex()
                        else:
                            parsed['segments'][0]['definitions'][0]['source_window']['byte_start'] += 1
                    return parsed

                with patch.object(corpus, '_parser', side_effect=inconsistent):
                    with self.assertRaisesRegex(core.Blocked, '^' + expected + '$'):
                        self.inspect(data)
                self.assertEqual(calls, 2)

    def test_nested_archives_do_not_conflate_equal_member_paths_or_names(self):
        def archive(members):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as target:
                for name, content in members:
                    target.writestr(name, content)
            return stream.getvalue()

        first = b'def useful():\n    return "first"\n'
        second = b'def useful():\n    return "second"\n'
        data = archive([('left.zip', archive([('same.py', first)])),
                        ('right.zip', archive([('same.py', second)]))])
        result = self.inspect(data, 'nested.zip')
        self.assertEqual(len(result['records']), 2)
        self.assertEqual({record['text'] for record in result['records']}, {first.decode(), second.decode()})
        self.assertEqual(len({record['id'] for record in result['records']}), 2)
        for record in result['records']:
            module = first if record['text'] == first.decode() else second
            self.assert_complete(record, module.decode(), module)
            self.assertEqual(record['container_sha256'], core.sha(data))

    def test_fenced_and_notebook_windows_use_segment_local_byte_offsets(self):
        body = '@mark\r\ndef useful():\r\n    return "caf\u00e9"\r\n'
        segment = '# source context\r\n' + body
        markdown = ('# Example\r\n\r\n```python\r\n' + segment + '```\r\n').encode()
        notebook = json.dumps({'nbformat': 4, 'nbformat_minor': 5,
                               'metadata': {'language_info': {'name': 'python'}},
                               'cells': [{'cell_type': 'code', 'metadata': {}, 'source': segment,
                                          'outputs': [], 'execution_count': None}]}).encode()
        for path, data, expected_lines in [('notes.md', markdown, (5, 7)),
                                           ('notes.ipynb', notebook, (2, 4))]:
            with self.subTest(path=path):
                result = self.inspect(data, path)
                self.assertEqual(len(result['records']), 1)
                record = result['records'][0]
                self.assert_complete(record, body, segment.encode())
                self.assertEqual((record['start_line'], record['end_line']), expected_lines)
                self.assertEqual(record['container_sha256'], core.sha(data))

    def test_unchanged_function_text_does_not_reuse_identity_after_module_context_change(self):
        body = 'def useful(value):\n    return value + OFFSET\n'
        first = self.inspect(('OFFSET = 1\n' + body).encode())['records'][0]
        second = self.inspect(('OFFSET = 2\n' + body).encode())['records'][0]
        self.assertEqual(first['text'], second['text'])
        self.assertEqual(first['text_sha256'], second['text_sha256'])
        self.assertNotEqual(first['source_sha256'], second['source_sha256'])
        self.assertNotEqual(first['id'], second['id'])


if __name__ == '__main__':
    unittest.main()
