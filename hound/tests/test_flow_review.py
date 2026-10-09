import json
from pathlib import Path
import unittest
import hound

class FrozenReviewCases(unittest.TestCase):
    """Expectations frozen before the flow repair; no target code executes."""
    pass

def make_test(case):
    def test(self):
        result=hound.scan_bytes(case['source'].encode(),source_id='diagnostic')
        got=[x['status'] for x in result['findings'] if x['profile_id']==case['profile']]
        self.assertEqual(got,case['expected'])
    return test
for case in json.loads(Path(__file__).with_name('flow_acceptance_cases.json').read_text())['cases']:
    setattr(FrozenReviewCases,'test_'+case['id'],make_test(case))

class PrimitiveAndAliasTests(unittest.TestCase):
    def scan(self,src,profile):
        return [f for f in hound.scan_bytes(src.encode(),source_id='fixture')['findings'] if f['profile_id']==profile]
    def test_chained_call_count_once(self):
        r=self.scan('from scipy.spatial import cKDTree\ndef f(x,y):\n return cKDTree(x).query(y)\n',hound.PRIMITIVES)
        self.assertEqual([x['resolved_api'] for x in r],['scipy.spatial.cKDTree','scipy.spatial.cKDTree.query'])
    def test_spatial_tree_alias(self):
        r=self.scan('from scipy.spatial import cKDTree as K\ndef f(x,y):\n t=K(x)\n t.query(y)\n',hound.PRIMITIVES)
        self.assertEqual(len(r),2)
    def test_false_library_name_not_counted(self):
        r=self.scan('def f(numpy,x):\n return numpy.linalg.svd(x)\n',hound.PRIMITIVES);self.assertFalse(r)
    def test_comment_not_computation(self):
        r=self.scan('def f():\n """numpy.linalg.svd(x)"""\n return 2\n',hound.PRIMITIVES);self.assertFalse(r)
    def test_import_alias_api(self):
        r=self.scan('import numpy as n\ndef f(x):\n return n.linalg.svd(x)\n',hound.PRIMITIVES)
        self.assertEqual(r[0]['operation'],'singular_value_decomposition')
    def test_input_method_not_spatial_index(self):
        self.assertFalse(self.scan('def f(t,x):\n return t.query(x)\n',hound.PRIMITIVES))
    def test_tuple_escape_invalidates_dict(self):
        r=self.scan('def f(fn):\n x={"status":"unknown"}\n pack=[x]\n fn(pack)\n return x\n',hound.ABSTAIN);self.assertFalse(r)
    def test_dict_values_kept_when_no_mutation(self):
        r=self.scan('def f():\n x={"status":"conflicting"}\n alias=x\n return alias\n',hound.ABSTAIN)
        self.assertEqual(r[0]['fields'],[{'field':'status','value':'conflicting'}])
    def test_while_false_not_scanned(self):
        self.assertFalse(self.scan('import numpy as n\ndef f(x):\n while False:\n  n.linalg.svd(x)\n',hound.PRIMITIVES))
    def test_list_comprehension_scope(self):
        r=self.scan('def f(meshes):\n return [m.copy().apply_scale(25.4) for m in meshes]\n',hound.SCALE)
        self.assertEqual(r[0]['status'],'COPY_CALL_RESULT_RECEIVER')
    def test_positional_walrus_evaluated_before_keywords(self):
        r=self.scan('import trimesh\ndef f(a,b):\n s=False\n return trimesh.registration.icp((s:=True),b,scale=s,reflection=False)\n',hound.RIGID)
        self.assertEqual(r[0]['status'],'CONTRADICTED_CALL_CONTRACT')
    def test_selected_returns_no_secret(self):
        r=self.scan('def f():\n secret="do-not-copy-secret"\n return {"status":"unknown","secret":secret}\n',hound.ABSTAIN)
        self.assertNotIn('do-not-copy-secret',json.dumps(r))
    def test_short_circuit_does_not_execute_right(self):
        r=self.scan('import numpy as n\ndef f(x):\n return False and n.linalg.svd(x)\n',hound.PRIMITIVES);self.assertFalse(r)
    def test_unit_factor_is_clue_not_units_proof(self):
        r=self.scan('def f(x):\n x.apply_scale(25.4)\n',hound.SCALE)
        self.assertEqual(r[0]['unit_conversion_clue']['status'],'FACTOR_ONLY_UNITS_NOT_PROVEN')
    def test_delete_clears_dict(self):
        r=self.scan('def f():\n x={"status":"unknown"}\n del x\n return x\n',hound.ABSTAIN);self.assertFalse(r)
