"""Black-box coverage of recovery trial input selection and recorded origin."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools/run_self_recovery_trial.py"
DEFAULT_ORIGIN = "This engine's own forge_core sources, packed into a local zip"


class TrialInputs(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        self.out = self.work / "result"

    def run_trial(self, *arguments):
        return subprocess.run(
            [sys.executable, str(RUNNER), "--out", str(self.out),
             "--require-recovery", *map(str, arguments)],
            text=True, capture_output=True, timeout=150,
        )

    def assert_pair_required(self, flag):
        result = self.run_trial(flag, self.work / "deliberately-absent")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("--source-root", result.stderr)
        self.assertIn("--manifest", result.stderr)
        self.assertFalse(self.out.exists())

    def test_manifest_without_root_is_rejected(self):
        self.assert_pair_required("--manifest")

    def test_root_without_manifest_is_rejected(self):
        self.assert_pair_required("--source-root")

    def test_supplied_pair_is_bound_to_its_manifest(self):
        source = self.work / "source"
        source.mkdir()
        archive = source / "subset.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for name in ("forge_core/corpus.py", "forge_core/common.py"):
                bundle.write(ROOT / name, name)
        manifest = self.work / "supplied.json"
        content = archive.read_bytes()
        manifest.write_text(json.dumps({"schema": 1, "files": [{
            "file_id": "two_files", "path": archive.name,
            "provider": "synthetic", "source_url": "fixture://two_files",
            "size": len(content), "sha256": hashlib.sha256(content).hexdigest(),
        }]}, indent=2) + "\n", encoding="utf-8")
        result = self.run_trial("--source-root", source, "--manifest", manifest)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads((self.out / "SUMMARY.json").read_bytes())
        self.assertEqual(summary["source_files"], 2)
        self.assertEqual(summary["first_faulted"]["summary"]["hound_segments"], 1)
        self.assertEqual(summary["second"]["summary"]["hound_segments"], 2)
        self.assertEqual(summary["second"]["new_attempts"], 1)
        self.assertEqual(summary["third"]["new_attempts"], 0)
        self.assertTrue(summary["cart_identical_to_steady"])
        self.assertEqual(summary['successful_segment_cache_count'], 1)
        self.assertEqual(summary['expected_successful_segment_cache_count'], 1)
        self.assertTrue(summary['successful_segment_cache_bytes_unchanged'])
        caches = list((self.out / 'home/corpora/recovery/segments').glob('*.json.gz'))
        self.assertEqual(len(caches), 2)
        self.assertIn("supplied", summary["source_origin"].lower())
        self.assertEqual(summary.get("source_manifest_sha256"),
                         hashlib.sha256(manifest.read_bytes()).hexdigest())

    def test_default_keeps_its_origin_and_binds_generated_manifest(self):
        result = self.run_trial()
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads((self.out / "SUMMARY.json").read_bytes())
        self.assertEqual(summary["source_origin"], DEFAULT_ORIGIN)
        self.assertEqual(summary["source_files"], len(list((ROOT / "forge_core").glob("*.py"))))
        self.assertEqual(summary.get("source_manifest_sha256"),
                         hashlib.sha256((self.out / "self-source-manifest.json").read_bytes()).hexdigest())

    def test_empty_success_cache_inventory_cannot_establish_preservation(self):
        source = self.work / 'source'; source.mkdir()
        data = (b'def inspect_one(data,source,home,profile,analyzer,segment_dir=None,job_binding=None):\n'
                b'    return None\n')
        (source / 'only.py').write_bytes(data)
        manifest = self.work / 'one.json'
        manifest.write_text(json.dumps({'schema': 1, 'files': [{
            'file_id': 'only', 'path': 'only.py', 'provider': 'synthetic',
            'source_url': 'fixture://only', 'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
        }]}), encoding='utf-8')
        result = self.run_trial('--source-root', source, '--manifest', manifest)
        self.assertEqual(result.returncode, 2, result.stderr)
        summary = json.loads((self.out / 'SUMMARY.json').read_bytes())
        self.assertEqual(summary['injected_timeouts'], 1)
        self.assertEqual(summary['second']['new_attempts'], 1)
        self.assertEqual(summary['successful_segment_cache_count'], 0)
        self.assertEqual(summary['expected_successful_segment_cache_count'], 0)
        self.assertFalse(summary['successful_segment_cache_bytes_unchanged'])
        self.assertEqual(summary['status'], 'RECOVERY_NOT_DEMONSTRATED')


if __name__ == "__main__":
    unittest.main()
