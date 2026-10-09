"""Execute bounded qualification rounds for THIS Hound package, not user code.

Two clean rounds mean repeatability in the declared test scope, not independent
validation, production authorization or absence of all defects. This runner
never edits code or tests. Repairs require review, then a new output directory.
"""
from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
import argparse,hashlib,json,os,subprocess,sys,time
import atlas_hunt

SCRIPTS=(('regression','run_tests.py','tests.json'),
         ('review','run_review_benchmark.py','comparison.json'),
         ('legacy','run_benchmark.py','comparison.json'),
         ('closure','run_defect_closure.py','closure.json'),
         ('library','run_library_validation.py','library_validation.json'))

def stamp():return datetime.now(timezone.utc).isoformat()
def package_binding(root):
 paths=set(root.glob('*.py'))
 for d in ('tests','profiles','missions','reference','data','contracts','missions_public'):
  paths.update(p for p in (root/d).rglob('*') if p.is_file() and p.suffix in ('.py','.json','.sqlite'))
 paths.add(root/'evidence'/'benchmark_contract.json')
 return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}

def check_stage(kind,record):
 """Validate observed report fields, not the truth of an untrusted producer."""
 if not isinstance(record,dict):raise ValueError('REPORT_MAPPING_REQUIRED')
 if kind=='regression':
  n=record.get('tests_run')
  if type(n) is not int or n<1:raise ValueError('NO_EXECUTED_TESTS')
  for key in ('failures','errors','skips'):
   if type(record.get(key)) is not int or record[key]!=0:raise ValueError('REGRESSION_'+key.upper())
  return
 if kind=='library':
  from run_library_validation import validate
  validate(record);return
 if kind=='closure':
  from run_defect_closure import validate_closure
  validate_closure(record);return
 if kind not in ('review','legacy'):raise ValueError('UNKNOWN_STAGE')
 n=record.get('case_count');rows=record.get('rows') if kind=='review' else record.get('cases')
 if type(n) is not int or n<1 or not isinstance(rows,list) or len(rows)!=n:raise ValueError('CASE_DENOMINATOR')
 ids=[]
 for row in rows:
  if not isinstance(row,dict):raise ValueError('CASE_SCHEMA')
  cid=row.get('id',row.get('case_id'))
  if not isinstance(cid,str) or not cid.strip() or cid in ids:raise ValueError('DUPLICATE_OR_MISSING_CASE')
  ids.append(cid)
  if row.get('candidate_correct') is not True:raise ValueError('CANDIDATE_CASE_FAILED')
  if type(row.get('baseline_correct')) is not bool:raise ValueError('BASELINE_CASE_MISSING')
  if row.get('candidate')!=row.get('expected'):raise ValueError('CASE_CLAIM_MISMATCH')
 if kind=='review':
  if type(record.get('candidate_correct')) is not int or record['candidate_correct']!=n:raise ValueError('AGGREGATE_MISMATCH')
  if type(record.get('regressions')) is not int or record['regressions']!=0:raise ValueError('REQUIRED_REGRESSION')
 else:
  if record.get('required_checks_passed') is not True or type(record.get('required_regressions')) is not int or record['required_regressions']!=0:
   raise ValueError('REQUIRED_REGRESSION')

def run(out:Path,rounds:int=2,timeout:int=90):
 root=Path(__file__).resolve().parent
 if type(rounds) is not int or not 2<=rounds<=3:raise ValueError('TWO_OR_THREE_ROUNDS_REQUIRED')
 if type(timeout) is not int or not 10<=timeout<=300:raise ValueError('INVALID_STAGE_TIMEOUT')
 if out.exists() or out.is_symlink():raise ValueError('NEW_OUTPUT_DIRECTORY_REQUIRED')
 if out.resolve().is_relative_to(root):raise ValueError('OUTPUT_MUST_BE_OUTSIDE_PACKAGE')
 binding=package_binding(root);out.mkdir(parents=True,mode=0o700)
 report={'schema':1,'started_utc':stamp(),'status':'RUNNING','rounds_requested':rounds,'rounds_completed':0,
         'package_binding':binding,'stages':[],'release_approved':False,'independent_evaluation':False,
         'scope':'Local synthetic regressions and frozen developer-authored diagnostics; no collected source execution.'}
 def save():
  (out/'qualification.json').write_text(json.dumps(report,indent=2,allow_nan=False))
 save()
 try:
  for round_no in range(1,rounds+1):
   for kind,script,artifact in SCRIPTS:
    stage_out=out/f'round-{round_no}'/kind;stage_out.parent.mkdir(exist_ok=True)
    if package_binding(root)!=binding:raise ValueError('PACKAGE_CHANGED_BEFORE_STAGE')
    started=time.perf_counter()
    bootstrap='import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_path(sys.argv.pop(1),run_name="__main__")'
    proc=subprocess.run([sys.executable,'-I','-B','-c',bootstrap,str(root),str(root/script),'--out',str(stage_out)],
       cwd=root,capture_output=True,timeout=timeout,env={'PATH':os.defpath,'LANG':'C.UTF-8'},check=False)
    (stage_out.parent/(kind+'-stdout.txt')).write_bytes(proc.stdout)
    (stage_out.parent/(kind+'-stderr.txt')).write_bytes(proc.stderr)
    row={'round':round_no,'kind':kind,'returncode':proc.returncode,'duration_seconds':round(time.perf_counter()-started,6)}
    report['stages'].append(row);save()
    if proc.returncode:raise ValueError('STAGE_PROCESS_FAILED:'+kind)
    raw=atlas_hunt.bounded_read(stage_out/artifact,8_000_000);record=atlas_hunt.strict_json(raw)
    check_stage(kind,record)
    if package_binding(root)!=binding:raise ValueError('PACKAGE_CHANGED_DURING_STAGE')
    row.update(status='PASSED',artifact=str((stage_out/artifact).relative_to(out)),sha256=hashlib.sha256(raw).hexdigest());save()
   report['rounds_completed']=round_no;save()
  report['status']='CLEAN_FOR_DECLARED_SCOPE_REVIEW_REQUIRED'
 except (ValueError,OSError,subprocess.TimeoutExpired) as exc:
  report.update(status='BLOCKED',blocker=type(exc).__name__+': '+str(exc))
 finally:
  report['finished_utc']=stamp();report['package_unchanged']=package_binding(root)==binding;save()
 return report

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
 p.add_argument('--rounds',type=int,default=2);p.add_argument('--timeout',type=int,default=90);a=p.parse_args()
 try:r=run(a.out,a.rounds,a.timeout)
 except (ValueError,OSError) as e:print(json.dumps({'status':'BLOCKED','reason':str(e)}));return 2
 print(json.dumps({k:r[k] for k in ('status','rounds_completed','package_unchanged','release_approved')},indent=2))
 return 0 if r['status']=='CLEAN_FOR_DECLARED_SCOPE_REVIEW_REQUIRED' and r['package_unchanged'] else 2
if __name__=='__main__':raise SystemExit(main())
