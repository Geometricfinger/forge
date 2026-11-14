"""Synthetic evidence packages: consistency is not independent execution proof."""
import hashlib
import json
from pathlib import Path
import stat
import os
import textwrap
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from unittest.mock import patch
from tools.verify_ci_evidence import EvidenceError, verify

ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'a' * 40


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True) + '\n').encode()


def fixture(status='PASSED_FOR_LOCAL_DECLARED_SCOPE', job='success'):
    files = {'qualification/verification.json': encoded({'status': status}),
             'runner-demo/mission/.runner.lock': b'', 'public-demo/result.json': b'{}\n',
             'nested/SHA256SUMS.txt': b'component manifest\n', 'nested/provenance.json': b'{}\n'}
    provenance = {'commit_sha': COMMIT, 'job_status': job, 'verification_status': status,
                  'verification_sha256': sha(files['qualification/verification.json']),
                  'files': {n: sha(data) for n, data in files.items()}}
    files['provenance.json'] = encoded(provenance)
    rechecksums(files)
    return files


def rechecksums(files):
    files['SHA256SUMS.txt'] = ''.join(sha(data) + '  ./' + n + '\n'
                                   for n, data in sorted(files.items()) if n != 'SHA256SUMS.txt').encode()


class EvidenceChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / 'artifact.zip'

    def tearDown(self):
        self.temp.cleanup()

    def write(self, files):
        with zipfile.ZipFile(self.path, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, data in files.items():
                z.writestr(name, data)
        return self.path

    def test_complete_including_hidden_files(self):
        self.write(fixture())
        result = verify(self.path, COMMIT, sha(self.path.read_bytes()))
        self.assertEqual(result['files_verified'], 7)
        self.assertEqual(result['hidden_files_verified'], 1)
        self.assertTrue(result['reported_qualification_passed'])
        self.assertTrue(result['provider_digest_checked'])
        self.assertFalse(result['independent_execution_verified'])
        self.assertFalse(result['release_approved'])

    def test_upload_recipe_preserves_manifest_members(self):
        from tools.prepare_public_evidence import prepare
        workflow = (ROOT / '.github/workflows/qualification.yml').read_text()
        uploader = workflow.split('name: Upload verification evidence', 1)[1]
        self.assertIn('include-hidden-files: true', uploader)
        self.assertIn('path: ${{ env.PUBLIC_EVIDENCE }}', uploader)
        self.assertIn("steps.public_evidence.outcome == 'success'", uploader)
        self.assertNotIn('env.EVIDENCE', uploader)
        # The exclusion now belongs to checked staging. Verify the original
        # manifest and hidden files survive that path without a raw-folder fallback.
        source = self.root / 'source'; source.mkdir()
        files = fixture()
        for name, data in files.items():
            path = source / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        excluded = source / 'qualification' / 'home'; excluded.mkdir()
        (excluded / '.env').write_text('PASSWORD=' + 'synthetic-exclusion-probe')
        staged = self.root / 'public'
        result = prepare(source, staged, COMMIT)
        self.assertEqual(result['status'], 'PUBLIC_EVIDENCE_PREPARED')
        actual = {p.relative_to(staged).as_posix(): p.read_bytes()
                  for p in staged.rglob('*') if p.is_file()}
        self.assertEqual(actual, files)
        self.write(actual)
        self.assertEqual(verify(self.path, COMMIT)['files_verified'], len(files))

    def test_missing_hidden_file_reproduces_download_failure(self):
        files = fixture(); del files['runner-demo/mission/.runner.lock']
        self.write(files)
        with self.assertRaisesRegex(EvidenceError, 'FILE_SET_MISMATCH: missing=1 extra=0'):
            verify(self.path, COMMIT)

    def test_modified_content_rejected(self):
        files = fixture(); files['public-demo/result.json'] = b'{"changed":true}\n'
        self.write(files)
        with self.assertRaisesRegex(EvidenceError, 'FILE_HASH_MISMATCH'):
            verify(self.path, COMMIT)

    def test_extra_file_rejected(self):
        files = fixture(); files['unlisted.txt'] = b'unknown'
        self.write(files)
        with self.assertRaisesRegex(EvidenceError, 'FILE_SET_MISMATCH'):
            verify(self.path, COMMIT)

    def test_wrong_commit_rejected(self):
        self.write(fixture())
        with self.assertRaisesRegex(EvidenceError, 'COMMIT_MISMATCH'):
            verify(self.path, 'b' * 40)

    def test_commit_cannot_be_omitted_or_arbitrary(self):
        self.write(fixture())
        for value in (None, 'main', '', True):
            with self.subTest(value=value), self.assertRaisesRegex(EvidenceError, 'EXPECTED_COMMIT_REQUIRED'):
                verify(self.path, value)

    def test_wrong_provider_digest_rejected(self):
        self.write(fixture())
        with self.assertRaisesRegex(EvidenceError, 'ARCHIVE_DIGEST_MISMATCH'):
            verify(self.path, COMMIT, '0' * 64)

    def test_duplicate_member_rejected(self):
        self.write(fixture())
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(self.path, 'a') as z:
                z.writestr('public-demo/result.json', b'{}\n')
        with self.assertRaisesRegex(EvidenceError, 'DUPLICATE_ARCHIVE_PATH'):
            verify(self.path, COMMIT)

    def test_unsafe_paths_never_extracted(self):
        for name in ('../outside.txt', '/absolute.txt', 'x/../outside.txt', 'x\\outside.txt', './a.txt', 'x//a.txt', 'x:\\a', 'a\nb'):
            with self.subTest(name=name):
                files = fixture(); files[name] = b'not written'
                self.write(files)
                with self.assertRaisesRegex(EvidenceError, 'UNSAFE_PATH'):
                    verify(self.path, COMMIT)
                self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['artifact.zip'])

    def test_symlink_member_rejected(self):
        self.write(fixture())
        with zipfile.ZipFile(self.path, 'a') as z:
            info = zipfile.ZipInfo('link'); info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, 'outside')
        with self.assertRaisesRegex(EvidenceError, 'UNSAFE_ARCHIVE_MEMBER'):
            verify(self.path, COMMIT)

    def test_duplicate_provenance_key_rejected(self):
        files = fixture(); files['provenance.json'] = b'{"files":{},"files":{}}'
        rechecksums(files); self.write(files)
        with self.assertRaisesRegex(EvidenceError, 'DUPLICATE_JSON_KEY'):
            verify(self.path, COMMIT)

    def test_checksum_list_is_verified_too(self):
        files = fixture(); files['SHA256SUMS.txt'] = b''; self.write(files)
        with self.assertRaisesRegex(EvidenceError, 'CHECKSUM_MANIFEST_MISMATCH'):
            verify(self.path, COMMIT)

    def test_duplicate_checksum_is_not_silently_collapsed(self):
        files = fixture(); files['SHA256SUMS.txt'] *= 2; self.write(files)
        with self.assertRaisesRegex(EvidenceError, 'DUPLICATE_CHECKSUM'):
            verify(self.path, COMMIT)

    def test_qualification_status_cannot_disagree_with_bound_report(self):
        files = fixture(); prov = json.loads(files['provenance.json'])
        prov['verification_status'] = 'BLOCKED'; files['provenance.json'] = encoded(prov)
        rechecksums(files); self.write(files)
        with self.assertRaisesRegex(EvidenceError, 'QUALIFICATION_BINDING'):
            verify(self.path, COMMIT)

    def test_failed_job_can_have_valid_evidence_without_passing_qualification(self):
        self.write(fixture('BLOCKED', 'failure'))
        result = verify(self.path, COMMIT)
        self.assertEqual(result['status'], 'EVIDENCE_INTEGRITY_VERIFIED')
        self.assertFalse(result['reported_qualification_passed'])
        self.assertEqual(result['qualification_status'], 'BLOCKED')

    def test_prequalification_failure_can_preserve_missing_status(self):
        files = fixture('MISSING', 'failure'); del files['qualification/verification.json']
        p = json.loads(files['provenance.json']); del p['files']['qualification/verification.json']
        p['verification_sha256'] = None; files['provenance.json'] = encoded(p)
        rechecksums(files); self.write(files)
        result = verify(self.path, COMMIT)
        self.assertFalse(result['reported_qualification_passed'])
        self.assertEqual(result['qualification_status'], 'MISSING')

    def test_archive_limit(self):
        self.write(fixture())
        with patch('tools.verify_ci_evidence.MAX_ARCHIVE_BYTES', 2):
            with self.assertRaisesRegex(EvidenceError, 'ARCHIVE_FILE_OR_SIZE'):
                verify(self.path, COMMIT)

    def test_member_and_expansion_limits(self):
        self.write(fixture())
        for key, value in (('MAX_FILES', 2), ('MAX_EXPANDED_BYTES', 2), ('MAX_MEMBER_BYTES', 2)):
            with self.subTest(key=key), patch('tools.verify_ci_evidence.' + key, value):
                with self.assertRaises(EvidenceError):
                    verify(self.path, COMMIT)

    def test_cli_is_readonly_and_reproducible(self):
        self.write(fixture()); before = self.path.read_bytes()
        cmd = [sys.executable, '-B', str(ROOT / 'tools/verify_ci_evidence.py'), str(self.path), '--commit', COMMIT]
        first = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        second = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['artifact.zip'])


