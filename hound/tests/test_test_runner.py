from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

class RunnerControls(unittest.TestCase):
    def test_empty_suite_never_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'tests').mkdir()
            shutil.copyfile(Path(__file__).resolve().parents[1]/'run_tests.py',root/'run_tests.py')
            p=subprocess.run([sys.executable,str(root/'run_tests.py'),'--out',str(root/'out')],capture_output=True,timeout=10)
            self.assertEqual(p.returncode,2)
    def test_retained_benchmark_reproduction(self):
        with tempfile.TemporaryDirectory() as td:
            script=Path(__file__).resolve().parents[1]/'run_benchmark.py'
            p=subprocess.run([sys.executable,str(script),'--out',str(Path(td)/'out')],capture_output=True,timeout=15)
            self.assertEqual(p.returncode,0,p.stderr.decode())
    def test_review_benchmark_reproduction(self):
        with tempfile.TemporaryDirectory() as td:
            script=Path(__file__).resolve().parents[1]/'run_review_benchmark.py'
            p=subprocess.run([sys.executable,str(script),'--out',str(Path(td)/'out')],capture_output=True,timeout=15)
            self.assertEqual(p.returncode,0,p.stderr.decode())
