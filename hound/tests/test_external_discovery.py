"""Frozen initial repair targets; synthetic only, no target imports/execution."""
import ast,hashlib,json,textwrap,unittest
import hound
P='geometry.computational_primitives'
C='geometry.object_factor_context'
SID='github:scipy/scipy@'+'a'*40+':scipy/linalg/example.py'

def scan(s,sid='synthetic:external'):
 return hound.scan_bytes(textwrap.dedent(s).encode(),source_id=sid)['findings']
def hits(s,profile=P,sid='synthetic:external'):
 return [f for f in scan(s,sid) if f['profile_id']==profile]

def first(rows):
 if not rows: raise AssertionError('Expected source-supported candidate was not surfaced')
 return rows[0]

class ExternalDiscovery(unittest.TestCase):
 def test_numpy_solve_positive_control(self):
  self.assertEqual(first(hits('import numpy as n\ndef f(a,b): return n.linalg.solve(a,b)'))['operation'],'linear_system_solve')
 def test_argument_parameter_shadow_no_match(self):
  self.assertFalse(hits('import numpy as n\ndef f(n,a,b): return n.linalg.solve(a,b)'))
 def test_similar_other_library_no_match(self):
  self.assertFalse(hits('import not_numpy as n\ndef f(a,b): return n.linalg.solve(a,b)'))
 def test_reassigned_import_no_match(self):
  self.assertFalse(hits('import numpy as n\ndef f(a,b,other):\n n=other\n return n.linalg.solve(a,b)'))
 def test_dead_call_no_match(self):
  self.assertFalse(hits('import numpy as n\ndef f(a,b):\n return None\n n.linalg.solve(a,b)'))
 def test_comment_no_match(self):
  self.assertFalse(hits('def f(a,b):\n # numpy.linalg.solve(a,b)\n return a'))
 def test_relative_import_context(self):
  r=hits('from ._decomp_svd import svd as factor\ndef f(a):return factor(a)',sid=SID)
  self.assertEqual(first(r)['resolved_api'],'scipy.linalg._decomp_svd.svd')
 def test_relative_import_without_context_no_guess(self):
  self.assertFalse(hits('from ._decomp_svd import svd\ndef f(a):return svd(a)'))
 def test_relative_wrong_package_no_guess(self):
  self.assertFalse(hits('from ._decomp_svd import svd\ndef f(a):return svd(a)',sid=SID.replace('scipy/linalg/example.py','unrelated/linalg/example.py')))
 def test_relative_local_import(self):
  self.assertTrue(hits('def f(a):\n from ._decomp_svd import svd as fac\n return fac(a)',sid=SID))
 def test_relative_before_import_no_match(self):
  self.assertFalse(hits('def f(a):\n result=fac(a)\n from ._decomp_svd import svd as fac\n return result',sid=SID))
 def test_array_namespace(self):
  s='from scipy._lib._array_api import array_namespace as ns\ndef f(a):\n xp=ns(a)\n return xp.linalg.svd(a)'
  r=hits(s);self.assertEqual(first(r)['resolved_api'],'array_api.linalg.svd');self.assertEqual(first(r)['backend'],'RUNTIME_SELECTED_NOT_IDENTIFIED')
 def test_array_namespace_alias(self):
  s='from array_api_compat import array_namespace\ndef f(a):\n xp=array_namespace(a)\n la=xp.linalg\n operation=la.svd\n return operation(a)'
  self.assertTrue(hits(s))
 def test_unknown_namespace_no_guess(self):
  self.assertFalse(hits('def f(a,factory):\n xp=factory(a)\n return xp.linalg.svd(a)'))
 def test_reassigned_namespace_no_guess(self):
  s='from scipy._lib._array_api import array_namespace\ndef f(a,other):\n xp=array_namespace(a)\n xp=other\n return xp.linalg.svd(a)'
  self.assertFalse(hits(s))
 def test_namespace_attribute_mutation(self):
  s='from scipy._lib._array_api import array_namespace\ndef f(a,other):\n xp=array_namespace(a)\n alias=xp.linalg\n alias.svd=other\n return xp.linalg.svd(a)'
  self.assertFalse(hits(s))
 def test_namespace_escape(self):
  s='from scipy._lib._array_api import array_namespace\ndef f(a,callback):\n xp=array_namespace(a)\n callback(xp)\n return xp.linalg.svd(a)'
  self.assertFalse(hits(s))
 def test_namespace_known_consumers(self):
  s='from scipy._lib._array_api import array_namespace, _asarray, is_numpy\ndef f(a):\n xp=array_namespace(a)\n a=_asarray(a,xp=xp)\n if is_numpy(xp): return a\n return xp.linalg.svd(a)'
  self.assertTrue(hits(s))
 def test_unknown_namespace_branch_no_guess(self):
  s='from scipy._lib._array_api import array_namespace\ndef f(a,flag,other):\n xp=array_namespace(a)\n if flag: xp=other\n return xp.linalg.svd(a)'
  self.assertFalse(hits(s))
 def test_state_context_not_name_match(self):
  s='import numpy as np\nclass Renamed:\n def __init__(me,factor=None,update=True):\n  me.k=1 if factor is None else factor\n  me.enabled=update\n def fit(me,x):\n  if me.enabled is True: me.k=np.sum(x)\n def apply(me,x): return me.k*np.dot(x,me.matrix)'
  r=hits(s,C);self.assertEqual(len(r),1);c=first(r)['field_context'];self.assertEqual(c['field'],'k');self.assertEqual(len(c['writers']),2)
  self.assertFalse(first(r)['fixed_unity_proven']);self.assertEqual(c['writers'][0]['initializer']['supplied_parameter'],'factor')
 def test_state_keyword_does_not_prove_contract(self):
  self.assertFalse(hits('class ScaleAwareModel:\n def transform(self,x): return x',C))
 def test_nonmatrix_scaling_not_transform(self):
  self.assertFalse(hits('class Tool:\n def score(self,x): return self.scale*x',C))
 def test_unknown_dot_not_assumed_numpy(self):
  self.assertFalse(hits('class Tool:\n def apply(self,x): return self.scale * magic.dot(x,self.R)',C))
 def test_scalar_summary_not_coordinate_transform(self):
  s='import numpy as np\ndef f(a):\n _,s,_=np.linalg.svd(a)\n scale=np.sum(s)\n return scale'
  self.assertFalse(hits(s,C))
 def test_state_refs_within_source(self):
  s='import numpy as np\nclass T:\n def __init__(self,s=1):self.s=s\n def apply(self,x):return self.s*np.dot(x,self.R)'
  r=first(hits(s,C));self.assertEqual(r['field_context']['writers'][0]['qualified_name'],'T.__init__');self.assertFalse(r['runtime_verified'])
