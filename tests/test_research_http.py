import http.client, threading, tempfile, unittest, subprocess, sys
from pathlib import Path
from urllib.parse import urlencode
from forge_core import corpus,corpus_demo
from forge_core.workspace import initialize,ROOT
from forge_core.server import Console
from forge_core.common import loads,canonical

class ResearchHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.w=initialize(Path(cls.temp.name)/'home');cls.demo=corpus_demo.run(cls.w);cls.r=corpus.get(cls.w,cls.demo['run_id']);cls.definition=next(corpus.rows(cls.r))['definition_id']
        cls.srv=Console(cls.w);cls.worker=threading.Thread(target=cls.srv.serve_forever,daemon=True);cls.worker.start()
    @classmethod
    def tearDownClass(cls):cls.srv.shutdown();cls.srv.server_close();cls.worker.join();cls.temp.cleanup()
    def request(self,route,params,auth=True):
        c=http.client.HTTPConnection('127.0.0.1',self.srv.server_address[1],timeout=10);h={}
        if auth:h['Authorization']='Bearer '+self.srv.token
        c.request('GET',route+'?'+urlencode(params),headers=h);r=c.getresponse();data=r.read();code=r.status;c.close();return code,loads(data)
    def test_grouped_http_with_actual_hound(self):
        s,r=self.request('/api/corpus',{'run':self.demo['run_id'],'q':'digest','distinct':'1'});self.assertEqual(s,200);self.assertEqual(len(r['results']),1);self.assertEqual(r['total_matching_occurrences'],2)
    def test_required_api_http(self):
        s,r=self.request('/api/corpus',{'run':self.demo['run_id'],'q':'digest','required_api':'hashlib.sha256'});self.assertEqual(s,200);self.assertEqual(len(r['results']),2)
    def test_unobserved_required_api_http(self):
        s,r=self.request('/api/corpus',{'run':self.demo['run_id'],'q':'digest','required_api':'os.fsync'});self.assertEqual(s,200);self.assertEqual(r['results'],[])
    def test_context_endpoint(self):
        s,r=self.request('/api/corpus-context',{'run':self.demo['run_id'],'definition':self.definition});self.assertEqual(s,200);self.assertEqual(r['status'],'STATIC_CONTEXT_LEADS');self.assertFalse(r['dependency_closure_complete'])
    def test_context_unauthorized(self):
        self.assertEqual(self.request('/api/corpus-context',{'run':self.demo['run_id'],'definition':self.definition},False)[0],403)
    def test_invalid_query_boolean(self):
        self.assertEqual(self.request('/api/corpus',{'run':self.demo['run_id'],'q':'digest','distinct':'yes'})[0],403)
    def test_invalid_api_query(self):
        self.assertEqual(self.request('/api/corpus',{'run':self.demo['run_id'],'q':'digest','required_api':'os.*'})[0],403)
    def test_cli_group_and_context(self):
        p=subprocess.run([sys.executable,str(ROOT/'forge.py'),'--home',str(self.w.home),'corpus-search','--run-id',self.demo['run_id'],'--query','digest','--distinct'],capture_output=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(len(loads(p.stdout)['results']),1)
        p=subprocess.run([sys.executable,str(ROOT/'forge.py'),'--home',str(self.w.home),'corpus-context','--run-id',self.demo['run_id'],'--definition',self.definition],capture_output=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(loads(p.stdout)['definition_id'],self.definition)
    def test_export_includes_context_and_origins(self):
        out=Path(self.temp.name)/'packet';corpus.export_search(self.r,'digest',out,distinct=True);p=loads((out/'reuse_candidates.json').read_bytes());self.assertEqual(len(p['results']),1);self.assertEqual(len(p['results'][0]['occurrences']),2);self.assertEqual(len(p['dependency_contexts']),1)

if __name__=='__main__':unittest.main()
