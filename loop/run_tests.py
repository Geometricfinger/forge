from pathlib import Path
from datetime import datetime,timezone
import argparse,json,os,sys,unittest,time
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent
from safeio import canonical,digest,write_new,Blocked

def main():
 p=argparse.ArgumentParser();p.add_argument('--runtime',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 if a.out.exists() or a.out.resolve().is_relative_to(ROOT):raise Blocked('NEW_EXTERNAL_OUTPUT')
 a.out.mkdir(parents=True);os.environ['FORGE_TEST_RUNTIME']=str(a.runtime.resolve())
 suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py')
 start=time.monotonic()
 with (a.out/'tests.txt').open('w') as log:r=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
 report={'tests_run':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skips':len(r.skipped),
         'failed_ids':[str(t) for t,_ in r.failures],'error_ids':[str(t) for t,_ in r.errors],
         'duration_seconds':time.monotonic()-start,'utc':datetime.now(timezone.utc).isoformat(),
         'python':sys.version,'test_sources':{p.name:digest(p.read_bytes()) for p in sorted((ROOT/'tests').glob('test_*.py'))},
         'release_approved':False,'independent_evaluation':False}
 write_new(a.out/'tests.json',canonical(report));print(json.dumps(report))
 return 0 if r.testsRun and not(r.failures or r.errors or r.skipped) else 2
if __name__=='__main__':raise SystemExit(main())
