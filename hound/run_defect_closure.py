"""Reproduce nine known defect families, then test repairs and their reversals.

Only this package's frozen synthetic fixtures execute, using a fake recording
API. No downloaded application source is executed. Reports are scoped evidence,
not universal bug-freedom or authenticated independent evaluation.
"""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,os,shutil,subprocess,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path

REPAIRS = {
 'C01_definition_defaults': ("for value in (*node.args.defaults,*node.args.kw_defaults):self.eval(value,env)","for value in ():self.eval(value,env)"),
 'C02_lambda_defaults': ("for value in (*node.args.defaults,*node.args.kw_defaults):\n                self.eval(value,env)","for value in ():\n                self.eval(value,env)"),
 'C03_class_effects': ("            self.invalidate_shared_cells(env);self.invalidate_mutables(env)\n        if node.decorator_list:","            pass # intentionally removed class effects\n        if node.decorator_list:"),
 'C04_inplace_aliases': ("                self.invalidate_object(receiver,env)\n                self.eval(s.value,env)","                pass # intentionally retained stale aliases\n                self.eval(s.value,env)"),
 'C05_comparison_short_circuit': ("if isinstance(node,ast.Compare):","if False and isinstance(node,ast.Compare):"),
 'C06_expanded_keyword_escape': ("[receiver,*arguments,*keywords.values(),*expanded]","[receiver,*arguments,*keywords.values()]"),
 'C07_exception_context_effects': ("                            for branch_result in branch_results:","                            for branch_result in ():"),
 'C08_comprehension_cells': ("            for key in self.nonlocal_cells:\n                if key in env:env[key]=merge_value(env[key],local.get(key,UNKNOWN))","            for key in ():\n                if key in env:env[key]=merge_value(env[key],local.get(key,UNKNOWN))"),
 'C09_parameter_callbacks': ("if target.kind!='import' or target.value not in APIS:","if target.kind=='unknown':"),
}
ROOT=Path(__file__).resolve().parent
CONTRACT=ROOT/'tests/test_defect_closure.py'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def binding(root):
 return {str(p.relative_to(root)):sha(p) for p in sorted(root.rglob('*')) if p.is_file() and p.suffix in ('.py','.json') and '__pycache__' not in p.parts and 'evidence' not in p.relative_to(root).parts}
def stamp():return datetime.now(timezone.utc).isoformat()

def worker(tool_root):
 sys.path.insert(0,str(tool_root))
 spec=importlib.util.spec_from_file_location('fixed_closure_fixture',CONTRACT)
 fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
 rows=[]
 for cid,body in fixture.CASES.items():
  for renamed in (False,True):
   obs=fixture.observe(body,rename=renamed)
   rows.append({'id':cid+('_renamed' if renamed else ''),'control':False,**obs})
 for cid,body in fixture.CONTROLS.items():
  obs=fixture.observe(body)
  if obs['checked_claims']==0:obs['errors'].append('control lost all determinate claims')
  rows.append({'id':cid,'control':True,**obs})
 return {'case_count':len(rows),'rows':rows,'disagreements':sum(bool(r['errors']) for r in rows),
         'control_count':sum(r['control'] for r in rows),'control_failures':sum(r['control'] and bool(r['errors']) for r in rows),
         'synthetic_invocations':sum(r['invocations'] for r in rows),'fixture_sha256':sha(CONTRACT),
         'engine_sha256':sha(tool_root/'flow_core.py')}

def audit(tool_root,out):
 p=subprocess.run([sys.executable,'-I','-B',str(Path(__file__).resolve()),'--worker',str(tool_root)],
     capture_output=True,timeout=30,env={'PATH':os.defpath,'LANG':'C.UTF-8'},check=False)
 if p.returncode:raise ValueError('ORACLE_PROCESS_FAILED:'+p.stderr.decode(errors='replace')[:1000])
 r=json.loads(p.stdout)
 out.write_bytes(p.stdout)
 return r

