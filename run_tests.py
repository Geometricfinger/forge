"""Execute the new workbench contract. Missing tests, errors and skips fail."""
from pathlib import Path
import argparse,io,json,sys,time,unittest
from forge_core.common import *
ROOT=Path(__file__).resolve().parent
p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
if a.out.exists():raise SystemExit('Choose a new output directory')
a.out.mkdir(parents=True)
binding={str(x.relative_to(ROOT)):sha(read(x)) for x in sorted((ROOT/'tests').glob('*.py'))}
start=time.monotonic();stream=io.StringIO();suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
r=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
obj={'tests_run':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skips':len(r.skipped),'failed_ids':[t.id() for t,_ in r.failures],'error_ids':[t.id() for t,_ in r.errors],'duration_seconds':time.monotonic()-start,'test_binding':binding,'independent_evaluation':False,'release_approved':False}
write(a.out/'tests.txt',stream.getvalue().encode(),new=True);write(a.out/'tests.json',canonical(obj),new=True)
print(json.dumps(obj));raise SystemExit(0 if r.testsRun>0 and r.wasSuccessful() and not r.skipped else 2)
