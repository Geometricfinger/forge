"""Portable static checks; Linux supervised execution has its own CI suite."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from examples.handoff_bench import build, run_bench, verify_run


class HandoffBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='forge-handoff-build-')
        cls.root = Path(cls.temporary.name)
        cls.output = cls.root / 'static'
        with patch.object(run_bench.boundary_harness, 'run_profile',
                          side_effect=AssertionError('Static mode executed a product')):
            cls.result = run_bench.run(cls.output)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_default_freezes_without_execution(self):
        self.assertEqual('FROZEN_NOT_EXECUTED', self.result['status'])
        self.assertFalse(self.result['product_executed'])
        self.assertFalse((self.output / 'execution').exists())
        self.assertEqual(24, self.result['legacy_cases'])
        self.assertEqual(5, self.result['handoff_cases'])
        binding = (self.output / 'frozen/binding.json').read_bytes()
        self.assertEqual(self.result['freeze_sha256'], hashlib.sha256(binding).hexdigest())

    def test_original_planner_and_frozen_labels_remain_exact(self):
        self.assertEqual(build.CORE_SHA256, hashlib.sha256((build.HERE / 'planner_core.py').read_bytes()).hexdigest())
        self.assertEqual(build.SPEC_SHA256, hashlib.sha256((build.HERE / 'SPEC.md').read_bytes()).hexdigest())
        rows, _ = verify_run.legacy_expected(self.output)
        self.assertEqual(24, len(rows))
        self.assertEqual(verify_run.HANDOFF_FREEZE,
            hashlib.sha256((self.output / 'frozen/inputs/handoff/freeze.json').read_bytes()).hexdigest())

    def test_product_preserves_all_reviewed_vendor_and_core_bytes(self):
        pins = json.loads((build.HERE / 'preservation_pins.json').read_bytes())
        with ZipFile(self.output / 'build/product.pyz') as archive:
            for ref in pins['files']:
                if ref['path'] == 'SPEC.md':
                    continue
                name = ref['path'].removeprefix('vendor/')
                actual = archive.read(name)
                self.assertEqual(ref['bytes'], len(actual))
                self.assertEqual(ref['sha256'], hashlib.sha256(actual).hexdigest())
            self.assertEqual((build.HERE / 'entrypoint.py').read_bytes(), archive.read('__main__.py'))

    def test_existing_destination_refused_without_replacing_it(self):
        summary = (self.output / 'summary.json').read_bytes()
        with self.assertRaises(ValueError):
            run_bench.run(self.output)
        self.assertEqual(summary, (self.output / 'summary.json').read_bytes())

    def test_changed_optimizer_refused_before_archive_publication(self):
        source = self.root / 'changed-source'
        shutil.copytree(build.HERE, source, ignore=shutil.ignore_patterns('__pycache__'))
        path = source / 'planner_core.py'
        path.write_bytes(path.read_bytes() + b'\n# Unexpected change\n')
        destination = self.root / 'changed-build'
        with self.assertRaises(ValueError):
            build.build_product(destination, source)
        self.assertFalse(destination.exists())


if __name__ == '__main__':
    unittest.main()
