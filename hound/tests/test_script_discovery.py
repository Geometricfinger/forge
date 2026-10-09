"""Frozen third-review tests: source-scope discovery is not a fake function."""
from contextlib import closing
import ast, gzip, hashlib, json, sqlite3, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import hound, persistent_hunt as ph, atlas_hunt

CALL='o.linalg.solve(a,b)'
SRC=('import numpy as o\nif __name__ == "__main__":\n '+CALL+'\n').encode()

def scan(src):return hound.scan_bytes(src.encode() if isinstance(src,str) else src,source_id='synthetic:script')
def observed(result,scope='script_findings',op='linear_system_solve'):
 return [f for f in result.get(scope,[]) if f.get('operation')==op]

class ScriptDiscovery(unittest.TestCase):
 def test_direct_module_call(self):self.assertTrue(observed(scan('import numpy as o\n'+CALL)))
 def test_main_guard_observed_not_executed(self):
  rows=observed(scan(SRC));self.assertTrue(rows)
  self.assertTrue(all(x.get('runtime_verified') is False and x.get('scope_kind')=='script' for x in rows))
 def test_no_fake_function(self):
  r=scan(SRC);self.assertEqual(r['functions_inspected'],0);self.assertEqual(r['findings'],[])
  rows=observed(r);self.assertTrue(rows)
  self.assertTrue(all(not ({'qualified_name','function_lines','function_id','card_id'} & set(x)) for x in rows))
 def test_function_body_not_script(self):
  r=scan('import numpy as o\ndef work(a,b,t):\n '+CALL)
  self.assertTrue(observed(r,'findings'));self.assertFalse(observed(r))
 def test_dead_branch_no_match(self):self.assertFalse(observed(scan('import numpy as o\nif False:\n '+CALL)))
 def test_misleading_comment(self):self.assertFalse(observed(scan('# '+CALL)))
 def test_shadowed_module(self):self.assertFalse(observed(scan('import numpy as o\no=object()\n'+CALL)))
 def test_import_after_call_not_used_early(self):self.assertFalse(observed(scan(CALL+'\nimport numpy as o')))
 def test_top_level_loop(self):self.assertTrue(observed(scan('import numpy as o\nfor item in range(3):\n '+CALL)))
 def test_empty_loop_no_match(self):self.assertFalse(observed(scan('import numpy as o\nfor item in []:\n '+CALL)))
 def test_no_original_execution(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'bad';scan(f'open({str(path)!r},"w").write("BAD")\nimport numpy as o\n'+CALL);self.assertFalse(path.exists())
 def test_deterministic_and_source_bound(self):
  a=scan(SRC);self.assertEqual(a,scan(SRC));rows=observed(a);self.assertTrue(rows)
  self.assertTrue(all(x['source_sha256']==hashlib.sha256(SRC).hexdigest() for x in rows))
 def test_numpy_solve(self):self.assertTrue(observed(scan('import numpy as n\ndef f(a,b):\n return n.linalg.solve(a,b)'),'findings','linear_system_solve'))
 def test_scipy_solve_alias(self):self.assertTrue(observed(scan('from scipy.linalg import solve as lin\ndef f(a,b):\n return lin(a,b)'),'findings','linear_system_solve'))
 def test_fake_solve_not_recognized(self):self.assertFalse(observed(scan('def f(np,a,b):\n return np.linalg.solve(a,b)'),'findings','linear_system_solve'))
 def test_rebound_solve_not_recognized(self):self.assertFalse(observed(scan('from numpy.linalg import solve\ndef f(a,b,other):\n solve=other\n return solve(a,b)'),'findings','linear_system_solve'))
 def test_array_solve(self):self.assertTrue(observed(scan('from array_api_compat import array_namespace\ndef f(a,b):\n xp=array_namespace(a)\n return xp.linalg.solve(a,b)'),'findings','linear_system_solve'))
 def test_valid_script_cache(self):
  r=scan(SRC);self.assertTrue(observed(r));ph.validate_result(r,r['source_id'],r['source_sha256'])
 def test_forged_function_binding_rejected(self):
  r=scan(SRC);self.assertTrue(observed(r));r['script_findings'][0]['card_id']='invented'
  f=r['script_findings'][0];f['finding_id']='finding_'+hound.sha(hound.canonical({k:v for k,v in f.items() if k!='finding_id'}))
  with self.assertRaises(ValueError):ph.validate_result(r,r['source_id'],r['source_sha256'])
 def test_bad_script_digest_rejected(self):
  r=scan(SRC);self.assertTrue(observed(r));r['script_findings'][0]['finding_id']='fake'
  with self.assertRaises(ValueError):ph.validate_result(r,r['source_id'],r['source_sha256'])
 def test_duplicate_script_rejected(self):
  r=scan(SRC);self.assertTrue(observed(r));r['script_findings'].append(r['script_findings'][0])
  with self.assertRaises(ValueError):ph.validate_result(r,r['source_id'],r['source_sha256'])

class ScriptAtlasIntegration(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.src=self.root/'src';self.src.mkdir();self.db=self.root/'c.sqlite';self.out=self.root/'out'
  (self.src/'script.py').write_bytes(SRC);self.sid='script-source';self.obs='observed-v1'
  source={'source_id':self.sid,'observation_id':self.obs,'sha256':hound.sha(SRC),'project_id':'synthetic','relative_path':'script.py','display_path':'script.py'}
  with closing(sqlite3.connect(self.db)) as c,c:
   c.executescript('CREATE TABLE sources(id TEXT,current_observation TEXT,active INT,payload TEXT); CREATE TABLE cards(id TEXT,source_id TEXT,observation_id TEXT,payload TEXT);')
   c.execute('INSERT INTO sources VALUES(?,?,?,?)',(self.sid,self.obs,1,json.dumps(source)))
  self.loc={self.sid:{'path':'script.py'}}
 def tearDown(self):self.tmp.cleanup()
 def test_persistent_source_bound_cart_and_resume(self):
  with patch.object(ph,'inspect_bounded',side_effect=lambda b,s: hound.scan_bytes(b,source_id=s)) as parse:
   a=ph.run(self.db,self.src,self.loc,self.out);self.assertEqual(a['coverage'],'COMPLETE_FOR_SUPPLIED_ATLAS');self.assertEqual(a['finding_count'],0)
   self.assertTrue(a.get('script_findings'));self.assertTrue(all(f['observation_id']==self.obs and 'card_id' not in f for f in a['script_findings']))
   parse.reset_mock();b=ph.run(self.db,self.src,self.loc,self.out);parse.assert_not_called();self.assertEqual(a['script_findings'],b['script_findings'])
   self.assertEqual(len(gzip.decompress((self.out/'script_findings.ndjson.gz').read_bytes()).splitlines()),len(a['script_findings']))
 def test_legacy_adapter_preserves_script_scope(self):
  with patch.object(atlas_hunt,'inspect_isolated',side_effect=lambda b,s: hound.scan_bytes(b,source_id=s)):
   r=atlas_hunt.hunt(self.db,self.src,self.loc,self.out)
  self.assertTrue(r.get('script_findings'));self.assertEqual(r['finding_count'],0)
 def test_source_change_does_not_reuse_script(self):
  with patch.object(ph,'inspect_bounded',side_effect=lambda b,s:hound.scan_bytes(b,source_id=s)):
   a=ph.run(self.db,self.src,self.loc,self.out);self.assertTrue(a.get('script_findings'))
   (self.src/'script.py').write_bytes(SRC+b'#change\n');b=ph.run(self.db,self.src,self.loc,self.out)
   self.assertEqual(b['coverage'],'PARTIAL_FOR_SUPPLIED_ATLAS');self.assertEqual(b.get('script_findings'),[])
