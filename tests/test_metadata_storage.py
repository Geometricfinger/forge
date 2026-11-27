"""Cache representation changes preserve values, ownership and recovery proof."""
import copy
import gzip
import json
from unittest.mock import patch
import unittest

from checkpoint_helpers import cache_bytes, cache_files, cache_read, cache_write
from forge_core import corpus
from forge_core.common import canonical, sha
import test_corpus as fixture
import test_analysis_recovery as recovery_fixture


def make_raw(path):
    """Create a legacy representation independently of the product codec."""
    data = gzip.decompress(path.read_bytes())
    raw = path.with_suffix('')
    raw.write_bytes(data)
    path.unlink()
    return raw


class MetadataStorageTests(unittest.TestCase):
    setUp = fixture.CorpusTests.setUp
    tearDown = fixture.CorpusTests.tearDown
    make = fixture.CorpusTests.make
    runit = fixture.CorpusTests.runit

    def test_new_explicit_formats_preserve_public_report_and_cart_values(self):
        report = self.runit()
        for directory in ('checkpoints', 'segments'):
            paths = list(cache_files(self.out / directory))
            self.assertEqual(len(paths), 2)
            self.assertTrue(all(path.name.endswith('.json.gz') for path in paths))
            for path in paths:
                data = cache_bytes(path)
                self.assertEqual(data, canonical(json.loads(data)))
                self.assertNotIn(b'source_hex', data)
        self.assertEqual((self.out / 'corpus.json').read_bytes(), canonical(report))
        self.assertEqual(corpus.read_report(self.out), report)
        expected = b''.join(canonical(row) + b'\n' for row in corpus.all_rows(report))
        self.assertEqual(gzip.decompress((self.out / 'capabilities.ndjson.gz').read_bytes()), expected)

    def test_legacy_raw_replay_requires_no_parser_or_analyzer_and_keeps_bytes(self):
        first = self.runit()
        old = {}
        for directory in ('checkpoints', 'segments'):
            for path in cache_files(self.out / directory):
                raw = make_raw(path); old[raw] = raw.read_bytes()
        before = self.a.calls
        with patch.object(corpus, '_parser', side_effect=AssertionError('Legacy replay parsed again')):
            second = self.runit(max_containers=0)
        self.assertEqual(second['sources'], first['sources'])
        self.assertEqual(list(corpus.all_rows(second)), list(corpus.all_rows(first)))
        self.assertEqual(self.a.calls, before)
        self.assertEqual(second['cache'], {'reused_containers': 2, 'new_containers': 0})
        for path, data in old.items():
            self.assertEqual(path.read_bytes(), data)
            self.assertFalse(path.with_suffix('.json.gz').exists())

    def test_mixed_legacy_and_compressed_sources_replay_together(self):
        first = self.runit()
        make_raw(next(cache_files(self.out / 'checkpoints')))
        make_raw(next(cache_files(self.out / 'segments')))
        with patch.object(corpus, '_parser', side_effect=AssertionError('Mixed replay parsed again')):
            second = self.runit(max_containers=0)
        self.assertEqual(second['sources'], first['sources'])
        self.assertEqual(second['cache']['reused_containers'], 2)

    def test_legacy_semantic_tampering_is_rejected_after_rehashing(self):
        self.runit()
        path = make_raw(next(cache_files(self.out / 'checkpoints')))
        obj = cache_read(path); obj['result']['inventory'] = None
        obj['result_sha256'] = sha(canonical(obj['result']))
        cache_write(path, obj)
        before = self.a.calls
        with patch.object(corpus, '_parser', side_effect=AssertionError('Malformed cache parsed again')):
            report = self.runit()
        self.assertEqual(report['coverage']['blocked'][0]['reason'], 'CORPUS_CHECKPOINT_INVENTORY')
        self.assertEqual(self.a.calls, before)

    def test_source_duplicate_format_is_not_silently_selected(self):
        self.runit()
        path = next(cache_files(self.out / 'checkpoints'))
        path.with_suffix('').write_bytes(gzip.decompress(path.read_bytes()))
        before = self.a.calls
        report = self.runit()
        self.assertEqual(report['coverage']['blocked'][0]['reason'], 'CORPUS_CHECKPOINT_FORMAT_CONFLICT')
        self.assertEqual(self.a.calls, before)

    def test_segment_duplicate_format_is_refused_even_with_complete_source_cache(self):
        self.runit()
        path = next(cache_files(self.out / 'segments'))
        path.with_suffix('').write_bytes(gzip.decompress(path.read_bytes()))
        before = self.a.calls
        report = self.runit()
        self.assertTrue(any(row['reason'] == 'CORPUS_CHECKPOINT_FORMAT_CONFLICT'
                            for row in report['coverage']['blocked']))
        self.assertEqual(self.a.calls, before)

    def test_duplicate_wire_segments_and_separate_inspections_are_isolated(self):
        code = 'def keep(value):\n    return value + 1\n'
        data = canonical({'metadata': {'kernelspec': {'language': 'python'}},
                          'cells': [{'cell_type': 'code', 'source': code}] * 2})
        source = {'provider': 'synthetic', 'file_id': 'duplicate', 'path': 'duplicate.ipynb',
                  'source_url': 'fixture://duplicate', 'sha256': sha(data), 'size': len(data)}
        profile = json.loads((corpus.ROOT / 'templates/corpus_profile.json').read_bytes())
        first = corpus.inspect_one(data, source, self.w.home, profile, self.a)
        second = corpus.inspect_one(data, source, self.w.home, profile, self.a)
        sibling = copy.deepcopy(first['segments'][1])
        other = copy.deepcopy(second)
        first['segments'][0]['definitions'][0]['source_window']['byte_start'] = 999
        first['segments'][0]['definitions'][0]['structure']['mutated'] = True
        self.assertEqual(first['segments'][1], sibling)
        self.assertEqual(second, other)
        self.assertNotIn('source_hex', json.dumps(first))
        self.assertEqual(sha(data), source['sha256'])

    def test_mutating_returned_metadata_does_not_change_cached_or_later_results(self):
        first = self.runit()
        expected_sources = copy.deepcopy(first['sources'])
        stored = {path: path.read_bytes() for directory in ('segments', 'checkpoints')
                  for path in cache_files(self.out / directory)}
        first['sources'][0]['segments'][0]['definitions'][0]['source_window']['byte_start'] = 999
        second = self.runit(max_containers=0)
        self.assertEqual(second['sources'], expected_sources)
        second['sources'][0]['segments'][0]['definitions'].clear()
        third = self.runit(max_containers=0)
        self.assertEqual(third['sources'], expected_sources)
        for path, data in stored.items():
            self.assertEqual(path.read_bytes(), data)


