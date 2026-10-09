"""Fixed review controls: missing checks cannot become qualification."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from forge_core import interop,contract_trial as trial
from forge_core.common import Blocked,canonical,sha

class TrialGateTests(unittest.TestCase):
    def valid(self):
        return {'external_vector_report':trial.external_vectors(),
                'reference_report':{'status':'PASSED','passed':True,'numeric_cases':538,'record_cases':104,'numeric_mismatches':[],'record_mismatches':[],'node':'v22.16.0','v8':'12.x'},
                'admission_report':trial.admission_probe(),
                'discovery':{'status':'COMPLETED_FOR_SELECTED_CONTAINERS','selected_public_api_found':True,'source_code_executed_during_discovery':False},
                'handoff':{'matched':True,'raw_bytes_equal':False,'different_original_bytes':True,'baseline_digest_agrees':False},
                'model_calls':0,'external_network_requests':0,'release_approved':False,'independent_evaluation':False,'historical_ids_migrated':False,'arbitrary_source_execution':False}
    def test_valid_gate(self):self.assertTrue(trial.evaluate_gate(self.valid()))
    def test_empty_vectors(self):
        x=self.valid();x['external_vector_report']={'case_count':0,'cases':[],'summary':{'selected_component':0}};self.assertFalse(trial.evaluate_gate(x))
    def test_missing_node(self):
        x=self.valid();x['reference_report']={'status':'NOT_RUN','passed':False};self.assertFalse(trial.evaluate_gate(x))
    def test_false_node_with_success_label(self):
        x=self.valid();x['reference_report']['passed']=False;self.assertFalse(trial.evaluate_gate(x))
    def test_reference_mismatch(self):
        x=self.valid();x['reference_report']['record_mismatches']=[0];self.assertFalse(trial.evaluate_gate(x))
    def test_reference_boolean_count(self):
        x=self.valid();x['reference_report']['record_cases']=True;self.assertFalse(trial.evaluate_gate(x))
    def test_discovery_missing(self):
        x=self.valid();x['discovery']={'status':'NOT_RUN'};self.assertFalse(trial.evaluate_gate(x))
    def test_wrong_api(self):
        x=self.valid();x['discovery']['selected_public_api_found']=False;self.assertFalse(trial.evaluate_gate(x))
    def test_incomplete_source_coverage(self):
        x=self.valid();x['discovery']['status']='COMPLETED_WITH_GAPS';self.assertFalse(trial.evaluate_gate(x))
    def test_admission_failed(self):
        x=self.valid();x['admission_report']['passed']=False;self.assertFalse(trial.evaluate_gate(x))
    def test_duplicate_vector_ids(self):
        x=self.valid();x['external_vector_report']['cases'][1]['id']=x['external_vector_report']['cases'][0]['id'];self.assertFalse(trial.evaluate_gate(x))
    def test_invented_summary(self):
        x=self.valid();x['external_vector_report']['cases'][0]['selected_component']['passed']=False;self.assertFalse(trial.evaluate_gate(x))
    def test_authority_cannot_promote(self):
        for key in ('release_approved','independent_evaluation','historical_ids_migrated','arbitrary_source_execution'):
            x=self.valid();x[key]=True;self.assertFalse(trial.evaluate_gate(x))
    def test_malformed_returns_false(self):
        for x in (None,[],{},'passed'):
            self.assertFalse(trial.evaluate_gate(x))
    def test_invented_expected_value(self):
        x=self.valid();x['external_vector_report']['cases'][0]['expected']='invented';self.assertFalse(trial.evaluate_gate(x))

class AdmissionAndReferenceTests(unittest.TestCase):
    def test_all_admission_cases_execute(self):
        r=trial.admission_probe();self.assertTrue(r['passed']);self.assertEqual(r['case_count'],len(r['cases']));self.assertGreaterEqual(r['case_count'],20)
    def test_number_vectors_count(self):
        r=trial.external_vectors();self.assertEqual(r['case_count'],28);self.assertEqual(r['summary']['selected_component'],28)
    def test_missing_node_explicit(self):
        with patch.object(trial.shutil,'which',return_value=None):r=trial.node_check()
        self.assertEqual(r['status'],'NOT_RUN');self.assertFalse(r['passed'])
    def test_pin_includes_spec_vectors(self):
        for x in ('rfc8785_numbers.json','rfc8785_objects.json'):
            self.assertIn('contracts/interop/'+x,interop.PINS)
    def test_every_reference_record_within_domain(self):
        for raw in trial.accepted_records():self.assertIsInstance(interop.normalize_record(raw),bytes)
    def test_new_trial_forbids_old_outputs(self):
        class W:
            home=Path('/tmp/nonexistent-forge-contract-home')
            def guard(self):pass
        with tempfile.TemporaryDirectory() as p:
            with self.assertRaises(Blocked):trial.run(W(),Path(p))
    def test_baseline_exact_bytes_unchanged(self):
        self.assertEqual(canonical({'x':1.0,'label':'é'}),b'{"label":"\\u00e9","x":1.0}')

if __name__=='__main__':unittest.main()
