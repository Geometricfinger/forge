"""Second review prompted by real retained-corpus output, not only fixture counts."""
import copy,unittest,json,tempfile
from pathlib import Path
from unittest.mock import patch
from forge_core.opportunities import *
from forge_core.opportunity_examples import example_bundle,live_case_study,source,cite

class ResultReview(unittest.TestCase):
    def setUp(self):self.b=example_bundle()
    def test_mechanism_not_guaranteed_to_supply_required_output(self):
        h=evaluate(self.b)['hypotheses'][0];m=next(x for x in h['mechanisms'] if x['id']=='matching')
        self.assertEqual(m['output_fit'],'SUPPORTING_COMPONENT_ONLY');self.assertIn('identity',m['unprovided_required_fields'])
    def test_exact_output_still_declared_not_verified(self):
        self.b['mechanisms'][0]['provides']=['identity'];m=next(x for x in evaluate(self.b)['hypotheses'][0]['mechanisms'] if x['id']=='matching')
        self.assertEqual(m['output_fit'],'DECLARED_OUTPUT_MATCH');self.assertFalse(m['implementation_tested'])
    def test_existing_solution_suppresses_new_application_pitch(self):
        self.b['observations'][0]['kind']='supported';h=evaluate(self.b)['hypotheses'][0]
        self.assertEqual(h['concept']['recommended_action'],'EVALUATE_EXISTING_OPTION');self.assertEqual(h['code_leads'],[])
    def test_conflict_pauses_build_recommendation(self):
        self.b['sources'][0]['kind']='firsthand';s=source('counter','The current workflow already supplies the required identity.',kind='product_docs');self.b['sources'].append(s)
        self.b['observations'].append({'id':'counter','workflow':'handoff','need':'identity','kind':'supported','scope':'target','statement':'Current support reported.','evidence':[cite(s)]})
        h=evaluate(self.b)['hypotheses'][0];self.assertEqual(h['concept']['recommended_action'],'RESOLVE_EVIDENCE_FIRST');self.assertEqual(h['code_leads'],[])
    def test_partial_code_match_not_main_candidate(self):
        response={'results':[{'name':'unrelated','definition_id':'d1','source_id':'s1','all_query_terms_matched':False}], 'index_binding':'i'}
        with patch('forge_core.corpus.search',return_value=response):
            r=code_leads([self.b['mechanisms'][0]],[('c',{})],{})
        self.assertEqual(r[0]['candidates'],[]);self.assertGreater(r[0]['partial_matches_held_for_review'],0)
    def test_exact_symbol_preferred_over_incidental_all_terms(self):
        response={'results':[{'name':'other','definition_id':'d1','source_id':'s1','all_query_terms_matched':True,'exact_symbol_match':False},
                             {'name':'match_catalog_entry','definition_id':'d2','source_id':'s2','all_query_terms_matched':True,'exact_symbol_match':True}], 'index_binding':'i'}
        m=copy.deepcopy(self.b['mechanisms'][0]);m['code_queries']=['match_catalog_entry']
        with patch('forge_core.corpus.search',return_value=response):r=code_leads([m],[('c',{})],{})
        self.assertEqual([x['name'] for x in r[0]['candidates']],['match_catalog_entry'])
    def test_no_hit_never_invents_function(self):
        with patch('forge_core.corpus.search',return_value={'results':[],'index_binding':'i'}):r=code_leads([self.b['mechanisms'][0]],[('c',{})],{})
        self.assertEqual(r[0]['candidates'],[])
    def test_script_does_not_get_function_identity(self):
        response={'results':[{'name':'<module>','source_id':'s1','all_query_terms_matched':True,'exact_symbol_match':False}], 'index_binding':'i'}
        m=copy.deepcopy(self.b['mechanisms'][0]);m['code_queries']=['module operation']
        with patch('forge_core.corpus.search',return_value=response):r=code_leads([m],[('c',{})],{})
        self.assertIsNone(r[0]['candidates'][0]['definition_id'])
    def test_draft_cues_not_automatic_semantic_evidence(self):
        r=draft(source('p','A user must do a manual action.'));self.assertTrue(all(x['status']=='ANNOTATION_REQUIRED' for x in r['suggestions']))
    def test_duplicate_need_for_same_handoff_blocked(self):
        n=copy.deepcopy(self.b['workflows'][0]['needs'][0]);n['id']='again';self.b['workflows'][0]['needs'].append(n);self.assertRaises(Blocked,evaluate,self.b)
    def test_scope_collection_status_explicit(self):
        r=evaluate(self.b);self.assertEqual(r['coverage']['internet_coverage'],'NOT_MEASURED');self.assertFalse(r['coverage']['exhaustive_market_review'])
    def test_source_capture_not_fabricated_verbatim(self):
        r=evaluate(live_case_study());s=next(s for s in r['source_register'] if s['id']=='operator_report')
        self.assertEqual(s['capture_method'],'attributed_summary')

if __name__=='__main__':unittest.main()