def validate_closure(r):
 if not isinstance(r,dict):raise ValueError('CLOSURE_REPORT_MAPPING')
 if r.get('status')!='KNOWN_DEFECTS_CLOSED_FOR_TESTED_SCOPE':raise ValueError('CLOSURE_NOT_CLEAN')
 if r.get('release_approved') is not False or r.get('package_unchanged') is not True or r.get('universal_bug_freedom') is not False or r.get('independent_evaluation') is not False:
  raise ValueError('CLOSURE_SCOPE_OR_BINDING')
 for key in ('baseline_disagreements','candidate_disagreements','case_count','control_count'):
  if type(r.get(key)) is not int:raise ValueError('CLOSURE_COUNT_TYPE')
 if r['baseline_disagreements']<1 or r['candidate_disagreements']!=0 or r['case_count']!=50 or r['control_count']!=8:
  raise ValueError('CLOSURE_COUNTS')
 rows=r.get('rows')
 if not isinstance(rows,list) or len(rows)!=50:raise ValueError('CLOSURE_ROWS')
 ids=set()
 for row in rows:
  if not isinstance(row,dict) or not isinstance(row.get('id'),str) or row['id'] in ids:raise ValueError('CLOSURE_CASE_ID')
  ids.add(row['id'])
  if row.get('candidate_errors')!=[] or not isinstance(row.get('baseline_errors'),list):raise ValueError('CLOSURE_CASE_FAILED')
 if sum(bool(x['baseline_errors']) for x in rows)!=r['baseline_disagreements']:raise ValueError('CLOSURE_BASELINE_COUNT')
 mutations=r.get('repair_reintroductions')
 if not isinstance(mutations,list) or len(mutations)!=len(REPAIRS):raise ValueError('CLOSURE_MUTATIONS')
 if not all(isinstance(m,dict) for m in mutations):raise ValueError('CLOSURE_MUTATION_SCHEMA')
 if len({m.get('id') for m in mutations})!=len(mutations) or {m.get('id') for m in mutations}!=set(REPAIRS):raise ValueError('CLOSURE_MUTATION_IDS')
 for m in mutations:
  if type(m.get('disagreements')) is not int or m['disagreements']<1 or m.get('fixture_sha256')!=r.get('fixture_sha256'):
   raise ValueError('REINTRODUCED_DEFECT_NOT_DETECTED')

def run(out):
 if out.exists() or out.is_symlink() or out.resolve().is_relative_to(ROOT):raise ValueError('NEW_EXTERNAL_OUTPUT_REQUIRED')
 before=binding(ROOT);out.mkdir(parents=True,mode=0o700)
 r={'started_utc':stamp(),'status':'RUNNING','fixture_sha256':sha(CONTRACT),'release_approved':False,
    'independent_evaluation':False,'universal_bug_freedom':False,'original_application_functions_executed':0}
 try:
  baseline=audit(ROOT/'reference/hound_0_4',out/'baseline.json')
  candidate=audit(ROOT,out/'candidate.json')
  if baseline['fixture_sha256']!=candidate['fixture_sha256'] or [x['id'] for x in baseline['rows']]!=[x['id'] for x in candidate['rows']]:
   raise ValueError('ORACLE_CONTRACT_MISMATCH')
  r.update(case_count=candidate['case_count'],baseline_disagreements=baseline['disagreements'],
           candidate_disagreements=candidate['disagreements'],control_count=candidate['control_count'],
           rows=[{'id':a['id'],'baseline_errors':a['errors'],'candidate_errors':b['errors']} for a,b in zip(baseline['rows'],candidate['rows'])])
  r['repair_reintroductions']=[]
  source=(ROOT/'flow_core.py').read_text()
  for mid,(old,new) in REPAIRS.items():
   if source.count(old)!=1:raise ValueError('MUTATION_SITE_CHANGED:'+mid)
   with tempfile.TemporaryDirectory(prefix='hound-reversal-') as td:
    target=Path(td)
    for name in ('hound.py','flow_core.py','baseline_sniffers.py','library_rules.py','script_scope.py'):shutil.copy2(ROOT/name,target/name)
    shutil.copytree(ROOT/'profiles',target/'profiles')
    (target/'flow_core.py').write_text(source.replace(old,new))
    result=audit(target,out/(mid+'.json'))
    r['repair_reintroductions'].append({'id':mid,'disagreements':result['disagreements'],
       'fixture_sha256':result['fixture_sha256'],'engine_sha256':result['engine_sha256']})
  r['package_unchanged']=binding(ROOT)==before
  r['status']='KNOWN_DEFECTS_CLOSED_FOR_TESTED_SCOPE'
  validate_closure(r)
 except (ValueError,OSError,KeyError,TypeError,subprocess.TimeoutExpired) as e:
  r.update(status='BLOCKED',reason=type(e).__name__+': '+str(e),package_unchanged=binding(ROOT)==before)
 r['finished_utc']=stamp()
 r['artifacts']={p.name:sha(p) for p in sorted(out.glob('*.json'))}
 (out/'closure.json').write_text(json.dumps(r,indent=2,allow_nan=False))
 return r

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path);p.add_argument('--worker',type=Path,help=argparse.SUPPRESS)
 a=p.parse_args()
 if a.worker:
  print(json.dumps(worker(a.worker),allow_nan=False));return 0
 if not a.out:p.error('--out is required')
 try:r=run(a.out)
 except (OSError,ValueError) as e:print(json.dumps({'status':'BLOCKED','reason':str(e)}));return 2
 print(json.dumps({k:r.get(k) for k in ('status','case_count','baseline_disagreements','candidate_disagreements','package_unchanged')}))
 return 0 if r['status']=='KNOWN_DEFECTS_CLOSED_FOR_TESTED_SCOPE' else 2
if __name__=='__main__':raise SystemExit(main())
