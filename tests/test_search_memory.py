"""Hash parity, result bounds and isolation for memory-conscious ranked search."""
import copy
from io import BytesIO
import tracemalloc
import unittest
from unittest import mock
from zipfile import ZipFile

from forge_core import corpus, retrieval
from forge_core.common import canonical, sha
from test_research_retrieval import report_from


def archive(files):
    stream = BytesIO()
    with ZipFile(stream, 'w') as zipped:
        for path, data in files.items():
            zipped.writestr(path, data)
    return stream.getvalue()


def poison(value, seen=None):
    """Change nested caller-owned metadata, including declaration evidence."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, dict):
        for child in list(value.values()):
            poison(child, seen)
        value['caller_change'] = True
    elif isinstance(value, list):
        for child in list(value):
            poison(child, seen)
        value.append('caller_change')


class SearchMemoryTests(unittest.TestCase):
    def setUp(self):
        with retrieval._CACHE_LOCK:
            retrieval._CACHE.clear()

    def tearDown(self):
        with retrieval._CACHE_LOCK:
            retrieval._CACHE.clear()

    def rich_report(self):
        source = ('class Converter:\n    """A converter for reviewed values."""\n'
                  '    def copper_to_silver(self, value: Copper) -> Silver:\n'
                  '        """Convert copper to silver."""\n        return value\n'
                  '    def another_copper_to_silver(self, value: Copper) -> Silver:\n'
                  '        """Convert copper to silver."""\n        return value\n')
        guide = '  ```python\n' + ''.join('  ' + line for line in source.splitlines(keepends=True)) + '  ```\n'
        data = archive({'nested/parts.py': source, 'guide.md': guide})
        return report_from([('first.zip', data), ('second.zip', data)])

    def test_streaming_hash_matches_existing_canonical_bytes(self):
        values = [None, False, True, 10**80, -0.0, 1.25e-30,
                  {'z': '\u2603\n\t\\"\ud800', 'a': [0, False, None, {'nested': 'café'}]},
                  [{'position': index, 'text': '\u03bb' * 40} for index in range(2000)],
                  self.rich_report()]
        for value in values:
            with self.subTest(kind=type(value).__name__):
                self.assertEqual(retrieval._canonical_digest(value), sha(canonical(value)))

    def test_streaming_hash_preserves_nonfinite_refusal(self):
        for value in (float('nan'), float('inf'), -float('inf')):
            for encode in (canonical, retrieval._canonical_digest):
                with self.subTest(value=value, encode=encode.__name__), self.assertRaises(ValueError):
                    encode({'nested': [value]})

    def test_constructor_snapshot_isolated_from_nested_input_changes(self):
        rows = list(corpus.all_rows(self.rich_report()))
        index = retrieval.SearchIndex(rows, 'owned-fixture')
        before = canonical(index.query('convert copper to Silver', distinct=True))
        poison(rows)
        self.assertEqual(canonical(index.query('convert copper to Silver', distinct=True)), before)

    def test_returned_rows_isolate_cache_source_and_other_results(self):
        for distinct in (False, True):
            with self.subTest(distinct=distinct):
                report = self.rich_report()
                original = canonical(report)
                result = corpus.search(report, 'convert copper to Silver', distinct=distinct)
                expected = canonical(result)
                self.assertGreater(len(result['results']), 1)
                target = next(row for row in result['results'] if 'analysis_view' in row)
                shared = [row for row in corpus.rows(report) if row['source_id'] == target['source_id']]
                self.assertEqual(len(shared), 2)
                self.assertIs(shared[0]['member_chain'], shared[1]['member_chain'])
                self.assertIs(shared[0]['analysis_view'], shared[1]['analysis_view'])
                self.assertIn('declared_output', target['rank_breakdown'])
                self.assertIn('directional_match', target['rank_breakdown'])
                self.assertTrue(target['class_context'])
                others = [row for row in result['results'] if row is not target]
                others_before = canonical(others)
                poison(target)
                self.assertEqual(canonical(others), others_before)
                self.assertEqual(canonical(report), original)
                self.assertEqual(canonical(corpus.search(report, 'convert copper to Silver', distinct=distinct)), expected)

    def test_distinct_occurrences_have_independent_nested_provenance(self):
        report = self.rich_report()
        result = corpus.search(report, 'convert copper to Silver', distinct=True)
        expected = canonical(result)
        row = result['results'][0]
        self.assertEqual(len(row['occurrences']), 2)
        primary_before = canonical(row['member_chain'])
        second_before = canonical(row['occurrences'][1])
        poison(row['occurrences'][0]['member_chain'])
        self.assertEqual(canonical(row['member_chain']), primary_before)
        self.assertEqual(canonical(row['occurrences'][1]), second_before)
        poison(row['member_chain'])
        self.assertEqual(canonical(row['occurrences'][1]), second_before)
        self.assertEqual(canonical(corpus.search(report, 'convert copper to Silver', distinct=True)), expected)

    def test_display_limit_keeps_global_counts_and_complete_occurrences(self):
        source = ''.join(f'def feature_{index:02}(): return {index}\n' for index in range(20))
        report = report_from([('first.py', source), ('second.py', source)])
        for distinct in (False, True):
            for filters in ({'record_kind': 'function'}, {'record_kind': 'function', 'container_ids': ['f1']}):
                with self.subTest(distinct=distinct, filters=filters):
                    full = corpus.search(report, 'feature', limit=100, distinct=distinct, filters=filters)
                    small = corpus.search(report, 'feature', limit=1, distinct=distinct, filters=filters)
                    expected = copy.deepcopy(full)
                    expected['results'] = expected['results'][:1]
                    self.assertEqual(small, expected)
                    self.assertEqual(small['distinct_matching_groups'], 20)
                    count = 20 if 'container_ids' in filters else 40
                    self.assertEqual(small['total_matching_occurrences'], count)
                    self.assertEqual(small['full_match_count'], count)
                    if distinct:
                        self.assertEqual(len(small['results'][0]['occurrences']), count // 20)

    def test_changed_input_uses_new_binding_without_poisoning_old_snapshot(self):
        original = self.rich_report()
        before = corpus.search(original, 'convert copper to Silver')
        changed = copy.deepcopy(original)
        changed['sources'][0]['segments'][0]['definitions'][0]['source_window']['sha256'] = '0' * 64
        after = corpus.search(changed, 'convert copper to Silver')
        self.assertNotEqual(before['index_binding'], after['index_binding'])
        self.assertEqual(after['receipt_binding'], sha(canonical(changed)))
        self.assertEqual(corpus.search(original, 'convert copper to Silver'), before)

    def test_broad_query_auxiliary_allocations_remain_bounded(self):
        base = next(corpus.rows(report_from([('module.py', 'def feature(value): return value\n')])))
        rows = [dict(base, definition_id='definition_' + format(i, '064x'),
                     name=f'feature_{i:05}', local_start_line=i + 1, local_end_line=i + 1)
                for i in range(5000)]
        index = retrieval.SearchIndex(rows, 'broad-query-fixture')
        for distinct in (False, True):
            with self.subTest(distinct=distinct):
                tracemalloc.start()
                try:
                    result = index.query('feature', limit=2, distinct=distinct)
                    _, peak = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
                self.assertEqual(len(result['results']), 2)
                self.assertEqual(result['total_matching_occurrences'], 5000)
                self.assertEqual(result['distinct_matching_groups'], 5000)
                self.assertEqual(result['full_match_count'], 5000)
                # This excludes the existing index. The ceiling allows wide
                # interpreter variation, but rejects a rich row/evidence copy
                # retained for every match of this small, fixed fixture.
                self.assertLess(peak, 10 * 1024 * 1024)

    def test_failed_feature_build_preserves_report_and_does_not_cache_partial_index(self):
        report = self.rich_report()
        original = canonical(report)
        fields = retrieval._fields
        calls = 0
        def exhausted(row):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise MemoryError()
            return fields(row)
        with mock.patch.object(retrieval, '_fields', side_effect=exhausted), self.assertRaises(MemoryError):
            corpus.search(report, 'convert copper to Silver')
        self.assertEqual(canonical(report), original)
        self.assertEqual(len(retrieval._CACHE), 0)
        result = corpus.search(report, 'convert copper to Silver')
        self.assertTrue(result['results'])
        self.assertEqual(corpus.search(report, 'convert copper to Silver'), result)
