"""Regular spelling evidence must improve discovery without claiming semantics."""
import unittest

from forge_core import corpus, lexical
from test_research_retrieval import report_from


class InflectionRetrievalTests(unittest.TestCase):
    def search_doc(self, documented, query):
        report = report_from([('neutral.py', f'def candidate():\n    """{documented}"""\n    return 1\n')])
        return corpus.search(report, query)

    def test_regular_forms_work_in_both_directions(self):
        for first, second in [('alternate', 'alternating'), ('item', 'items'),
                              ('iterable', 'iterables'), ('retry', 'retries'),
                              ('carry', 'carried'), ('match', 'matches'),
                              ('batch', 'batches'), ('plan', 'planned'),
                              ('stop', 'stopping'), ('parse', 'parsed'),
                              ('use', 'using'), ('use', 'used'), ('tie', 'tying'),
                              ('tie', 'tied'), ('age', 'aged'), ('ice', 'iced'),
                              ('bus', 'buses'), ('gas', 'gases'),
                              ('alias', 'aliases'), ('status', 'statuses')]:
            for query, documented in [(first, second), (second, first)]:
                with self.subTest(query=query, documented=documented):
                    result = self.search_doc(documented, query)
                    self.assertEqual(result['results'][0]['name'], 'candidate')
                    self.assertEqual(result['results'][0]['matched_fields']['documentation'][query], [documented])
                    self.assertIn(documented, result['query_expansion'][query])

    def test_different_inflected_forms_share_a_regular_base(self):
        result = self.search_doc('Planned batches', 'planning batch')
        self.assertEqual(result['search_status'], 'MATCHES_FOUND')
        self.assertEqual(result['results'][0]['matched_query_terms'], ['batch', 'planning'])

    def test_derivational_near_words_are_not_regular_forms(self):
        for query, documented in [('alternate', 'alternative'), ('multiple', 'multiplication'),
                                  ('iterable', 'iterator'), ('iterable', 'iteration'),
                                  ('universe', 'university'), ('general', 'generate')]:
            with self.subTest(query=query, documented=documented):
                self.assertEqual(self.search_doc(documented, query)['results'], [])

    def test_arbitrary_substrings_do_not_match(self):
        self.assertEqual(self.search_doc('printing', 'tin')['results'], [])

    def test_short_vowel_doubling_does_not_merge_distinct_verbs(self):
        for query, documented in [('filled', 'filing'), ('filling', 'filed'),
                                  ('planned', 'planing'), ('planning', 'planed')]:
            with self.subTest(query=query, documented=documented):
                self.assertEqual(self.search_doc(documented, query)['results'], [])

    def test_alphanumeric_identifiers_are_not_inflected(self):
        self.assertEqual(self.search_doc('sha256s', 'sha256')['results'], [])

    def test_literal_symbol_stays_ahead_of_inflected_symbol(self):
        report = report_from([('neutral.py', 'def plans(): return 1\ndef plan(): return 2\n')])
        for query in ('plan', 'plans'):
            with self.subTest(query=query):
                result = corpus.search(report, query)
                self.assertEqual(result['results'][0]['name'], query)
                self.assertTrue(result['results'][0]['exact_symbol_match'])

    def test_literal_identifier_precedes_conflicting_declaration_cues(self):
        source = ('def copper_to_silver(x):\n    """Convert silver to copper."""\n    return x\n'
                  'def other(x):\n    """Convert copper to silver."""\n    return x\n')
        report = report_from([('neutral.py', source)])
        self.assertEqual(corpus.search(report, 'copper_to_silver')['results'][0]['name'], 'copper_to_silver')
        self.assertEqual(corpus.search(report, 'convert copper to silver')['results'][0]['name'], 'other')

    def test_partial_coverage_remains_partial(self):
        result = self.search_doc('Alternating iterables', 'alternate iterable semaphore')
        self.assertEqual(result['search_status'], 'PARTIAL_TERM_MATCHES_ONLY')
        self.assertEqual(result['query_evidence']['unmatched_terms'], ['semaphore'])

    def test_expansion_never_adds_extra_query_votes(self):
        result = self.search_doc('Plan plans planned planning', 'plan')
        self.assertEqual(result['results'][0]['match_count'], 1)
        self.assertEqual(result['full_match_count'], 1)

    def test_context_only_documentation_remains_discoverable(self):
        report = report_from([('neutral.py', 'class Holder:\n    """Schedules batches"""\n    def __call__(self): return 1\n')])
        result = corpus.search(report, 'schedule batch')
        self.assertEqual(result['results'][0]['name'], 'Holder.__call__')
        self.assertIn('class_documentation', result['results'][0]['matched_fields'])

    def test_direct_documentation_breaks_equal_coverage_ties(self):
        source = ('class Index:\n    """Encode percent URL path segment"""\n'
                  '    def path(self):\n        """Generate text"""\n        return 1\n'
                  'def quote():\n    """Encode percent URL path segment"""\n    return 2\n')
        result = corpus.search(report_from([('neutral.py', source)]), 'encode percent URL path segment')
        self.assertEqual(result['results'][0]['name'], 'quote')
        self.assertEqual(result['full_match_count'], 2)
        self.assertEqual(result['results'][1]['name'], 'Index.path')
        self.assertEqual(result['results'][1]['rank_breakdown']['direct_matched_terms'], ['path'])

    def test_expansion_cannot_satisfy_an_observed_api_filter(self):
        report = report_from([('neutral.py', 'def candidate():\n    """Schedules writes"""\n    return 1\n')])
        result = corpus.search(report, 'schedule write', filters={'required_apis': ['os.write']})
        self.assertEqual(result['results'], [])
        self.assertEqual(result['exclusion_counts']['required_api_not_observed'], 1)

    def test_declared_type_names_do_not_gain_inflection_equivalence(self):
        report = report_from([('neutral.py', 'def candidate() -> Plan:\n    """Plans a route"""\n    return None\n')])
        result = corpus.search(report, 'parse text into a Plans')
        output = result['results'][0]['rank_breakdown']['declared_output']
        self.assertFalse(output['name_match'])
        self.assertNotIn('plan', lexical.expand_query(['plans'], ())['plans'])

    def test_legacy_rows_keep_their_previous_spelling_rules(self):
        report = report_from([('neutral.py', 'def candidate():\n    """Alternating"""\n    return 1\n')])
        for definition in report['sources'][0]['segments'][0]['definitions']:
            definition.pop('structure')
        result = corpus.search(report, 'alternate')
        self.assertEqual(result['ranking_mode'], 'LEGACY_COMPATIBILITY')
        self.assertEqual(result['results'], [])

    def test_prepared_snapshot_retains_spelling_evidence(self):
        report = report_from([('neutral.py', 'def candidate():\n    """Scheduled batches"""\n    return 1\n')])
        prepared = corpus.prepare_search(report)
        first = prepared.query('schedule batch')
        first['query_expansion']['schedule'].clear()
        report['sources'].clear()
        second = prepared.query('schedule batch')
        self.assertIn('scheduled', second['query_expansion']['schedule'])
        self.assertEqual(second['results'][0]['name'], 'candidate')

    def test_duplicate_copies_do_not_multiply_spelling_votes(self):
        code = 'def candidate():\n    """Scheduled batches"""\n    return 1\n'
        one = corpus.search(report_from([('one.py', code)]), 'schedule batch', distinct=True)
        many = corpus.search(report_from([('one.py', code), ('two.py', code)]), 'schedule batch', distinct=True)
        self.assertEqual(one['group_statistics_count'], many['group_statistics_count'])
        self.assertEqual(one['results'][0]['match_count'], many['results'][0]['match_count'])
        self.assertEqual(len(many['results'][0]['occurrences']), 2)

    def test_only_observed_vocabulary_is_added(self):
        expansion = lexical.expand_inflections({'schedule': ['schedule']}, {'scheduled', 'unrelated'})
        self.assertEqual(expansion, {'schedule': ['schedule', 'scheduled']})

    def test_vocabulary_is_not_scanned_or_copied_per_query(self):
        class Vocabulary:
            def __contains__(self, token): return token == 'scheduled'
            def __iter__(self): raise AssertionError('query scanned the vocabulary')
        self.assertIn('scheduled', lexical.expand_inflections({'schedule': ['schedule']}, Vocabulary())['schedule'])

    def test_forms_are_bounded_and_do_not_rewrite_short_literals(self):
        for token in ('a', 'is', 'os', 'x1', 'a' * 64, 'planning', 'retries'):
            with self.subTest(token=token):
                variants = lexical.inflection_variants(token)
                self.assertIn(token, variants)
                self.assertLessEqual(len(variants), 64)
                self.assertTrue(all(len(word) <= 64 for word in variants))
        self.assertEqual(lexical.inflection_variants('is'), {'is'})

    def test_negation_is_still_not_a_verified_constraint(self):
        result = self.search_doc('Writes batches', 'without writing')
        self.assertIn('NATURAL_LANGUAGE_NEGATION_NOT_ENFORCED_USE_EXPLICIT_REQUIREMENTS', result['warnings'])
        self.assertEqual(result['runtime_validation'], 'NOT_RUN')
        self.assertFalse(result['release_approved'])


if __name__ == '__main__':
    unittest.main()
