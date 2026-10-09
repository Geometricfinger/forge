"""Frozen reproductions for the fixed trusted-tool execution boundary.
These programs are first-party synthetic checks, not discovered code.
"""
from pathlib import Path
import os,sys,tempfile,time,unittest
from forge_core.engine import run_fixed
from forge_core.common import Blocked

LIMIT=8_000_000
class StreamBudgetContract(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.home=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def run_code(self,code,input=None,timeout=5):
        return run_fixed([sys.executable,'-I','-c',code],self.home,timeout,input)
    def test_small_stdout_preserved(self):
        self.assertEqual(self.run_code("import os;os.write(1,b'answer\\x00\\xff')"),b'answer\x00\xff')
    def test_small_stderr_not_added_to_result(self):
        self.assertEqual(self.run_code("import os;os.write(2,b'diagnostic');os.write(1,b'answer')"),b'answer')
    def test_stderr_counts_against_budget(self):
        with self.assertRaisesRegex(Blocked,'^TOOL_OUTPUT_LIMIT$'):
            self.run_code(f"import sys;sys.stderr.buffer.write(b'x'*{LIMIT+1});sys.stderr.flush();print('accepted')")
    def test_combined_streams_count_against_one_budget(self):
        with self.assertRaisesRegex(Blocked,'^TOOL_OUTPUT_LIMIT$'):
            self.run_code("import sys;sys.stdout.buffer.write(b'x'*5000000);sys.stdout.flush();sys.stderr.buffer.write(b'y'*4000000);sys.stderr.flush()")
    def test_stdout_overflow_detected_before_deadline(self):
        with self.assertRaisesRegex(Blocked,'^TOOL_OUTPUT_LIMIT$'):
            self.run_code(f"import sys,time;sys.stdout.buffer.write(b'x'*{LIMIT+1});sys.stdout.flush();time.sleep(3)",timeout=.8)
    def test_stderr_overflow_detected_before_deadline(self):
        with self.assertRaisesRegex(Blocked,'^TOOL_OUTPUT_LIMIT$'):
            self.run_code(f"import sys,time;sys.stderr.buffer.write(b'x'*{LIMIT+1});sys.stderr.flush();time.sleep(3)",timeout=.8)
    def test_exact_limit_is_admitted(self):
        self.assertEqual(len(self.run_code(f"import sys;sys.stdout.buffer.write(b'x'*{LIMIT})")),LIMIT)
    def test_one_byte_over_limit_is_rejected(self):
        with self.assertRaisesRegex(Blocked,'^TOOL_OUTPUT_LIMIT$'):
            self.run_code(f"import sys;sys.stdout.buffer.write(b'x'*{LIMIT+1})")
    def test_input_roundtrip(self):
        payload=bytes(range(256))*4096
        self.assertEqual(self.run_code('import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())',payload),payload)
    def test_output_before_input_cannot_deadlock(self):
        payload=b'x'*200000
        out=self.run_code("import sys;sys.stdout.buffer.write(b'y'*200000);sys.stdout.flush();data=sys.stdin.buffer.read();sys.stderr.write('done');sys.stdout.buffer.write(data)",payload)
        self.assertEqual(out,b'y'*200000+payload)
    def test_nonzero_exit_remains_blocked(self):
        with self.assertRaisesRegex(Blocked,'^TOOL_EXIT_7$'):
            self.run_code("import sys;print('not success');sys.exit(7)")
    def test_timeout_remains_blocked(self):
        with self.assertRaisesRegex(Blocked,'^TOOL_TIMEOUT$'):
            self.run_code('import time;time.sleep(3)',timeout=.1)
    def test_environment_is_scrubbed(self):
        os.environ['FORGE_PRIVATE_CANARY']='not-for-child'
        try:self.assertEqual(self.run_code("import os;print(os.environ.get('FORGE_PRIVATE_CANARY','absent'))"),b'absent\n')
        finally:os.environ.pop('FORGE_PRIVATE_CANARY',None)
    def test_child_exit_before_input_drains(self):
        self.assertEqual(self.run_code('pass',b'x'*200000),b'')

if __name__=='__main__':unittest.main()
