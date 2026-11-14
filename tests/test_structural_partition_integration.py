"""Independent end-to-end checks for complete-unit structural recovery.

The fixture bodies are exact expected bytes. They are never imported/executed.
The fixed parser worker, corpus publication, search and source reacquisition run
through their real boundaries; the API analyzer must not receive oversized code.
"""
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from forge_core import corpus
from forge_core.common import Blocked, canonical, sha


class Workbench:
    def __init__(self, path):
        self.home = path
        path.mkdir()

    def guard(self):
        pass


class NoLargeApiAnalysis:
    def scan(self, *args, **kwargs):
        raise AssertionError('Structural partitions must not bypass the API source limit')


def module_bytes(*, oversized=False, crlf=False):
    count = 70_000 if oversized else 35_000
    first = ('def first(value):\n    constants = [' + ','.join(['0'] * count) +
             ']\n    return second(value)\n')
    second = ('def second(value):\n' + ('' if oversized else
              '    constants = [' + ','.join(['1'] * 35_000) + ']\n') +
              '    return digest_lib.sha256(value).digest()\n')
    text = 'import hashlib as digest_lib\nSCALE = 3\n\n' + first + '\n' + second
    if len(text.encode()) <= 250_000:
        text += '\n#' + 'padding ' * ((250_001 - len(text.encode())) // 8 + 2) + '\n'
    if crlf:
        text = '# café\n' + text
        text = text.replace('\n', '\r\n')
        first = first.replace('\n', '\r\n')
        second = second.replace('\n', '\r\n')
    return text.encode(), {'first': first.encode(), 'second': second.encode()}


class StructuralPartitionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / 'source'
        self.source.mkdir()
        self.work = Workbench(self.base / 'work')

    def tearDown(self):
        self.temp.cleanup()

    def run_source(self, payload, name='module.py', run='trial'):
        target = self.source / name
        target.write_bytes(payload)
        row = {'provider': 'local', 'file_id': name, 'path': name,
               'size': len(payload), 'sha256': sha(payload), 'source_url': 'local://' + name}
        manifest = self.base / (run + '-manifest.json')
        manifest.write_bytes(canonical({'schema': 1, 'files': [row]}))
        out = self.work.home / run
        report = corpus.run(self.work, self.source, manifest, out,
                            analyzer=NoLargeApiAnalysis())
        return report, out

    def test_fixed_worker_recovers_complete_units_search_and_inspection(self):
        data, bodies = module_bytes()
        report, out = self.run_source(data)
        self.assertEqual(report['summary']['definitions'], 2, report['gaps'])
        self.assertEqual(report['summary']['segments'], 1)
        self.assertEqual(report['summary']['source_roundtrips_verified'], 1)
        self.assertEqual(report['coverage']['definitions']['api_analyzed'], 0)
        self.assertTrue(report['coverage']['definitions']['discovery_complete'])
        self.assertEqual(report['status'], 'COMPLETED_WITH_GAPS')
        self.assertEqual(corpus.read_report(out), report)
        self.assertFalse(report['source_bodies_retained'])
        rows = list(corpus.rows(report))
        self.assertEqual({r['name'] for r in rows}, set(bodies))
        self.assertEqual(len({r['source_id'] for r in rows}), 1)
        for row in rows:
            inspected = corpus.inspect_definition(report, row['definition_id'], self.source)
            self.assertEqual(inspected['source_text'].encode(), bodies[row['name']])
            self.assertEqual(inspected['source_window']['sha256'], sha(bodies[row['name']]))
            self.assertTrue(inspected['source_window']['complete'])
            self.assertFalse(inspected['source_code_executed'])
        first = next(r for r in rows if r['name'] == 'first')
        context = corpus.dependency_context(report, first['definition_id'])
        self.assertTrue(any(r.get('call') == 'second' or r.get('name') == 'second'
                            for r in context['references']), context)
        second = next(r for r in rows if r['name'] == 'second')
        self.assertIn('hashlib.sha256', second['declared_api_leads'])
        found = corpus.search(report, 'second', limit=5)
        self.assertIn(second['definition_id'], {r.get('definition_id') for r in found['results']})
        self.assertEqual((self.source / 'module.py').read_bytes(), data)

    def test_atomic_omission_keeps_neighbors_and_withholds_module_inference(self):
        data, bodies = module_bytes(oversized=True)
        report, _ = self.run_source(data)
        self.assertEqual(report['summary']['definitions'], 1, report['gaps'])
        self.assertFalse(report['coverage']['definitions']['discovery_complete'])
        self.assertFalse(report['coverage']['definitions']['omitted_definition_count_known'])
        self.assertEqual(report['summary']['source_roundtrips_verified'], 1)
        row = next(corpus.rows(report))
        self.assertEqual(row['name'], 'second')
        self.assertEqual(row['declared_api_leads'], [])
        self.assertTrue(any(g.get('status') not in ('SOURCE_SIZE_LIMIT', 'DEFINITION_SIZE_LIMIT')
                            and 'source_id' not in g for g in report['gaps']))
        result = corpus.inspect_definition(report, row['definition_id'], self.source)
        self.assertEqual(result['source_text'].encode(), bodies['second'])
        with self.assertRaises(Blocked):
            corpus.inspect_definition(report, row['definition_id'], self.source,
                                      max_bytes=len(bodies['second']) - 1)
        self.assertEqual((self.source / 'module.py').read_bytes(), data)

    def test_archive_crlf_offsets_and_changed_original_rejection(self):
        data, bodies = module_bytes(crlf=True)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_STORED) as archive:
            archive.writestr('pkg/module.py', b'\xef\xbb\xbf' + data)
        payload = stream.getvalue()
        report, _ = self.run_source(payload, 'source.zip')
        self.assertEqual(report['summary']['definitions'], 2, report['gaps'])
        for row in corpus.rows(report):
            result = corpus.inspect_definition(report, row['definition_id'], self.source)
            self.assertEqual(result['source_text'].encode(), bodies[row['name']])
            self.assertEqual(result['member_chain'], [{'path': 'pkg/module.py',
                              'sha256': sha(b'\xef\xbb\xbf' + data)}])
        row = next(corpus.rows(report))
        (self.source / 'source.zip').write_bytes(payload + b'changed')
        with self.assertRaisesRegex(Blocked, 'SOURCE_VERSION_MISMATCH'):
            corpus.inspect_definition(report, row['definition_id'], self.source)


if __name__ == '__main__':
    unittest.main()
