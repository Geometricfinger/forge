"""Package verification through the maintainer and extracted-archive entry points."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


CHECKER = Path(__file__).resolve().parents[1] / 'tools' / 'check_package.py'


class PackageManifest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name).resolve()
        self.runner = self.repo / 'runner'
        self.runner.mkdir()
        (self.runner / 'tools').mkdir()
        shutil.copyfile(CHECKER, self.runner / 'tools/check_package.py')
        (self.runner / 'entry.py').write_text('pass\n')
        (self.repo / '.gitignore').write_text('__pycache__/\noutput/\n')
        (self.runner / 'FILE_HASHES.json').write_text('{}\n')
        self.git('init', '-q')
        self.git('add', '.')
        self.verify('--refresh')

    def git(self, *args):
        subprocess.run(['git', '-C', str(self.repo), *args], capture_output=True, check=True)

    def verify(self, *args, code=0):
        cmd = [sys.executable, '-B', str(self.runner / 'tools/check_package.py'), *args]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, code, result.stderr + result.stdout)
        return result

    def test_staged_source_requires_refresh_and_local_files_are_ignored(self):
        (self.runner / 'output').mkdir()
        (self.runner / 'output/result.json').write_text('{}')
        self.assertTrue(json.loads(self.verify().stdout)['consistent'])
        extra = self.runner / 'test_added.py'
        extra.write_text('pass\n')
        baseline = (self.runner / 'FILE_HASHES.json').read_bytes()
        self.assertTrue(json.loads(self.verify().stdout)['consistent'])
        self.verify('--refresh')
        self.assertEqual((self.runner / 'FILE_HASHES.json').read_bytes(), baseline)
        self.git('add', 'runner/test_added.py')
        self.assertEqual(json.loads(self.verify(code=2).stdout)['unlisted_files'], ['test_added.py'])
        self.assertEqual(json.loads(self.verify('--refresh').stdout)['manifest_files'], 3)
        manifest = (self.runner / 'FILE_HASHES.json').read_bytes()
        self.verify('--refresh')
        self.assertEqual((self.runner / 'FILE_HASHES.json').read_bytes(), manifest)

    def test_changed_and_missing_bytes_fail_in_checkout_and_archive(self):
        path = self.runner / 'entry.py'
        path.write_text('past\n')
        self.assertEqual(json.loads(self.verify(code=2).stdout)['mismatches'], ['entry.py'])
        shutil.rmtree(self.repo / '.git')
        result = json.loads(self.verify(code=2).stdout)
        self.assertEqual(result['inventory_scope'], 'MANIFEST_ENTRIES_ONLY')
        self.assertEqual(result['mismatches'], ['entry.py'])
        path.write_text('pass\n')
        self.assertTrue(json.loads(self.verify().stdout)['consistent'])
        path.unlink()
        self.assertEqual(json.loads(self.verify(code=2).stdout)['mismatches'], ['entry.py'])
        self.verify('--refresh', code=2)

    def test_untracked_manifest_entry_and_missing_tracked_file(self):
        self.git('rm', '--cached', 'runner/entry.py')
        result = json.loads(self.verify(code=2).stdout)
        self.assertEqual(result['untracked_manifest_entries'], ['entry.py'])
        self.git('add', 'runner/entry.py')
        (self.runner / 'entry.py').unlink()
        self.assertEqual(json.loads(self.verify(code=2).stdout)['mismatches'], ['entry.py'])
        manifest = (self.runner / 'FILE_HASHES.json').read_bytes()
        self.verify('--refresh', code=2)
        self.assertEqual((self.runner / 'FILE_HASHES.json').read_bytes(), manifest)

    def test_refresh_preserves_previously_recorded_historical_contracts(self):
        contracts = self.runner / 'contracts'
        contracts.mkdir()
        pin = contracts / 'TESTS_FROZEN.sha256'
        pin.write_text('original historical commitment\n')
        self.git('add', 'runner/contracts')
        self.verify('--refresh')
        manifest = (self.runner / 'FILE_HASHES.json').read_bytes()
        pin.write_text('changed historical commitment\n')
        self.assertEqual(json.loads(self.verify(code=2).stdout)['mismatches'],
                         ['contracts/TESTS_FROZEN.sha256'])
        self.verify('--refresh', code=2)
        self.git('rm', '--cached', '--force', 'runner/contracts/TESTS_FROZEN.sha256')
        self.verify('--refresh', code=2)
        self.assertEqual((self.runner / 'FILE_HASHES.json').read_bytes(), manifest)
