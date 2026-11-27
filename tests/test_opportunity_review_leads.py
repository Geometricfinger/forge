"""Inspectable partial retrieval stays separate from selected source candidates."""
import copy
import unittest
from test_prepared_search import mock_prepared_search

from forge_core import corpus, opportunities as op
from forge_core.common import Blocked, canonical
from forge_core.opportunity_examples import example_bundle
from test_research_retrieval import report_from


def hit(name, full=False, matched=None):
    return {'name':name,'definition_id':'definition_'+name,'source_id':'source_'+name,
            'source_sha256':'a'*64,'file_id':'archive','member_path':'package/helpers.py',
            'member_start_line':8,'member_end_line':12,
            'all_query_terms_matched':full,
            'matched_query_terms':['records'] if matched is None else matched}


def response(rows, total=None):
    return {'results':rows,'query_terms':['group','records','duplicates'],
            'index_binding':'b'*64,
            'distinct_matching_groups':len(rows) if total is None else total}


class ReviewLeadPolicy(unittest.TestCase):
    def run_leads(self, result, queries=None, corpora=None, cache=None):
        with mock_prepared_search(return_value=result) as search:
            groups=op.code_leads([{'id':'grouping','code_queries':queries or ['group records duplicates']}],
                                 corpora or [('parts',{})],{} if cache is None else cache)
        return groups[0],search

    def test_fourth_partial_is_inspectable_but_not_a_primary_candidate(self):
        rows=[hit('unrelated_'+str(i)) for i in range(3)]+[hit('group_records',matched=['group','records'])]
        group,search=self.run_leads(response(rows))
        review=group['review_candidates'][3]
        self.assertEqual(group['candidates'],[])
        self.assertEqual((review['name'],review['rank']),('group_records',4))
        self.assertEqual(review['missing_query_terms'],['duplicates'])
        self.assertEqual(review['matched_query_terms'],['group','records'])
        self.assertEqual((review['definition_id'],review['source_id']),
                         ('definition_group_records','source_group_records'))
        self.assertEqual((review['path'],review['start_line'],review['end_line']),
                         ('package/helpers.py',8,12))
        self.assertEqual(review['review_reason'],'PARTIAL_QUERY_TERMS')
        self.assertFalse(review['runtime_tested'])
        self.assertFalse(review['rights_reviewed'])
        self.assertEqual(search.call_args.kwargs,{'limit':12,'distinct':True})

    def test_full_matches_keep_the_primary_shortlist(self):
        rows=[hit('full_'+str(i),True,matched=['group','records','duplicates']) for i in range(4)]
        group,_=self.run_leads(response(rows))
        self.assertEqual([row['name'] for row in group['candidates']],['full_'+str(i) for i in range(4)])
        self.assertEqual(group['review_candidates'],[])
        self.assertTrue(all(row['all_query_terms_matched'] for row in group['candidates']))

    def test_symbol_intent_never_falls_back_to_partial_incidental_names(self):
        for query in ['symbol:group_records','group_records','Store.group']:
            with self.subTest(query=query):
                group,_=self.run_leads(response([hit('other_group'),hit('full_other',True)]),queries=[query])
                self.assertEqual(group['candidates'],[])
                self.assertEqual(group['review_candidates'],[])
                self.assertEqual(group['partial_matches_excluded_by_symbol'],1)

    def test_exact_symbol_preference_is_preserved_for_concept_queries(self):
        group,_=self.run_leads(response([hit('incidental',True),hit('Store.group',True)]),queries=['group'])
        self.assertEqual([row['name'] for row in group['candidates']],['Store.group'])

    def test_multiple_queries_deduplicate_without_losing_corpus_binding(self):
        group,_=self.run_leads(response([hit('shared')]),queries=['group records','collect records'],
                               corpora=[('one',{}),('two',{})])
        self.assertEqual([(row['corpus'],row['name']) for row in group['review_candidates']],
                         [('one','shared'),('two','shared')])
        self.assertEqual({row['query'] for row in group['review_candidates']},{'group records'})
        self.assertEqual(len(group['search_coverage']),4)

    def test_partial_also_found_by_a_full_query_is_not_duplicated_in_review(self):
        with mock_prepared_search(side_effect=[response([hit('shared')]),
                                                        response([hit('shared',True)])]):
            group=op.code_leads([{'id':'m','code_queries':['group records','collect records']}],
                                [('parts',{})],{})[0]
        self.assertEqual([row['name'] for row in group['candidates']],['shared'])
        self.assertEqual(group['candidates'][0]['query'],'collect records')
        self.assertEqual(group['review_candidates'],[])

    def test_both_shortlists_and_search_window_are_bounded(self):
        results=[response([hit(f'full_{i}',True) for i in range(13)],40),
                 response([hit(f'other_{i}',True) for i in range(12)],12),
                 response([hit(f'partial_{i}') for i in range(13)],43),
                 response([hit(f'more_{i}') for i in range(12)],12)]
        with mock_prepared_search(side_effect=results):
            group=op.code_leads([{'id':'m','code_queries':['one','two','three','four']}],[('parts',{})],{})[0]
        self.assertEqual(len(group['candidates']),12)
        self.assertEqual(len(group['review_candidates']),12)
        self.assertEqual(group['candidates_omitted'],12)
        self.assertEqual(group['review_candidates_omitted'],12)
        self.assertEqual(group['search_coverage'][0]['results_considered'],12)
        self.assertEqual(group['search_coverage'][0]['distinct_groups_omitted'],28)
        self.assertEqual(group['search_coverage'][2]['distinct_groups_omitted'],31)

    def test_unknown_search_totals_remain_unknown(self):
        result=response([hit('group_records')])
        del result['distinct_matching_groups']
        group,_=self.run_leads(result)
        self.assertIsNone(group['search_coverage'][0]['distinct_matching_groups'])
        self.assertIsNone(group['search_coverage'][0]['distinct_groups_omitted'])

    def test_empty_result_does_not_invent_an_inspectable_lead(self):
        group,_=self.run_leads(response([]))
        self.assertEqual(group['candidates'],[])
        self.assertEqual(group['review_candidates'],[])
        self.assertEqual(group['review_status'],'NO_PARTIAL_REVIEW_LEADS')
        self.assertEqual(group['search_coverage'][0]['distinct_groups_omitted'],0)

    def test_source_scope_is_inspectable_without_inventing_definition_identity(self):
        row=hit('<module>')
        del row['definition_id']
        group,_=self.run_leads(response([row]))
        self.assertIsNone(group['review_candidates'][0]['definition_id'])
        self.assertEqual(group['review_candidates'][0]['source_id'],'source_<module>')

    def test_missing_identity_is_not_published_as_a_review_lead(self):
        row={'name':'unbound','all_query_terms_matched':False}
        with self.assertRaisesRegex(Blocked,'CODE_RESULT_ID_MISSING'):
            self.run_leads(response([row]))

    def test_shared_search_is_cached_and_returned_terms_do_not_mutate_cache(self):
        result=response([hit('shared')]);before=canonical(result);cache={}
        with mock_prepared_search(return_value=result) as search:
            groups=op.code_leads([{'id':'a','code_queries':['group records']},
                                 {'id':'b','code_queries':['group records']}],[('parts',{})],cache)
        self.assertEqual(search.call_count,1)
        groups[0]['review_candidates'][0]['matched_query_terms'].append('poison')
        self.assertEqual(canonical(result),before)
        self.assertNotIn('poison',groups[1]['review_candidates'][0]['matched_query_terms'])


