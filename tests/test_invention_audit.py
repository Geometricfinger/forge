"""Frozen regression challenge for the 0.8 inventor audit.

These are developer-authored counterexamples, not independent market evidence.
Source/application code is not executed. Real retained metadata is queried in
separate comparisons, rather than fabricated in these policy unit tests.
"""
import copy
import unittest
from unittest.mock import patch
from forge_core import opportunities as op
from forge_core.common import Blocked
from forge_core.opportunity_examples import example_bundle, source, cite


def result_row(name, full=True):
    return {'name':name, 'definition_id':'definition_'+name,
            'source_id':'source1', 'source_sha256':'a'*64,
            'file_id':'container1', 'path':'pkg/module.py',
            'start_line':1,'end_line':4,'all_query_terms_matched':full}


def run_leads(query, rows):
    with patch('forge_core.corpus.search',return_value={'results':rows,'index_binding':'b'*64}):
        return op.code_leads([{'id':'m','code_queries':[query]}],[('catalog',{})],{})[0]


def blocked_flow():
    b=example_bundle(); w=b['workflows'][0]; ev=w['steps'][0]['evidence']
    w['steps'].insert(0,{'id':'measure','action':'Record distinguishing attributes',
        'requires':['calibration_record'],'produces':['distinguishing_information'],'evidence':ev})
    return b


def add_related_excerpt(b, uri):
    s=source('second','The same incident is summarized differently here.',kind='firsthand',uri=uri)
    b['sources'].append(s)
    b['observations'].append({**b['observations'][0],'id':'second_obs','evidence':[cite(s)]})


class QueryIntentTests(unittest.TestCase):
    def test_plain_hash_retains_full_semantic_candidate(self):
        self.assertEqual([r['name'] for r in run_leads('hash',[result_row('file_hash')])['candidates']],['file_hash'])
    def test_plain_alignment_retains_full_candidate(self):
        self.assertEqual(len(run_leads('alignment',[result_row('align_records')])['candidates']),1)
    def test_plain_receipt_prefers_present_exact_name(self):
        rows=[result_row('require_receipt'),result_row('Workspace.receipt')]
        self.assertEqual([r['name'] for r in run_leads('receipt',rows)['candidates']],['Workspace.receipt'])
    def test_legacy_underscored_symbol_remains_exact(self):
        self.assertEqual(run_leads('file_hash',[result_row('different_hash')])['candidates'],[])
    def test_qualified_symbol_remains_exact(self):
        self.assertEqual(run_leads('Store.receipt',[result_row('Other.receipt')])['candidates'],[])
    def test_explicit_symbol_prefix_resolves_exact(self):
        self.assertEqual(len(run_leads('symbol:hash',[result_row('hash'),result_row('file_hash')])['candidates']),1)
    def test_explicit_missing_symbol_not_broadened(self):
        self.assertEqual(run_leads('symbol:hash',[result_row('file_hash')])['candidates'],[])
    def test_malformed_explicit_symbol_rejected(self):
        self.assertRaises(Blocked,run_leads,'symbol:hash thing',[result_row('hash')])
    def test_partial_never_promoted_to_primary(self):
        row=run_leads('hash archive',[result_row('hash',False)])
        self.assertEqual(row['candidates'],[])
        self.assertEqual(row['partial_matches_held_for_review'],1)
    def test_concept_candidate_is_not_runtime_proof(self):
        c=run_leads('hash',[result_row('file_hash')])['candidates']
        self.assertTrue(c)
        self.assertIs(c[0]['runtime_tested'],False)
        self.assertIs(c[0]['rights_reviewed'],False)
    def test_empty_query_explicit_symbol_rejected(self):
        self.assertRaises(Blocked,run_leads,'symbol:',[])
    def test_query_selection_retains_source_identity(self):
        c=run_leads('hash',[result_row('file_hash')])['candidates']
        self.assertTrue(c)
        self.assertEqual(c[0]['source_sha256'],'a'*64)


class WorkflowDependencyTests(unittest.TestCase):
    def test_unmet_step_does_not_supply_downstream_prerequisite(self):
        b=blocked_flow(); h=next(h for h in op.evaluate(b)['hypotheses'] if h['need']['id']=='identity')
        m=next(m for m in h['mechanisms'] if m['id']=='matching')
        self.assertIn('distinguishing_information',m['missing_prerequisites'])
    def test_missing_output_propagates_to_consumer(self):
        b=blocked_flow(); w=b['workflows'][0]
        w['steps'][1]['requires'].append('distinguishing_information')
        self.assertIn('file',op.missing_fields(w)['review'])
    def test_independent_step_still_produces_output(self):
        w=blocked_flow()['workflows'][0]
        self.assertNotIn('file',op.missing_fields(w)['review'])
    def test_supplying_missing_root_unlocks_supported_chain(self):
        b=blocked_flow(); w=b['workflows'][0];w['initial_fields'].append('calibration_record')
        h=next(h for h in op.evaluate(b)['hypotheses'] if h['need']['id']=='identity')
        m=next(m for m in h['mechanisms'] if m['id']=='matching')
        self.assertEqual(m['missing_prerequisites'],[])
        self.assertIs(m['implementation_tested'],False)
    def test_later_producer_never_retroactively_fixes_earlier_consumer(self):
        b=blocked_flow(); w=b['workflows'][0]
        w['steps'].append({'id':'late','action':'Later measurement','requires':['artifact'],
                          'produces':['calibration_record'],'evidence':w['steps'][0]['evidence']})
        self.assertIn('calibration_record',op.missing_fields(w)['measure'])
    def test_known_field_is_not_revoked_by_an_unready_reproducer(self):
        w=blocked_flow()['workflows'][0];w['initial_fields'].append('distinguishing_information')
        w['steps'][1]['requires'].append('distinguishing_information')
        self.assertNotIn('file',op.missing_fields(w)['review'])
    def test_ordered_root_cause_trace_is_available(self):
        w=blocked_flow()['workflows'][0];w['steps'][1]['requires'].append('distinguishing_information')
        trace=op.workflow_trace(w)
        self.assertEqual(trace['review']['upstream_missing_inputs']['file'],['calibration_record'])
        self.assertIs(trace['review']['runtime_verified'],False)
    def test_initial_fields_and_sources_unchanged(self):
        b=blocked_flow();original=copy.deepcopy(b);op.evaluate(b);self.assertEqual(b,original)


