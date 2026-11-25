"""Explicit return names improve leads without claiming runtime type identity."""
import unittest

from forge_core import corpus
from test_research_retrieval import report_from


class ReturnTypeRetrievalTests(unittest.TestCase):
    query = 'Parse text into a Jewel'

    def search(self, source, query=None):
        return corpus.search(report_from([('fixture.py', source)]), query or self.query)

    def test_simple_annotation_prefers_declared_name_without_filtering_other_type(self):
        source = ('def other(x) -> Stone:\n    """Parse text into a Jewel."""\n    return x\n'
                  'def matching(x) -> Jewel:\n    """Parse text."""\n    return x\n')
        result = self.search(source)
        self.assertEqual(result['results'][0]['name'], 'matching')
        self.assertEqual({r['name'] for r in result['results']}, {'other', 'matching'})
        for row in result['results']:
            self.assertEqual(row['rank_breakdown']['declared_output']['runtime_type_relationship'], 'NOT_ESTABLISHED')
        self.assertFalse(result['release_approved'])

    def test_rtype_and_return_class_fields_retain_their_declared_basis(self):
        for field, basis in [(':rtype: Jewel', 'DOCSTRING_RTYPE'),
                             (':return:\n        Returns a :class:`types.Jewel` object.', 'DOCSTRING_RETURN_CLASS')]:
            with self.subTest(field=field):
                source = 'def matching(x):\n    """Parse text.\n\n    ' + field + '\n    """\n    return x\n'
                result = self.search(source)
                output = result['results'][0]['rank_breakdown']['declared_output']
                self.assertTrue(output['name_match'])
                self.assertEqual(output['declarations'][0]['basis'], basis)

    def test_later_warning_paragraph_is_not_part_of_return_type_statement(self):
        source = ('def matching(x):\n    """Parse text.\n\n    :return:\n'
                  '        Returns a :class:`types.Jewel` object.\n\n'
                  '    .. warning::\n        Does not establish quality or provenance.\n'
                  '    """\n    return x\n')
        self.assertTrue(self.search(source)['results'][0]['rank_breakdown']['declared_output']['name_match'])

    def test_unions_optional_any_none_and_ambiguous_docs_abstain(self):
        annotations = ['Jewel | None', 'Optional[Jewel]', 'typing.Optional[Jewel]', 'None', 'Any', '"Jewel | Stone"']
        for annotation in annotations:
            with self.subTest(annotation=annotation):
                source = 'def matching(x) -> ' + annotation + ':\n    """Parse text.\n\n    :rtype: Jewel\n    """\n    return x\n'
                self.assertNotIn('declared_output', self.search(source)['results'][0]['rank_breakdown'])
        for field in (':rtype: Union[Jewel, Stone]', ':return: A :class:`Jewel` or None.',
                      ':return: A :class:`Jewel` or :class:`Stone`.'):
            source = 'def matching(x):\n    """Parse text.\n\n    ' + field + '\n    """\n    return x\n'
            self.assertNotIn('declared_output', self.search(source)['results'][0]['rank_breakdown'])

    def test_negative_return_descriptions_do_not_earn_a_match(self):
        for description in ('Does not return a :class:`Jewel`.', 'Never a :class:`Jewel`.',
                            'A value without a :class:`Jewel`.', 'The length of a :class:`Jewel` object.',
                            'A count of :class:`Jewel` items.', "A :class:`Jewel` object's size."):
            source = 'def matching(x):\n    """Parse text.\n\n    :return: ' + description + '\n    """\n    return x\n'
            self.assertNotIn('declared_output', self.search(source)['results'][0]['rank_breakdown'])

    def test_disagreeing_or_repeated_type_declarations_abstain(self):
        for source in ('def matching(x) -> Jewel:\n    """Parse text.\n\n    :rtype: Stone\n    """\n    return x\n',
                       'def matching(x):\n    """Parse text.\n\n    :rtype: Jewel\n    :rtype: Stone\n    """\n    return x\n'):
            self.assertNotIn('declared_output', self.search(source)['results'][0]['rank_breakdown'])

    def test_subclass_or_import_alias_relationship_is_never_claimed(self):
        source = ('from library import Jewel as Renamed\nclass SpecialJewel(Jewel): pass\n'
                  'def derived(x) -> SpecialJewel:\n    """Parse text into a Jewel."""\n    return x\n'
                  'def alias(x) -> Renamed:\n    """Parse text into a Jewel."""\n    return x\n'
                  'def base(x) -> Jewel:\n    """Parse text."""\n    return x\n')
        result = self.search(source)
        self.assertEqual(result['results'][0]['name'], 'base')
        self.assertEqual({r['name'] for r in result['results']}, {'derived', 'alias', 'base'})
        for row in result['results']:
            output = row['rank_breakdown']['declared_output']
            self.assertEqual(output['runtime_type_relationship'], 'NOT_ESTABLISHED')
            self.assertNotIn('compatible', output)
            self.assertNotIn('incompatible', output)

    def test_quoted_simple_type_is_a_declaration_even_when_body_disagrees(self):
        result = self.search('def matching(x) -> "Jewel":\n    """Parse text."""\n    return None\n')
        output = result['results'][0]['rank_breakdown']['declared_output']
        self.assertTrue(output['name_match'])
        self.assertEqual(output['evidence'], 'DECLARED_RETURN_NAME_NOT_BEHAVIOR_PROOF')
        self.assertEqual(result['runtime_validation'], 'NOT_RUN')

    def test_qualified_request_does_not_assume_a_leaf_name_is_the_same_type(self):
        for requested in ('types.Jewel', 'jewel'):
            result = self.search('def matching(x) -> Jewel:\n    """Parse text."""\n    return x\n',
                                 'Parse text into ' + requested)
            self.assertFalse(result['results'][0]['rank_breakdown']['declared_output']['name_match'])

    def test_no_explicit_output_does_not_change_type_preference(self):
        result = self.search('def matching(x) -> Jewel:\n    """Parse text."""\n    return x\n', 'Jewel text')
        self.assertNotIn('requested_output', result)
        self.assertNotIn('declared_output', result['results'][0]['rank_breakdown'])

    def test_unknown_full_coverage_keeps_priority(self):
        source = ('def matching(x) -> Jewel:\n    """Parse text."""\n    return x\n'
                  'def unknown(x):\n    """Parse text into a Jewel."""\n    return x\n')
        self.assertEqual(self.search(source)['results'][0]['name'], 'unknown')

    def test_async_generator_and_stub_declarations_do_not_earn_preference(self):
        for source in ('async def matching(x) -> Jewel:\n    """Parse text."""\n    return x\n',
                       'def matching(x) -> Jewel:\n    """Parse text."""\n    yield x\n',
                       'def matching(x) -> Jewel:\n    """Parse text."""\n    ...\n'):
            self.assertNotIn('declared_output', self.search(source)['results'][0]['rank_breakdown'])

    def test_nested_generator_does_not_change_outer_declaration(self):
        source = ('def matching(x) -> Jewel:\n    """Parse text."""\n'
                  '    def nested():\n        yield x\n    return x\n')
        result = self.search(source)
        matching = next(row for row in result['results'] if row['name'] == 'matching')
        self.assertTrue(matching['rank_breakdown']['declared_output']['name_match'])

    def test_old_metadata_cannot_reconstruct_missing_return_declarations(self):
        report = report_from([('fixture.py', 'def matching(x) -> Jewel:\n    """Parse text."""\n    return x\n')])
        metadata = report['sources'][0]['segments'][0]['definitions'][0]['structure']
        metadata.pop('return_type_declarations')
        result = corpus.search(report, self.query)
        self.assertEqual(result['requested_output']['matching_occurrences'], 0)
        self.assertNotIn('declared_output', result['results'][0]['rank_breakdown'])


if __name__ == '__main__':
    unittest.main()
