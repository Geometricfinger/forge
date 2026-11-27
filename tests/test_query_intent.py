"""Developer-authored adversarial controls for declaration-aware search."""
import unittest

from forge_core import corpus, direction
from forge_core.lexical import compound_parts, words
from test_research_retrieval import report_from


class QueryIntentTests(unittest.TestCase):
    def search(self, source, query):
        return corpus.search(report_from([('fixture.py', source)]), query)

    def test_negative_request_forms_warn_without_claiming_enforcement(self):
        source = 'def transform(value):\n    """Convert bronze to glass."""\n    return value\n'
        warning = 'NATURAL_LANGUAGE_NEGATION_NOT_ENFORCED_USE_EXPLICIT_REQUIREMENTS'
        for prefix in ('do not', "don't", 'don\u2019t', 'cannot', 'never', 'avoid',
                       'avoiding', 'avoided', 'exclude', 'excludes', 'excluded',
                       'excluding', 'without', 'no'):
            query = prefix + ' convert bronze to glass'
            with self.subTest(query=query):
                result = self.search(source, query)
                self.assertIn(warning, result['warnings'])
                self.assertEqual(result['results'][0]['name'], 'transform')
                self.assertEqual(result['runtime_validation'], 'NOT_RUN')
                self.assertFalse(result['release_approved'])
        for query in ('convert bronze except glass', 'convert bronze unless glass'):
            with self.subTest(query=query):
                self.assertIn(warning, self.search(source, query)['warnings'])

    def test_negative_cues_do_not_match_positive_word_fragments(self):
        source = 'def summarize(value):\n    """Catalog notable exceptions and exclusions."""\n    return value\n'
        warning = 'NATURAL_LANGUAGE_NEGATION_NOT_ENFORCED_USE_EXPLICIT_REQUIREMENTS'
        for query in ('notable catalog', 'exceptions exclusions', 'convert bronze to glass',
                      'True False None'):
            with self.subTest(query=query):
                self.assertNotIn(warning, self.search(source, query)['warnings'])

    def test_python_literals_are_retrievable_documentation_terms(self):
        source = ('def optional(x):\n    """Preserve None."""\n    return x\n'
                  'def affirmative(x):\n    """Return True."""\n    return x\n'
                  'def negative(x):\n    """Return False."""\n    return x\n')
        for query, expected in [('None', 'optional'), ('True', 'affirmative'), ('False', 'negative')]:
            with self.subTest(query=query):
                result = self.search(source, query)
                self.assertEqual(result['query_terms'], [query.lower()])
                self.assertEqual(result['results'][0]['name'], expected)
                self.assertEqual(result['runtime_validation'], 'NOT_RUN')

    def test_rst_role_types_do_not_truncate_conversion(self):
        source = ('def encode_value(x):\n    """Convert a :class:`int` value to hexadecimal."""\n    return x\n'
                  'def decode_value(x):\n    """Convert hexadecimal to an integer value."""\n    return x\n')
        result = self.search(source, 'Convert integer to hexadecimal')
        self.assertEqual(result['results'][0]['name'], 'encode_value')
        declaration = result['results'][0]['rank_breakdown']['directional_match']
        self.assertEqual(declaration, {'basis': 'DOCSTRING_SUMMARY', 'input': ['int'], 'output': ['hexadecimal']})

    def test_endpoint_spelling_families_are_bounded_lexical_expansions(self):
        source = 'def hex_to_int(x):\n    return x\ndef int_to_hex(x):\n    return x\n'
        result = self.search(source, 'Convert integers to hexadecimal')
        self.assertEqual(result['results'][0]['name'], 'int_to_hex')
        self.assertIn('int', result['query_expansion']['integers'])
        self.assertIn('hex', result['query_expansion']['hexadecimal'])
        self.assertNotIn('convert', result['results'][0]['matched_query_terms'])

    def test_shared_endpoint_words_do_not_erase_direction(self):
        source = ('def unpack_values(x):\n    """Convert integer triplets to percent triplets."""\n    return x\n'
                  'def pack_values(x):\n    """Convert percentages to integer triplets."""\n    return x\n')
        result = self.search(source, 'Convert percent triplets to integer triplets')
        self.assertEqual(result['results'][0]['name'], 'pack_values')
        self.assertEqual(result['requested_direction']['reverse_declared_occurrences'], 1)

    def test_annotation_aliases_supply_terms_but_not_unmatched_qualifiers(self):
        result = self.search('def bridge(value: Integer) -> Hexadecimal:\n    return value\n',
                             'convert int to normalized hex')
        self.assertEqual(result['results'][0]['name'], 'bridge')
        self.assertEqual(result['results'][0]['matched_query_terms'], ['hex', 'int'])
        self.assertNotIn('normalized', result['results'][0]['matched_query_terms'])
        self.assertEqual(result['results'][0]['rank_breakdown']['directional_match']['basis'], 'TYPE_ANNOTATIONS')

    def test_reverse_full_coverage_yields_to_a_forward_declaration(self):
        source = ('def reverse(x):\n    """Convert silver values to copper."""\n    return x\n'
                  'def forward(x):\n    """Convert copper to silver."""\n    return x\n')
        result = self.search(source, 'convert copper to silver values')
        self.assertEqual(result['results'][0]['name'], 'forward')
        reverse = next(r for r in result['results'] if r['name'] == 'reverse')
        self.assertTrue(reverse['all_query_terms_matched'])
        self.assertIn('directional_conflict', reverse['rank_breakdown'])
        self.assertEqual(reverse['match_count'], len(reverse['matched_query_terms']))

    def test_reverse_without_forward_is_retained_as_a_reviewable_lead(self):
        result = self.search('def reverse(x):\n    """Convert silver to copper."""\n    return x\n',
                             'convert copper to silver')
        self.assertEqual(result['results'][0]['name'], 'reverse')
        self.assertIn('directional_conflict', result['results'][0]['rank_breakdown'])
        self.assertEqual(result['requested_direction']['matching_occurrences'], 0)

    def test_full_neutral_coverage_still_has_priority(self):
        source = ('def forward(x):\n    """Convert copper to silver."""\n    return x\n'
                  'def status(x):\n    """Audit convert silver copper."""\n    return x\n')
        self.assertEqual(self.search(source, 'audit convert copper to silver')['results'][0]['name'], 'status')

    def test_conflicting_declarations_have_no_direction_preference_or_penalty(self):
        source = 'def copper_to_tin(x):\n    """Convert tin to copper."""\n    return x\n'
        result = self.search(source, 'convert copper to tin')
        breakdown = result['results'][0]['rank_breakdown']
        self.assertNotIn('directional_match', breakdown)
        self.assertNotIn('directional_conflict', breakdown)

    def test_exact_conversion_spelling_is_not_its_inverse(self):
        source = 'def copper_to_tin(x):\n    return x\ndef tin_to_copper(x):\n    return x\n'
        for name in ('copper_to_tin', 'tin_to_copper'):
            result = self.search(source, name)
            self.assertEqual(result['results'][0]['name'], name)
            self.assertTrue(result['results'][0]['exact_symbol_match'])
            self.assertFalse(result['results'][1]['exact_symbol_match'])

    def test_ambiguous_or_negated_requests_do_not_acquire_direction(self):
        for query in ('do not convert copper to tin', 'convert copper to tin or gold',
                      'convert copper to tin and back', 'compare copper to tin',
                      'convert copper to tin (or silver)', 'convert copper to tin (and back)',
                      'convert copper to tin (never do this)'):
            with self.subTest(query=query):
                self.assertIsNone(direction.phrase(query))

    def test_compound_expansion_matches_symbols_not_called_identifiers(self):
        source = ('def lookup(x):\n    return is_titlecase(x)\n'
                  'def is_titlecase(x):\n    return bool(x)\n')
        result = self.search(source, 'title case')
        self.assertEqual([r['name'] for r in result['results']], ['is_titlecase'])
        self.assertNotIn('titlecase', result['query_expansion']['title'])
        self.assertIn('titlecase', result['symbol_query_expansion']['title'])

    def test_predicate_query_prefers_matching_compound_subject(self):
        source = ('def titleize(value):\n    """Convert a string to title case."""\n    return value\n'
                  'def is_titlecase(value):\n    """Determine whether a string already has the requested form."""\n    return bool(value)\n')
        result = self.search(source, 'Determine whether a string uses title case without converting it')
        self.assertEqual(result['results'][0]['name'], 'is_titlecase')
        support = result['results'][0]['rank_breakdown']['predicate_match']
        self.assertEqual(support['matched_subject_terms'], ['case', 'title'])
        self.assertEqual(support['evidence'], 'PREDICATE_DECLARATION_NOT_BEHAVIOR_PROOF')
        self.assertIn('NATURAL_LANGUAGE_NEGATION_NOT_ENFORCED_USE_EXPLICIT_REQUIREMENTS', result['warnings'])

    def test_predicate_name_alone_does_not_supply_an_unrelated_subject(self):
        result = self.search('def is_tin(value):\n    """Check whether copper is available."""\n    return True\n',
                             'check whether copper is available')
        self.assertEqual(result['requested_predicate']['matching_occurrences'], 0)
        self.assertNotIn('predicate_match', result['results'][0]['rank_breakdown'])

    def test_nonpredicate_query_preserves_conversion_preference(self):
        source = ('def titleize(value):\n    """Convert a string to title case."""\n    return value\n'
                  'def is_titlecase(value):\n    """Determine whether a string already has the requested form."""\n    return bool(value)\n')
        result = self.search(source, 'convert a string to title case')
        self.assertEqual(result['results'][0]['name'], 'titleize')
        self.assertNotIn('requested_predicate', result)

    def test_stub_or_legacy_name_does_not_earn_a_predicate_preference(self):
        source = 'def is_copper(value):\n    """Check whether copper."""\n    ...\n'
        self.assertEqual(self.search(source, 'check whether copper')['requested_predicate']['matching_occurrences'], 0)
        report = report_from([('fixture.py', source)])
        report['sources'][0]['segments'][0]['definitions'][0].pop('structure')
        result = corpus.search(report, 'check whether copper')
        self.assertEqual(result['ranking_mode'], 'LEGACY_COMPATIBILITY')
        self.assertNotIn('requested_predicate', result)

    def test_compound_identifier_requires_complete_partition(self):
        self.assertEqual(compound_parts('titlecase', ['title', 'case']), ('title', 'case'))
        for token, query in [('printing', ['tin']), ('titlecases', ['title', 'case']),
                             ('titlecase', ['title']), ('abcde', ['ab', 'cde'])]:
            with self.subTest(token=token, query=query):
                self.assertEqual(compound_parts(token, query), ())
        self.assertEqual(words('HTTPServer True None'), ['http', 'server', 'true', 'none'])
        self.assertEqual(compound_parts('abcabcabcabcabc', ['abc', 'abcabc']), ('abc', 'abcabc', 'abcabc'))


if __name__ == '__main__':
    unittest.main()
