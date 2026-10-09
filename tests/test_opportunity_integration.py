import copy,http.client,json,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from forge_core.workspace import initialize,ROOT
from forge_core.server import Console
from forge_core.common import canonical,loads,sha,Blocked
from forge_core.opportunities import OpportunityStore,verify_export,evaluate,guided_bundle
from forge_core.opportunity_examples import example_bundle,live_case_study

class OpportunityIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.w=initialize(Path(cls.temp.name)/'home')
        cls.book=OpportunityStore(cls.w);cls.report=cls.book.run(example_bundle())
        cls.srv=Console(cls.w);cls.thread=threading.Thread(target=cls.srv.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):cls.srv.shutdown();cls.srv.server_close();cls.thread.join();cls.temp.cleanup()
    def request(self,path,body=None,auth=True,origin=None):
        c=http.client.HTTPConnection('127.0.0.1',self.srv.server_address[1],timeout=20)
        h={'Authorization':'Bearer '+self.srv.token} if auth else {}
        if origin:h['Origin']=origin
        if body is not None:h['Content-Type']='application/json'
        c.request('GET' if body is None else 'POST',path,body=canonical(body) if body is not None else None,headers=h)
        r=c.getresponse();data=r.read();status=r.status;c.close();return status,data
    def test_http_list(self):
        s,b=self.request('/api/opportunities');self.assertEqual(s,200);self.assertTrue(loads(b)['runs'])
    def test_http_read(self):
        s,b=self.request('/api/opportunity?run='+self.report['id']);self.assertEqual(s,200);self.assertEqual(loads(b),self.report)
    def test_http_unauthenticated(self):self.assertEqual(self.request('/api/opportunities',auth=False)[0],403)
    def test_host_origin_guard(self):self.assertEqual(self.request('/api/opportunities',origin='https://attacker.invalid')[0],403)
    def test_http_analyze_matches_engine(self):
        s,b=self.request('/api/opportunity/analyze',{'bundle':example_bundle()});self.assertEqual(s,200);self.assertEqual(loads(b),self.report)
    def test_unknown_fields_cannot_request_execution(self):
        s,b=self.request('/api/opportunity/analyze',{'bundle':example_bundle(),'execute':True});self.assertEqual(s,400)
    def test_changed_evidence_rejected_http(self):
        d=example_bundle();d['observations'][0]['evidence'][0]['quote']='made up';self.assertEqual(self.request('/api/opportunity/analyze',{'bundle':d})[0],400)
    def test_unknown_saved_corpus_fails_not_fake_empty(self):
        self.assertNotEqual(self.request('/api/opportunity/analyze',{'bundle':example_bundle(),'corpora':['missing']})[0],200)
    def test_feedback_http_cannot_approve(self):
        n={'hypothesis':self.report['hypotheses'][0]['id'],'decision':'APPROVE','reason':'Because I say so','actor':'Owner'}
        self.assertEqual(self.request('/api/opportunity/feedback',{'run':self.report['id'],'note':n})[0],400)
    def test_export_and_verify(self):
        s,b=self.request('/api/opportunity/export',{'run':self.report['id']});self.assertEqual(s,200);v=verify_export(Path(loads(b)['path']));self.assertFalse(v['market_validated'])
    def test_export_tamper_detected(self):
        d=Path(self.temp.name)/'damaged';self.book.export(self.report['id'],d);(d/'feedback.json').write_text('[] ');self.assertRaises(Blocked,verify_export,d)
    def test_export_extra_file_detected(self):
        d=Path(self.temp.name)/'extra';self.book.export(self.report['id'],d);(d/'extra').write_text('x');self.assertRaises(Blocked,verify_export,d)
    def test_static_page_served(self):
        s,b=self.request('/opportunities',auth=False);self.assertEqual(s,200);self.assertIn(b'Opportunity Lab',b)
    def test_cli_show_identical_to_http(self):
        p=subprocess.run([sys.executable,str(ROOT/'forge.py'),'--home',str(self.w.home),'opportunity-show',self.report['id']],capture_output=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(loads(p.stdout),self.report)
    def test_cli_invalid_bundle_nonzero(self):
        f=Path(self.temp.name)/'invalid.json';f.write_text('{"bundle":false}')
        p=subprocess.run([sys.executable,str(ROOT/'forge.py'),'--home',str(self.w.home),'opportunity-run','--bundle',str(f)],capture_output=True,timeout=20)
        self.assertNotEqual(p.returncode,0)
    def test_cli_packet_verifier(self):
        d=Path(self.temp.name)/'verifier';self.book.export(self.report['id'],d)
        p=subprocess.run([sys.executable,str(ROOT/'forge.py'),'opportunity-verify',str(d)],capture_output=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(loads(p.stdout)['files_verified'],5)
    def test_reopen_store_retains_exact_run(self):self.assertEqual(OpportunityStore(self.w).get(self.report['id']),self.report)
    def test_guided_form_pipeline(self):
        form={'note':'I manually reconstruct the identity of a received file.','actor':'Operator','domain':'Engineering','received':'file','needed':'identity','pattern':'identity_loss','observation':'manual'}
        s,b=self.request('/api/opportunity/guided',form);self.assertEqual(s,200);r=loads(b);self.assertEqual(r['hypotheses'][0]['state'],'SINGLE_SOURCE_PROBLEM')
    def test_guided_default_unknown_not_invented_pain(self):
        form={'note':'An interesting product listing.','actor':'Operator','domain':'Engineering','received':'file','needed':'identity','pattern':'identity_loss','observation':'unknown'}
        r=evaluate(guided_bundle(form));self.assertEqual(r['hypotheses'][0]['state'],'INFERRED_HANDOFF_GAP')
    def test_actual_retained_corpus_search_in_demo(self):
        from forge_core.opportunity_demo import run
        r=run(self.w);h=self.book.get(r['run']);self.assertEqual(r['summary']['hypotheses'],4);self.assertGreater(r['summary']['code_lead_count'],0)
        candidates=[c for x in h['hypotheses'] for m in x['code_leads'] for c in m['candidates']]
        self.assertTrue(any(c['name']=='match_catalog_entry' for c in candidates))
        self.assertTrue(all(c['source_sha256'] and len(c['source_sha256'])==64 for c in candidates))
        self.assertTrue(all(c['all_query_terms_matched'] for c in candidates))
        self.assertTrue(all(not c['runtime_tested'] for c in candidates))
    def test_schema_capacity_guard(self):
        d=example_bundle();d['sources']=[None];self.assertEqual(self.request('/api/opportunity/analyze',{'bundle':d})[0],400)

if __name__=='__main__':unittest.main()
