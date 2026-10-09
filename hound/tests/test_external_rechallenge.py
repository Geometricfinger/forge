"""Second review: invalidation, context boundaries and honest state explanations."""
import unittest,json,hashlib,tempfile
from pathlib import Path
import hound
from test_external_discovery import hits,scan,first,P,C,SID

class ExternalRechallenge(unittest.TestCase):
 def test_namespace_survives_declared_array_ops(self):
  s='from scipy._lib._array_api import array_namespace\ndef f(a):\n xp=array_namespace(a)\n b=xp.asarray(a)\n b=b-xp.mean(b,axis=0)\n return xp.linalg.svd(b)'
  self.assertTrue(hits(s))
 def test_closure_callback_invalidates_namespace(self):
  s='from scipy._lib._array_api import array_namespace\ndef f(a,callback):\n xp=array_namespace(a)\n def change(): xp.linalg.svd=callback\n change()\n return xp.linalg.svd(a)'
  self.assertFalse(hits(s))
 def test_import_member_alias_mutation(self):
  s='import numpy as n\ndef f(a,b,other):\n alias=n.linalg\n alias.solve=other\n return n.linalg.solve(a,b)'
  self.assertFalse(hits(s))
 def test_namespace_unresolved_method_invalidates(self):
  s='from scipy._lib._array_api import array_namespace\ndef f(a):\n xp=array_namespace(a)\n xp.extension_can_mutate_namespace()\n return xp.linalg.svd(a)'
  self.assertFalse(hits(s))
 def test_namespace_factory_named_other(self):
  self.assertFalse(hits('from malicious import array_namespace\ndef f(a):\n xp=array_namespace(a)\n return xp.linalg.svd(a)'))
 def test_relative_parent(self):
  s='from ..linalg._decomp_svd import svd\ndef f(a): return svd(a)'
  self.assertTrue(hits(s,sid=SID.replace('scipy/linalg/example.py','scipy/spatial/other.py')))
 def test_relative_beyond_root_unresolved(self):
  self.assertFalse(hits('from ...linalg._decomp_svd import svd\ndef f(a): return svd(a)',sid=SID))
 def test_relative_star_unresolved(self):
  self.assertFalse(hits('from ._decomp_svd import *\ndef f(a): return svd(a)',sid=SID))
 def test_relative_init(self):
  self.assertTrue(hits('from ._decomp_svd import svd\ndef f(a): return svd(a)',sid=SID.replace('example.py','__init__.py')))
 def test_relative_forged_unpinned_id_unresolved(self):
  self.assertFalse(hits('from ._decomp_svd import svd\ndef f(a): return svd(a)',sid=SID.replace('a'*40,'main')))
 def test_state_links_control_initializer(self):
  s='import numpy as np\nclass Renamed:\n def __init__(me,s=None,learn=True):\n  me.factor=1 if s is None else s\n  me.adjust=learn\n def update(me,x):\n  if me.adjust is True: me.factor=np.sum(x)\n def apply(me,x): return me.factor*np.dot(x,me.matrix)'
  row=first(hits(s,C));control=row['field_context']['control_fields']['adjust'][0]
  self.assertTrue(control['initializer']['declared_default']['value']);self.assertFalse(row['fixed_unity_proven'])
 def test_state_staticmethod_excluded(self):
  s='import numpy as np\nclass T:\n @staticmethod\n def f(self,x): return self.factor*np.dot(x,self.R)'
  self.assertFalse(hits(s,C))
 def test_state_classmethod_excluded(self):
  s='import numpy as np\nclass T:\n @classmethod\n def f(cls,x): return cls.factor*np.dot(x,cls.R)'
  self.assertFalse(hits(s,C))
 def test_state_no_false_scalar_proof(self):
  s='import numpy as np\nclass T:\n def f(self,x): return self.possibly_array*np.dot(x,self.R)'
  row=first(hits(s,C));self.assertFalse(row['fixed_unity_proven']);self.assertIn('scalar/type/coordinate', ' '.join(row['limitations']))
 def test_state_wrong_dot_package(self):
  self.assertFalse(hits('import fake as np\nclass T:\n def f(self,x):return self.s*np.dot(x,self.R)',C))
 def test_deterministic_identity(self):
  s='import numpy as n\ndef f(a,b): return n.linalg.solve(a,b)'
  self.assertEqual(scan(s),scan(s))
 def test_source_no_execution(self):
  with tempfile.TemporaryDirectory() as d:
   target=Path(d)/'bad';s=f'open({str(target)!r},"w").write("bad")\nimport numpy as n\ndef f(a,b):return n.linalg.solve(a,b)'
   scan(s);self.assertFalse(target.exists())
 def test_rules_affect_engine_hash(self):
  source=Path(hound.__file__).read_text();self.assertIn("with_name('library_rules.py')",source)
 def test_rules_affect_persistent_binding(self):
  import persistent_hunt,inspect
  self.assertIn('library_rules.py',inspect.getsource(persistent_hunt.fingerprint))
 def test_new_profile_is_mission_usable(self):
  import mission_hunt
  mission_hunt.validate_mission({'id':'factor','title':'Object factor context','requirements':[{'id':'r','profile_id':C,'statuses':['OBJECT_FACTOR_CONTEXT_REQUIRES_REVIEW']} ]})
