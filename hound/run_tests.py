"""Run the shipped regressions and retain hashes; output must be a new directory."""
from pathlib import Path
import argparse,datetime,hashlib,json,platform,sys,unittest

def main():
 p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 if a.out.exists():p.error('Use a new evidence directory')
 root=Path(__file__).resolve().parent
 a.out.mkdir(parents=True)
 suite=unittest.defaultTestLoader.discover(str(root/'tests'))
 with (a.out/'tests.txt').open('w') as stream:
  r=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
 report={'timestamp_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
  'tests_run':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skips':len(r.skipped),
  'python':platform.python_version(),'platform':platform.platform(),
  'scope':'synthetic detector/gate/integration tests; no application geometry execution or independent model',
  'hashes':{x.relative_to(root).as_posix():hashlib.sha256(x.read_bytes()).hexdigest()
    for x in sorted(root.rglob('*')) if x.is_file() and x.suffix in {'.py','.json'} and 'evidence' not in x.relative_to(root).parts}}
 (a.out/'tests.json').write_text(json.dumps(report,indent=2))
 print(json.dumps({k:report[k] for k in ('tests_run','failures','errors','skips')}))
 return 0 if r.testsRun > 0 and r.wasSuccessful() and not r.skipped else 2

if __name__=='__main__':raise SystemExit(main())
