"""Context and source-window contracts for bounded large-module intake.

The fixtures are synthetic source; none is imported or executed. Expected
definition bodies and relationships are specified separately from the reader.
"""
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from forge_core import corpus
from forge_core.common import Blocked, canonical, sha
from forge_core.mixed_intake import DEFAULTS, inspect_container


MODULE = '''import hashlib as digest_lib
SCALE = 2

def decorate(fn):
    return fn

@decorate
async def decode_record(
    payload: bytes,
    *,
    scale: int = SCALE,
) -> bytes:
    return digest_lib.sha256(payload).digest()

def outer(value):
    def inner(other):
        return helper(other) + value
    return inner(value)

class Codec:
    @staticmethod
    def encode(value):
        return helper(value)

def helper(value):
    return value * SCALE
'''
NAMES = ['decorate', 'decode_record', 'outer', 'outer.inner', 'Codec.encode', 'helper']


def padded(source=MODULE, minimum=250_001):
    """Append comments without changing existing definition positions."""
    data = source.encode('utf-8') if isinstance(source, str) else source
    if len(data) < minimum:
        data += b'#' + b'.' * (minimum - len(data) - 2) + b'\n'
    return data


def stored_archive(name, data):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_STORED) as archive:
        archive.writestr(name, data)
    return out.getvalue()


class GuardlessWorkbench:
    def __init__(self, home):
        self.home = home
        home.mkdir()

    def guard(self):
        pass


class RecordingAnalyzer:
    def __init__(self):
        self.sizes = []

    def scan(self, data, source_id, profile):
        self.sizes.append(len(data))
        if len(data) > 250_000:
            raise AssertionError('Large source was sent to the API analyzer')
        return {'source_id': source_id, 'source_sha256': sha(data),
                'upstream_code_executed': False, 'findings': []}


