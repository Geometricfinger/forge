"""Execute ONLY these reviewed synthetic programs using a fake recording API.

This tests static claims against Python behavior. No collected file, numerical
library, model, or user application is imported/executed by this oracle.
"""
import builtins,inspect,types,unittest
import hound

BODIES={
 'literal_false':'s=False\ntrimesh.registration.icp(a,b,scale=s,reflection=False)',
 'literal_true':'s=True\ntrimesh.registration.icp(a,b,scale=s,reflection=False)',
 'conditional':'s=flag\ntrimesh.registration.icp(a,b,scale=s,reflection=False)',
 'mutated_list':'settings=[False,False]\nsettings[0]=True\ns,r=settings\ntrimesh.registration.icp(a,b,scale=s,reflection=r)',
 'nonlocal_callback':'s=False\ndef flip():\n    nonlocal s\n    s=flag\nflip()\ntrimesh.registration.icp(a,b,scale=s,reflection=False)',
 'loop_else':'s=True\nfor x in a:\n    pass\nelse:\n    s=False\ntrimesh.registration.icp(a,b,scale=s,reflection=False)',
 'break_skips_else':'s=False\nfor x in [1]:\n    break\nelse:\n    s=True\ntrimesh.registration.icp(a,b,scale=s,reflection=False)',
 'terminated_else':'for x in a:\n    pass\nelse:\n    return None\ntrimesh.registration.icp(a,b,scale=False,reflection=False)',
 'shortcircuit':'s=False\nflag and (s:=True)\ntrimesh.registration.icp(a,b,scale=s,reflection=False)',
}

def oracle(body):
 code='import trimesh\ndef f(a,b,flag):\n'+''.join('    '+x+'\n' for x in body.splitlines())
 calls=[]
 def capture(a,b,**kw):
  calls.append((inspect.currentframe().f_back.f_lineno,kw));return None
 fake=types.SimpleNamespace(registration=types.SimpleNamespace(icp=capture))
 def importer(name,*args,**kw):
  if name!='trimesh':raise ImportError('Oracle allows only its fake API')
  return fake
 env={'__builtins__':{**vars(builtins),'__import__':importer}}
 exec(compile(code,'<reviewed-synthetic-oracle>','exec'),env)
 for seq in ([],[1],[1,2]):
  for flag in (False,True):env['f'](seq,None,flag)
 fs=[f for f in hound.scan_bytes(code.encode(),source_id='synthetic-oracle')['findings'] if f['profile_id']==hound.RIGID]
 return fs,calls

class RuntimeOracle(unittest.TestCase):pass

def build_test(body):
 def test(self):
  fs,calls=oracle(body)
  for f in fs:
   site=f['evidence'][0]['line_start'];seen=[v for line,v in calls if line==site]
   if f['status']=='STATIC_CANDIDATE':
    self.assertTrue(seen,'Known-dead source site was reported as a candidate')
    self.assertTrue(all(v['scale'] is False and v['reflection'] is False for v in seen))
   elif f['status']=='CONTRADICTED_CALL_CONTRACT':
    self.assertTrue(seen)
    self.assertTrue(all(v['scale'] is True or v['reflection'] is True for v in seen))
  # Unknowns remain allowed; this oracle does not turn abstention into accuracy.
 return test
for name,body in BODIES.items():setattr(RuntimeOracle,'test_'+name,build_test(body))
