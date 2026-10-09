"""Deterministic interleaving checks at the capture-completion boundary."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from forge_core.common import Blocked
from forge_core.bounded_io import _Capture, run_bounded

class CompletionBoundary(unittest.TestCase):
    def check_edge(self, event, expected):
        capture = _Capture(100)
        class FinishedProcess:
            stdin = None
            stdout = io.BytesIO()
            stderr = io.BytesIO()
            returncode = 0
            pid = 99999999
            def poll(self):
                # Readers can publish an error after the supervisor's first
                # flag check, but before its all-streams-finished check.
                getattr(capture, event).set()
                return 0
            def wait(self, timeout=None):
                return 0
        with tempfile.TemporaryDirectory() as home:
            with patch('forge_core.bounded_io._Capture', return_value=capture), \
                 patch('forge_core.bounded_io.subprocess.Popen', return_value=FinishedProcess()), \
                 patch('forge_core.bounded_io._kill'):
                with self.assertRaisesRegex(Blocked, expected):
                    run_bounded(['fixed-tool'], Path(home), {}, timeout=2)
    def test_late_overflow_cannot_become_success(self):
        self.check_edge('overflow', 'TOOL_OUTPUT_LIMIT')
    def test_late_io_failure_cannot_become_success(self):
        self.check_edge('failed', 'TOOL_IO_FAILED')

if __name__ == '__main__':
    unittest.main()