class EvidenceProducerChecks(unittest.TestCase):
    def test_real_workflow_producer_covers_nested_manifests_and_hidden_files(self):
        workflow = (ROOT / '.github/workflows/qualification.yml').read_text()
        section = workflow.split('name: Bind evidence to the commit', 1)[1].split('name: Upload verification evidence', 1)[0]
        script = textwrap.dedent(section.split("python3 - <<'PY'\n", 1)[1].split('\n          PY', 1)[0])
        checksum = next(line.strip() for line in section.splitlines() if line.strip().startswith('find . -type f'))
        with tempfile.TemporaryDirectory() as t:
            root = Path(t).resolve()
            files = fixture()
            for name, data in files.items():
                if name in ('provenance.json', 'SHA256SUMS.txt'): continue
                p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
            excluded = root / 'qualification/home/ignored.txt'
            excluded.parent.mkdir(parents=True); excluded.write_text('not part of the evidence export')
            env = dict(os.environ, COMMIT_SHA=COMMIT, REF='refs/test', RUN_ID='1', RUN_ATTEMPT='1', JOB_STATUS='success', PYTHONDONTWRITEBYTECODE='1')
            subprocess.run([sys.executable, '-B', '-c', script], cwd=root, env=env, check=True, capture_output=True, timeout=10)
            subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', checksum], cwd=root, env=env, check=True, capture_output=True, timeout=10)
            provenance = json.loads((root / 'provenance.json').read_text())
            self.assertIn('nested/SHA256SUMS.txt', provenance['files'])
            self.assertIn('nested/provenance.json', provenance['files'])
            self.assertIn('runner-demo/mission/.runner.lock', provenance['files'])
            self.assertNotIn('qualification/home/ignored.txt', provenance['files'])
            out = root.parent / (root.name + '.zip')
            try:
                with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
                    for p in root.rglob('*'):
                        if p.is_file() and p.relative_to(root).parts[:2] != ('qualification', 'home'):
                            z.write(p, p.relative_to(root))
                result = verify(out, COMMIT)
                self.assertTrue(result['reported_qualification_passed'])
            finally:
                out.unlink(missing_ok=True)
