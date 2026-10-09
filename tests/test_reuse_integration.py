"""Executed first-party integration and HTTP workflows, not stubbed scanner results."""
import copy,http.client,json,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from forge_core.common import Blocked,canonical,loads,sha,read
from forge_core.workspace import initialize,Workbench,ROOT
from forge_core.reuse import Casebook
from forge_core import reuse_trial,reuse_examples
from forge_core.server import Console

class FixedReuseTrial(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.w=initialize(Path(cls.temp.name)/'home')
        cls.result=reuse_trial.run(cls.w);cls.book=Casebook(cls.w.store)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def test_actual_scanner_observations(self):
        s=self.w.store.snapshot(self.result['mission'])
        self.assertEqual(s['counts']['DONE'],5);self.assertEqual(len(s['candidates']),1)
        self.assertEqual({f['qualified_name'] for f in s['candidates'][0]['findings']},set(reuse_trial.FUNCTIONS))
    def test_compare_before_tests_unknown(self):self.assertEqual(self.result['before_verdicts'],['NEEDS_EVIDENCE']*2)
    def test_working_candidate_passes_all(self):
        r=self.result['test_results'];bykey={x['candidate']['qualified_name']:x['candidate']['key'] for x in self.result['comparison']['rows']}
        self.assertEqual(sum(x['passed'] for x in r['results'] if x['candidate']==bykey['strict_json_fingerprint']),14)
    def test_plausible_candidate_rejected(self):self.assertEqual(self.result['verdicts']['simple_json_fingerprint'],'CONTRADICTED')
    def test_positive_candidate_remains_review_only(self):
        self.assertEqual(self.result['verdicts']['strict_json_fingerprint'],'READY_FOR_INTEGRATION_REVIEW')
        self.assertFalse(self.result['comparison']['release_approved']);self.assertFalse(self.result['comparison']['reuse_approved'])
    def test_results_are_executed_not_reviewed(self):
        rows=self.result['comparison']['rows']
        strict=next(x for x in rows if x['candidate']['qualified_name']=='strict_json_fingerprint')
        self.assertEqual(sum(x['state']=='TEST_PASSED' for x in strict['requirements']),14)
    def test_repeat_trial_is_idempotent(self):
        before=self.book.history(self.result['case_id']);self.book.attach_fixed_trial(self.result['case_id'])
        self.assertEqual(before,self.book.history(self.result['case_id']))
    def test_package_inputs_unchanged(self):
        self.assertEqual(sha(read(Path(reuse_examples.__file__))),self.result['test_results']['source_sha256'])
        self.assertTrue(self.result['test_results']['source_preserved'])
    def test_export_no_implementation_body(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'packet';self.book.export(self.result['case_id'],p)
            self.assertNotIn('def strict_json_fingerprint',(p/'comparison.json').read_text())
            self.assertIn('not permission to execute',(p/'Integration_Plan.md').read_text())
    def test_new_process_reads_same_comparison(self):
        p=subprocess.run([sys.executable,str(ROOT/'forge.py'),'--home',str(self.w.home),'review',self.result['case_id']],capture_output=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(loads(p.stdout),self.book.compare(self.result['case_id']))
    def test_source_binding_required_for_runtime(self):
        case=self.book.get(self.result['case_id']);case['candidates'][0]['source_sha256']='f'*64
        with self.assertRaises(Blocked):reuse_trial.run_for_case(case)
    def test_fixed_cases_cannot_be_replaced(self):
        case=self.book.get(self.result['case_id']);case['contract']['requirements']=case['contract']['requirements'][:1]
        with self.assertRaises(Blocked):reuse_trial.run_for_case(case)
    def test_strict_has_no_cross_language_promise(self):self.assertIn('not RFC 8785',reuse_trial.run_for_case(self.book.get(self.result['case_id']))['limitations'][1])
    def test_changed_record_observed(self):
        self.assertNotEqual(reuse_examples.strict_json_fingerprint(b'{"a":1}'),reuse_examples.strict_json_fingerprint(b'{"a":2}'))
    def test_oversize_rejected(self):
        with self.assertRaises(Blocked):reuse_examples.strict_json_fingerprint(b' '*100001)
    def test_receipt_rejects_missing_cases(self):
        r=copy.deepcopy(self.result['test_results']);r['results'].pop()
        with self.assertRaises(Blocked):reuse_trial.validate_receipt(self.book.get(self.result['case_id']),r)
    def test_receipt_rejects_duplicate_cases(self):
        r=copy.deepcopy(self.result['test_results']);r['results'][0]=r['results'][1]
        with self.assertRaises(Blocked):reuse_trial.validate_receipt(self.book.get(self.result['case_id']),r)
    def test_receipt_boolean_is_not_string(self):
        r=copy.deepcopy(self.result['test_results']);r['results'][0]['passed']='true'
        with self.assertRaises(Blocked):reuse_trial.validate_receipt(self.book.get(self.result['case_id']),r)
    def test_receipt_cannot_grant_release(self):
        r=copy.deepcopy(self.result['test_results']);r['release_approved']=True
        with self.assertRaises(Blocked):reuse_trial.validate_receipt(self.book.get(self.result['case_id']),r)
    def test_receipt_requires_original_fixture(self):
        r=copy.deepcopy(self.result['test_results']);r['fixture_sha256']='a'*64
        with self.assertRaises(Blocked):reuse_trial.validate_receipt(self.book.get(self.result['case_id']),r)
    def test_receipt_call_count_not_boolean(self):
        r=copy.deepcopy(self.result['test_results']);r['function_invocations']=True
        with self.assertRaises(Blocked):reuse_trial.validate_receipt(self.book.get(self.result['case_id']),r)
    def test_numeric_normalization_not_claimed(self):
        self.assertNotEqual(reuse_examples.strict_json_fingerprint(b'{"v":1}'),reuse_examples.strict_json_fingerprint(b'{"v":1.0}'))
    def test_unicode_normalization_not_claimed(self):
        self.assertNotEqual(reuse_examples.strict_json_fingerprint('{"v":"é"}'.encode()),reuse_examples.strict_json_fingerprint('{"v":"e\u0301"}'.encode()))
    def test_bytes_contract(self):
        with self.assertRaises(Blocked):reuse_examples.strict_json_fingerprint('{"a":1}')

class ReuseHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.w=initialize(Path(cls.tmp.name)/'home');cls.result=reuse_trial.run(cls.w)
        cls.srv=Console(cls.w);cls.thread=threading.Thread(target=cls.srv.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):cls.srv.shutdown();cls.srv.server_close();cls.thread.join();cls.tmp.cleanup()
    def request(self,path,body=None,auth=True):
        c=http.client.HTTPConnection('127.0.0.1',self.srv.server_address[1],timeout=10)
        headers={'Content-Type':'application/json'}
        if auth:headers['Authorization']='Bearer '+self.srv.token
        c.request('POST' if body is not None else 'GET',path,canonical(body) if body is not None else None,headers)
        r=c.getresponse();b=r.read();status=r.status;c.close();return status,loads(b)
    def test_comparison_route(self):
        status,r=self.request('/api/reuse?case='+self.result['case_id']);self.assertEqual(status,200);self.assertEqual(len(r['rows']),2)
    def test_new_state_lists_cases(self):
        s,r=self.request('/api/state');self.assertEqual(s,200);self.assertIn(self.result['case_id'],[x['id'] for x in r['reuse_cases']])
    def test_unauthorized_cannot_read(self):self.assertEqual(self.request('/api/reuse?case='+self.result['case_id'],auth=False)[0],403)
    def test_query_not_ambiguous(self):self.assertEqual(self.request('/api/reuse?case=a&case=b')[0],403)
    def test_export_http(self):
        s,r=self.request('/api/reuse/export',{'case':self.result['case_id']});self.assertEqual(s,200);self.assertTrue((Path(r['path'])/'SOURCE_LOCK.json').is_file())
    def test_no_command_endpoint(self):self.assertEqual(self.request('/api/reuse-demo',{'command':'anything'})[0],400)
    def test_bad_evidence_cannot_set_runtime(self):
        row=self.result['comparison']['rows'][0]
        s,_=self.request('/api/reuse/evidence',{'case':self.result['case_id'],'note':{'candidate':row['candidate']['key'],'requirement':'context','verdict':'SUPPORTED','actor':'test','note':'test','supersedes':None,'runtime_verified':True}})
        self.assertEqual(s,400)
    def test_no_case_scope_escape(self):self.assertEqual(self.request('/api/reuse?case=../../etc/passwd')[0],403)
    def test_contract_reuses_exact_state(self):
        s,r=self.request('/api/reuse',{'mission':self.result['mission'],'contract':reuse_trial.requirement_contract()})
        self.assertEqual(s,200);self.assertEqual(r['id'],self.result['case_id'])
