"""Markdown interpretation is explicit; original source bytes remain evidence."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from forge_core import corpus
from forge_core.common import Blocked, canonical, sha
from forge_core.mixed_intake import inspect_container
from forge_core.source_inspection import inspect_definition
from forge_core.source_views import analysis_bytes, fence_view, partition_source_windows
from forge_core.workspace import initialize


PROFILE = {'schema': 1, 'id': 'fence', 'title': 'Synthetic fence API check',
           'targets': [{'api': 'hashlib.sha256', 'capability': 'digest'}]}
CODE = ('import hashlib\nraise RuntimeError("INSPECTED_SOURCE_MUST_NOT_RUN")\n'
        '@(\n    decorate\n)\ndef digest(payload):\n'
        '    return hashlib.sha256(payload).digest()\n')


def fenced(code=CODE, indent=2, newline='\n', marker='```', closing_indent=None):
    prefix = ' ' * indent
    body = ''.join(prefix + line for line in code.splitlines(keepends=True)).replace('\n', newline)
    closing_indent = indent if closing_indent is None else closing_indent
    document = ('# Synthetic example' + newline + prefix + marker + 'python' + newline +
                body + ' ' * closing_indent + marker + newline)
    return document.encode('utf-8'), body.encode('utf-8')


def source(data, file_id='example', path='example.md'):
    return {'provider': 'synthetic', 'file_id': file_id, 'path': path,
            'size': len(data), 'sha256': sha(data), 'source_url': 'fixture://' + file_id}


class Recorder:
    def __init__(self):
        self.payloads = []
    def scan(self, data, sid, profile):
        self.payloads.append(data)
        return {'source_id': sid, 'source_sha256': sha(data),
                'upstream_code_executed': False, 'findings': []}


class FenceViewUnitTests(unittest.TestCase):
    def test_zero_to_three_spaces_keep_original_identity_and_windows(self):
        for indent in range(4):
            for marker in ['```', '~~~~']:
                with self.subTest(indent=indent, marker=marker):
                    document, raw = fenced(indent=indent, marker=marker, closing_indent=0)
                    result = inspect_container(document, 'example.md')
                    self.assertFalse(result['gaps'])
                    self.assertEqual(len(result['segments']), 1)
                    segment = result['segments'][0]
                    self.assertEqual(bytes.fromhex(segment['source_hex']), raw)
                    self.assertEqual(segment['source_sha256'], sha(raw))
                    self.assertEqual([d['name'] for d in segment['definitions']], ['digest'])
                    window = segment['definitions'][0]['source_window']
                    expected = b''.join(raw.splitlines(keepends=True)[2:])
                    self.assertEqual(raw[window['byte_start']:window['byte_end']], expected)
                    self.assertEqual(window['sha256'], sha(expected))
                    self.assertTrue(window['complete'])
                    locator = {k: segment[k] for k in ['member_chain', 'format', 'start_line', 'end_line', 'cell_index', 'source_sha256']}
                    self.assertEqual(segment['segment_local_id'], 'segment_' + sha(canonical(locator)))
                    self.assertEqual(analysis_bytes(segment, raw), CODE.encode())
                    if indent:
                        self.assertEqual(segment['analysis_view']['column_basis'], 'ANALYSIS_VIEW_UTF8')
                        self.assertEqual(segment['analysis_view']['line_mapping'], 'IDENTITY')
                    else:
                        self.assertNotIn('analysis_view', segment)

    def test_remove_up_to_opening_indent_per_line_not_common_dedent(self):
        raw = b' def first():\n     return 1\n   def second():\n       return 2\n# unindented\n'
        expected = b'def first():\n  return 1\ndef second():\n    return 2\n# unindented\n'
        data = b'   ```python\n' + raw + b'```\n'
        result = inspect_container(data, 'mixed.md')
        self.assertEqual([d['name'] for d in result['segments'][0]['definitions']], ['first', 'second'])
        self.assertEqual(analysis_bytes(result['segments'][0], raw), expected)

    def test_partial_leading_tabs_use_four_column_stops_and_keep_internal_tabs(self):
        cases = [
            (1, b'\tvalue\n', b'   value\n'),
            (2, b' \tvalue\n', b'  value\n'),
            (3, b'  \tvalue\n', b' value\n'),
            (3, b'   \tvalue\n', b'\tvalue\n'),
            (2, b'    \tvalue\n', b'  \tvalue\n'),
            (3, b'value\tinside\n', b'value\tinside\n'),
            (0, b'\tvalue\n', b'\tvalue\n'),
        ]
        for indent, raw, expected in cases:
            with self.subTest(indent=indent, raw=raw):
                actual, metadata = fence_view(raw, indent)
                self.assertEqual(actual, expected)
                self.assertEqual(metadata['source_sha256'], sha(raw))
                self.assertEqual(metadata['analysis_sha256'], sha(expected))

    def test_decorator_unicode_and_physical_newlines_return_original_lines(self):
        code = '@(\n decorate("é")\n)\nasync def café(value="雪"):\n return value\n'
        for newline in ['\n', '\r\n', '\r']:
            document, raw = fenced(code, indent=3, newline=newline)
            result = inspect_container(document, 'unicode.md')
            segment = result['segments'][0]
            definition = segment['definitions'][0]
            self.assertEqual(definition['name'], 'café')
            self.assertEqual((definition['start_line'], definition['end_line']), (1, 5))
            window = definition['source_window']
            self.assertEqual(raw[window['byte_start']:window['byte_end']], raw)
            self.assertEqual(bytes.fromhex(segment['source_hex']), raw)
            self.assertEqual(analysis_bytes(segment, raw), code.replace('\n', newline).encode())

    def test_plain_python_indentation_is_not_repaired(self):
        result = inspect_container(b' def bad():\n     return 1\n', 'bad.py')
        self.assertFalse(result['segments'])
        self.assertIn('PYTHON_SYNTAX_ERROR', {g['status'] for g in result['gaps']})
        data, raw = fenced('def good():\n return 1\n')
        segment = inspect_container(data, 'good.md')['segments'][0]
        segment['format'] = 'python_file'
        with self.assertRaisesRegex(Blocked, '^FENCE_ANALYSIS_BINDING$'):
            analysis_bytes(segment, raw)

    def test_normalized_module_bytes_cannot_exceed_original_parser_allowance(self):
        body = b' def f():\n     return 1\n' + b'\t# comment\n' * 30
        normalized, _ = fence_view(body, 1)
        self.assertGreater(len(normalized), len(body))
        limit = (len(body) + len(normalized)) // 2
        result = inspect_container(b' ```python\n' + body + b' ```\n', 'limits.md',
                                   limits={'max_module_bytes': limit})
        self.assertFalse(result['segments'])
        gap = next(g for g in result['gaps'] if g['status'] == 'MODULE_SIZE_LIMIT')
        self.assertEqual(gap['source_bytes'], len(body))
        self.assertEqual(gap['analysis_bytes'], len(normalized))

    def test_tampered_analysis_view_policy_sizes_hashes_and_types_reject(self):
        data, raw = fenced()
        original = inspect_container(data, 'example.md')['segments'][0]
        mutations = {'schema': True, 'policy': 'unknown', 'indent_columns': False,
                     'source_sha256': '0' * 64, 'analysis_sha256': '0' * 64,
                     'source_bytes': len(raw) + 1, 'analysis_bytes': 1,
                     'line_mapping': 'SHIFTED', 'column_basis': 'RAW_SOURCE'}
        for key, value in mutations.items():
            with self.subTest(key=key):
                changed = copy.deepcopy(original)
                changed['analysis_view'][key] = value
                with self.assertRaisesRegex(Blocked, '^FENCE_ANALYSIS_BINDING$'):
                    analysis_bytes(changed, raw)
        changed = copy.deepcopy(original)
        changed['analysis_view']['unexpected'] = True
        with self.assertRaises(Blocked):
            analysis_bytes(changed, raw)

    def test_worker_view_tamper_rejected_before_analyzer(self):
        data, _ = fenced()
        parsed = inspect_container(data, 'example.md')
        parsed['segments'][0]['analysis_view']['analysis_sha256'] = '0' * 64
        recorder = Recorder()
        with tempfile.TemporaryDirectory() as folder, patch.object(corpus, '_parser', return_value=parsed):
            with self.assertRaisesRegex(Blocked, '^FENCE_ANALYSIS_BINDING$'):
                corpus.inspect_one(data, source(data), Path(folder), PROFILE, recorder)
        self.assertEqual(recorder.payloads, [])

    def test_hound_cannot_bypass_raw_or_normalized_source_budget(self):
        code = ' def f():\n     return 1\n'
        raw_large = (code + '   #' + '.' * (250_001 - len(code) - 5) + '\n').encode()
        self.assertEqual(len(raw_large), 250_001)
        raw_small = code.encode() + (b'\t#' + b'.' * 90 + b'\n') * 2650
        self.assertLess(len(raw_small), 250_000)
        self.assertGreater(len(fence_view(raw_small, 1)[0]), 250_000)
        for raw, indent in [(raw_large, 3), (raw_small, 1)]:
            # The first fixture intentionally uses a valid mixed prefix width.
            data = b' ' * indent + b'```python\n' + raw + b'```\n'
            recorder = Recorder()
            with tempfile.TemporaryDirectory() as folder:
                result = corpus.inspect_one(data, source(data), Path(folder), PROFILE, recorder)
            self.assertEqual(len(result['segments']), 1)
            self.assertEqual(result['segments'][0]['hound_reason'], 'SOURCE_LIMIT')
            self.assertEqual(recorder.payloads, [])

    def test_cache_view_and_hound_identity_are_checked_after_outer_checksum(self):
        data, _ = fenced()
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            cache = base / 'segments'
            cache.mkdir()
            recorder = Recorder()
            first = corpus.inspect_one(data, source(data), base, PROFILE, recorder, cache, 'synthetic-job')
            self.assertEqual(len(recorder.payloads), 1)
            saved_path = cache / (first['segments'][0]['source_id'] + '.json')
            original = json.loads(saved_path.read_text())
            for area in ['analysis_view', 'hound', 'view_type']:
                changed = copy.deepcopy(original)
                if area == 'view_type':
                    changed['result']['segment']['analysis_view']['schema'] = True
                else:
                    key = 'analysis_sha256' if area == 'analysis_view' else 'source_sha256'
                    changed['result']['segment'][area][key] = '0' * 64
                changed['result_sha256'] = sha(canonical(changed['result']))
                saved_path.write_bytes(canonical(changed))
                with self.subTest(area=area):
                    with self.assertRaisesRegex(Blocked, '^SEGMENT_CHECKPOINT_BINDING$'):
                        corpus.inspect_one(data, source(data), base, PROFILE, recorder, cache, 'synthetic-job')
            saved_path.write_bytes(canonical(original))

    def test_omitted_partition_unit_keeps_raw_and_analysis_coordinates(self):
        code = ('import hashlib\ndef oversized(value):\n' + '    value += 1\n' * 25 +
                '    return value\ndef retained(value):\n    return hashlib.sha256(value)\n')
        data, raw = fenced(code, indent=3, newline='\r\n')
        result = inspect_container(data, 'partial.md', limits={'max_nodes': 30})
        segment = result['segments'][0]
        analysis = analysis_bytes(segment, raw)
        partition = segment['structural_partition']
        self.assertEqual([d['name'] for d in segment['definitions']], ['retained'])
        self.assertEqual(partition['units_omitted'], 1)
        self.assertFalse(partition['module_context_complete'])
        self.assertEqual(segment['definitions'][0]['structure']['declared_api_leads'], [])
        self.assertEqual(partition['source_sha256'], sha(raw))
        self.assertEqual(partition['analysis_view_sha256'], sha(canonical(segment['analysis_view'])))
        self.assertEqual(b''.join(raw[u['byte_start']:u['byte_end']] for u in partition['units']), raw)
        for unit in partition['units']:
            self.assertEqual(unit['byte_basis'], 'UTF8_SEGMENT_SOURCE')
            self.assertEqual(unit['sha256'], sha(raw[unit['byte_start']:unit['byte_end']]))
            window = unit['analysis_window']
            self.assertEqual(window['byte_basis'], 'UTF8_ANALYSIS_VIEW')
            self.assertEqual(window['module_sha256'], sha(analysis))
            self.assertEqual(window['sha256'], sha(analysis[window['byte_start']:window['byte_end']]))
        omitted = next(u for u in partition['units'] if u['status'] == 'AST_UNIT_LIMIT')
        gap = next(g for g in result['gaps'] if g['status'] == 'AST_UNIT_LIMIT')
        for key in ('byte_start', 'byte_end', 'bytes', 'sha256', 'byte_basis', 'analysis_window'):
            self.assertEqual(gap[key], omitted[key])

    def test_partition_mapping_rejects_changed_analysis_ranges_or_hashes(self):
        from forge_core.structural_units import plan
        _, raw = fenced()
        analysis, view = fence_view(raw, 2)
        metadata = plan(analysis.decode(), 60_000)['metadata']
        for key, value in [('byte_start', 1), ('end_line', 999), ('sha256', '0' * 64)]:
            changed = copy.deepcopy(metadata)
            changed['units'][0][key] = value
            with self.subTest(key=key):
                with self.assertRaisesRegex(Blocked, '^FENCE_PARTITION_BINDING$'):
                    partition_source_windows(changed, raw, analysis, view)

    def test_context_preserves_caller_and_distinct_target_views_without_aliasing(self):
        caller = ('from target import helper\ndef peer(value):\n    return value\n'
                  'def caller(value):\n    return helper(peer(value))\n')
        target = 'def helper(value):\n    return value\n'
        first, _ = fenced(caller, indent=1)
        second, _ = fenced(target, indent=3)
        data = (first.replace(b'# Synthetic example', b'# caller.py') +
                second.replace(b'# Synthetic example', b'# target.py'))
        with tempfile.TemporaryDirectory() as folder:
            result = corpus.inspect_one(data, source(data), Path(folder), PROFILE, Recorder())
        report = {'schema': corpus.SCHEMA, 'sources': [result], 'upstream_code_executed': False,
                  'source_bodies_retained': False}
        first_segment, target_segment = result['segments']
        definition = next(d for d in first_segment['definitions'] if d['name'] == 'caller')
        context = corpus.dependency_context(report, definition['definition_id'])
        self.assertEqual(context['analysis_view'], first_segment['analysis_view'])
        references = {r['call']: r for r in context['references']}
        self.assertEqual(references['helper']['status'], 'DECLARED_IMPORT_CANDIDATE')
        for call, segment in [('helper', target_segment), ('peer', first_segment)]:
            candidate = references[call]['candidates'][0]
            self.assertEqual(candidate['analysis_view'], segment['analysis_view'])
            self.assertEqual(candidate['source_sha256'], segment['source_sha256'])
            candidate['analysis_view']['policy'] = 'caller-mutation'
            self.assertEqual(segment['analysis_view']['policy'], 'commonmark-fence-indent-1')
        context['analysis_view']['policy'] = 'caller-mutation'
        self.assertEqual(first_segment['analysis_view']['policy'], 'commonmark-fence-indent-1')


class FenceViewIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        cls.target = cls.base / 'source'
        cls.target.mkdir()
        cls.expected = {}
        rows = []
        for indent in range(4):
            data, body = fenced(indent=indent, newline='\r\n')
            name = 'notes%d.md' % indent
            (cls.target / name).write_bytes(data)
            cls.expected[name] = (data, body)
            rows.append(source(data, 'file%d' % indent, name))
        cls.manifest = cls.base / 'manifest.json'
        cls.manifest.write_bytes(canonical({'schema': 1, 'files': rows}))
        cls.workbench = initialize(cls.base / 'home')
        cls.out = cls.workbench.home / 'corpora' / 'fence'
        cls.report = corpus.run(cls.workbench, cls.target, cls.manifest, cls.out, PROFILE, max_containers=500)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_actual_hound_observations_bind_analysis_bytes_and_raw_source(self):
        self.assertEqual(self.report['status'], 'COMPLETED_FOR_SELECTED_CONTAINERS')
        self.assertEqual(self.report['summary']['hound_observations'], 4)
        self.assertEqual(self.report['summary']['source_roundtrips_verified'], 4)
        for row in self.report['sources']:
            data, raw = self.expected[row['source']['path']]
            segment = row['segments'][0]
            expected = analysis_bytes(segment, raw)
            self.assertEqual(segment['source_sha256'], sha(raw))
            self.assertEqual(segment['hound']['source_sha256'], sha(expected))
            self.assertFalse(segment['hound']['upstream_code_executed'])
            finding = segment['hound']['findings'][0]
            self.assertEqual(finding['resolved_api'], 'hashlib.sha256')
            self.assertEqual(finding['source_sha256'], sha(expected))
            self.assertEqual((self.target / row['source']['path']).read_bytes(), data)

    def test_search_exposes_analysis_binding_without_persisting_bodies(self):
        matches = corpus.search(self.report, 'digest')['results']
        self.assertEqual(len(matches), 4)
        for row in matches:
            self.assertIn('hashlib.sha256', row['observed_apis'])
            if row['path'] != 'notes0.md':
                self.assertEqual(row['analysis_view']['source_sha256'], row['source_sha256'])
                self.assertEqual(row['analysis_view']['column_basis'], 'ANALYSIS_VIEW_UTF8')
        saved = (self.out / 'corpus.json').read_text()
        self.assertNotIn('source_hex', saved)
        self.assertNotIn('INSPECTED_SOURCE_MUST_NOT_RUN', saved)
        self.assertNotIn('analysis_source_hex', saved)

    def test_reacquisition_returns_exact_raw_decorator_window(self):
        for row in self.report['sources']:
            segment = row['segments'][0]
            definition = segment['definitions'][0]
            inspection = inspect_definition(self.report, definition['definition_id'], self.target)
            _, raw = self.expected[row['source']['path']]
            expected = b''.join(raw.splitlines(keepends=True)[2:])
            self.assertEqual(inspection['source_text'].encode(), expected)
            self.assertEqual(inspection['source_sha256'], sha(raw))
            self.assertEqual(inspection.get('analysis_view'), segment.get('analysis_view'))
            self.assertEqual(inspection['start_line'], 5)
            self.assertFalse(inspection['source_code_executed'])

    def test_reacquisition_rejects_tampered_analysis_view(self):
        for mutation in ['policy', 'remove', 'type']:
            changed = copy.deepcopy(self.report)
            row = next(r for r in changed['sources'] if r['source']['path'] == 'notes2.md')
            segment = row['segments'][0]
            if mutation == 'remove':
                segment.pop('analysis_view')
            elif mutation == 'type':
                segment['analysis_view']['schema'] = True
            else:
                segment['analysis_view']['policy'] = 'different-policy'
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(Blocked, '^DEFINITION_SOURCE_BINDING$'):
                    inspect_definition(changed, segment['definitions'][0]['definition_id'], self.target)

    def test_completed_intake_reuses_checkpoints_without_parser_or_analyzer(self):
        with patch.object(corpus, '_parser', side_effect=AssertionError('Unexpected reparse')):
            again = corpus.run(self.workbench, self.target, self.manifest, self.out, PROFILE, max_containers=500)
        self.assertEqual(again['cache'], {'new_containers': 0, 'reused_containers': 4})
        self.assertEqual(again['summary'], self.report['summary'])


class PartitionedFenceIntegrationTests(unittest.TestCase):
    def test_large_fence_keeps_original_units_windows_and_unchanged_hound_limit(self):
        first = '@(\n marker("é")\n)\ndef first(value):\n    #' + 'a' * 130_000 + '\n    return second(value)\n'
        second = 'def second(value):\n    #' + 'b' * 130_000 + '\n    return hashlib.sha256(value)\n'
        code = 'import hashlib\n' + first + second
        data, raw = fenced(code, indent=3, newline='\r\n')
        expected = {'first': fenced(first, indent=3, newline='\r\n')[1],
                    'second': fenced(second, indent=3, newline='\r\n')[1]}
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            target = base / 'source'
            target.mkdir()
            (target / 'large.md').write_bytes(data)
            manifest = base / 'manifest.json'
            manifest.write_bytes(canonical({'schema': 1, 'files': [source(data, path='large.md')]}))
            workbench = initialize(base / 'home')
            out = workbench.home / 'corpora' / 'large-fence'
            report = corpus.run(workbench, target, manifest, out, PROFILE)
            segment = report['sources'][0]['segments'][0]
            analysis = analysis_bytes(segment, raw)
            self.assertGreater(min(len(raw), len(analysis)), 250_000)
            self.assertEqual(report['status'], 'COMPLETED_WITH_GAPS')
            self.assertEqual(segment['hound_reason'], 'SOURCE_LIMIT')
            self.assertEqual(segment['hound'], {})
            self.assertEqual(report['summary']['hound_observations'], 0)
            self.assertEqual(report['summary']['source_roundtrips_verified'], 1)
            partition = segment['structural_partition']
            self.assertEqual((partition['units_analyzed'], partition['units_omitted']), (3, 0))
            self.assertTrue(partition['module_context_complete'])
            self.assertEqual(partition['byte_basis'], 'UTF8_SEGMENT_SOURCE')
            self.assertEqual(partition['source_sha256'], sha(raw))
            self.assertEqual(partition['analysis_view_sha256'], sha(canonical(segment['analysis_view'])))
            raw_pieces, analysis_pieces = [], []
            for unit in partition['units']:
                original = raw[unit['byte_start']:unit['byte_end']]
                window = unit['analysis_window']
                interpreted = analysis[window['byte_start']:window['byte_end']]
                self.assertEqual((len(original), sha(original)), (unit['bytes'], unit['sha256']))
                self.assertEqual((len(interpreted), sha(interpreted)), (window['bytes'], window['sha256']))
                self.assertEqual(unit['byte_basis'], 'UTF8_SEGMENT_SOURCE')
                self.assertEqual(window['byte_basis'], 'UTF8_ANALYSIS_VIEW')
                self.assertEqual(window['module_sha256'], sha(analysis))
                raw_pieces.append(original)
                analysis_pieces.append(interpreted)
            self.assertEqual(b''.join(raw_pieces), raw)
            self.assertEqual(b''.join(analysis_pieces), analysis)
            for definition in segment['definitions']:
                window = definition['source_window']
                self.assertEqual(raw[window['byte_start']:window['byte_end']], expected[definition['name']])
                inspected = inspect_definition(report, definition['definition_id'], target)
                self.assertEqual(inspected['source_text'].encode(), expected[definition['name']])
                self.assertEqual(inspected['analysis_view'], segment['analysis_view'])
                self.assertEqual(inspected['context']['analysis_view'], segment['analysis_view'])
            rows = corpus.search(report, 'first second')['results']
            self.assertTrue(rows)
            self.assertTrue(all(r['structural_partition_status'] == 'COMPLETE' for r in rows))
            self.assertTrue(all(r['analysis_view'] == segment['analysis_view'] for r in rows))
            with patch.object(corpus, '_parser', side_effect=AssertionError('Unexpected reparse')):
                again = corpus.run(workbench, target, manifest, out, PROFILE)
            self.assertEqual(again['cache'], {'new_containers': 0, 'reused_containers': 1})
            self.assertEqual((target / 'large.md').read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