class LegacyRecoveryStorageTests(unittest.TestCase):
    setUp = recovery_fixture.RecoveryTests.setUp
    tearDown = recovery_fixture.RecoveryTests.tearDown
    prepare = recovery_fixture.RecoveryTests.prepare
    runit = recovery_fixture.RecoveryTests.runit

    def test_legacy_failed_checkpoint_history_and_success_bytes_survive_mixed_retry(self):
        self.runit()
        source = make_raw(next(cache_files(self.out / 'checkpoints')))
        previous = source.read_bytes()
        success = make_raw(next(cache_files(self.out / 'segments')))
        successful_bytes = success.read_bytes()
        second = self.runit()
        self.assertEqual(second['status'], 'COMPLETED_FOR_SELECTED_CONTAINERS')
        self.assertEqual(success.read_bytes(), successful_bytes)
        self.assertTrue(source.exists())
        self.assertFalse(source.with_suffix('.json.gz').exists())
        history = self.out / 'recovery_history' / (sha(previous) + '.json')
        self.assertEqual(history.read_bytes(), previous)
        formats = sorted(path.suffix for path in cache_files(self.out / 'segments'))
        self.assertEqual(formats, ['.gz', '.json'])
        self.assertEqual(second['recovery']['recovered_segments'], 1)
        attempts = len(self.p.calls)
        third = self.runit(max_containers=0)
        self.assertEqual(len(self.p.calls), attempts)
        self.assertEqual(third['sources'], second['sources'])


if __name__ == '__main__':
    unittest.main()
