"""Require observed recovery before attributing recovery to a self-scan."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]


class MissingFaultTarget(unittest.TestCase):
    def check_no_recovery(self, strict):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "source"
            source.mkdir()
            archive = source / "one-file.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.write(ROOT / "forge_core/common.py", "forge_core/common.py")
            data = archive.read_bytes()
            manifest = work / "manifest.json"
            manifest.write_text(json.dumps({"schema": 1, "files": [{
                "provider": "synthetic", "file_id": "control",
                "source_url": "fixture://control", "path": archive.name,
                "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
            }]}), encoding="utf-8")
            out = work / "result"
            args = [sys.executable, str(ROOT / "tools/run_self_recovery_trial.py"),
                    "--source-root", str(source), "--manifest", str(manifest),
                    "--out", str(out)]
            if strict:
                args.append("--require-recovery")
            result = subprocess.run(args, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 2 if strict else 0, result.stderr)
            summary = json.loads((out / "SUMMARY.json").read_bytes())
            self.assertEqual(summary["injected_timeouts"], 0)
            self.assertEqual(summary["second"]["new_attempts"], 0)
            self.assertEqual(summary["first_faulted"]["summary"]["hound_segments"], 1)
            self.assertEqual(summary["status"], "RECOVERY_NOT_DEMONSTRATED")
            self.assertEqual(json.loads(result.stdout)["status"], summary["status"])

    def test_absent_fault_target_in_strict_mode(self):
        self.check_no_recovery(strict=True)

    def test_absent_fault_target_in_optional_mode(self):
        self.check_no_recovery(strict=False)


if __name__ == "__main__":
    unittest.main()
