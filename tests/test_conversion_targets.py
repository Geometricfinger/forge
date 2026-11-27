"""Truthful destination-only ranking and call-example declaration boundaries."""
import ast
import unittest

from forge_core import corpus, direction
from test_research_retrieval import report_from


class ConversionTargets(unittest.TestCase):
    query = 'decode percent escapes into bytes'

    def search(self, source, query=None):
        return corpus.search(report_from([('conversions.py', source)]), query or self.query)

    def target_rows(self, found):
        return [row for row in found['results']
                if 'conversion_target_match' in row['rank_breakdown']]

    def test_call_result_example_does_not_invent_conversion_endpoints(self):
        for summary in ("peel_to_bytes('abc%20def') -> b'abc def'.",
                        'Mapping.viewvalues() -> an object providing a view',
                        '>>> change(value) -> output',
                        '``change(value)`` -> bytes',
                        ':func:`change`(value) -> bytes',
                        ':py:func:`~module.change`(value) -> bytes',
                        'change (value) -> bytes',
                        'change ("x -> y") -> bytes',
                        'change("x → y") → bytes',
                        'peel_to_bytes("x -> y")',
                        'peel_to_bytes("x → y")',
                        'change(normalize(value)) -> bytes',
                        '```python\nchange(value) -> bytes\n```',
                        '~~~python\nchange(value) -> bytes\n~~~',
                        '.. code-block:: python\nchange(value) -> bytes'):
            with self.subTest(summary=summary):
                node = ast.parse('def inspect(value):\n    ' + repr(summary) + '\n    return value\n').body[0]
                self.assertEqual(direction.declarations(node), [])

    def test_example_rejection_preserves_independent_name_declaration(self):
        node = ast.parse("def peel_to_bytes(value):\n    \"\"\"peel_to_bytes('x') -> b'x'.\"\"\"\n    return value\n").body[0]
        self.assertEqual(direction.declarations(node),
                         [{'basis': 'FUNCTION_NAME', 'input': ['peel'], 'output': ['bytes']}])

    def test_prose_and_plain_arrow_declarations_remain_supported(self):
        for summary in ('Convert copper into bytes.', 'copper -> bytes',
                        'Decode copper (an encoded value) into bytes.',
                        'Convert (encoded) copper into bytes.',
                        'copper (encoded representation) -> bytes',
                        'Convert :class:`copper` into :class:`bytes`.'):
            with self.subTest(summary=summary):
                node = ast.parse('def apply(value):\n    ' + repr(summary) + '\n    return value\n').body[0]
                self.assertEqual(direction.declarations(node),
                                 [{'basis': 'DOCSTRING_SUMMARY', 'input': ['copper'], 'output': ['bytes']}])

    def test_destination_can_break_a_partial_coverage_tie(self):
        found = self.search('def describe(value):\n    """decode percent metadata"""\n    return value\n'
                            'def peel_to_bytes(value):\n    return value\n')
        row = found['results'][0]
        self.assertEqual(row['name'], 'peel_to_bytes')
        self.assertEqual(row['matched_query_terms'], ['bytes'])
        self.assertEqual(row['match_count'], 1)
        self.assertFalse(row['all_query_terms_matched'])
        self.assertEqual(found['search_status'], 'PARTIAL_TERM_MATCHES_ONLY')
        self.assertEqual(found['requested_direction']['matching_occurrences'], 0)
        self.assertEqual(found['requested_direction']['destination_only_occurrences'], 1)
        cue = row['rank_breakdown']['conversion_target_match']
        self.assertEqual(cue['input_match'], 'NOT_ESTABLISHED')
        self.assertEqual(cue['declaration']['input'], ['peel'])
        self.assertIn('NOT_RETURN_TYPE_OR_BEHAVIOR_PROOF', cue['evidence'])
        self.assertEqual(found['runtime_validation'], 'NOT_RUN')

    def test_full_coverage_keeps_priority(self):
        found = self.search('def complete(value):\n    """decode percent escapes into bytes"""\n    return value\n'
                            'def peel_to_bytes(value):\n    return value\n')
        self.assertEqual(found['results'][0]['name'], 'complete')
        self.assertTrue(found['results'][0]['all_query_terms_matched'])

    def test_full_forward_evidence_is_not_replaced_by_destination_only(self):
        found = self.search('def copper_to_bytes(value):\n    return value\n', 'convert copper into bytes')
        self.assertIn('directional_match', found['results'][0]['rank_breakdown'])
        self.assertEqual(self.target_rows(found), [])

    def test_reverse_relation_does_not_receive_destination_preference(self):
        found = self.search('def bytes_to_copper(value):\n    return value\n', 'convert copper into bytes')
        self.assertEqual(self.target_rows(found), [])
        self.assertIn('directional_conflict', found['results'][0]['rank_breakdown'])

    def test_disagreeing_name_doc_and_annotations_abstain(self):
        sources = [
            'def peel_to_bytes(value):\n    """Convert peel into copper."""\n    return value\n',
            'def peel_to_bytes(value: bytes) -> peel:\n    return value\n',
            'def peel_to_bytes(value) -> str:\n    return value\n',
            'def peel_to_bytes(value) -> bytes | None:\n    return value\n',
            'async def peel_to_bytes(value) -> bytes:\n    return value\n',
        ]
        for source in sources:
            with self.subTest(source=source):
                self.assertEqual(self.target_rows(self.search(source)), [])

    def test_stub_and_missing_context_abstain(self):
        self.assertEqual(self.target_rows(self.search('def peel_to_bytes(value):\n    ...\n')), [])
        report = report_from([('m.py', 'def peel_to_bytes(value):\n    return value\n')])
        del report['sources'][0]['segments'][0]['definitions'][0]['structure']
        result = corpus.search(report, self.query)
        self.assertEqual(result['ranking_mode'], 'LEGACY_COMPATIBILITY')
        self.assertEqual(self.target_rows(result), [])

    def test_only_unambiguous_single_destination_requests_qualify(self):
        source = 'def peel_to_bytes(value):\n    return value\n'
        for query in ('bytes', 'compare percent escapes and bytes',
                      'do not decode percent escapes into bytes',
                      'decode percent escapes into raw bytes',
                      'decode percent escapes into bytes or text',
                      'convert bytes into bytes'):
            with self.subTest(query=query):
                self.assertEqual(self.target_rows(self.search(source, query)), [])

    def test_multiple_declared_output_tokens_do_not_become_single_type(self):
        found = self.search('def peel_to_raw_bytes(value):\n    return value\n')
        self.assertEqual(self.target_rows(found), [])

    def test_qualified_destination_does_not_promote_namespace_spelling(self):
        source = ('def decode_percent(value):\n    return value\n'
                  'def peel_to_models(value):\n    return value\n'
                  'def peel_to_bytes(value):\n    return value\n')
        found = self.search(source, 'decode percent escapes into models.Bytes')
        self.assertEqual(self.target_rows(found), [])
        self.assertEqual(found['results'][0]['name'], 'decode_percent')

    def test_literal_identifier_keeps_its_priority(self):
        found = self.search('def peel_to_bytes(value):\n    """Convert bytes into peel."""\n    return value\n'
                            'def other(value):\n    """Convert peel into bytes."""\n    return value\n',
                            'peel_to_bytes')
        self.assertEqual(found['results'][0]['name'], 'peel_to_bytes')
        self.assertTrue(found['results'][0]['exact_symbol_match'])

    def test_destination_and_return_name_share_one_allowance(self):
        found = self.search('def relevant(value):\n    """decode percent escapes"""\n    return value\n'
                            'def peel_to_bytes(value) -> bytes:\n    return value\n')
        self.assertEqual(found['results'][0]['name'], 'relevant')
        row = next(row for row in found['results'] if row['name'] == 'peel_to_bytes')
        self.assertIn('conversion_target_match', row['rank_breakdown'])
        self.assertTrue(row['rank_breakdown']['declared_output']['name_match'])
        self.assertEqual(row['match_count'], 1)

    def test_prepared_and_returned_declarations_are_independent(self):
        prepared = corpus.prepare_search(report_from([('m.py', 'def peel_to_bytes(value):\n    return value\n')]))
        first = prepared.query(self.query)
        first['results'][0]['rank_breakdown']['conversion_target_match']['declaration']['input'].append('invented')
        second = prepared.query(self.query)
        self.assertEqual(second['results'][0]['rank_breakdown']['conversion_target_match']['declaration']['input'], ['peel'])


if __name__ == '__main__':
    unittest.main()
