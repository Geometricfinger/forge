"""Research-informed retrieval contracts; no external code or models execute."""
import copy, math, unittest
from forge_core.common import Blocked, canonical, sha
from forge_core.mixed_intake import inspect_container
from forge_core import corpus
from forge_core.retrieval import SearchIndex, bm25_term, reciprocal_rank_fusion, validate_filters
from forge_core.lexical import words

def report_from(files):
    sources=[]
    for i,(name,raw) in enumerate(files):
        fid='f'+str(i);data=raw.encode() if isinstance(raw,str) else raw
        src={'file_id':fid,'path':name,'size':len(data),'sha256':sha(data),'source_url':'https://drive.google.com/file/d/'+fid+'/view'}
        p=inspect_container(data,name);segments=[]
        for s in p['segments']:
            s=copy.deepcopy(s);s.pop('source_hex');s['source_id']='source_'+sha(canonical([fid,s['segment_local_id']]))
            for d in s['definitions']:
                d['definition_id']='definition_'+sha(canonical([s['source_id'],d['name'],d['start_line'],d['end_line']]))
                d['member_start_line']=s['start_line']+d['start_line']-1;d['member_end_line']=s['start_line']+d['end_line']-1
            s['hound']={'findings':[]};s['hound_status']='INSPECTED';segments.append(s)
        sources.append({'source':src,'segments':segments,'inventory':p['inventory'],'gaps':p['gaps'],'hound_errors':[]})
    return {'schema':1,'status':'COMPLETED_FOR_SELECTED_CONTAINERS','sources':sources,'source_bodies_retained':False,'upstream_code_executed':False,'corpus_manifest_sha256':sha(canonical([s['source'] for s in sources]))}

