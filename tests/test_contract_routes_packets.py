"""Actual local routes and fixed-candidate integration, not a hostile-code sandbox."""
import copy,http.client,json,shutil,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from forge_core import interop,contract_trial as trial
from forge_core.common import Blocked,canonical,loads,sha
from forge_core.workspace import initialize,ROOT,code_binding
from forge_core.server import Console

class ContractRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.w=initialize(Path(cls.temp.name)/'home');cls.srv=Console(cls.w)
        cls.thread=threading.Thread(target=cls.srv.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):cls.srv.shutdown();cls.srv.server_close();cls.thread.join();cls.temp.cleanup()
    def request(self,path,obj=None,auth=True,origin=None):
        c=http.client.HTTPConnection('127.0.0.1',self.srv.server_port,timeout=10);headers={}
        if auth:headers['Authorization']='Bearer '+self.srv.token
        if origin:headers['Origin']=origin
        if obj is not None:headers['Content-Type']='application/json'
        c.request('GET' if obj is None else 'POST',path,body=None if obj is None else canonical(obj),headers=headers)
        r=c.getresponse();data=r.read();s=r.status;c.close();return s,data
    def test_show_contract_http(self):
        s,b=self.request('/api/integration-contract');self.assertEqual(s,200);self.assertEqual(loads(b),interop.contract())
    def test_contract_no_auth(self):self.assertEqual(self.request('/api/integration-contract',auth=False)[0],403)
    def test_create_matches_python_api(self):
        s,b=self.request('/api/interop/fingerprint',{'raw_json':'{"x":1.0}'});self.assertEqual(s,200);self.assertEqual(loads(b),interop.fingerprint(b'{"x":1.0}'))
    def test_duplicate_denied(self):self.assertEqual(self.request('/api/interop/fingerprint',{'raw_json':'{"x":1,"x":2}'})[0],400)
    def test_source_code_not_accepted(self):self.assertEqual(self.request('/api/interop/fingerprint',{'raw_json':'{}','code':'raise Exception()'})[0],400)
    def test_origin_rejected(self):self.assertEqual(self.request('/api/interop/fingerprint',{'raw_json':'{}'},origin='https://example.org')[0],400)
    def test_post_auth_rejected(self):self.assertEqual(self.request('/api/interop/fingerprint',{'raw_json':'{}'},auth=False)[0],400)
    def test_verify_equivalent_record(self):
        s,b=self.request('/api/interop/verify',{'raw_json':'{ "x":1 }','receipt':interop.fingerprint(b'{"x":1.0}')});self.assertEqual(s,200);self.assertTrue(loads(b)['matched']);self.assertFalse(loads(b)['raw_bytes_equal'])
    def test_invalid_receipt_rejected(self):self.assertEqual(self.request('/api/interop/verify',{'raw_json':'{}','receipt':{}})[0],400)
    def test_trial_cannot_override_command(self):self.assertEqual(self.request('/api/contract-trial',{'command':'anything'})[0],400)
    def test_trial_button_dispatches_only_fixed_operation(self):
        with patch.object(self.srv,'start_operation') as op:
            s,b=self.request('/api/contract-trial',{});self.assertEqual(s,200);op.assert_called_once_with('contract')
    def test_static_page(self):
        s,b=self.request('/contracts',auth=False);self.assertEqual(s,200);self.assertIn(b'Run fixed integration trial',b);self.assertIn(b'not a signature',b)
    def test_cli_fingerprint_no_write_to_input(self):
        folder=Path(self.temp.name);src=folder/'record.json';dst=folder/'receipt.json';src.write_bytes(b'{"x":1.0}')
        p=subprocess.run([sys.executable,str(ROOT/'forge.py'),'interop-fingerprint','--input',str(src),'--out',str(dst)],capture_output=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr);self.assertEqual(loads(dst.read_bytes()),interop.fingerprint(src.read_bytes()));self.assertEqual(src.read_bytes(),b'{"x":1.0}')
        p=subprocess.run([sys.executable,str(ROOT/'forge.py'),'interop-fingerprint','--input',str(src),'--out',str(dst)],capture_output=True,timeout=15);self.assertEqual(p.returncode,2)
    def test_cli_verify_and_show(self):
        p=subprocess.run([sys.executable,str(ROOT/'forge.py'),'contract-show'],capture_output=True,timeout=15);self.assertEqual(p.returncode,0);self.assertEqual(loads(p.stdout),interop.contract())
    def test_nested_dependency_covered_by_binding(self):
        b=code_binding();self.assertIn('forge_core/_vendor/rfc8785/_impl.py',b);self.assertIn('contracts/interop/rfc8785_numbers.json',b);self.assertIn('tools/interop_reference.cjs',b)

class ContractPacketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.w=initialize(Path(cls.temp.name)/'home');cls.out=Path(cls.temp.name)/'result'
        cls.result=trial.run(cls.w,cls.out)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def copy(self):
        p=Path(self.temp.name)/self._testMethodName;shutil.copytree(self.out,p);return p
    def rehash(self,p):
        data=loads((p/'packet.json').read_bytes());data['files']={k:sha((p/k).read_bytes()) for k in data['files']};(p/'packet.json').write_bytes(canonical(data))
    def test_actual_inspection_and_execution(self):
        self.assertEqual(self.result['discovery']['summary']['segments'],2);self.assertEqual(self.result['discovery']['summary']['definitions'],7)
        self.assertTrue(self.result['discovery']['selected_public_api_found']);self.assertTrue(self.result['reviewed_public_component_executed']);self.assertFalse(self.result['historical_ids_migrated'])
    def test_packet_roundtrip(self):
        r=trial.verify_packet(self.out);self.assertEqual(r['status'],'VERIFIED_LOCAL_PACKET');self.assertFalse(r['authenticated'])
    def test_modified_receipt_rejected(self):
        p=self.copy();r=loads((p/'handoff-receipt.json').read_bytes());r['authenticated']=True;(p/'handoff-receipt.json').write_bytes(canonical(r));self.rehash(p)
        with self.assertRaises(Blocked):trial.verify_packet(p)
    def test_added_member_rejected(self):
        p=self.copy();(p/'extra').write_text('extra')
        with self.assertRaises(Blocked):trial.verify_packet(p)
    def test_rehashed_authority_rejected(self):
        p=self.copy();r=loads((p/'results.json').read_bytes());r['release_approved']=True;r['status']='INCOMPLETE_OR_FAILED';(p/'results.json').write_bytes(canonical(r));(p/'Review.html').write_text(trial.render(r));self.rehash(p)
        with self.assertRaises(Blocked):trial.verify_packet(p)
    def test_changed_contract_rejected(self):
        p=self.copy();(p/'contract.json').write_bytes(b'{}');self.rehash(p)
        with self.assertRaises(Blocked):trial.verify_packet(p)
    def test_empty_tests_do_not_pass(self):
        p=self.copy();r=loads((p/'results.json').read_bytes());r['external_vector_report']['cases']=[];r['external_vector_report']['case_count']=0;(p/'results.json').write_bytes(canonical(r));(p/'Review.html').write_text(trial.render(r));self.rehash(p)
        with self.assertRaises(Blocked):trial.verify_packet(p)
    def test_original_components_unchanged(self):self.assertEqual(interop.verify_pins(),interop.PINS)

if __name__=='__main__':unittest.main()
