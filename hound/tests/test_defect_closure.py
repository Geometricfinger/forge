"""Reviewed synthetic counterexamples. Never executes collected application code."""
import builtins
from contextlib import nullcontext
import inspect
import types
import unittest
import hound

CASES = {
 'D01_default_expression': "s=False\ndef helper(x=(s:=flag)): pass\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'D02_lambda_default': "s=False\nhelper=lambda x=(s:=flag): x\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'D03_class_execution': "s=False\nclass C:\n    nonlocal s\n    s=flag\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'D04_inplace_alias': "d={'status':'ambiguous'}\nalias=d\nd |= {'status':'ok'}\nreturn alias",
 'D05_short_circuit_compare': "s=True\n2 < 1 < (s:=False)\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'D06_expanded_argument_escape': "settings=[False,False]\nmutate(**{'options':settings})\ns,r=settings\ntrimesh.registration.icp(a,b,scale=s,reflection=r)",
 'D07_exception_scope_effect': "s=False\ndef flip():\n    nonlocal s\n    s=flag\ntry:\n    flip()\nexcept ValueError: pass\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'D08_context_scope_effect': "s=False\ndef flip():\n    nonlocal s\n    s=flag\nwith cm:\n    flip()\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'D09_comprehension_effect': "s=False\ndef flip():\n    nonlocal s\n    s=flag\n[flip() for _ in a]\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'D01_keyword_default': "s=False\ndef helper(*,x=(s:=flag)): pass\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'D01_default_order': "s=True\ndef helper(x=(s:=False),y=(s:=True)): pass\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'D02_keyword_lambda': "s=False\nhelper=lambda *, x=(s:=flag): x\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'D03_decorator_callback': "s=False\ndef deco(f):\n    nonlocal s\n    s=flag\n    return f\n@deco\ndef helper(): pass\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'D04_list_inplace': "xs=[False,False]\nalias=xs\nxs += [True]\ns,r,*rest=alias\ntrimesh.registration.icp(a,b,scale=s,reflection=r)",
 'D05_long_chain': "s=True\n2 < 1 < 3 < (s:=False)\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'D05_possible_chain': "s=False\n1 < len(a) < (s:=True)\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'D06_nested_expansion': "settings=[False,False]\nkwargs={'options':settings}\nmutate(**kwargs)\ns,r=settings\ntrimesh.registration.icp(a,b,scale=s,reflection=r)",
 'D07_nested_handler': "s=False\ndef flip():\n    nonlocal s\n    s=flag\ntry:\n    raise ValueError()\nexcept ValueError:\n    flip()\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'D08_context_enter_effect': "s=False\ndef flip():\n    nonlocal s\n    s=flag\nwith factory(flip): pass\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'D09_set_comprehension': "s=False\ndef flip():\n    nonlocal s\n    s=flag\n{flip() for _ in a}\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'D09_dict_comprehension': "s=False\ndef flip():\n    nonlocal s\n    s=flag\n{idx:flip() for idx in a}\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
}
CONTROLS={
 'literal_rigid': "trimesh.registration.icp(a,b,scale=False,reflection=False)",
 'literal_contradiction': "trimesh.registration.icp(a,b,scale=True,reflection=False)",
 'unrelated_definition': "s=False\ndef helper(x=True): return x\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'lambda_body_not_executed': "s=False\nhelper=lambda: (s:=True)\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'immutable_rebinding': "d={'status':'ambiguous'}\nalias=d\nd={'status':'ok'}\nreturn alias",
 'dead_comprehension': "s=False\ndef flip():\n    nonlocal s\n    s=True\n[flip() for _ in []]\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'unrelated_try': "s=False\ntry: pass\nexcept ValueError: pass\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
 'short_chain_no_change': "s=False\n2<1<(s:=False)\ntrimesh.registration.icp(a,b,scale=s,reflection=False)",
}

def observe(body, *, rename=False):
    code='import trimesh\ndef f(a,b,flag,mutate,cm,factory):\n'+''.join('    '+line+'\n' for line in body.splitlines())
    if rename:code=code.replace('trimesh','tm').replace('import tm','import trimesh as tm').replace('def f(','def renamed(')
    name='renamed' if rename else 'f'
    events=[];results=[]
    def capture(*args,**kw):
        events.append((inspect.currentframe().f_back.f_lineno,dict(kw)));return None
    fake=types.SimpleNamespace(registration=types.SimpleNamespace(icp=capture))
    def importer(module,*args,**kw):
        if module!='trimesh':raise ImportError('Synthetic oracle only permits its recording API')
        return fake
    env={'__name__':'closure_oracle','__builtins__':{**vars(builtins),'__import__':importer}}
    exec(compile(code,'<fixed-defect-oracle>','exec'),env)
    def mutate(**kw):kw['options'][0]=True
    def factory(fn):fn();return nullcontext()
    for a in ([],[1],[1,2]):
        for flag in (False,True):results.append(env[name](a,None,flag,mutate,nullcontext(),factory))
    findings=[f for f in hound.scan_bytes(code.encode(),source_id='reviewed-closure-fixture')['findings'] if f['qualified_name']==name]
    errors=[];checked=0
    for f in findings:
        if f['profile_id']==hound.RIGID:
            seen=[kw for line,kw in events if f['evidence'][0]['line_start']<=line<=f['evidence'][0]['line_end']]
            if not seen:errors.append('reported call has no runtime witness')
            for key,value in f.get('known_controls',{}).items():
                checked+=1
                if any(kw.get(key) is not value for kw in seen):errors.append('false fixed control: '+key)
        if f['profile_id']==hound.ABSTAIN and f['status']=='EXPLICIT_NONDECISION_LITERAL':
            checked+=1
            if not all(isinstance(r,dict) and all(x['field'] in r and r[x['field']]==x['value'] for x in f['fields']) for r in results):
                errors.append('returned mapping disagrees with claimed fields')
    return {'findings':findings,'runtime_calls':events,'returns':results,'errors':errors,'checked_claims':checked,'invocations':6}

class DefectClosure(unittest.TestCase):pass

def build(body,rename=False):
    def test(self):
        r=observe(body,rename=rename)
        self.assertFalse(r['errors'],r['errors'])
    return test
for cid,body in CASES.items():
    setattr(DefectClosure,'test_'+cid,build(body))
    setattr(DefectClosure,'test_alias_'+cid,build(body,True))
for cid,body in CONTROLS.items():
    def make(body):
        def test(self):
            r=observe(body);self.assertFalse(r['errors'],r['errors'])
            self.assertGreater(r['checked_claims'],0,'abstaining on everything is not an acceptable fix')
        return test
    setattr(DefectClosure,'test_control_'+cid,make(body))
