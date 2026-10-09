"""Validate public-library repair regressions and intentional repair removals.

Executes known synthetic detector tests only; no fetched implementations or model
calls. Report integrity and repeatability are not independent evaluation.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime,timezone
import argparse, hashlib, io, json, os, shutil, subprocess, sys, tempfile, unittest
ROOT=Path(__file__).resolve().parent
TESTS=('test_external_discovery','test_external_rechallenge','test_script_discovery')
REPAIRS={
 'L01_api_mappings':('library_rules.py',"known=(ARRAY_APIS if target.kind=='namespace' else EXTRA_APIS).get(api)","known=None"),
 'L02_relative_imports':('library_rules.py',' package=package_from_source_id(source_id); records=[]',' return []\n package=package_from_source_id(source_id); records=[]'),
 'L03_namespace_factories':('library_rules.py',"if target.kind=='import' and api in FACTORIES:","if False and target.kind=='import' and api in FACTORIES:"),
 'L05_object_factor_context':('library_rules.py',' classes=[]\n for node in ast.walk(tree):',' return\n classes=[]\n for node in ast.walk(tree):'),
 'L06_script_scope':('script_scope.py'," scope=ast.parse('def _analysis_scope():\\n pass\\n').body[0]"," return []\n scope=ast.parse('def _analysis_scope():\\n pass\\n').body[0]")}

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def contract():return json.loads((ROOT/'contracts/LIBRARY_TEST_CONTRACT.json').read_text())
def worker(tool):
 sys.path[:0]=[str(tool),str(tool/'tests')]
 suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(n) for n in TESTS)
 def flatten(s):
  for x in s:
   if isinstance(x,unittest.TestSuite):yield from flatten(x)
   else:yield x
 ids=sorted(t.id() for t in flatten(suite));log=io.StringIO();result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
 return {'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
         'case_ids':ids,'failed_case_ids':[x.id() for x,_ in result.failures],
         'fixture_hashes':{f'tests/{n}.py':sha(tool/'tests'/(n+'.py')) for n in TESTS},'log':log.getvalue()}

def validate(r):
 c=contract()
 if not isinstance(r,dict) or r.get('status')!='LIBRARY_REGRESSIONS_AND_REMOVALS_VERIFIED':raise ValueError('LIBRARY_NOT_CLEAN')
 for k in ('independent_evaluation','release_approved','universal_correctness'):
  if r.get(k) is not False:raise ValueError('LIBRARY_SCOPE')
 if r.get('package_unchanged') is not True or r.get('contract_sha256')!=sha(ROOT/'contracts/LIBRARY_TEST_CONTRACT.json'):raise ValueError('LIBRARY_BINDING')
 def check(row):
  if not isinstance(row,dict) or row.get('fixture_hashes')!=c['fixture_hashes'] or row.get('case_ids')!=c['case_ids']:raise ValueError('LIBRARY_FIXTURE_OR_CASES')
  if any(type(row.get(k)) is not int for k in ('tests_run','failures','errors','skips')):raise ValueError('LIBRARY_COUNT_TYPE')
  failed=row.get('failed_case_ids')
  if not isinstance(failed,list) or not all(isinstance(x,str) for x in failed) or len(set(failed))!=len(failed) or set(failed)-set(c['case_ids']) or len(failed)!=row['failures']:raise ValueError('LIBRARY_FAILURE_IDENTITIES')
  if row['tests_run']!=c['expected_tests'] or row['errors']!=0 or row['skips']!=0:raise ValueError('LIBRARY_INCOMPLETE_TESTS')
 check(r.get('candidate'))
 if r['candidate']['failures']!=0:raise ValueError('LIBRARY_CANDIDATE_FAILED')
 removals=r.get('repair_removals')
 if not isinstance(removals,list) or len(removals)!=len(REPAIRS) or not all(isinstance(x,dict) for x in removals):raise ValueError('LIBRARY_REMOVALS')
 if {x.get('id') for x in removals}!=set(REPAIRS):raise ValueError('LIBRARY_REMOVAL_IDS')
 for x in removals:
  check(x)
  if not 0<x['failures']<=c['expected_tests']:raise ValueError('LIBRARY_REMOVAL_SURVIVED')

def execute(tool,path):
 p=subprocess.run([sys.executable,'-I','-B',str(Path(__file__).resolve()),'--worker',str(tool)],capture_output=True,timeout=30,env={'PATH':os.defpath,'LANG':'C.UTF-8'})
 if p.returncode:raise ValueError('LIBRARY_PROCESS_FAILED:'+p.stderr.decode(errors='replace')[:500])
 r=json.loads(p.stdout);path.write_bytes(p.stdout);return r

def bindings():
 names=set(['hound.py','flow_core.py','baseline_sniffers.py','library_rules.py','script_scope.py','persistent_hunt.py','atlas_hunt.py','mission_hunt.py','run_library_validation.py','profiles/baseline.json','contracts/LIBRARY_TEST_CONTRACT.json'])|{f'tests/{n}.py' for n in TESTS}
 return {n:sha(ROOT/n) for n in sorted(names)}

def run(out):
 if out.exists() or out.is_symlink() or out.resolve().is_relative_to(ROOT):raise ValueError('NEW_SEPARATE_OUTPUT_REQUIRED')
 before=bindings();out.mkdir(parents=True);r={'status':'RUNNING','started_utc':datetime.now(timezone.utc).isoformat(),
 'contract_sha256':sha(ROOT/'contracts/LIBRARY_TEST_CONTRACT.json'),'independent_evaluation':False,'release_approved':False,'universal_correctness':False}
 try:
  r['candidate']=execute(ROOT,out/'candidate.json');r['repair_removals']=[]
  for mid,(name,old,new) in REPAIRS.items():
   content=(ROOT/name).read_text()
   if content.count(old)!=1:raise ValueError('LIBRARY_MUTATION_SITE_CHANGED:'+mid)
   with tempfile.TemporaryDirectory(prefix='hound-library-removal-') as d:
    p=Path(d)
    for src in ROOT.glob('*.py'):shutil.copy2(src,p/src.name)
    for folder in ('profiles','tests'):shutil.copytree(ROOT/folder,p/folder,ignore=shutil.ignore_patterns('__pycache__'))
    (p/name).write_text(content.replace(old,new))
    result=execute(p,out/(mid+'.json'));r['repair_removals'].append({'id':mid,**result})
  r.update(status='LIBRARY_REGRESSIONS_AND_REMOVALS_VERIFIED',package_unchanged=bindings()==before)
  validate(r)
 except (ValueError,OSError,subprocess.TimeoutExpired) as e:r.update(status='BLOCKED',reason=str(e))
 r.update(finished_utc=datetime.now(timezone.utc).isoformat(),package_unchanged=bindings()==before)
 (out/'library_validation.json').write_text(json.dumps(r,indent=2));return r

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--worker',type=Path);p.add_argument('--out',type=Path);a=p.parse_args()
 if a.worker:print(json.dumps(worker(a.worker)));raise SystemExit(0)
 if not a.out:p.error('--out required')
 r=run(a.out);print(json.dumps({k:r.get(k) for k in ('status','package_unchanged','reason')}));raise SystemExit(0 if r['status']=='LIBRARY_REGRESSIONS_AND_REMOVALS_VERIFIED' else 2)
