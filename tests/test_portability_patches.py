"""Checks for the portability and robustness patches (multi-envelope ids, parser output cap, killpg EPERM fallback, corpus-run --profile, report read limit)."""
import errno, io, json, os, signal, subprocess, sys, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
from forge_core import bounded_io
from forge_core.common import Blocked, canonical, git_sha, sha
from forge_core.engine import run_fixed
from forge_core.github_corpus import validate_source, KIND

class KillEpermTest(unittest.TestCase):
    def test_eperm_falls_back_to_direct_child(self):
        p = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], start_new_session=True)
        try:
            with patch('forge_core.bounded_io.os.killpg', side_effect=PermissionError(errno.EPERM, 'EPERM')):
                bounded_io._kill(p)          # must not raise
            self.assertEqual(p.wait(5), -signal.SIGKILL)
        finally:
            if p.poll() is None: p.kill(); p.wait()

    def test_eperm_on_exited_child_is_silent(self):
        p = subprocess.Popen([sys.executable, '-c', 'pass'], start_new_session=True); p.wait()
        with patch('forge_core.bounded_io.os.killpg', side_effect=PermissionError(errno.EPERM, 'EPERM')):
            bounded_io._kill(p)

    def test_failing_tool_reports_exit_not_permission_error(self):
        home = Path(tempfile.mkdtemp())
        with patch('forge_core.bounded_io.os.killpg', side_effect=PermissionError(errno.EPERM, 'EPERM')):
            with self.assertRaisesRegex(Blocked, 'TOOL_EXIT_3'):
                run_fixed([sys.executable, '-c', 'raise SystemExit(3)'], home)

class OutputLimitTest(unittest.TestCase):
    def setUp(self): self.home = Path(tempfile.mkdtemp())
    def test_default_cap_unchanged(self):
        self.assertEqual(bounded_io.MAX_OUTPUT_BYTES, 8_000_000)
        with self.assertRaisesRegex(Blocked, 'TOOL_OUTPUT_LIMIT'):
            run_fixed([sys.executable, '-c', 'import sys;sys.stdout.write("x"*8_100_000)'], self.home)
    def test_explicit_parser_cap(self):
        out = run_fixed([sys.executable, '-c', 'import sys;sys.stdout.write("x"*9_000_000)'], self.home,
                        output_limit=bounded_io.MAX_PARSER_OUTPUT_BYTES)
        self.assertEqual(len(out), 9_000_000)
    def test_cap_bounds(self):
        for bad in (0, -1, bounded_io.MAX_PARSER_OUTPUT_BYTES + 1, 1.5, True):
            with self.assertRaisesRegex(Blocked, 'TOOL_OUTPUT_BOUNDS'):
                run_fixed([sys.executable, '-c', 'pass'], self.home, output_limit=bad)

def envelope(file_id):
    body = b'def f():\n    return 1\n'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z: z.writestr('pkg/a.py', body)
    data = buf.getvalue(); repo = 'owner/repo'; commit = 'a' * 40
    return {'provider': 'github', 'file_id': file_id, 'path': 'e.zip', 'size': len(data), 'sha256': sha(data),
            'source_url': f'https://github.com/{repo}/tree/{commit}', 'repository': repo, 'commit': commit,
            'container_kind': KIND, 'selected_paths': ['pkg/a.py'],
            'members': [{'path': 'pkg/a.py', 'size': len(body), 'sha256': sha(body), 'git_blob_sha1': git_sha(body)}]}

class MultiEnvelopeTest(unittest.TestCase):
    base = 'github:owner/repo@' + 'a' * 40 + ':selected-files'
    def test_original_id_still_valid(self): validate_source(envelope(self.base))
    def test_part_ids_valid(self):
        for n in ('001', '042', '999'): validate_source(envelope(self.base + ':part-' + n))
    def test_bad_part_ids_rejected(self):
        for bad in (':part-1', ':part-0001', ':part-abc', ':other', ':part-001:x'):
            with self.assertRaisesRegex(Blocked, 'GITHUB_CORPUS_ID'): validate_source(envelope(self.base + bad))
    def test_other_commit_rejected(self):
        with self.assertRaisesRegex(Blocked, 'GITHUB_CORPUS_ID'):
            validate_source(envelope('github:owner/repo@' + 'b' * 40 + ':selected-files:part-001'))

class CorpusProfileFlagTest(unittest.TestCase):
    def test_cli_exposes_profile(self):
        root = Path(__file__).resolve().parents[1]
        out = subprocess.run([sys.executable, str(root / 'forge.py'), 'corpus-run', '--help'], capture_output=True, text=True, cwd=root)
        self.assertIn('--profile', out.stdout)

if __name__ == '__main__': unittest.main()
