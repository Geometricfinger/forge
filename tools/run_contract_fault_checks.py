#!/usr/bin/env python3
"""Fixed fault controls in disposable copies; never modify the installed build."""
from pathlib import Path
import sys,subprocess,json,shutil,tempfile,argparse,hashlib,os
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from forge_core.common import Blocked,safe_path,canonical,write
FAULTS={
 'duplicate_keys': [('forge_core/interop.py',"if k in result: raise Blocked('DUPLICATE_JSON_KEY')","if False: raise Blocked('DUPLICATE_JSON_KEY')")],
 'component_pin': [('forge_core/interop.py',"if actual != expected: raise Blocked('COMPONENT_BINDING_CHANGED')","if False: raise Blocked('COMPONENT_BINDING_CHANGED')")],
 'missing_reference': [('forge_core/contract_trial.py',"if n['status']!='PASSED' or n['passed'] is not True:return False","if n['status']!='PASSED' or n['passed'] is not True:return True")],
 'unsafe_numeric_domain': [('forge_core/interop.py','if exact == exact.to_integral_value() and abs(exact) > MAX_SAFE_INTEGER:','if False:'),('forge_core/interop.py','if f.is_integer() and abs(f) > MAX_SAFE_INTEGER:','if False:')]
}
DRIVER="""import sys,json,unittest,importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('fixed',Path('tools/contract_fault_cases.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(m))
Path(sys.argv[1]).write_text(json.dumps({'tests_run':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skips':len(r.skipped),'failed_ids':[t.id() for t,_ in r.failures]}))
"""
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();out=safe_path(a.out)
 if out.exists() or out.is_relative_to(ROOT) or ROOT.is_relative_to(out):raise Blocked('NEW_EXTERNAL_OUTPUT_REQUIRED')
 out.mkdir(parents=True);testhash=hashlib.sha256((ROOT/'tools/contract_fault_cases.py').read_bytes()).hexdigest();rows=[]
 with tempfile.TemporaryDirectory(prefix='forge-fixed-faults-') as temp:
  for name,changes in [('healthy',[])]+list(FAULTS.items()):
   root=Path(temp)/name;shutil.copytree(ROOT,root,ignore=shutil.ignore_patterns('__pycache__','*.pyc','evidence','history','data','dependencies','Preview.html'))
   for file,old,new in changes:
    p=root/file;text=p.read_text()
    if text.count(old)!=1:raise Blocked('FAULT_TARGET_AMBIGUOUS')
    p.write_text(text.replace(old,new))
   if hashlib.sha256((root/'tools/contract_fault_cases.py').read_bytes()).hexdigest()!=testhash:raise Blocked('TESTS_CHANGED')
   dest=out/(name+'.json')
   env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','HOME','TMPDIR')};env['PYTHONDONTWRITEBYTECODE']='1'
   with (out/(name+'.log')).open('xb') as log:
    p=subprocess.run([sys.executable,'-c',DRIVER,str(dest)],cwd=root,stdout=log,stderr=subprocess.STDOUT,env=env,timeout=30)
   if p.returncode or not dest.exists():raise Blocked('FAULT_HARNESS_ERROR')
   r=json.loads(dest.read_text());r['variant']=name;rows.append(r)
 good=rows[0]['tests_run']==4 and rows[0]['failures']==0 and all(x['tests_run']==4 and x['errors']==0 and x['skips']==0 and (x['failures']>0 if x['variant']!='healthy' else True) for x in rows)
 result={'status':'DETECTED_ALL_FIXED_FAULTS' if good else 'FAILED','test_sha256':testhash,'results':rows,'independent_evaluation':False,'release_approved':False}
 write(out/'summary.json',canonical(result),new=True);print(json.dumps(result));return 0 if good else 2
if __name__=='__main__':raise SystemExit(main())
