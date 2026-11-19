"""Order-sensitive retrieval of declared transformations on synthetic sources."""
import unittest

from forge_core import corpus, direction
from test_research_retrieval import report_from


class DirectionalRetrieval(unittest.TestCase):
    def search(self, source, query):
        return corpus.search(report_from([('transform.py', source)]), query)

    def test_docstring_orients_reverse_and_forward_without_changing_term_count(self):
        source = ('def amber(data):\n    """Convert copper to silver."""\n    return data\n'
                  'def blue(data):\n    """Convert silver to copper."""\n    return data\n')
        for query, name in [('Convert copper to silver', 'amber'), ('Convert silver to copper', 'blue')]:
            found = self.search(source, query)
            row = found['results'][0]
            self.assertEqual(row['name'], name)
            self.assertEqual(row['match_count'], len(row['matched_query_terms']))
            self.assertEqual(row['rank_breakdown']['directional_match']['basis'], 'DOCSTRING_SUMMARY')
            self.assertEqual(found['requested_direction']['matching_occurrences'], 1)
        neutral = self.search(source, 'copper silver')
        self.assertNotIn('requested_direction', neutral)
        self.assertNotIn('conversion_endpoints', neutral['results'][0])

    def test_signature_identifier_and_annotation_only_have_captured_basis(self):
        source = ('def form_a(value: Amber) -> Jade:\n    return value\n'
                  'def form_b(value: Jade) -> Amber:\n    return value\n'
                  'def copper_from_tin(value):\n    return value\n')
        for query, name, basis in [('convert amber into jade', 'form_a', 'TYPE_ANNOTATIONS'),
                                   ('transform tin to copper', 'copper_from_tin', 'FUNCTION_NAME')]:
            row = self.search(source, query)['results'][0]
            self.assertEqual((row['name'], row['rank_breakdown']['directional_match']['basis']),
                             (name, basis))
            self.assertIn('conversion_declaration', row['matched_fields'])

    def test_conflicting_documents_missing_metadata_and_stubs_do_not_earn_preference(self):
        source = ('def copper_to_tin(value):\n    """Convert tin to copper."""\n    return value\n'
                  'def also(value):\n    """Convert copper to tin."""\n    ...\n')
        found = self.search(source, 'convert copper to tin')
        self.assertEqual(found['requested_direction']['matching_occurrences'], 0)
        report = report_from([('m.py', 'def apply(value):\n    """Convert lead to gold."""\n    return value\n')])
        del report['sources'][0]['segments'][0]['definitions'][0]['structure']['conversion_endpoints']
        self.assertEqual(corpus.search(report, 'convert lead to gold')['requested_direction']['matching_occurrences'], 0)

    def test_only_explicit_unambiguous_positive_direction_changes_policy(self):
        for query in ('compare mercury to bronze', 'convert mercury', 'convert both ways between bronze and mercury',
                      'avoid converting mercury to bronze', 'no conversion from mercury to bronze',
                      'don’t convert mercury to bronze', 'convert mercury to bronze or brass'):
            with self.subTest(query=query):
                self.assertIsNone(direction.phrase(query))
        self.assertEqual(direction.phrase('transfer from mercury to bronze'), (['mercury'], ['bronze']))

    def test_full_overlap_keeps_priority_over_partial_directional_match(self):
        source = ('def trace(value):\n    """Convert copper to silver."""\n    return value\n'
                  'def status(value):\n    """Audit convert silver copper."""\n    return value\n')
        # "audit" is an explicit part of the request that only the second row covers.
        found = self.search(source, 'audit convert copper to silver')
        self.assertEqual(found['results'][0]['name'], 'status')
        self.assertTrue(found['results'][0]['all_query_terms_matched'])
        self.assertEqual(found['requested_direction']['matching_occurrences'], 1)
