"""Qualification receipts cannot substitute empty checks or ineffective removals."""
import copy,unittest
import run_library_validation as lv
from run_qualification import check_stage

def clean():
 c=lv.contract();ids=c['case_ids'];row={'tests_run':len(ids),'failures':0,'errors':0,'skips':0,'case_ids':ids,'failed_case_ids':[],'fixture_hashes':c['fixture_hashes']}
 return {'status':'LIBRARY_REGRESSIONS_AND_REMOVALS_VERIFIED','package_unchanged':True,'release_approved':False,'independent_evaluation':False,'universal_correctness':False,
  'contract_sha256':lv.sha(lv.ROOT/'contracts/LIBRARY_TEST_CONTRACT.json'),'candidate':row,
  'repair_removals':[{'id':k,**copy.deepcopy(row),'failures':1,'failed_case_ids':[ids[0]]} for k in lv.REPAIRS]}
class LibraryGate(unittest.TestCase):
 def reject(self,change):
  r=clean();change(r)
  with self.assertRaises(ValueError):check_stage('library',r)
 def test_accept_correctly_scoped_receipt(self):check_stage('library',clean())
 def test_qualification_includes_stage(self):
  from run_qualification import SCRIPTS
  self.assertIn(('library','run_library_validation.py','library_validation.json'),SCRIPTS)
 def test_empty_candidate(self):self.reject(lambda r:r['candidate'].update(tests_run=0,case_ids=[]))
 def test_bool_count(self):self.reject(lambda r:r['candidate'].update(tests_run=True))
 def test_failed_candidate(self):self.reject(lambda r:r['candidate'].update(failures=1,failed_case_ids=[r['candidate']['case_ids'][0]]))
 def test_skipped_candidate(self):self.reject(lambda r:r['candidate'].update(skips=1))
 def test_error_candidate(self):self.reject(lambda r:r['candidate'].update(errors=1))
 def test_missing_removal(self):self.reject(lambda r:r['repair_removals'].pop())
 def test_duplicate_removal(self):self.reject(lambda r:r['repair_removals'].__setitem__(0,copy.deepcopy(r['repair_removals'][1])))
 def test_removal_survives(self):self.reject(lambda r:r['repair_removals'][0].update(failures=0,failed_case_ids=[]))
 def test_mutation_fixture_error(self):self.reject(lambda r:r['repair_removals'][0].update(errors=1))
 def test_changed_cases(self):self.reject(lambda r:r['candidate'].update(case_ids=['fake']*69))
 def test_changed_fixtures(self):self.reject(lambda r:r['candidate'].update(fixture_hashes={}))
 def test_release_claim(self):self.reject(lambda r:r.update(release_approved=True))
 def test_universal_claim(self):self.reject(lambda r:r.update(universal_correctness=True))
 def test_unmatched_failed_ids(self):self.reject(lambda r:r['repair_removals'][0].update(failed_case_ids=['not-a-test']))
 def test_missing_failed_ids(self):self.reject(lambda r:r['repair_removals'][0].update(failed_case_ids=[]))
 def test_changed_contract(self):self.reject(lambda r:r.update(contract_sha256='f'*64))
