"""Frozen adversarial contracts for mission provenance and value-review eligibility."""
import copy,json,tempfile,unittest
from pathlib import Path
import hound,mission_hunt as mh,value_gate as vg
import test_mission_hunt as mf
import test_hound as gf

class MissionContractReview(unittest.TestCase):
 def setUp(self):
  self.f=mf.MissionTests();self.f.setUp()
 def tearDown(self):self.f.tearDown()
 def run_query(self):return mh.query(self.f.m,self.f.r,self.f.db)
 def resign(self,f):
  f.pop('finding_id',None);f['finding_id']='finding_'+hound.sha(hound.canonical(f))
 def test_disqualifier_profile_must_have_run(self):
  self.f.m['disqualifiers'].append({'id':'scale','profile_id':hound.SCALE,'statuses':['INPUT_ALIAS_RECEIVER']})
  self.f.r['selected_profiles']=[hound.RIGID]
  with self.assertRaises(ValueError):self.run_query()
 def test_required_profile_must_have_run(self):
  self.f.r['selected_profiles']=[hound.ABSTAIN]
  with self.assertRaises(ValueError):self.run_query()
 def test_duplicate_coverage_rejected(self):
  self.f.r['source_coverage'].append(copy.deepcopy(self.f.r['source_coverage'][0]))
  with self.assertRaises(ValueError):self.run_query()
 def test_stale_coverage_observation_rejected(self):
  self.f.r['source_coverage'][0]['observation_id']='stale'
  with self.assertRaises(ValueError):self.run_query()
 def test_stale_coverage_hash_rejected(self):
  self.f.r['source_coverage'][0]['source_sha256']='b'*64
  with self.assertRaises(ValueError):self.run_query()
 def test_renamed_finding_rejected_even_if_rehashed(self):
  f=self.f.r['findings'][0];f['qualified_name']='someone_else';self.resign(f)
  with self.assertRaises(ValueError):self.run_query()
 def test_different_function_range_rejected(self):
  f=self.f.r['findings'][0];f['function_lines']=[1,999];self.resign(f)
  with self.assertRaises(ValueError):self.run_query()
 def test_outside_evidence_range_rejected(self):
  f=self.f.r['findings'][0];f['evidence'][0]['line_start']=999;self.resign(f)
  with self.assertRaises(ValueError):self.run_query()
 def test_unknown_status_does_not_silently_return_empty(self):
  self.f.m['requirements'][0]['statuses']=['ALL_GOOD_TRUST_ME']
  with self.assertRaises(ValueError):self.run_query()
 def test_cross_profile_status_rejected(self):
  self.f.m['requirements'][0]['statuses']=['INPUT_ALIAS_RECEIVER']
  with self.assertRaises(ValueError):self.run_query()
 def test_empty_dimension_set_rejected(self):
  self.f.m['requirements'][0]['dimensions']=[]
  with self.assertRaises(ValueError):self.run_query()
 def test_empty_operations_rejected(self):
  self.f.m['requirements'][0]['operations']=[]
  with self.assertRaises(ValueError):self.run_query()
 def test_empty_clause_id_rejected(self):
  self.f.m['requirements'][0]['id']=''
  with self.assertRaises(ValueError):self.run_query()
 def add_context_card(self,active,card_obs):
  import sqlite3
  from contextlib import closing
  with closing(sqlite3.connect(self.f.db)) as c,c:
   src={'source_id':'helper','observation_id':'now','sha256':'c'*64,'project_id':'p','relative_path':'helper.py'}
   card={'card_id':'helper-card','source_id':'helper','observation_id':card_obs,'function_id':'hf','project_id':'p','relative_path':'helper.py','qualified_name':'helper'}
   c.execute('INSERT INTO sources VALUES(?,?,?,?)',('helper','now',active,json.dumps(src)))
   c.execute('INSERT INTO cards VALUES(?,?,?,?)',('helper-card','helper',card_obs,json.dumps(card)))
  self.f.r['catalog_sha256']=hound.sha(self.f.db.read_bytes())
  captures=[]
  from unittest.mock import patch
  with patch.object(mh,'context_trails',side_effect=lambda card,cards:captures.extend(cards) or []):self.run_query()
  return [c['card_id'] for c in captures]
 def test_inactive_context_target_not_used(self):
  self.assertNotIn('helper-card',self.add_context_card(0,'now'))
 def test_old_context_observation_not_used(self):
  self.assertNotIn('helper-card',self.add_context_card(1,'old'))

class ValueContractReview(unittest.TestCase):
 def setUp(self):self.f=gf.GateTests();self.f.setUp()
 def tearDown(self):self.f.tearDown()
 def rejected(self):self.assertEqual(self.f.run_gate()['status'],'NOT_ELIGIBLE_FOR_ADOPTION_REVIEW')
 def test_different_with_regression_rejected(self):
  self.f.p.update(mode='different',requirement_id='context');self.f.e['required_regressions']=1;self.rejected()
 def test_different_missing_regression_evidence_rejected(self):
  self.f.p.update(mode='different',requirement_id='context');self.f.e.pop('required_regressions');self.rejected()
 def test_bool_not_integer_regression_count(self):
  self.f.e['required_regressions']=False;self.rejected()
 def test_nonfinite_delta_rejected(self):
  self.f.e['metrics']['correct_decisions']={'baseline':-1e308,'candidate':1e308};self.rejected()
 def test_tradeoff_cannot_be_boolean(self):
  self.f.e['tradeoffs']=True;self.rejected()
 def test_limitations_cannot_be_whitespace(self):
  self.f.e['limitations']=[' '];self.rejected()
 def test_evidence_root_must_be_mapping(self):
  self.f.e=[];self.rejected()
 def test_proposal_must_be_mapping(self):
  r=vg.assess([],self.f.root);self.assertEqual(r['status'],'NOT_ELIGIBLE_FOR_ADOPTION_REVIEW')
 def test_difference_test_ids_must_be_list(self):
  self.f.p.update(mode='different',requirement_id='context');self.f.e['requirements']['context']['acceptance_test_ids']='case1';self.rejected()
 def test_difference_test_ids_must_be_unique(self):
  self.f.p.update(mode='different',requirement_id='context');self.f.e['requirements']['context']['acceptance_test_ids']=['case1','case1'];self.rejected()
 def test_empty_problem_rejected(self):
  self.f.p['problem']=' ';self.rejected()
 def test_invalid_suite_digest_rejected(self):
  self.f.p['suite_sha256']='not-a-digest';self.f.e['suite_sha256']='not-a-digest';self.rejected()