class RealMetadataReview(unittest.TestCase):
    def test_partial_components_after_full_text_decoys_remain_visible_across_tasks(self):
        tasks=[('group records preserving duplicates','group_records','Group records into buckets.'),
               ('escape markup preserving attributes','escape_markup','Escape markup text.'),
               ('hash streams preserving position','hash_stream','Hash streams in chunks.')]
        for query,name,description in tasks:
            with self.subTest(query=query):
                source="raise RuntimeError('source must never be imported')\n"
                for index in range(3):
                    source+=f'def notes_{index}(value):\n    """{query}."""\n    return value\n'
                source+=f'def {name}(value):\n    """{description}"""\n    return value\n'
                report=report_from([('components.py',source)])
                original=copy.deepcopy(report)
                old_window=corpus.search(report,query,limit=3,distinct=True)
                self.assertNotIn(name,[row['name'] for row in old_window['results']])
                group=op.code_leads([{'id':'m','code_queries':[query]}],[('parts',report)],{})[0]
                review=next(row for row in group['review_candidates'] if row['name']==name)
                self.assertGreater(review['rank'],3)
                self.assertTrue(review['missing_query_terms'])
                self.assertNotIn(name,[row['name'] for row in group['candidates']])
                self.assertEqual(report,original)


class ReviewLeadRendering(unittest.TestCase):
    def report(self,group):
        report=op.evaluate(example_bundle())
        report['hypotheses'][0]['code_leads']=[group]
        return report

    def test_partial_review_text_and_ids_are_escaped(self):
        value='<img src=x onerror=alert(1)>'
        result=response([hit(value)])
        with mock_prepared_search(return_value=result):
            group=op.code_leads([{'id':value,'code_queries':[value]}],[(value,{})],{})[0]
        group['review_candidates'][0]['missing_query_terms']=[value]
        group['review_candidates_omitted']=2
        rendered=op.render(self.report(group))
        self.assertNotIn('<img',rendered)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;',rendered)
        self.assertIn('definition_&lt;img',rendered)
        self.assertIn('Query: &lt;img',rendered)
        self.assertIn('Missing terms: &lt;img',rendered)
        self.assertIn('Review only. Inspect complete sources',rendered)
        self.assertIn('2 additional distinct partial leads omitted',rendered)

    def test_empty_partial_shortlist_has_an_explicit_state(self):
        rendered=op.render(self.report({'mechanism':'grouping','candidates':[],
                                        'review_candidates':[]}))
        self.assertIn('No partial review leads retained for this mechanism.',rendered)

    def test_historical_count_is_not_presented_as_an_empty_reviewed_shortlist(self):
        rendered=op.render(self.report({'mechanism':'grouping','candidates':[],
                                        'partial_matches_held_for_review':4}))
        self.assertNotIn('No partial review leads retained for this mechanism.',rendered)
        self.assertNotIn('Partial matches to inspect:',rendered)


if __name__=='__main__':
    unittest.main()
