"""Real preserved Hound, then real authenticated loopback endpoints. No source execution."""
import http.client,json,threading,unittest,tempfile,subprocess,sys
from pathlib import Path
from forge_core.workspace import initialize,ROOT
from forge_core import corpus,corpus_demo
from forge_core.server import Console
from forge_core.common import canonical,loads

class CorpusIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.w=initialize(Path(cls.temp.name)/'home');cls.demo=corpus_demo.run(cls.w);cls.r=corpus.get(cls.w,cls.demo['run_id'])
        cls.srv=Console(cls.w);cls.thread=threading.Thread(target=cls.srv.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):cls.srv.shutdown();cls.srv.server_close();cls.thread.join();cls.temp.cleanup()
    def request(self,path,body=None,auth=True):
        c=http.client.HTTPConnection('127.0.0.1',self.srv.server_address[1],timeout=10);h={'Content-Type':'application/json'}
        if auth:h['Authorization']='Bearer '+self.srv.token
        c.request('POST' if body is not None else 'GET',path,canonical(body) if body is not None else None,h);r=c.getresponse();b=r.read();s=r.status;c.close();return s,loads(b)
    def test_actual_hound(self):self.assertEqual(self.r['summary']['hound_observations'],3);self.assertEqual(self.r['summary']['hound_segments'],3)
    def test_api_binding_to_function(self):
        r=corpus.search(self.r,'digest');self.assertEqual(len(r['results']),2);self.assertTrue(all('hashlib.sha256' in x['observed_apis'] for x in r['results']))
    def test_original_code_not_executed(self):self.assertFalse(self.r['upstream_code_executed']);self.assertTrue(all(x['runtime_status']=='NOT_RUN' for x in corpus.rows(self.r)))
    def test_list_endpoint(self):
        s,r=self.request('/api/corpora');self.assertEqual(s,200);self.assertIn(self.demo['run_id'],[x['id'] for x in r['corpora']])
    def test_search_endpoint(self):
        s,r=self.request('/api/corpus?run='+self.demo['run_id']+'&q=digest');self.assertEqual(s,200);self.assertEqual(r['full_match_count'],2)
    def test_private_without_token(self):self.assertEqual(self.request('/api/corpora',auth=False)[0],403)
    def test_no_arbitrary_source_root(self):self.assertEqual(self.request('/api/corpus-demo',{'source_root':'/etc'})[0],400)
    def test_query_traversal(self):self.assertEqual(self.request('/api/corpus?run=..%2f..%2fsecret&q=digest')[0],403)
    def test_new_process_cli(self):
        p=subprocess.run([sys.executable,str(ROOT/'forge.py'),'--home',str(self.w.home),'corpus-search','--run-id',self.demo['run_id'],'--query','digest'],capture_output=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(loads(p.stdout)['full_match_count'],2)
    def test_report_and_cart_no_implementation(self):
        s=json.dumps(self.r);self.assertNotIn('source_hex',s);self.assertNotIn('def digest(',s);self.assertFalse(self.r['release_approved'])

    def test_script_with_functions_keeps_real_hound_script_calls(self):
        from forge_core.engine import Analyzer
        from forge_core.common import sha,read
        body=b"import hashlib\ndef helper(): return 1\nhashlib.sha256(b'a')\n"
        source={'file_id':'scriptfixture','path':'script.py','sha256':sha(body),'size':len(body),'source_url':'fixture://scriptfixture'}
        profile=loads(read(ROOT/'templates/corpus_profile.json'))
        observed=corpus.inspect_one(body,source,self.w.home,profile,Analyzer(self.w.home))
        rows=list(corpus.source_rows({'sources':[observed]}))
        self.assertEqual(len(rows),1);self.assertIn('hashlib.sha256',rows[0]['observed_apis']);self.assertNotIn('definition_id',rows[0])

if __name__=='__main__':unittest.main()