class RankingTests(unittest.TestCase):
    def simple(self):
        return report_from([('x.py','import os\ndef publish(path):\n    os.fsync(path)\n    return os.replace(path,path)\n\ndef a_notes():\n    """replace replace replace. not a publication implementation."""\n    return 1\n')])
    def test_call_only_retrieval(self):
        q=corpus.search(self.simple(),'fsync replace');self.assertEqual(q['results'][0]['name'],'publish');self.assertEqual(q['full_match_count'],1)
    def test_declared_api_is_not_observed(self):
        r=self.simple();q=corpus.search(r,'fsync');self.assertEqual(q['results'][0]['observed_apis'],[]);self.assertIn('os.fsync',q['results'][0]['declared_api_leads'])
    def test_required_api_rejects_declaration(self):
        q=corpus.search(self.simple(),'fsync',filters={'required_apis':['os.fsync']});self.assertEqual(q['results'],[]);self.assertEqual(q['exclusion_counts']['required_api_not_observed'],1)
    def test_required_api_accepts_hound_observation(self):
        r=self.simple();s=r['sources'][0]['segments'][0];s['hound']['findings']=[{'qualified_name':'publish','resolved_api':'os.fsync'}]
        q=corpus.search(r,'fsync',filters={'required_apis':['os.fsync']});self.assertEqual(q['results'][0]['name'],'publish');self.assertFalse(q['release_approved'])
    def test_excluded_api_blocks(self):
        r=self.simple();r['sources'][0]['segments'][0]['hound']['findings']=[{'qualified_name':'publish','resolved_api':'os.fsync'}]
        self.assertEqual(corpus.search(r,'fsync',filters={'exclude_apis':['os.fsync']})['results'],[])
    def test_exclusion_unknown_warning(self):
        q=corpus.search(self.simple(),'fsync',filters={'exclude_apis':['os.fsync']});self.assertTrue(q['warnings']);self.assertEqual(len(q['results']),1)
    def test_negation_not_silently_interpreted(self):
        q=corpus.search(self.simple(),'without replace');self.assertIn('NATURAL_LANGUAGE_NEGATION_NOT_ENFORCED_USE_EXPLICIT_REQUIREMENTS',q['warnings'])
    def test_no_result_is_not_absence(self):
        q=corpus.search(self.simple(),'teleporter');self.assertEqual(q['results'],[]);self.assertTrue(q['match_absence_is_not_capability_absence'])
    def test_exact_symbol(self):
        r=report_from([('x.py','def a():\n    """digest digest digest"""\n    return 1\ndef digest():\n    return 2\n')])
        self.assertEqual(corpus.search(r,'digest')['results'][0]['name'],'digest')
    def test_acronym_tokenization(self):self.assertEqual(words('HTTPServer parseJSONValue'),['http','server','parse','json','value'])
    def test_alias_search(self):
        r=report_from([('x.py','from hashlib import sha256 as h\ndef digest(x):\n    return h(x)\n')]);q=corpus.search(r,'sha256');self.assertEqual(q['results'][0]['name'],'digest')
    def test_alias_rebinding_not_resolved(self):
        r=report_from([('x.py','from hashlib import sha256 as h\ndef digest(x):\n    h = x\n    return h(x)\n')]);self.assertEqual(list(corpus.rows(r))[0]['declared_api_leads'],[])
    def test_nested_calls_not_parent(self):
        r=report_from([('x.py','import os\ndef outer():\n    def inner():\n        return os.fsync(1)\n    return inner\n')]);q=corpus.search(r,'fsync');self.assertEqual([x['name'] for x in q['results']],['outer.inner'])
    def test_group_keeps_all_locations(self):
        code='def digest(x):\n    return x\n';r=report_from([('a.py',code),('b.py',code)])
        q=corpus.search(r,'digest',distinct=True);self.assertEqual(len(q['results']),1);self.assertEqual(len(q['results'][0]['occurrences']),2);self.assertEqual(q['total_matching_occurrences'],2)
    def test_default_occurrence_compatibility(self):
        code='def digest(x):\n    return x\n';r=report_from([('a.py',code),('b.py',code)]);self.assertEqual(len(corpus.search(r,'digest')['results']),2)
    def test_different_source_not_grouped(self):
        r=report_from([('a.py','def digest(x): return x\n'),('b.py','def digest(x): return x+1\n')]);self.assertEqual(corpus.search(r,'digest',distinct=True)['distinct_matching_groups'],2)
    def test_duplicate_votes_not_multiplied(self):
        code='def lookup():\n    """retrieve reference"""\n    return 1\n'
        a=corpus.search(report_from([('a.py',code)]),'lookup',distinct=True)
        b=corpus.search(report_from([('a.py',code)]*1+[('copy'+str(i)+'.py',code) for i in range(30)]),'lookup',distinct=True)
        self.assertEqual(a['group_statistics_count'],b['group_statistics_count']);self.assertEqual(a['results'][0]['rank_breakdown']['rrf'],b['results'][0]['rank_breakdown']['rrf'])
    def test_filters_before_grouping(self):
        code='def digest(x): return x\n';r=report_from([('a.py',code),('b.py',code)]);q=corpus.search(r,'digest',distinct=True,filters={'container_ids':['f1']});self.assertEqual(q['results'][0]['occurrences'][0]['file_id'],'f1');self.assertEqual(len(q['results'][0]['occurrences']),1)
    def test_filtered_tests_do_not_return(self):
        r=report_from([('tests/test_a.py','def digest(): return 1\n')]);self.assertFalse(corpus.search(r,'digest')['results']);self.assertEqual(len(corpus.search(r,'digest',include_tests=True)['results']),1)
    def test_kind_filter(self):
        r=report_from([('x.py','import os\nx=os.fsync(1)\n')]);self.assertEqual(corpus.search(r,'fsync',filters={'record_kind':'function'})['results'],[])
    def test_script_not_fake_definition(self):
        r=report_from([('x.py','import os\nx=os.fsync(1)\n')]);q=corpus.search(r,'fsync',distinct=True);self.assertNotIn('definition_id',q['results'][0]);self.assertEqual(q['results'][0]['record_kind'],'source_scope')
    def test_rank_components_exposed(self):
        q=corpus.search(self.simple(),'fsync');self.assertIn('declared_call',q['results'][0]['matched_fields']);self.assertIn('field_bm25',q['results'][0]['rank_breakdown'])
    def test_no_confidence_claim(self):
        q=corpus.search(self.simple(),'fsync');self.assertNotIn('confidence',q);self.assertEqual(q['runtime_validation'],'NOT_RUN')
    def test_index_changes_when_report_changes(self):
        r=self.simple();a=corpus.search(r,'fsync');r['status']='COMPLETED_WITH_GAPS';b=corpus.search(r,'fsync');self.assertNotEqual(a['index_binding'],b['index_binding'])
    def test_metadata_not_mutated(self):
        r=self.simple();before=canonical(r);corpus.search(r,'fsync',distinct=True);self.assertEqual(before,canonical(r))
    def test_caller_result_cannot_poison_cache(self):
        r=self.simple();q=corpus.search(r,'fsync');q['results'][0]['name']='poison';self.assertEqual(corpus.search(r,'fsync')['results'][0]['name'],'publish')
    def test_bm25_equation(self):self.assertAlmostEqual(bm25_term(2,100,100,3),3*2*2.2/3.2)
    def test_bm25_no_term(self):self.assertEqual(bm25_term(0,1,1,4),0)
    def test_bm25_length_normalization(self):self.assertGreater(bm25_term(1,10,100,1),bm25_term(1,200,100,1))
    def test_bm25_saturation(self):self.assertLess(bm25_term(100,100,100,1),2.2)
    def test_rrf_equation(self):self.assertAlmostEqual(reciprocal_rank_fusion([['a','b'],['b','a']])['a'],1/61+1/62)
    def test_rrf_duplicate_rejected(self):
        with self.assertRaises(Blocked):reciprocal_rank_fusion([['a','a']])
    def test_invalid_boolean(self):
        with self.assertRaises(Blocked):corpus.search(self.simple(),'fsync',distinct=1)
    def test_invalid_filters(self):
        for filters in [{'required_apis':'os.fsync'},{'run':'rm'},{'required_apis':['os.*']},{'required_apis':['a'],'exclude_apis':['a']},{'record_kind':'anything'},{'container_ids':[]}]:
            with self.subTest(filters=filters),self.assertRaises(Blocked):validate_filters(filters)
    def test_duplicate_id_rejected(self):
        r=list(corpus.rows(self.simple()));
        with self.assertRaises(Blocked):SearchIndex([r[0],r[0]],'x')
    def test_bad_term_frequencies(self):
        r=self.simple();r['sources'][0]['segments'][0]['definitions'][0]['structure']['term_frequencies']['symbol']={'foo':True}
        with self.assertRaises(Blocked):corpus.search(r,'foo')
    def test_legacy_metadata_searchable(self):
        r=self.simple()
        for d in r['sources'][0]['segments'][0]['definitions']:d.pop('structure')
        q=corpus.search(r,'publish');self.assertEqual(q['results'][0]['context_status'],'REINDEX_REQUIRED')
    def test_partial_not_full(self):
        q=corpus.search(self.simple(),'fsync unicorn');self.assertEqual(q['search_status'],'PARTIAL_TERM_MATCHES_ONLY');self.assertEqual(q['full_match_count'],0)

if __name__=='__main__':unittest.main()