class LargeModuleIntakeTests(unittest.TestCase):
    def segment(self, data, **kwargs):
        result = inspect_container(data, 'module.py', **kwargs)
        self.assertEqual(len(result['segments']), 1, result['inventory'])
        return result, result['segments'][0]

    def test_large_file_retains_one_module_and_all_definitions(self):
        data = padded()
        result, segment = self.segment(data)
        self.assertTrue(segment['large_module'])
        self.assertEqual(segment['source_bytes'], len(data))
        self.assertEqual(segment['source_sha256'], sha(data))
        self.assertEqual([d['name'] for d in segment['definitions']], NAMES)
        self.assertEqual(segment['imports'], ['hashlib'])
        self.assertFalse(result['upstream_code_executed'])
        self.assertEqual(segment['module_structure']['imports'][0]['alias'], 'digest_lib')
        gaps = [g for g in result['gaps'] if g['status'] == 'SOURCE_SIZE_LIMIT']
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]['stage'], 'api_analysis')

    def test_many_small_definitions_have_no_missing_or_duplicate_windows(self):
        bodies = ['def operation_%03d(value):\n    """%s"""\n    return value + %d\n' %
                  (index, 'bounded operation ' + 'x' * 800, index) for index in range(320)]
        data = '\n'.join(bodies).encode()
        self.assertGreater(len(data), 250_000)
        segment = self.segment(data)[1]
        self.assertEqual([d['name'] for d in segment['definitions']],
                         ['operation_%03d' % index for index in range(320)])
        for definition, body in zip(segment['definitions'], bodies):
            window = definition['source_window']
            self.assertEqual(window['status'], 'AVAILABLE')
            self.assertEqual(data[window['byte_start']:window['byte_end']], body.encode())

    def test_analyzer_boundary_does_not_change_structure(self):
        low = self.segment(padded(minimum=250_000))[1]
        high = self.segment(padded(minimum=250_001))[1]
        self.assertFalse(low['large_module'])
        self.assertTrue(high['large_module'])
        self.assertEqual(low['module_structure'], high['module_structure'])
        for small, large in zip(low['definitions'], high['definitions']):
            for key in ('name', 'start_line', 'definition_line', 'end_line',
                        'parameters', 'structure'):
                self.assertEqual(small[key], large[key], key)

    def test_forced_large_path_matches_complete_small_module_reference(self):
        data = MODULE.encode()
        ordinary = self.segment(data)[1]
        bounded = self.segment(data, limits={'max_source_bytes': 200})[1]
        self.assertFalse(ordinary['large_module'])
        self.assertTrue(bounded['large_module'])
        self.assertEqual(ordinary['module_structure'], bounded['module_structure'])
        self.assertEqual(ordinary['source_sha256'], bounded['source_sha256'])
        self.assertEqual(ordinary['definitions'], bounded['definitions'])

    def test_complete_decorated_async_and_nested_definition_windows(self):
        data = padded()
        segment = self.segment(data)[1]
        expected = {
            'decode_record': '@decorate\nasync def decode_record(\n'
                             '    payload: bytes,\n    *,\n    scale: int = SCALE,\n'
                             ') -> bytes:\n    return digest_lib.sha256(payload).digest()\n',
            'outer.inner': '    def inner(other):\n        return helper(other) + value\n',
            'Codec.encode': '    @staticmethod\n    def encode(value):\n        return helper(value)\n',
        }
        for definition in segment['definitions']:
            window = definition['source_window']
            self.assertEqual(window['module_sha256'], sha(data))
            self.assertEqual(window['byte_basis'], 'UTF8_SEGMENT_SOURCE')
            self.assertTrue(window['complete'])
            self.assertEqual(window['status'], 'AVAILABLE')
            selected = data[window['byte_start']:window['byte_end']]
            self.assertEqual(window['bytes'], len(selected))
            self.assertEqual(window['sha256'], sha(selected))
            if definition['name'] in expected:
                self.assertEqual(selected, expected[definition['name']].encode())

    def test_utf8_and_crlf_windows_reproduce_original_bytes(self):
        body = 'def café(value="é"):\r\n    return "雪" + value\r\n'
        data = padded(('# π\r\n' + body).encode())
        segment = self.segment(data)[1]
        definition = segment['definitions'][0]
        self.assertEqual((definition['start_line'], definition['end_line']), (2, 3))
        window = definition['source_window']
        self.assertEqual(data[window['byte_start']:window['byte_end']], body.encode())
        self.assertEqual(window['sha256'], sha(body.encode()))

    def test_literal_unicode_separator_is_not_a_python_source_line_break(self):
        body = 'def value():\n    return "left\u2028right"\n'
        data = padded(('PREFIX = "before\u2028after"\n' + body).encode())
        definition = self.segment(data)[1]['definitions'][0]
        self.assertEqual((definition['start_line'], definition['end_line']), (2, 3))
        window = definition['source_window']
        self.assertEqual(data[window['byte_start']:window['byte_end']], body.encode())

    def test_oversized_definition_remains_in_inventory_without_truncation(self):
        huge = 'def huge():\n    value = "' + 'x' * 250_001 + '"\n    return value\n'
        data = (huge + '\ndef small():\n    return 7\n').encode()
        result, segment = self.segment(data)
        self.assertEqual([d['name'] for d in segment['definitions']], ['huge', 'small'])
        first, second = [d['source_window'] for d in segment['definitions']]
        self.assertEqual(first['status'], 'DEFINITION_SIZE_LIMIT')
        self.assertEqual(first['bytes'], len(huge.encode()))
        self.assertEqual(first['sha256'], sha(huge.encode()))
        self.assertTrue(first['complete'])
        self.assertEqual(second['status'], 'AVAILABLE')
        self.assertIn('DEFINITION_SIZE_LIMIT', {g['status'] for g in result['gaps']})

    def test_structural_module_limit_does_not_publish_partial_definitions(self):
        result = inspect_container(padded(minimum=2_000_001), 'module.py')
        self.assertFalse(result['segments'])
        self.assertIn('MODULE_SIZE_LIMIT', {g['status'] for g in result['gaps']})

    def test_node_limit_still_applies_to_large_modules(self):
        result = inspect_container(padded(), 'module.py', limits={'max_nodes': 10})
        self.assertFalse(result['segments'])
        self.assertIn('AST_LIMIT', {g['status'] for g in result['gaps']})

    def test_syntax_error_at_end_does_not_publish_valid_prefix(self):
        result = inspect_container(padded() + b'def broken(:\n', 'module.py')
        self.assertFalse(result['segments'])
        self.assertIn('PYTHON_SYNTAX_ERROR', {g['status'] for g in result['gaps']})

    def test_large_archive_member_keeps_original_member_identity(self):
        data = padded()
        result = inspect_container(stored_archive('pkg/module.py', data), 'source.zip')
        self.assertEqual(len(result['segments']), 1)
        segment = result['segments'][0]
        self.assertEqual(segment['member_chain'], [{'path': 'pkg/module.py', 'sha256': sha(data)}])
        self.assertEqual(segment['source_sha256'], sha(data))
        self.assertTrue(segment['large_module'])

    def test_module_bound_does_not_relax_member_or_container_limit(self):
        data = padded()
        archive = inspect_container(stored_archive('module.py', data), 'source.zip',
                                    limits={'max_member_bytes': 250_000})
        self.assertFalse(archive['segments'])
        self.assertIn('MEMBER_SIZE_LIMIT', {g['status'] for g in archive['gaps']})
        container = inspect_container(data, 'module.py', limits={'max_container_bytes': 250_000})
        self.assertFalse(container['segments'])
        self.assertIn('CONTAINER_SIZE_LIMIT', {g['status'] for g in container['gaps']})

    def test_new_limit_cannot_exceed_declared_maximum(self):
        self.assertEqual(DEFAULTS['max_module_bytes'], 2_000_000)
        self.assertEqual(DEFAULTS['max_source_bytes'], 250_000)
        for value in (True, 0, 2_000_001):
            with self.subTest(value=value), self.assertRaises(Blocked):
                inspect_container(b'def f(): pass\n', 'module.py', limits={'max_module_bytes': value})


class LargeModuleCorpusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_root = self.root / 'inputs'
        self.source_root.mkdir()
        self.workbench = GuardlessWorkbench(self.root / 'work')
        self.manifest = self.root / 'manifest.json'
        self.analyzer = RecordingAnalyzer()
        self.data = padded()
        self.write_input(self.data)

    def tearDown(self):
        self.temp.cleanup()

    def write_input(self, data, name='module.py'):
        self.name = name
        self.input = self.source_root / name
        self.input.write_bytes(data)
        self.manifest.write_bytes(canonical({'schema': 1, 'files': [
            {'provider': 'synthetic', 'file_id': 'large-module', 'path': name,
             'size': len(data), 'sha256': sha(data), 'source_url': 'fixture://large-module'}]}))

    def runit(self, name='run'):
        return corpus.run(self.workbench, self.source_root, self.manifest,
                          self.workbench.home / 'corpora' / name, analyzer=self.analyzer)

    def row(self, report, name):
        return next(row for row in corpus.rows(report) if row['name'] == name)

    def test_large_module_is_searchable_with_explicit_api_omission(self):
        report = self.runit()
        self.assertEqual(self.analyzer.sizes, [])
        self.assertEqual(report['status'], 'COMPLETED_WITH_GAPS')
        self.assertEqual(report['summary']['definitions'], len(NAMES))
        self.assertEqual(report['summary']['hound_segments'], 0)
        self.assertEqual(report['summary']['hound_observations'], 0)
        segment = report['sources'][0]['segments'][0]
        self.assertEqual(segment['hound_status'], 'BLOCKED')
        self.assertEqual(segment['hound_reason'], 'SOURCE_LIMIT')
        self.assertEqual(corpus.search(report, 'decode_record')['results'][0]['name'], 'decode_record')

    def test_nested_and_module_helper_relationships_survive_large_intake(self):
        report = self.runit()
        expected = {'outer': 'outer.inner', 'outer.inner': 'helper', 'Codec.encode': 'helper'}
        for name, target in expected.items():
            context = corpus.dependency_context(report, self.row(report, name)['definition_id'])
            candidates = [c['name'] for ref in context['references'] for c in ref['candidates']]
            self.assertIn(target, candidates)
            self.assertFalse(context['dependency_closure_complete'])

    def test_repeat_reuses_large_module_checkpoint_without_parser_or_analyzer(self):
        first = self.runit()
        with patch.object(corpus, '_parser', side_effect=AssertionError('Unexpected reparse')):
            second = self.runit()
        self.assertEqual(second['cache'], {'reused_containers': 1, 'new_containers': 0})
        self.assertEqual(first['sources'], second['sources'])
        self.assertEqual(second['status'], 'COMPLETED_WITH_GAPS')
        self.assertEqual(self.analyzer.sizes, [])

    def test_module_context_change_invalidates_definition_identity(self):
        first = self.runit()
        old = self.row(first, 'helper')
        self.write_input(self.data.replace(b'SCALE = 2', b'SCALE = 3'))
        second = self.runit('changed')
        new = self.row(second, 'helper')
        self.assertNotEqual(old['definition_id'], new['definition_id'])
        self.assertNotEqual(old['source_sha256'], new['source_sha256'])
        old_definition = next(d for d in first['sources'][0]['segments'][0]['definitions'] if d['name'] == 'helper')
        new_definition = next(d for d in second['sources'][0]['segments'][0]['definitions'] if d['name'] == 'helper')
        self.assertEqual(old_definition['source_window']['sha256'], new_definition['source_window']['sha256'])
        self.assertNotEqual(old_definition['source_window']['module_sha256'], new_definition['source_window']['module_sha256'])

    def test_source_unchanged_and_source_bodies_not_persisted(self):
        before = (self.input.read_bytes(), self.input.stat().st_mtime_ns)
        report = self.runit()
        self.assertEqual((self.input.read_bytes(), self.input.stat().st_mtime_ns), before)
        self.assertFalse(report['source_bodies_retained'])
        self.assertNotIn('source_hex', json.dumps(report))
        self.assertNotIn('return value * SCALE', json.dumps(report))
        for path in (self.workbench.home / 'corpora' / 'run').rglob('*.json'):
            self.assertNotIn('source_hex', path.read_text())

    def test_large_source_is_never_imported_or_executed(self):
        self.write_input(padded('raise AssertionError("LARGE_SOURCE_MUST_NOT_EXECUTE")\n' + MODULE))
        report = self.runit()
        self.assertEqual(report['summary']['definitions'], len(NAMES))
        self.assertEqual(report['status'], 'COMPLETED_WITH_GAPS')
        self.assertFalse(report['upstream_code_executed'])
        self.assertEqual(self.analyzer.sizes, [])

    def test_inspection_returns_exact_complete_definition_with_context(self):
        report = self.runit()
        definition = self.row(report, 'outer.inner')
        result = corpus.inspect_definition(report, definition['definition_id'], self.source_root)
        self.assertEqual(result['status'], 'COMPLETE_DEFINITION_SOURCE')
        self.assertEqual(result['source_text'], '    def inner(other):\n        return helper(other) + value\n')
        self.assertEqual(result['source_sha256'], sha(self.data))
        self.assertEqual(result['container_sha256'], sha(self.data))
        self.assertFalse(result['source_code_executed'])
        self.assertFalse(result['dependency_closure_complete'])
        self.assertIn('helper', [c['name'] for r in result['context']['references'] for c in r['candidates']])

    def test_inspection_refuses_changed_original_module_even_when_body_same(self):
        report = self.runit()
        definition = self.row(report, 'helper')
        self.input.write_bytes(self.data.replace(b'SCALE = 2', b'SCALE = 9'))
        with self.assertRaisesRegex(Blocked, '^SOURCE_VERSION_MISMATCH$'):
            corpus.inspect_definition(report, definition['definition_id'], self.source_root)

    def test_inspection_byte_budget_does_not_return_partial_source(self):
        report = self.runit()
        definition = self.row(report, 'decode_record')
        with self.assertRaisesRegex(Blocked, '^DEFINITION_SIZE_LIMIT$'):
            corpus.inspect_definition(report, definition['definition_id'], self.source_root, max_bytes=10)

    def test_inspection_budget_rejects_boolean_and_out_of_bounds_values(self):
        report = self.runit()
        definition_id = self.row(report, 'helper')['definition_id']
        for value in (True, False, 0, -1, 250_001, 2.5):
            with self.subTest(value=value), self.assertRaises(Blocked):
                corpus.inspect_definition(report, definition_id, self.source_root, max_bytes=value)

    def test_inspection_rejects_rehashed_but_wrong_definition_window(self):
        report = self.runit()
        altered = copy.deepcopy(report)
        definition = next(d for d in altered['sources'][0]['segments'][0]['definitions']
                          if d['name'] == 'helper')
        window = definition['source_window']
        prefix = b'import hashlib as digest_lib\n'
        window.update(byte_start=0, byte_end=len(prefix), bytes=len(prefix), sha256=sha(prefix))
        with self.assertRaisesRegex(Blocked, '^DEFINITION_SOURCE_BINDING$'):
            corpus.inspect_definition(altered, definition['definition_id'], self.source_root)

    def test_oversized_definition_inspection_refused_but_small_peer_available(self):
        huge = b'def huge():\n    return "' + b'x' * 250_001 + b'"\n\ndef small():\n    return 7\n'
        self.write_input(huge)
        report = self.runit()
        with self.assertRaisesRegex(Blocked, '^DEFINITION_SIZE_LIMIT$'):
            corpus.inspect_definition(report, self.row(report, 'huge')['definition_id'], self.source_root)
        small = corpus.inspect_definition(report, self.row(report, 'small')['definition_id'], self.source_root)
        self.assertEqual(small['source_text'], 'def small():\n    return 7\n')

    def test_inspection_reacquires_exact_archive_member(self):
        self.write_input(stored_archive('pkg/module.py', self.data), 'source.zip')
        report = self.runit()
        result = corpus.inspect_definition(report, self.row(report, 'helper')['definition_id'], self.source_root)
        self.assertEqual(result['source_sha256'], sha(self.data))
        self.assertEqual(result['container_sha256'], sha(self.input.read_bytes()))
        self.assertEqual(result['source_text'], 'def helper(value):\n    return value * SCALE\n')

    def test_legacy_definition_requires_reindex_for_source_inspection(self):
        report = self.runit()
        legacy = copy.deepcopy(report)
        definition = legacy['sources'][0]['segments'][0]['definitions'][0]
        definition.pop('source_window')
        with self.assertRaisesRegex(Blocked, '^DEFINITION_REINDEX_REQUIRED$'):
            corpus.inspect_definition(legacy, definition['definition_id'], self.source_root)

    def test_definition_coverage_retains_oversized_occurrence_in_denominator(self):
        data = b'def huge():\n    return "' + b'x' * 250_001 + b'"\ndef small():\n    return 7\n'
        self.write_input(data)
        coverage = self.runit()['coverage']['definitions']
        self.assertEqual(coverage['discovered'], 2)
        self.assertEqual(coverage['inspection_available'], 1)
        self.assertEqual(coverage['inspection_omitted'], 1)
        self.assertEqual(coverage['api_analyzed'], 0)
        self.assertEqual(coverage['large_modules'], 1)
        self.assertTrue(coverage['discovery_complete'])
        self.assertTrue(coverage['omitted_definition_count_known'])

    def test_rejected_module_does_not_claim_zero_omitted_definitions(self):
        self.write_input(padded(minimum=2_000_001))
        report = self.runit()
        coverage = report['coverage']['definitions']
        self.assertEqual(report['status'], 'COMPLETED_WITH_GAPS')
        self.assertEqual(coverage['discovered'], 0)
        self.assertFalse(coverage['discovery_complete'])
        self.assertFalse(coverage['omitted_definition_count_known'])

    def test_inspection_cli_rejects_workspace_overlap_before_any_writes(self):
        before = {p.relative_to(self.root).as_posix(): sha(p.read_bytes())
                  for p in self.root.rglob('*') if p.is_file()}
        before_dirs = {p.relative_to(self.root).as_posix()
                       for p in self.root.rglob('*') if p.is_dir()}
        for home in (self.source_root / 'new-workspace', self.source_root, self.root):
            with self.subTest(home=home.name):
                process = subprocess.run(
                    [sys.executable, '-B', str(corpus.ROOT / 'forge.py'), '--home', str(home),
                     'corpus-inspect', '--source-root', str(self.source_root),
                     '--run-id', 'missing', '--definition', 'definition_' + 'a' * 64],
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                self.assertEqual(json.loads(process.stdout)['reason'], 'CORPUS_SEPARATE_OUTPUT')
                self.assertEqual({p.relative_to(self.root).as_posix(): sha(p.read_bytes())
                                  for p in self.root.rglob('*') if p.is_file()}, before)
                self.assertEqual({p.relative_to(self.root).as_posix()
                                  for p in self.root.rglob('*') if p.is_dir()}, before_dirs)


if __name__ == '__main__':
    unittest.main()
