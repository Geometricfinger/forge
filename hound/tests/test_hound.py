import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import hound
from atlas_hunt import bounded_read,inspect_isolated,source_path,strict_json,read_locator
from value_gate import assess


def scan(code,profile=hound.RIGID):
    return [r for r in hound.scan_bytes(code.encode(),source_id='synthetic://case')['findings'] if r['profile_id']==profile]

def wrap(body,params='a,b,flag',imports='import trimesh'):
    return imports+'\ndef f('+params+'):\n'+''.join('    '+x+'\n' for x in body.splitlines())

class FlowTests(unittest.TestCase):
    def test_variable_false(self):
        r=scan(wrap('s=False\nreturn trimesh.registration.icp(a,b,scale=s,reflection=False)'))
        self.assertEqual(r[0]['status'],'STATIC_CANDIDATE');self.assertEqual(r[0]['value_definition_lines'],[3])
    def test_variable_reassigned_true(self):
        r=scan(wrap('s=False\ns=True\nreturn trimesh.registration.icp(a,b,scale=s,reflection=False)'))
        self.assertEqual(r[0]['status'],'CONTRADICTED_CALL_CONTRACT')
    def test_unknown_branch(self):
        r=scan(wrap('s=False\nif flag:\n    s=True\nreturn trimesh.registration.icp(a,b,scale=s,reflection=False)'))
        self.assertEqual(r[0]['status'],'NEEDS_CONTEXT')
    def test_unresolved_parameter_default(self):
        r=scan(wrap('return trimesh.registration.icp(a,b,scale=flag,reflection=False)',params='a,b,flag=False'))
        self.assertEqual(r[0]['status'],'NEEDS_CONTEXT')
    def test_import_alias_assignment(self):
        r=scan(wrap('op=trimesh.registration.icp\ns=False\nreturn op(a,b,scale=s,reflection=False)'))
        self.assertEqual(r[0]['status'],'STATIC_CANDIDATE')
    def test_rebound_library(self):
        self.assertEqual(scan(wrap('return trimesh.registration.icp(a,b,scale=False,reflection=False)',params='a,b,trimesh')),[])
    def test_monkeypatch_suppressed(self):
        self.assertEqual(scan(wrap('trimesh.registration.icp=flag\nreturn trimesh.registration.icp(a,b,scale=False,reflection=False)')),[])
    def test_copy_receiver(self):
        r=scan(wrap('scratch=a.copy()\nscratch.apply_scale(2)\nreturn a'),hound.SCALE)
        self.assertEqual(r[0]['status'],'COPY_CALL_RESULT_RECEIVER');self.assertEqual(r[0]['origin_parameters'],['a'])
    def test_alias_is_input(self):
        r=scan(wrap('alias=a\nalias.apply_scale(2)'),hound.SCALE)
        self.assertEqual(r[0]['status'],'INPUT_ALIAS_RECEIVER')
    def test_rebound_copy_is_input(self):
        r=scan(wrap('x=a.copy()\nx=b\nx.apply_scale(2)'),hound.SCALE)
        self.assertEqual(r[0]['status'],'INPUT_ALIAS_RECEIVER');self.assertEqual(r[0]['origin_parameters'],['b'])
    def test_condition_copy_maybe_input(self):
        r=scan(wrap('x=a.copy()\nif flag:\n    x=a\nx.apply_scale(2)'),hound.SCALE)
        self.assertEqual(r[0]['status'],'UNRESOLVED_RECEIVER')
    def test_called_factory_is_unknown(self):
        self.assertEqual(scan(wrap('x=flag(a)\nx.apply_scale(2)'),hound.SCALE)[0]['status'],'UNRESOLVED_RECEIVER')
    def test_no_scaling_keyword_false_alarm(self):
        self.assertEqual(scan(wrap('"""apply_scale(a)"""\nreturn a'),hound.SCALE),[])
    def test_return_none_is_not_identification(self):
        self.assertEqual(scan(wrap('return None'),hound.ABSTAIN),[])
    def test_explicit_abstention(self):
        self.assertEqual(scan(wrap('return {"status":"ambiguous"}'),hound.ABSTAIN)[0]['fields'],[{'field':'status','value':'ambiguous'}])
    def test_field_name_confidence_does_not_prove(self):
        self.assertEqual(scan(wrap('return {"confidence":0.9}'),hound.ABSTAIN),[])
    def test_no_source_bodies(self):
        code=wrap('secret="DO_NOT_RETAIN_ABC123"\nreturn {"status":"unknown"}')
        self.assertNotIn('DO_NOT_RETAIN_ABC123',json.dumps(scan(code,hound.ABSTAIN)))
    def test_literal_unreachable_skipped(self):
        self.assertEqual(scan(wrap('if False:\n    return trimesh.registration.icp(a,b,scale=False,reflection=False)')),[])
    def test_changed_bytes_change_identity(self):
        a=scan(wrap('return {"status":"unknown"}'),hound.ABSTAIN)[0]
        b=scan(wrap('# comment\nreturn {"status":"unknown"}'),hound.ABSTAIN)[0]
        self.assertNotEqual(a['finding_id'],b['finding_id'])
    def test_nested_attribute_to_own_scope(self):
        r=scan('import trimesh\ndef outer(a,b):\n def inner(a,b):\n  return trimesh.registration.icp(a,b,scale=False,reflection=False)\n return a\n')
        self.assertEqual(r,[]) # Captured/global import is deliberately unresolved in nested scopes.
    def test_worker_nonexecution(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'bad'
            code=f'open({str(f)!r},"w").write("oops")\n'+wrap('return {"status":"unknown"}')
            inspect_isolated(code.encode(),'synthetic://nonexecution');self.assertFalse(f.exists())
    def test_bad_source_fails(self):
        with self.assertRaises(SyntaxError):hound.scan_bytes(b'def !!!',source_id='x')
    def test_bounded_input(self):
        with self.assertRaises(ValueError):hound.scan_bytes(b'x'*256001,source_id='x')
    def test_loop_reassigned_flag_is_unknown(self):
        r=scan(wrap('s=False\nfor x in a:\n    trimesh.registration.icp(a,b,scale=s,reflection=False)\n    s=True'))
        self.assertEqual(r[0]['status'],'NEEDS_CONTEXT')
    def test_kwargs_abstains(self):
        self.assertEqual(scan(wrap('return trimesh.registration.icp(a,b,scale=False,reflection=False,**flag)'))[0]['status'],'NEEDS_CONTEXT')

class IOTests(unittest.TestCase):
    def test_path_escape(self):
        with self.assertRaises(ValueError):source_path(Path('/tmp'), '../outside')
    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'a').write_bytes(b'a');(p/'b').symlink_to(p/'a')
            with self.assertRaises(ValueError):bounded_read(p/'b',20)
    def test_duplicate_json_key(self):
        with self.assertRaises(ValueError):strict_json('{"a":1,"a":2}')
    def test_json_nan(self):
        with self.assertRaises(ValueError):strict_json('{"a":NaN}')
    def test_locator_wrong_hash(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'a.py').write_text('x=1')
            with self.assertRaises(ValueError):read_locator(p,{'path':'a.py'},{'sha256':'0'*64})