class OriginAndScopeTests(unittest.TestCase):
    def bundle(self):
        b=example_bundle();b['sources'][0]['kind']='firsthand'
        b['sources'][0]['uri']='https://example.org/incident#one';return b
    def test_same_resource_paraphrases_are_one_group(self):
        b=self.bundle();add_related_excerpt(b,'https://example.org/incident#two')
        h=op.evaluate(b)['hypotheses'][0]
        self.assertEqual(h['state'],'SINGLE_SOURCE_PROBLEM')
    def test_same_resource_does_not_claim_independence(self):
        b=self.bundle();add_related_excerpt(b,'https://example.org/incident#two')
        h=op.evaluate(b)['hypotheses'][0]
        self.assertEqual(h['evidence']['independent_origin_groups'],1)
        self.assertIs(h['evidence']['independence_verified'],False)
    def test_same_uri_reordered_sources_same_families(self):
        b=self.bundle();add_related_excerpt(b,'https://example.org/incident#two')
        self.assertEqual(op.families(b['sources']),op.families(list(reversed(b['sources']))))
    def test_distinct_case_sensitive_paths_are_not_merged(self):
        b=self.bundle();add_related_excerpt(b,'https://example.org/Incident#two')
        self.assertEqual(op.evaluate(b)['hypotheses'][0]['evidence']['independent_origin_groups'],2)
    def test_distinct_query_ids_are_not_merged(self):
        b=self.bundle();b['sources'][0]['uri']='https://example.org/incident?id=1'
        add_related_excerpt(b,'https://example.org/incident?id=2')
        self.assertEqual(op.evaluate(b)['hypotheses'][0]['evidence']['independent_origin_groups'],2)
    def test_uri_provenance_is_not_rewritten(self):
        b=self.bundle();add_related_excerpt(b,'https://example.org/incident#two')
        r=op.evaluate(b)
        self.assertEqual({s['uri'] for s in r['source_register']},{s['uri'] for s in b['sources']})
    def test_other_workflow_omission_not_target_information_gap(self):
        b=example_bundle();b['observations'][0]['kind']='not_described';b['observations'][0]['scope']='analogy'
        self.assertEqual(op.evaluate(b)['hypotheses'][0]['state'],'INFERRED_HANDOFF_GAP')
    def test_unknown_scope_omission_not_target_information_gap(self):
        b=example_bundle();b['observations'][0]['kind']='not_described';b['observations'][0]['scope']='unspecified'
        self.assertEqual(op.evaluate(b)['hypotheses'][0]['state'],'INFERRED_HANDOFF_GAP')
    def test_actual_target_omission_remains_information_only(self):
        b=example_bundle();b['observations'][0]['kind']='not_described'
        self.assertEqual(op.evaluate(b)['hypotheses'][0]['state'],'INFORMATION_GAP_ONLY')


class InvestigationPlanTests(unittest.TestCase):
    def test_next_investigation_exists_and_is_not_executed(self):
        h=op.evaluate(example_bundle())['hypotheses'][0]
        p=h['next_investigation'];self.assertEqual(p['status'],'PLAN_ONLY')
        self.assertIs(p['execution_authorized'],False)
    def test_missing_discriminator_gets_a_falsification_question(self):
        h=op.evaluate(blocked_flow())['hypotheses'][0]
        plans=[x for x in op.evaluate(blocked_flow())['hypotheses'] if x['need']['id']=='identity'][0]['next_investigation']
        self.assertTrue(any(t['kind']=='prerequisite' for t in plans['tasks']))
    def test_existing_option_plan_does_not_request_build(self):
        b=example_bundle();b['observations'][0]['kind']='supported'
        h=op.evaluate(b)['hypotheses'][0]
        self.assertEqual(h['next_investigation']['tasks'][0]['kind'],'existing_solution_fit')
        self.assertIs(h['next_investigation']['execution_authorized'],False)
    def test_conflicting_evidence_goes_before_build_plan(self):
        b=example_bundle();b['sources'][0]['kind']='firsthand';add_related_excerpt(b,'https://example.org/other')
        b['observations'][-1]['kind']='refutes'
        self.assertEqual(op.evaluate(b)['hypotheses'][0]['next_investigation']['tasks'][0]['kind'],'resolve_conflict')
    def test_plan_has_falsification_and_not_just_support(self):
        h=op.evaluate(example_bundle())['hypotheses'][0]
        self.assertTrue(all(t['falsifier'] for t in h['next_investigation']['tasks']))
    def test_user_preference_never_becomes_success_probability(self):
        b=example_bundle();b['preferences']['identity_loss']=5
        h=op.evaluate(b)['hypotheses'][0]
        self.assertIs(h['priority_is_probability'],False)
        self.assertIs(h['next_investigation']['market_validated'],False)


if __name__=='__main__':unittest.main()
