"""The CLI's exhaustion response must not depend on allocating formatted JSON."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MemoryRefusalTests(unittest.TestCase):
    def test_cli_releases_failed_frame_and_emits_without_json_formatter(self):
        script = '''
import atexit, json, os, runpy, sys
from forge_core import workspace
released = False
raw_write = os.write
class HeavyLocals:
    def __init__(self):
        self.data = bytearray(8 * 1024 * 1024)
    def __del__(self):
        global released
        released = True
def exhausted(*args, **kwargs):
    retained = HeavyLocals()
    raise MemoryError()
def no_formatter(*args, **kwargs):
    raise AssertionError('JSON formatter unavailable')
def checked_write(fd, data):
    if fd == 1 and not released:
        raise AssertionError('failed command frame retained during refusal')
    return raw_write(fd, data)
workspace.initialize = exhausted
json.dumps = no_formatter
os.write = checked_write
atexit.register(lambda: raw_write(2, b'NORMAL_CLEANUP\\n'))
sys.argv = ['forge.py', '--home', sys.argv[1], *sys.argv[2:], 'init']
runpy.run_path('forge.py', run_name='__main__')
'''
        with tempfile.TemporaryDirectory() as temporary:
            for mode in ([], ['--pretty'], ['--human']):
                with self.subTest(mode=mode):
                    result = subprocess.run([sys.executable, '-c', script,
                                             str(Path(temporary).resolve() / 'unused-home'), *mode],
                                            cwd=ROOT, env=os.environ | {'PYTHONDONTWRITEBYTECODE': '1'},
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
                    self.assertEqual(result.returncode, 2, result.stderr.decode())
                    self.assertEqual(result.stderr, b'NORMAL_CLEANUP\n')
                    if mode == ['--human']:
                        self.assertEqual(result.stdout, b'Status: BLOCKED\nreason: MEMORY_BUDGET_EXHAUSTED\nrelease_approved: no\n')
                    else:
                        self.assertEqual(json.loads(result.stdout),
                                         {'status': 'BLOCKED', 'reason': 'MEMORY_BUDGET_EXHAUSTED',
                                          'release_approved': False})
            self.assertFalse((Path(temporary) / 'unused-home').exists())
