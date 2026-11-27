"""Rehashed local caches must retain typed, mutually consistent coverage."""
from checkpoint_helpers import cache_files, cache_read, cache_write
import copy
import io
import json
import unittest
import zipfile
from unittest.mock import patch

from forge_core import corpus, mixed_intake
from forge_core.common import canonical, sha
import test_corpus as fixture


class CheckpointConsistencyTests(unittest.TestCase):
    setUp = fixture.CorpusTests.setUp
    tearDown = fixture.CorpusTests.tearDown
    make = fixture.CorpusTests.make
    runit = fixture.CorpusTests.runit

    def prepare(self, name='broken.py', data=b'def broken(:\n'):
        self.files = [(name, data)]
        self.make()
        first = self.runit()
        self.checkpoint = next(cache_files(self.out / 'checkpoints'))
        self.saved = cache_read(self.checkpoint)
        self.original_source = (self.src / name).read_bytes()
        return first

    def mutate(self, change):
        saved = copy.deepcopy(self.saved)
        change(saved['result'])
        saved['result_sha256'] = sha(canonical(saved['result']))
        cache_write(self.checkpoint,saved)

    def blocked(self, reason):
        with patch.object(corpus, '_parser', side_effect=AssertionError('Cache must be rejected before parsing')):
            report = self.runit()
        self.assertEqual(report['status'], 'INCOMPLETE')
        self.assertFalse(report['coverage']['definitions']['discovery_complete'])
        self.assertEqual(report['coverage']['blocked'], [{'file_id': 'fixture0', 'reason': reason}])
        self.assertEqual((self.src / self.files[0][0]).read_bytes(), self.original_source)
        self.assertEqual(json.loads((self.out / 'corpus.json').read_bytes())['status'], 'INCOMPLETE')

    def test_null_inventory_is_controlled_refusal(self):
        self.prepare()
        self.mutate(lambda r: r.update(inventory=None))
        self.blocked('CORPUS_CHECKPOINT_INVENTORY')

    def test_scalar_inventory_row_is_controlled_refusal(self):
        self.prepare()
        self.mutate(lambda r: r.update(inventory=[1]))
        self.blocked('CORPUS_CHECKPOINT_INVENTORY')

    def test_null_gaps_is_controlled_refusal(self):
        self.prepare()
        self.mutate(lambda r: r.update(gaps=None))
        self.blocked('CORPUS_CHECKPOINT_GAPS')

    def test_scalar_gap_row_is_controlled_refusal(self):
        self.prepare()
        self.mutate(lambda r: r.update(gaps=[1]))
        self.blocked('CORPUS_CHECKPOINT_GAPS')

    def test_removed_python_syntax_gap_cannot_complete(self):
        self.assertEqual(self.prepare()['status'], 'COMPLETED_WITH_GAPS')
        self.mutate(lambda r: r.update(gaps=[]))
        self.blocked('CORPUS_CHECKPOINT_GAP_MISMATCH')

    def test_removed_fence_syntax_gap_cannot_complete(self):
        self.prepare('notes.md', b'```python\ndef broken(:\n```\n')
        self.mutate(lambda r: r.update(gaps=[]))
        self.blocked('CORPUS_CHECKPOINT_GAP_MISMATCH')

    def test_removed_python_notebook_syntax_gap_cannot_complete(self):
        self.prepare('notes.ipynb', canonical({'metadata': {'kernelspec': {'language': 'python'}},
            'cells': [{'cell_type': 'code', 'source': 'def broken(:\n'}]}))
        self.assertEqual(self.saved['result']['gaps'][0]['status'], 'PYTHON_SYNTAX_ERROR')
        self.mutate(lambda r: r.update(gaps=[]))
        self.blocked('CORPUS_CHECKPOINT_GAP_MISMATCH')

    def test_removed_inventory_and_gaps_cannot_erase_intake_failure(self):
        self.prepare('notes.ipynb', canonical({'metadata': {'kernelspec': {'language': 'python'}},
            'cells': [{'cell_type': 'code', 'source': 'def broken(:\n'}]}))
        self.mutate(lambda r: r.update(inventory=[], gaps=[]))
        self.blocked('CORPUS_CHECKPOINT_INTAKE_MISMATCH')

    def test_removed_intake_status_is_not_assumed_complete(self):
        self.prepare()
        self.mutate(lambda r: r.pop('intake_status'))
        self.blocked('CORPUS_CHECKPOINT_INTAKE_MISMATCH')

    def test_changed_inventory_status_does_not_hide_gap(self):
        self.prepare()
        self.mutate(lambda r: r['inventory'][0].update(status='CONTEXT_ONLY'))
        self.blocked('CORPUS_CHECKPOINT_GAP_MISMATCH')

    def test_unrecognized_inventory_status_is_refused(self):
        self.prepare()
        self.mutate(lambda r: r['inventory'][0].update(status='UNDECLARED_STATE'))
        self.blocked('CORPUS_CHECKPOINT_INVENTORY')

    def test_missing_inventory_for_retained_segment_is_refused(self):
        self.prepare('valid.py', b'def keep():\n    return 1\n')
        self.mutate(lambda r: r.update(inventory=[]))
        self.blocked('CORPUS_CHECKPOINT_INVENTORY_MISMATCH')

    def test_missing_segment_for_inventory_is_refused(self):
        self.prepare('valid.py', b'def keep():\n    return 1\n')
        self.mutate(lambda r: r.update(segments=[]))
        self.blocked('CORPUS_CHECKPOINT_INVENTORY_MISMATCH')

    def test_segment_collections_and_consumed_fields_are_typed(self):
        self.prepare('valid.py', b'def keep():\n    return 1\n')
        mutations = {
            'null_segments': lambda r: r.update(segments=None),
            'scalar_segment': lambda r: r.update(segments=[1]),
            'null_definitions': lambda r: r['segments'][0].update(definitions=None),
            'scalar_definition': lambda r: r['segments'][0].update(definitions=[1]),
            'null_hound': lambda r: r['segments'][0].update(hound=None),
            'null_findings': lambda r: r['segments'][0]['hound'].update(findings=None),
            'null_structure': lambda r: r['segments'][0].update(module_structure=None),
            'null_analysis_view': lambda r: r['segments'][0].update(analysis_view=None),
            'null_partition': lambda r: r['segments'][0].update(structural_partition=None),
            'missing_cell_index': lambda r: r['segments'][0].pop('cell_index'),
            'missing_logical_path': lambda r: r['segments'][0].pop('logical_path_hint'),
            'null_class_context_line': lambda r: r['segments'][0]['definitions'][0]['structure'].update(
                class_context={'start_line': 'wrong'}),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):
                self.mutate(mutation)
                self.blocked('CORPUS_CHECKPOINT_SEGMENTS')

    def test_source_reconstruction_is_typed_before_summary(self):
        self.prepare()
        self.mutate(lambda r: r.update(source_reconstruction=None))
        self.blocked('CORPUS_CHECKPOINT_SOURCE')

    def test_segment_checkpoint_is_validated_when_aggregate_is_missing(self):
        self.prepare('valid.py', b'def keep():\n    return 1\n')
        self.checkpoint.unlink()
        checkpoint = next(cache_files(self.out / 'segments'))
        saved = cache_read(checkpoint)
        saved['result']['segment'] = None
        saved['result_sha256'] = sha(canonical(saved['result']))
        cache_write(checkpoint,saved)
        before = self.a.calls
        report = self.runit()
        self.assertEqual(report['coverage']['blocked'][0]['reason'], 'SEGMENT_CHECKPOINT_BINDING')
        self.assertEqual(self.a.calls, before)

    def test_valid_syntax_failure_replays_without_parser_or_analyzer(self):
        first = self.prepare()
        with patch.object(corpus, '_parser', side_effect=AssertionError('Unexpected parser replay')):
            report = self.runit(max_containers=0)
        self.assertEqual(report['gaps'], first['gaps'])
        self.assertEqual(report['status'], 'COMPLETED_WITH_GAPS')
        self.assertEqual(report['cache'], {'reused_containers': 1, 'new_containers': 0})
        self.assertEqual(self.a.calls, 0)

    def test_empty_archive_replays_with_empty_inventory(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w'):
            pass
        self.prepare('empty.zip', data.getvalue())
        self.assertEqual(self.saved['result']['inventory'], [])
        with patch.object(corpus, '_parser', side_effect=AssertionError('Unexpected parser replay')):
            report = self.runit(max_containers=0)
        self.assertEqual(report['status'], 'COMPLETED_FOR_SELECTED_CONTAINERS')

    def test_empty_python_notebook_replays_with_empty_inventory(self):
        self.prepare('empty.ipynb', canonical({'metadata': {'language_info': {'name': 'python'}}, 'cells': []}))
        self.assertEqual(self.saved['result']['inventory'], [])
        with patch.object(corpus, '_parser', side_effect=AssertionError('Unexpected parser replay')):
            report = self.runit(max_containers=0)
        self.assertEqual(report['status'], 'COMPLETED_FOR_SELECTED_CONTAINERS')

    def test_maximum_container_replay_performs_no_new_analysis(self):
        self.files = [(f'file_{i:03}.py', b'def keep():\n    return 1\n') for i in range(500)]
        self.make()
        # Worker isolation has separate tests. Parse actual synthetic bytes here
        # without starting 500 subprocesses for the cache cardinality check.
        with patch.object(corpus, '_parser', side_effect=lambda data, name, home:
                          mixed_intake.inspect_container(data, name)):
            first = self.runit(max_containers=500)
        self.assertEqual(first['summary']['definitions'], 500)
        before = self.a.calls
        with patch.object(corpus, '_parser', side_effect=AssertionError('Unexpected parser replay')):
            report = self.runit(max_containers=0)
        self.assertEqual(report['status'], 'COMPLETED_FOR_SELECTED_CONTAINERS')
        self.assertEqual(report['cache'], {'reused_containers': 500, 'new_containers': 0})
        self.assertEqual(report['summary']['definitions'], 500)
        self.assertEqual(self.a.calls, before)


if __name__ == '__main__':
    unittest.main()
