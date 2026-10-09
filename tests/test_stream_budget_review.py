"""Second review of the bounded I/O repair, using fixed trusted synthetic tools."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import os,sys,tempfile,threading,time,unittest
from unittest.mock import patch
from forge_core.engine import run_fixed
from forge_core.bounded_io import _Capture
from forge_core.common import Blocked

class StreamBudgetReview(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.home=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def call(self,code='pass',**kwargs):return run_fixed([sys.executable,'-I','-c',code],self.home,**kwargs)
    def test_invalid_deadlines_rejected_before_launch(self):
        for value in [True,False,None,'5',0,-1,float('inf'),float('nan'),601]:
            with self.subTest(value=value),patch('forge_core.bounded_io.subprocess.Popen') as popen:
                with self.assertRaisesRegex(Blocked,'TOOL_TIMEOUT_BOUNDS'):self.call(timeout=value)
                popen.assert_not_called()
    def test_invalid_input_rejected_before_launch(self):
        for value in ['text',bytearray(b'a'),b'a'*8_000_001]:
            with self.subTest(kind=type(value).__name__),patch('forge_core.bounded_io.subprocess.Popen') as popen:
                with self.assertRaisesRegex(Blocked,'TOOL_INPUT_BOUNDS'):self.call(input=value)
                popen.assert_not_called()
    def test_invalid_argv_rejected_before_launch(self):
        for argv in [[],[''],['a\x00b'],'echo a',[1],['x']*101]:
            with self.subTest(argv=str(argv)[:40]),patch('forge_core.bounded_io.subprocess.Popen') as popen:
                with self.assertRaisesRegex(Blocked,'TOOL_ARGUMENTS'):run_fixed(argv,self.home)
                popen.assert_not_called()
    def test_empty_input_closes_stdin(self):self.assertEqual(self.call('import sys;print(len(sys.stdin.buffer.read()))',input=b''),b'0\n')
    def test_tuple_argv_supported(self):self.assertEqual(run_fixed((sys.executable,'-c','print(5)'),self.home),b'5\n')
    def test_literal_shell_characters(self):
        self.assertEqual(run_fixed([sys.executable,'-c','import sys;print(sys.argv[1])','; echo NOT_EXECUTED'],self.home),b'; echo NOT_EXECUTED\n')
    def test_no_diagnostic_disclosure(self):
        with self.assertRaises(Blocked) as e:self.call("import sys;sys.stderr.write('PRIVATE_CANARY');sys.exit(4)")
        self.assertEqual(str(e.exception),'TOOL_EXIT_4')
    def test_concurrent_invocations_separate(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            values=list(pool.map(lambda n:self.call(f'print({n})'),range(8)))
        self.assertEqual(values,[f'{i}\n'.encode() for i in range(8)])
    def test_capture_retains_no_more_than_allowance(self):
        c=_Capture(10);c.accept(b'aaa',False);c.accept(b'b'*20,True)
        self.assertEqual(bytes(c.output),b'b'*7);self.assertTrue(c.overflow.is_set());self.assertEqual(c.consumed,23)
    def test_capture_exact_combined_allowance(self):
        c=_Capture(10);c.accept(b'1234',False);c.accept(b'abcdef',True)
        self.assertEqual(bytes(c.output),b'abcdef');self.assertFalse(c.overflow.is_set())
    def test_capture_shared_budget_threads(self):
        c=_Capture(1000)
        ts=[threading.Thread(target=c.accept,args=(b'x'*300,True)) for _ in range(10)]
        for t in ts:t.start()
        for t in ts:t.join()
        self.assertEqual(len(c.output),1000);self.assertEqual(c.consumed,3000);self.assertTrue(c.overflow.is_set())
    def test_stderr_chunk_boundary(self):
        with patch('forge_core.bounded_io.MAX_OUTPUT_BYTES',1024):
            with self.assertRaisesRegex(Blocked,'TOOL_OUTPUT_LIMIT'):
                self.call("import sys;sys.stderr.buffer.write(b'x'*1025)")
    def test_combined_exact_limit(self):
        with patch('forge_core.bounded_io.MAX_OUTPUT_BYTES',1000):
            self.assertEqual(self.call("import sys;sys.stderr.buffer.write(b'x'*300);sys.stderr.flush();sys.stdout.buffer.write(b'y'*700)"),b'y'*700)
    def test_no_helper_threads_remain(self):
        before={t.ident for t in threading.enumerate() if t.name.startswith('forge-tool-')}
        for _ in range(3):self.call("import sys;sys.stdout.write('ok');sys.stderr.write('note')")
        after={t.ident for t in threading.enumerate() if t.name.startswith('forge-tool-')}
        self.assertEqual(before,after)
    def test_timeout_reaps_immediate_child(self):
        import subprocess
        original=subprocess.Popen;children=[]
        def capture(*args,**kwargs):
            child=original(*args,**kwargs);children.append(child);return child
        with patch('forge_core.bounded_io.subprocess.Popen',side_effect=capture):
            with self.assertRaisesRegex(Blocked,'TOOL_TIMEOUT'):self.call('import time;time.sleep(5)',timeout=.25)
        self.assertEqual(len(children),1)
        self.assertIsNotNone(children[0].returncode)
        with self.assertRaises(ProcessLookupError):os.kill(children[0].pid,0)
    def test_pipe_owner_descendant_cannot_hide_parent_exit(self):
        # POSIX group cleanup is exercised on this Linux test host.
        if os.name!='posix':self.skipTest('POSIX process-group qualification required')
        script="import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(1)'])"
        with self.assertRaisesRegex(Blocked,'TOOL_TIMEOUT'):self.call(script,timeout=.15)
    def test_stream_failure_reaps_before_return(self):
        pidfile=self.home/'pid'
        code=f"import os,sys,time;open({str(pidfile)!r},'w').write(str(os.getpid()));sys.stderr.buffer.write(b'x'*9000000);sys.stderr.flush();time.sleep(5)"
        with self.assertRaisesRegex(Blocked,'TOOL_OUTPUT_LIMIT'):self.call(code)
        with self.assertRaises(ProcessLookupError):os.kill(int(pidfile.read_text()),0)
    def test_input_output_and_error_simultaneously(self):
        script="import sys,threading\ndef error():\n sys.stderr.buffer.write(b'e'*1000000);sys.stderr.flush()\nt=threading.Thread(target=error);t.start();sys.stdout.buffer.write(sys.stdin.buffer.read());sys.stdout.flush();t.join()"
        payload=b'v'*1000000;self.assertEqual(self.call(script,input=payload),payload)

if __name__=='__main__':unittest.main()
