import unittest
from pathlib import Path
import tempfile
from run_qualification import check_stage,run

class QualificationControls(unittest.TestCase):
 def test_clean_regression(self):check_stage('regression',{'tests_run':1,'failures':0,'errors':0,'skips':0})
 def test_zero_tests_fail(self):
  with self.assertRaises(ValueError):check_stage('regression',{'tests_run':0,'failures':0,'errors':0,'skips':0})
 def test_bool_count_fails(self):
  with self.assertRaises(ValueError):check_stage('regression',{'tests_run':True,'failures':0,'errors':0,'skips':0})
 def test_skipped_required_tests_fail(self):
  with self.assertRaises(ValueError):check_stage('regression',{'tests_run':10,'failures':0,'errors':0,'skips':1})
 def test_failed_required_tests_fail(self):
  with self.assertRaises(ValueError):check_stage('regression',{'tests_run':10,'failures':1,'errors':0,'skips':0})
 def test_missing_report_fails(self):
  with self.assertRaises(ValueError):check_stage('regression',None)
 def review(self):return {'case_count':1,'candidate_correct':1,'regressions':0,'rows':[{'id':'x','expected':['ok'],'candidate':['ok'],'candidate_correct':True,'baseline_correct':False}]}
 def test_matching_diagnostic(self):check_stage('review',self.review())
 def test_empty_diagnostic(self):
  r=self.review();r.update(case_count=0,rows=[])
  with self.assertRaises(ValueError):check_stage('review',r)
 def test_inflated_count(self):
  r=self.review();r['case_count']=2
  with self.assertRaises(ValueError):check_stage('review',r)
 def test_falsely_claimed_correct(self):
  r=self.review();r['rows'][0]['candidate']=['wrong']
  with self.assertRaises(ValueError):check_stage('review',r)
 def test_duplicate_case(self):
  r=self.review();r['case_count']=2;r['rows']*=2
  with self.assertRaises(ValueError):check_stage('review',r)
 def test_aggregate_does_not_replace_case(self):
  r=self.review();r['rows'][0]['candidate_correct']=False
  with self.assertRaises(ValueError):check_stage('review',r)
 def test_existing_output_is_not_reused(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):run(Path(d))
 def test_unbounded_rounds_refused(self):
  with self.assertRaises(ValueError):run(Path('/not-written'),rounds=100000)