class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.e={'baseline_id':'old','candidate_id':'new','suite_sha256':'a'*64,'input_set_sha256':'b'*64,
          'required_checks_passed':True,'required_regressions':0,'case_count':10,
          'metrics':{'correct_decisions':{'baseline':5,'candidate':8}},'tradeoffs':['more parsing'],
          'limitations':['synthetic only'],'requirements':{'context':{'baseline_met':False,'candidate_met':True,'acceptance_test_ids':['case1']}}}
        self.p={**{k:self.e[k] for k in ('baseline_id','candidate_id','suite_sha256','input_set_sha256')},
          'mode':'better','problem':'wrong control inference','metric':'correct_decisions','direction':'higher','minimum_change':1}
    def tearDown(self):self.tmp.cleanup()
    def run_gate(self):
        data=json.dumps(self.e).encode();(self.root/'e.json').write_bytes(data)
        self.p['artifact']={'path':'e.json','sha256':hashlib.sha256(data).hexdigest()}
        return assess(self.p,self.root)
    def test_measured_not_released(self):
        r=self.run_gate();self.assertEqual(r['status'],'MEASURED_ADVANTAGE_REVIEW_REQUIRED');self.assertFalse(r['release_approved'])
    def test_equivalent_rejected(self):
        self.e['metrics']['correct_decisions']['candidate']=5
        self.assertEqual(self.run_gate()['status'],'NOT_ELIGIBLE_FOR_ADOPTION_REVIEW')
    def test_regression_rejected(self):
        self.e['required_regressions']=1
        self.assertEqual(self.run_gate()['status'],'NOT_ELIGIBLE_FOR_ADOPTION_REVIEW')
    def test_renaming_alone_rejected(self):
        self.p.update(mode='different',requirement_id='context');self.e['requirements']['context']['baseline_met']=True
        self.assertEqual(self.run_gate()['status'],'NOT_ELIGIBLE_FOR_ADOPTION_REVIEW')
    def test_useful_difference(self):
        self.p.update(mode='different',requirement_id='context')
        self.assertEqual(self.run_gate()['status'],'USEFUL_DIFFERENCE_REVIEW_REQUIRED')
    def test_no_cases(self):
        self.e['case_count']=0
        self.assertEqual(self.run_gate()['status'],'NOT_ELIGIBLE_FOR_ADOPTION_REVIEW')
    def test_same_identity(self):
        self.p['candidate_id']='old'
        self.assertEqual(self.run_gate()['status'],'NOT_ELIGIBLE_FOR_ADOPTION_REVIEW')
    def test_tamper(self):
        self.run_gate();(self.root/'e.json').write_text('{}')
        self.assertEqual(assess(self.p,self.root)['status'],'NOT_ELIGIBLE_FOR_ADOPTION_REVIEW')
    def test_threshold_invalid(self):
        self.p['minimum_change']=0
        self.assertEqual(self.run_gate()['status'],'NOT_ELIGIBLE_FOR_ADOPTION_REVIEW')
    def test_binding_failure(self):
        self.p['suite_sha256']='c'*64
        self.assertEqual(self.run_gate()['status'],'NOT_ELIGIBLE_FOR_ADOPTION_REVIEW')
    def test_nan_metrics(self):
        self.e['metrics']['correct_decisions']['candidate']=float('nan')
        self.assertEqual(self.run_gate()['status'],'NOT_ELIGIBLE_FOR_ADOPTION_REVIEW')

if __name__=='__main__':unittest.main()
