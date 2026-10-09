"""Command exit codes must agree with the recorded review verdict."""
from pathlib import Path
import json,os,shutil,subprocess,sys,tempfile,unittest
class BenchmarkExitReview(unittest.TestCase):
 def test_unmet_value_threshold_returns_failure(self):
  root=Path(__file__).resolve().parents[1]
  with tempfile.TemporaryDirectory() as td:
   code=Path(td)/'tool';shutil.copytree(root,code,ignore=shutil.ignore_patterns('__pycache__','data'))
   p=code/'evidence/benchmark_contract.json';contract=json.loads(p.read_text());contract['minimum_change']=10**6;p.write_text(json.dumps(contract))
   out=Path(td)/'out'
   run=subprocess.run([sys.executable,'-B',str(code/'run_benchmark.py'),'--out',str(out)],capture_output=True,timeout=15,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
   self.assertEqual(json.loads((out/'value_gate.json').read_text())['status'],'NOT_ELIGIBLE_FOR_ADOPTION_REVIEW')
   self.assertEqual(run.returncode,2)
