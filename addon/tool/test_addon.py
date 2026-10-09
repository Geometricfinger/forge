"""Tests for the additive configured-API probe, not a replacement Hound suite."""
from pathlib import Path
import argparse, copy, hashlib, json, os, shutil, sys, tempfile, unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import mission_probe as m
from run_supervised_hunt import validate_manifest, run
ROOT=Path(__file__).resolve().parents[1]
B=None
C=json.loads((ROOT/'contracts/challenges.json').read_text())
P=C['profile']
S=b'import ast\ndef f(s):\n return ast.parse(s)\n'

class ContractCases(unittest.TestCase):
    pass
for case in C['cases']:
    def test(self,case=case):
        r=m.scan(case['code'].encode(),'synthetic:'+case['id'],P,B)
        self.assertEqual(len(r['findings']),case['expected_count'])
        m.validate_result(r,case['code'].encode(),'synthetic:'+case['id'],P,B)
    setattr(ContractCases,'test_'+case['id'],test)

class ProfileAndScope(unittest.TestCase):
    def test_no_executable_profile(self):
        p=copy.deepcopy(P);p['code']='import os'
        with self.assertRaises(ValueError):m.validate_profile(p)
    def test_target_wildcard_rejected(self):
        p=copy.deepcopy(P);p['targets'][0]['api']='ast.*'
        with self.assertRaises(ValueError):m.validate_profile(p)
    def test_duplicate_target(self):
        p=copy.deepcopy(P);p['targets'].append(p['targets'][0])
        with self.assertRaises(ValueError):m.validate_profile(p)
    def test_boolean_schema(self):
        p=copy.deepcopy(P);p['schema']=True
        with self.assertRaises(ValueError):m.validate_profile(p)
    def test_empty_registry(self):
        p=copy.deepcopy(P);p['targets']=[]
        with self.assertRaises(ValueError):m.validate_profile(p)
    def test_registry_limit(self):
        p=copy.deepcopy(P);p['targets']=[{'api':f'library.feature{i}','capability':'candidate'} for i in range(129)]
        with self.assertRaises(ValueError):m.validate_profile(p)
    def test_profile_name_code(self):
        p=copy.deepcopy(P);p['id']='../../bad'
        with self.assertRaises(ValueError):m.validate_profile(p)
    def test_source_limit(self):
        with self.assertRaises(ValueError):m.scan(b' '*(m.MAX_SOURCE+1),'x',P,B)
    def test_duplicate_json(self):
        with self.assertRaises(ValueError):m.strict_json('{"a":1,"a":2}')
    def test_json_nan(self):
        with self.assertRaises(ValueError):m.strict_json('{"a":NaN}')
    def test_no_target_execution(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'sentinel'
            s=f'from pathlib import Path\nPath({str(p)!r}).write_text("executed")\nimport ast\nx=ast.parse("x=1")\n'.encode()
            m.scan(s,'synthetic:no-exec',P,B)
            self.assertFalse(p.exists())
    def test_no_body_retention(self):
        r=m.scan(b'import ast\ndef f(s):\n secret="DO_NOT_COPY_THIS_LITERAL_91381"\n return ast.parse(s)\n','synthetic:body',P,B)
        self.assertNotIn('DO_NOT_COPY_THIS_LITERAL',json.dumps(r))
    def test_profile_changes_identity(self):
        a=m.scan(S,'same',P,B);p=copy.deepcopy(P);p['id']='another-registry';b=m.scan(S,'same',p,B)
        self.assertNotEqual(a['findings'][0]['finding_id'],b['findings'][0]['finding_id'])
    def test_idempotence(self):
        self.assertEqual(m.scan(S,'same',P,B),m.scan(S,'same',P,B))
    def test_nested_shadow(self):
        s=b'import ast\ndef outer(ast):\n def inner(s):\n  return ast.parse(s)\n return inner\n'
        self.assertEqual(m.scan(s,'nested:shadow',P,B)['findings'],[])
    def test_nested_module_rebound(self):
        s=b'import ast\nast=None\ndef outer():\n def inner(s):\n  return ast.parse(s)\n return inner\n'
        self.assertEqual(m.scan(s,'nested:rebound',P,B)['findings'],[])
    def test_nested_alias(self):
        s=b'from ast import parse as parser\ndef outer():\n def inner(s):\n  return parser(s)\n return inner\n'
        r=m.scan(s,'nested:alias',P,B)
        self.assertEqual(len(r['findings']),1)
        self.assertIn('<locals>',r['findings'][0]['qualified_name'])
    def test_nested_closure_rebind(self):
        s=b'import ast\ndef outer(x):\n ast=x\n def inner(s):\n  return ast.parse(s)\n return inner\n'
        self.assertEqual(m.scan(s,'nested:closure',P,B)['findings'],[])
    def test_nested_assignment_after_use(self):
        s=b'import ast\ndef outer():\n def inner(s):\n  result=ast.parse(s)\n  ast=None\n  return result\n return inner\n'
        self.assertEqual(m.scan(s,'nested:local',P,B)['findings'],[])
    def test_nested_import_member_mutation(self):
        s=b'import ast\nast.parse=None\ndef outer():\n def inner(s):\n  return ast.parse(s)\n return inner\n'
        self.assertEqual(m.scan(s,'nested:mutation',P,B)['findings'],[])
    def test_hound_changed_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for n in json.loads((ROOT/'contracts/hound_binding.json').read_text()):
                p=root/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(B/n,p)
            (root/'hound.py').write_text((root/'hound.py').read_text()+'\n# tamper\n')
            with self.assertRaises(ValueError):m.load_hound(root)
    def test_isolated_worker(self):
        a=m.isolated_scan(S,'synthetic:isolated',P,B)
        self.assertEqual(len(a['findings']),1)
    def test_real_source_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'f.py';p.write_bytes(S);before=(p.read_bytes(),p.stat().st_mtime_ns)
            m.isolated_scan(m.read_regular(p,10000),'synthetic:preserved',P,B)
            self.assertEqual(before,(p.read_bytes(),p.stat().st_mtime_ns))
    def test_source_link(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'f.py';p.write_bytes(S);q=Path(d)/'link.py';q.symlink_to(p)
            with self.assertRaises(ValueError):m.read_regular(q,10000)

class ReceiptChecks(unittest.TestCase):
    def setUp(self):self.r=m.scan(S,'synthetic:receipt',P,B)
    def check_bad(self,change):
        r=copy.deepcopy(self.r);change(r)
        with self.assertRaises(ValueError):m.validate_result(r,S,'synthetic:receipt',P,B)
    def test_source_binding(self):self.check_bad(lambda r:r.update(source_sha256='0'*64))
    def test_engine_binding(self):self.check_bad(lambda r:r.update(hound_engine_sha256='0'*64))
    def test_probe_binding(self):self.check_bad(lambda r:r.update(probe_code_sha256='0'*64))
    def test_profile_binding(self):self.check_bad(lambda r:r.update(registry_sha256='0'*64))
    def test_release_promotion(self):self.check_bad(lambda r:r.update(release_approved=True))
    def test_finding_promotion(self):self.check_bad(lambda r:r['findings'][0].update(runtime_verified=True))
    def test_duplicate_findings(self):self.check_bad(lambda r:r['findings'].append(r['findings'][0]))
    def test_wrong_digest(self):self.check_bad(lambda r:r['findings'][0].update(finding_id='fake'))
    def test_wrong_target(self):self.check_bad(lambda r:r['findings'][0].update(resolved_api='os.system'))
    def test_wrong_function(self):self.check_bad(lambda r:r['findings'][0].update(qualified_name='other'))
    def test_wrong_count(self):self.check_bad(lambda r:r.update(functions_inspected=999))
    def test_bool_count(self):self.check_bad(lambda r:r.update(functions_inspected=True))
    def test_range(self):self.check_bad(lambda r:r['findings'][0]['evidence'][0].update(line_start=99999))
    def test_script_has_no_function(self):
        r=m.scan(b'import ast\nx=ast.parse("x=1")\n','synthetic:script',P,B)
        self.assertNotIn('qualified_name',r['findings'][0]);self.assertNotIn('function_id',r['findings'][0])
        r['findings'][0]['function_id']='fake'
        with self.assertRaises(ValueError):m.validate_result(r,b'import ast\nx=ast.parse("x=1")\n','synthetic:script',P,B)
    def test_manifest_traversal(self):
        with self.assertRaises(ValueError):validate_manifest({'schema':1,'sources':[{'path':'../x.py','source_id':'a','sha256':'a'*64}]})
    def test_manifest_duplicate(self):
        row={'path':'x.py','source_id':'a','sha256':'a'*64}
        with self.assertRaises(ValueError):validate_manifest({'schema':1,'sources':[row,row]})
    def test_manifest_empty(self):
        with self.assertRaises(ValueError):validate_manifest({'schema':1,'sources':[]})
    def test_hunt_rejects_changed_source(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'x.py').write_bytes(S)
            manifest={'schema':1,'sources':[{'source_id':'test:x','path':'x.py','sha256':'0'*64}]}
            with self.assertRaises(ValueError):run(B,root,manifest,[P],root.parent/(root.name+'-unused'))


class ScopeCacheChecks(unittest.TestCase):
    def test_one_table_for_eighty_nested_functions(self):
        from unittest.mock import patch
        import symtable
        data=(ROOT/'contracts/scope_cache_fixture.py').read_bytes()
        with patch('symtable.symtable', wraps=symtable.symtable) as tracked:
            r=m.scan(data,'cache:many',P,B)
        self.assertEqual(tracked.call_count,1)
        self.assertEqual(len(r['findings']),80)
    def test_no_cross_source_state(self):
        from unittest.mock import patch
        import symtable
        a=b'import ast\ndef outer():\n def inner(s):\n  return ast.parse(s)\n return inner\n'
        b=b'import ast\nast=None\ndef outer():\n def inner(s):\n  return ast.parse(s)\n return inner\n'
        with patch('symtable.symtable', wraps=symtable.symtable) as tracked:
            ra=m.scan(a,'cache:first',P,B);rb=m.scan(b,'cache:second',P,B)
        self.assertEqual(tracked.call_count,2)
        self.assertEqual(len(ra['findings']),1)
        self.assertEqual(rb['findings'],[])
    def test_lazy_when_not_needed(self):
        from unittest.mock import patch
        import symtable
        with patch('symtable.symtable', wraps=symtable.symtable) as tracked:
            r=m.scan(S,'cache:flat',P,B)
        self.assertEqual(tracked.call_count,0)
        self.assertEqual(len(r['findings']),1)


def main():
    global B
    ap=argparse.ArgumentParser();ap.add_argument('--hound',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    B=args.hound.resolve()
    if args.out.exists():raise ValueError('NEW_OUTPUT_REQUIRED')
    args.out.mkdir(parents=True)
    with (args.out/'tests.txt').open('w') as f:
        r=unittest.TextTestRunner(stream=f,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    report={'tests_run':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skips':len(r.skipped),'contract_sha256':m.sha((ROOT/'contracts/challenges.json').read_bytes()),'probe_sha256':m.sha((ROOT/'tool/mission_probe.py').read_bytes()),'independent':False}
    (args.out/'tests.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
    return 0 if r.testsRun and r.wasSuccessful() and not r.skipped else 2
if __name__=='__main__':raise SystemExit(main())
