"""Fault injection into closure receipts. Integrity checks are not authentication."""
import copy
import unittest
from run_defect_closure import validate_closure, REPAIRS
from run_qualification import check_stage

def clean():
 return {'status':'KNOWN_DEFECTS_CLOSED_FOR_TESTED_SCOPE','package_unchanged':True,
  'universal_bug_freedom':False,'independent_evaluation':False,'release_approved':False,
  'baseline_disagreements':1,'candidate_disagreements':0,'case_count':50,'control_count':8,
  'rows':[{'id':str(i),'baseline_errors':['wrong'] if i==0 else [],'candidate_errors':[]} for i in range(50)],
  'fixture_sha256':'f'*64,'repair_reintroductions':[{'id':k,'disagreements':1,'fixture_sha256':'f'*64} for k in REPAIRS]}

class ClosureGate(unittest.TestCase):
 def test_clean_receipt(self):validate_closure(clean())
 def test_qualification_actually_checks_closure(self):check_stage('closure',clean())
 def rejected(self,change):
  r=clean();change(r)
  with self.assertRaises(ValueError):check_stage('closure',r)
 def test_missing_candidate_rows(self):self.rejected(lambda r:r.update(rows=[]))
 def test_reintroduced_bug_survives(self):self.rejected(lambda r:r['repair_reintroductions'][0].update(disagreements=0))
 def test_duplicate_repair(self):self.rejected(lambda r:r['repair_reintroductions'].__setitem__(1,copy.deepcopy(r['repair_reintroductions'][0])))
 def test_unfixed_candidate_case(self):self.rejected(lambda r:r['rows'][0].update(candidate_errors=['wrong']))
 def test_inflated_baseline_failures(self):self.rejected(lambda r:r.update(baseline_disagreements=40))
 def test_changed_contract(self):self.rejected(lambda r:r['repair_reintroductions'][0].update(fixture_sha256='a'*64))
 def test_no_discovery_witness(self):self.rejected(lambda r:r.update(baseline_disagreements=0))
 def test_universal_claim_refused(self):self.rejected(lambda r:r.update(universal_bug_freedom=True))
 def test_boolean_counter(self):self.rejected(lambda r:r.update(candidate_disagreements=False))
 def test_missing_control_cases(self):self.rejected(lambda r:r.update(control_count=0))
 def test_duplicate_case_id(self):self.rejected(lambda r:r['rows'][1].update(id=r['rows'][0]['id']))
 def test_invalid_repair_record(self):self.rejected(lambda r:r['repair_reintroductions'].__setitem__(0,42))
 def test_release_not_inferred(self):self.rejected(lambda r:r.update(release_approved=True))
