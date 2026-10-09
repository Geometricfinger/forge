import unittest
from test_defect_closure import observe
VARIATIONS={
 'definition_branch':"s=False\nif flag:\n    def helper(x=(s:=True)): pass\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'lambda_argument':"s=False\nunused=(lambda x=(s:=flag): x)\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'class_mutable':"d={'status':'ambiguous'}\nclass C:\n    d['status']='ok'\nreturn d",
 'tuple_nested_expansion':"settings=[False,False]\nmutate(**{'options':settings})\ns,r=settings\ntrimesh.registration.icp(a,b,scale=s,reflection=r)",
 'comparison_open':"s=False\n1 < len(a) < (s:=True)\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'comparison_closed':"s=False\n1 > 2 > (s:=True)\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'try_inplace':"d={'status':'ambiguous'}\nalias=d\ntry:\n    d |= {'status':'ok'}\nexcept ValueError: pass\nreturn alias",
 'comprehension_filter':"s=False\ndef flip():\n    nonlocal s\n    s=flag\n[None for _ in a if flip()]\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'comprehension_iterable':"s=False\ndef flip():\n    nonlocal s\n    s=flag\n    return a\n[x for x in flip()]\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
}
class ClosureVariations(unittest.TestCase):pass
for name,body in VARIATIONS.items():
 for renamed in (False,True):
  def make(body,renamed):
   def test(self):self.assertFalse(observe(body,rename=renamed)['errors'])
   return test
  setattr(ClosureVariations,'test_'+name+('_renamed' if renamed else ''),make(body,renamed))
