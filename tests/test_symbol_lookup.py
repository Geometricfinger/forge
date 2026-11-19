"""Exact definition provenance and weak natural-language evidence on a real corpus."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

from forge_core import corpus
from forge_core.common import Blocked
from forge_core.symbol_lookup import lookup
import test_corpus as fixture


ROOT = Path(__file__).resolve().parents[1]
CALLER = b'from provider import assemble\n\ndef wrapper():\n    return assemble()\n\nassemble()\n'
PROVIDER = b'def assemble():\n    return 1\n'


class SymbolLookup(unittest.TestCase):
    setUp = fixture.CorpusTests.setUp
    tearDown = fixture.CorpusTests.tearDown
    make = fixture.CorpusTests.make
    runit = fixture.CorpusTests.runit

    def scan(self, files, **options):
        self.files = files
        self.make()
        return self.runit(**options)

    def test_withheld_definition_keeps_separate_module_and_function_calls(self):
        report = self.scan([('caller.py', CALLER)])
        result = lookup(report, 'assemble')
        self.assertEqual((result['status'], result['definitions']), ('NO_INDEXED_DEFINITION', []))
        self.assertTrue(result['definition_coverage'])
        calls = {r['caller']: r for r in result['call_references']}
        self.assertIsNone(calls['<module>']['caller_definition_id'])
        self.assertEqual((calls['<module>']['line'], calls['wrapper']['line']), (6, 4))
        self.assertEqual(calls['wrapper']['path'], 'caller.py')
        self.assertEqual(result['call_reference_count'], 2)

    def test_named_source_is_inspectable_and_cli_export_matches(self):
        from forge_core.workspace import initialize
        self.w = initialize(self.root / 'cli-home')
        self.out = self.w.home / 'corpora' / 'symbols'
        report = self.scan([('caller.py', CALLER), ('provider.py', PROVIDER)])
        found = lookup(report, 'assemble')
        self.assertEqual(found['status'], 'ONE_INDEXED_DEFINITION')
        self.assertEqual(found['call_reference_count'], 2)
        hit, = found['definitions']
        self.assertEqual(corpus.inspect_definition(report, hit['definition_id'], self.src)['source_text'],
                         PROVIDER.decode())
        # The bundled CLI loads a saved report by run ID, independently of the helper above.
        exported = self.root / 'lookup.json'
        cmd = [sys.executable, '-B', str(ROOT / 'forge.py'), '--home', str(self.w.home),
               'corpus-symbol', '--run-id', 'symbols', '--name', 'assemble', '--out', str(exported)]
        actual = subprocess.run(cmd, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(actual.stdout), found)
        self.assertEqual(json.loads(exported.read_bytes()), found)

    def test_qualified_methods_duplicates_and_limits_preserve_identity(self):
        report = self.scan([('a.py', b'class Alpha:\n    def same(self):\n        return 1\n'),
                            ('b.py', b'class Beta:\n    def same(self):\n        return 2\n')])
        matches = lookup(report, 'same', limit=1)
        self.assertEqual(matches['status'], 'MULTIPLE_INDEXED_DEFINITIONS')
        self.assertEqual((matches['definition_count'], len(matches['definitions'])), (2, 1))
        exact = lookup(report, 'Beta.same')
        self.assertEqual((exact['definition_count'], exact['definitions'][0]['path']), (1, 'b.py'))
        missed = lookup(report, 'Beta.SAME')
        self.assertEqual(missed['definition_count'], 0)
        self.assertEqual(missed['suggested_leaf_lookup'], 'SAME')

    def test_scope_and_legacy_reports_cannot_claim_complete_absence(self):
        report = self.scan([('caller.py', CALLER), ('provider.py', PROVIDER)], max_containers=1)
        result = lookup(report, 'assemble')
        self.assertEqual(result['status'], 'DEFINITION_COVERAGE_UNKNOWN')
        self.assertFalse(result['definition_coverage'])
        self.assertEqual(result['call_reference_count'], 2)
        del report['coverage']['definitions']
        self.assertIsNone(lookup(report, 'absent')['definition_coverage'])

    def test_excludes_tests_by_default_and_preserves_stub_label(self):
        report = self.scan([('tests/test_stub.pyi', b'def assemble(): ...\n')])
        self.assertEqual(lookup(report, 'assemble')['definition_count'], 0)
        hit, = lookup(report, 'assemble', include_tests=True)['definitions']
        self.assertEqual(hit['implementation_kind'], 'DECLARATION_OR_STUB')
        for name in ['a b', 'a..b', 'def', 'one()']:
            with self.subTest(name=name), self.assertRaises(Blocked):
                lookup(report, name)

    def test_missing_and_truncated_structure_limit_captured_call_counts(self):
        data = b'def owner():\n' + b'    visit()\n' * 128 + b'    marker()\n'
        report = self.scan([('example.py', data)])
        found = lookup(report, 'marker')
        self.assertEqual(found['call_reference_count'], 0)
        self.assertEqual(found['call_reference_scope'], 'CAPTURED_NAMED_CALLS_ONLY')
        self.assertGreater(found['missing_or_truncated_call_scopes'], 0)
        del report['sources'][0]['segments'][0]['module_structure']
        self.assertGreater(lookup(report, 'marker')['missing_or_truncated_call_scopes'],
                           found['missing_or_truncated_call_scopes'])

    def test_weak_language_hit_remains_available_without_becoming_an_answer(self):
        report = self.scan([('sample.py', b'def data_archive():\n    """history known saved"""\n    return 1\n')])
        partial = corpus.search(report, 'archive weather charts')
        self.assertEqual(partial['search_status'], 'PARTIAL_TERM_MATCHES_ONLY')
        self.assertEqual(partial['results'][0]['name'], 'data_archive')
        self.assertEqual(partial['query_evidence']['term_coverage'], 'SINGLE_TERM_OVERLAP')
        self.assertEqual(partial['query_evidence']['matched_terms'], ['archive'])
        self.assertEqual(partial['query_evidence']['unmatched_terms'], ['charts', 'weather'])
        self.assertEqual(corpus.search(report, 'archive')['query_evidence']['term_coverage'], 'ALL_QUERY_TERMS')
        multi = corpus.search(report, 'data archive weather')['query_evidence']
        self.assertEqual(multi['term_coverage'], 'MULTIPLE_TERM_OVERLAP')
        zero = corpus.search(report, 'weather')['query_evidence']
        self.assertEqual(zero['term_coverage'], 'NO_TERM_OVERLAP')
        for query, n, total, action in (
            ('data archive weather charts river stone field', 2, 7, 'INSPECT_SOURCE_OR_REFINE_QUERY'),
            ('data archive history known saved river stone', 5, 7, 'INSPECT_SOURCE'),
            ('data archive weather charts', 2, 4, 'INSPECT_SOURCE'),
            ('archive', 1, 1, 'INSPECT_SOURCE'),
        ):
            with self.subTest(query=query):
                result = corpus.search(report, query)
                self.assertEqual(result['results'][0]['name'], 'data_archive')
                evidence = result['query_evidence']
                self.assertEqual((evidence['matched_term_count'], evidence['query_term_count'],
                                  evidence['next_action']), (n, total, action))
