import json,subprocess,sys,tempfile,unittest
from pathlib import Path
from research_runner.core import no_links,Blocked
ROOT=Path(__file__).resolve().parents[1]
class Paths(unittest.TestCase):
 def test_relative_parent_normalized(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'app').mkdir();self.assertEqual(no_links(root/'app/../state'),root/'state')
 def test_symlink_even_before_parent(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'real').mkdir();(root/'link').symlink_to(root/'real')
   with self.assertRaises(Blocked):no_links(root/'link/../state')
 def test_cli_missing_input_controlled(self):
  p=subprocess.run([sys.executable,str(ROOT/'forge_research.py'),'run','--manifest','/this-input-does-not-exist','--source-root','/also-missing','--work','/missing-work','--forge-root','/missing-forge'],capture_output=True,text=True)
  self.assertEqual(p.returncode,2);self.assertNotIn('Traceback',p.stderr);self.assertEqual(json.loads(p.stdout)['status'],'BLOCKED')
