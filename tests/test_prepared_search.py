"""Prepared batches preserve full evidence and isolate mutable caller state."""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from forge_core import corpus, opportunities, retrieval
from forge_core.common import canonical, sha
from test_research_retrieval import report_from
from test_search_memory import poison


@contextmanager
def mock_prepared_search(**kwargs):
    query=Mock(**kwargs)
    with patch('forge_core.corpus.prepare_search',return_value=SimpleNamespace(query=query)):
        yield query


class PreparedSearchTests(unittest.TestCase):
    def report(self, name='parts.py'):
        return report_from([(name,'class Converter:\n    """A reviewed converter."""\n'
                                  '    def copper_to_silver(self, value: Copper) -> Silver:\n'
                                  '        """Convert copper to silver."""\n        return value\n'
                                  'def has_silver(value): return value is not None\n')])

    def test_prepared_queries_match_complete_ordinary_responses(self):
        report=self.report();prepared=corpus.prepare_search(report)
        for query in ('convert copper to silver','has silver','unknown capability','without copper'):
            for distinct in (False,True):
                for filters in (None,{'record_kind':'function'},{'required_apis':['hashlib.sha256']}):
                    with self.subTest(query=query,distinct=distinct,filters=filters):
                        self.assertEqual(prepared.query(query,limit=2,distinct=distinct,filters=filters),
                                         corpus.search(report,query,limit=2,distinct=distinct,filters=filters))

    def test_caller_mutation_does_not_rewrite_prepared_receipt_or_rows(self):
        report=self.report();prepared=corpus.prepare_search(report);before=prepared.query('copper')
        report['status']='COMPLETED_WITH_GAPS'
        report['sources'][0]['segments'][0]['definitions'][0]['source_window']['sha256']='0'*64
        changed=corpus.search(report,'copper')
        self.assertNotEqual(changed['index_binding'],before['index_binding'])
        self.assertNotEqual(changed['receipt_binding'],before['receipt_binding'])
        self.assertEqual(prepared.query('copper'),before)
        self.assertEqual(changed['coverage_status'],'COMPLETED_WITH_GAPS')

    def test_nested_returned_mutation_does_not_poison_other_queries(self):
        report=self.report();original=canonical(report);prepared=corpus.prepare_search(report)
        expected=prepared.query('copper',distinct=True)
        returned=prepared.query('copper',distinct=True);poison(returned)
        self.assertEqual(prepared.query('copper',distinct=True),expected)
        self.assertEqual(canonical(report),original)

    def test_timing_change_reuses_semantic_binding_but_not_receipt(self):
        first=self.report();second=copy.deepcopy(first);second['elapsed_seconds']=3.25
        a=corpus.prepare_search(first).query('copper');b=corpus.prepare_search(second).query('copper')
        self.assertEqual(a['index_binding'],b['index_binding'])
        self.assertNotEqual(a['receipt_binding'],b['receipt_binding'])
        self.assertEqual(b['receipt_binding'],sha(canonical(second)))

    def test_snapshot_survives_lru_eviction_and_concurrent_queries(self):
        prepared=corpus.prepare_search(self.report());expected=prepared.query('copper')
        for index in range(4):corpus.search(self.report(f'other_{index}.py'),'copper')
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:prepared.query('copper'),range(20)))
        self.assertTrue(all(result==expected for result in results))
        results[0]['results'][0]['name']='caller mutation'
        self.assertEqual(prepared.query('copper'),expected)

    def test_opportunity_batch_preserves_full_leads_and_prepares_per_corpus(self):
        reports=[('one',self.report()),('two',self.report('other.py'))]
        options=[{'id':'a','code_queries':['convert copper to silver','symbol:has_silver']},
                 {'id':'b','code_queries':['copper','convert copper to silver']}]
        cache={(name,opportunities.query_intent(q)[0]):corpus.search(report,opportunities.query_intent(q)[0],
                  limit=opportunities.CODE_SEARCH_LIMIT,distinct=True)
               for name,report in reports for option in options for q in option['code_queries']}
        expected=opportunities.code_leads(options,reports,copy.deepcopy(cache))
        with patch('forge_core.corpus.prepare_search',wraps=corpus.prepare_search) as prepare:
            actual_cache={};actual=opportunities.code_leads(iter(options),iter(reports),actual_cache)
        self.assertEqual(actual,expected);self.assertEqual(actual_cache,cache)
        self.assertEqual(prepare.call_count,2)

    def test_empty_or_cached_queries_do_not_prepare_reports(self):
        report=self.report();options=[{'id':'a','code_queries':['copper']}]
        cache={('one','copper'):corpus.search(report,'copper',limit=12,distinct=True)}
        with patch('forge_core.corpus.prepare_search',side_effect=AssertionError('Unneeded preparation')):
            opportunities.code_leads(options,[('one',report)],cache)
            self.assertEqual(opportunities.code_leads([], [('one',report)],{}),[])
            self.assertEqual(opportunities.code_leads([{'id':'empty','code_queries':[]}],[('one',report)],{})[0]['candidates'],[])


if __name__=='__main__':unittest.main()
