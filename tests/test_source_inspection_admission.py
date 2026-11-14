"""Inspection preserves read-only admission and physical source locations."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forge_core import corpus
from forge_core.common import sha
from test_corpus import FakeAnalyzer


class SourceInspectionAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='forge-inspection-test-')
        self.root = Path(self.temp.name).resolve()
        self.source_root = self.root / 'source'
        self.worker_home = self.root / 'worker'
        self.source_root.mkdir()
        self.worker_home.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def report(self, data, name):
        (self.source_root / name).write_bytes(data)
        source = {'provider': 'synthetic', 'file_id': 'fixture', 'path': name,
                  'size': len(data), 'sha256': sha(data), 'source_url': 'fixture://fixture'}
        inspected = corpus.inspect_one(data, source, self.worker_home, {}, FakeAnalyzer())
        return {'schema': 1, 'sources': [inspected], 'source_bodies_retained': False,
                'upstream_code_executed': False}

    def test_ambient_temp_directory_inside_target_creates_no_target_entries(self):
        data = b'def use(): return 1\n'
        report = self.report(data, 'module.py')
        definition = next(corpus.rows(report))
        before = sorted(p.relative_to(self.source_root).as_posix()
                        for p in self.source_root.rglob('*'))
        parser = corpus._parser
        observed_workdirs = []

        def observe_parser(raw, name, home):
            observed_workdirs.append(home)
            self.assertTrue(home.is_dir())
            self.assertFalse(home.is_relative_to(self.source_root))
            # Check while parsing too: a temporary write followed by cleanup is
            # still a violation of the read-only target contract.
            self.assertEqual(sorted(p.relative_to(self.source_root).as_posix()
                                    for p in self.source_root.rglob('*')), before)
            return parser(raw, name, home)

        with patch.object(tempfile, 'tempdir', str(self.source_root)), \
                patch.object(corpus, '_parser', observe_parser):
            result = corpus.inspect_definition(report, definition['definition_id'], self.source_root)

        self.assertEqual(len(observed_workdirs), 1)
        self.assertEqual(result['source_text'].encode('utf-8'), data)
        self.assertEqual(sorted(p.relative_to(self.source_root).as_posix()
                                for p in self.source_root.rglob('*')), before)
        self.assertEqual((self.source_root / 'module.py').read_bytes(), data)

    def test_unicode_separator_before_fence_keeps_physical_definition_line(self):
        data = 'heading\u2028continued\n```python\ndef use(): return 1\n```\n'.encode('utf-8')
        report = self.report(data, 'notes.md')
        definition = next(corpus.rows(report))
        result = corpus.inspect_definition(report, definition['definition_id'], self.source_root)
        self.assertEqual((definition['start_line'], definition['end_line']), (3, 3))
        self.assertEqual((result['start_line'], result['end_line']), (3, 3))
        self.assertEqual(result['source_text'], 'def use(): return 1\n')
        self.assertEqual(result['context']['additional_context_member_start_line'], 3)
        self.assertEqual((self.source_root / 'notes.md').read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
