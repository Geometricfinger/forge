"""Whole archived modules, resources and notices reach the exact product together."""
import copy
from io import BytesIO
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import warnings
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from forge_core import creation_quest, quest_sources
from forge_core.common import Blocked, canonical, sha
from forge_core.engine import ROOT
from forge_core.workspace import Workbench


class ArchiveQuestTests(unittest.TestCase):
    @classmethod
    def cli(cls, *args):
        result = subprocess.run([sys.executable, '-B', str(ROOT/'forge.py'), '--home', str(cls.home),
                                 *map(str, args)], capture_output=True, timeout=90,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        if result.stderr:
            raise AssertionError(result.stderr.decode(errors='replace'))
        return result.returncode, json.loads(result.stdout)

    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name).resolve()
        cls.home = cls.root/'home'; cls.pool = cls.root/'pool'; cls.pool.mkdir()
        cls.module = ('# coding: latin-1\r\nimport json\r\nfrom importlib.resources import files\r\n'
                      'from pkgutil import get_data\r\nACCENT = "caf\xe9"\r\n'
                      'def read_label():\r\n    """Read a package label."""\r\n'
                      '    return json.loads(files(__package__).joinpath("data/labels.json").read_text())\r\n'
                      'def read_legacy():\r\n    return json.loads(get_data(__package__, "data/labels.json"))\r\n').encode('latin-1')
        cls.files = {'release-1.0/toolkit/__init__.py': b'PACKAGE = "fixture"\n',
                     'release-1.0/toolkit/readers.py': cls.module,
                     'release-1.0/toolkit/data/labels.json': b'{"label":"steady"}\n',
                     'release-1.0/LICENSE': b'Synthetic fixture notice.\n',
                     'release-1.0/other/__init__.py': b'OTHER = True\n',
                     'release-1.0/unselected.py': b'raise RuntimeError("must never run")\ndef unused(): pass\n',
                     'release-1.0/latin_key.py': b'KEY = 1\n',
                     'release-1.0/unicode_key.py': b'KEY = 2\n',
                     'release-1.0/stub.pyi': b'def declared(): ...\n',
                     'release-1.0/guide.md': b'```python\ndef fenced(): pass\n```\n'}
        inner = BytesIO()
        with ZipFile(inner, 'w') as archive:
            archive.writestr('nested/__init__.py', b'VALUE = 3\n')
            archive.writestr('nested/measure.py', b'from . import VALUE\ndef amount(): return VALUE\n')
            archive.writestr('NOTICE', b'Nested fixture notice.\n')
        cls.files['release-1.0/inner.zip'] = inner.getvalue()
        with ZipFile(cls.pool/'release.zip', 'w') as archive:
            for path, data in cls.files.items():
                archive.writestr(path, data)
        code, result = cls.cli('corpus-run', '--source-root', cls.pool, '--run-id', 'archive')
        if code:
            raise AssertionError(result)
        cls.report = json.loads((cls.home/'corpora/archive/corpus.json').read_bytes())
        cls.source = cls.report['sources'][0]['source']
        cls.segments = {segment['path']: segment for segment in cls.report['sources'][0]['segments']}
        cls.quest = cls.root/'quest/quest.json'
        code, result = cls.cli('quest-start', '--run-id', 'archive', '--scout', '--out', cls.quest.parent)
        if code:
            raise AssertionError(result)
        cls.leads = json.loads(cls.quest.read_bytes())['leads']
        cls.component = next(row for row in cls.leads if row['name'] == 'read_label')

    def setUp(self):
        self.work = self.root/self._testMethodName; self.work.mkdir()
        self.entry = self.work/'main.py'
        self.entry.write_text('from toolkit.readers import read_label, read_legacy, ACCENT\n'
                              'import json\nprint(json.dumps({"modern":read_label(),'
                              '"legacy":read_legacy(),"accent":ACCENT}))\n')
        self.out = self.work/'product'
        self.recipe_file = self.work/'recipe.json'
        self.recipe = {'schema': 1, 'audience': 'Fixture reviewer', 'purpose': 'Read a retained package label.',
                       'acceptance': ['Both resource APIs read the original label.'],
                       'components': [self.component['definition_id']],
                       'module_files': [self.mapping('release-1.0/toolkit/readers.py', 'toolkit/readers.py'),
                                        self.mapping('release-1.0/toolkit/__init__.py', 'toolkit/__init__.py')],
                       'assets': [self.asset('release-1.0/toolkit/data/labels.json', 'toolkit/data/labels.json'),
                                  self.asset('release-1.0/LICENSE', 'licenses/toolkit/LICENSE', 'notice')],
                       'evaluation_plan': {'schema': 1, 'cases': [
                           {'id': 'label', 'acceptance': 0, 'needs': [],
                            'expected': {'modern': {'label': 'steady'}, 'legacy': {'label': 'steady'}, 'accent': 'caf\xe9'}}]}}

    def mapping(self, original, destination):
        return {'source_id': self.segments[original]['source_id'], 'path': destination}

    def asset(self, original, destination, role='resource'):
        data = self.files[original]
        return {'file_id': self.source['file_id'], 'member_chain': [{'path': original, 'sha256': sha(data)}],
                'path': destination, 'bytes': len(data), 'sha256': sha(data), 'role': role}

    def build(self, *, source=None, out=None):
        self.recipe_file.write_bytes(canonical(self.recipe))
        return self.cli('quest-build', '--quest', self.quest, '--recipe', self.recipe_file,
                        '--entry', self.entry, '--source-root', source or self.pool, '--out', out or self.out)

    def blocked(self, expected):
        code, result = self.build()
        self.assertEqual((code, result.get('reason')), (2, expected), result)
        self.assertFalse(self.out.exists())

    def test_archive_product_preserves_bytes_resources_notices_and_assesses(self):
        before = (self.pool/'release.zip').read_bytes()
        self.assertEqual(self.component['source_id'], self.segments['release-1.0/toolkit/readers.py']['source_id'])
        self.assertTrue(self.component['member_chain'])
        self.assertEqual(self.build()[0], 0)
        receipt_raw = (self.out/'receipt.json').read_bytes(); receipt = json.loads(receipt_raw)
        with ZipFile(self.out/'product.pyz') as archive:
            self.assertEqual(archive.read('toolkit/readers.py'), self.module)
            self.assertEqual(archive.read('toolkit/data/labels.json'), self.files['release-1.0/toolkit/data/labels.json'])
            self.assertEqual(archive.read('licenses/toolkit/LICENSE'), self.files['release-1.0/LICENSE'])
            self.assertNotIn('unselected.py', archive.namelist())
        source = next(row for row in receipt['source_files'] if row['path'] == 'toolkit/readers.py')
        self.assertNotEqual(source['sha256'], source['origin']['analysis_source_sha256'])
        self.assertEqual(source['origin']['container_sha256'], sha(before))
        self.assertEqual(receipt['runtime_validation'], 'NOT_RUN')
        self.assertIs(receipt['source_code_executed'], False)
        # The test harness explicitly runs only this owned, packaged synthetic program.
        run = subprocess.run([sys.executable, '-I', str(self.out/'product.pyz')], cwd=self.work,
                             capture_output=True, check=True, timeout=10)
        self.assertEqual(json.loads(run.stdout), self.recipe['evaluation_plan']['cases'][0]['expected'])
        observations = self.work/'observations'; observations.mkdir()
        (observations/'label.json').write_bytes(run.stdout)
        results = {'schema': 1, 'receipt_sha256': sha(receipt_raw), 'product_sha256': receipt['product_sha256'],
                   'evaluation_plan_sha256': sha(canonical(self.recipe['evaluation_plan'])), 'checker_sha256': None,
                   'cases': [{'id': 'label', 'status': 'OBSERVED', 'artifact':
                              {'path': 'label.json', 'bytes': len(run.stdout), 'sha256': sha(run.stdout)}}]}
        results_file = self.work/'results.json'; results_file.write_bytes(canonical(results))
        code, result = self.cli('quest-assess', '--receipt', self.out/'receipt.json', '--recipe', self.recipe_file,
                                '--product', self.out/'product.pyz', '--results', results_file,
                                '--artifact-root', observations, '--out', self.work/'assessment')
        self.assertEqual((code, result.get('outcome_status')), (0, 'REPORTED_PASS'), result)
        self.assertEqual((self.pool/'release.zip').read_bytes(), before)
        self.assertEqual(sorted(path.name for path in self.pool.iterdir()), ['release.zip'])

    def test_archived_component_requires_explicit_destination(self):
        self.recipe['module_files'] = self.recipe['module_files'][1:]
        self.blocked('QUEST_MODULE_MAPPING_REQUIRED')

    def test_nfkc_collision_reaches_preflight_after_path_validation(self):
        normal, distinct = 'K.py', '\U0001d542.py'
        self.assertNotEqual(normal.casefold(), distinct.casefold())
        self.recipe['module_files'] += [self.mapping('release-1.0/latin_key.py', normal),
                                        self.mapping('release-1.0/unicode_key.py', distinct)]
        code, result = self.build()
        self.assertEqual(code, 0, result)
        self.assertEqual(result['preflight_status'], 'REVIEW_REQUIRED')
        report = json.loads((self.out/'preflight.json').read_bytes())
        collision = next(row for row in report['issues'] if row['code'] == 'NORMALIZED_MODULE_COLLISION')
        self.assertEqual(collision['module'], 'K')
        self.assertEqual(collision['paths'], [normal, distinct])
        with ZipFile(self.out/'product.pyz') as archive:
            self.assertEqual(archive.read(normal), self.files['release-1.0/latin_key.py'])
            self.assertEqual(archive.read(distinct), self.files['release-1.0/unicode_key.py'])
        receipt = json.loads((self.out/'receipt.json').read_bytes())
        self.assertEqual(receipt['preflight']['report_sha256'], sha((self.out/'preflight.json').read_bytes()))
        self.assertEqual(receipt['runtime_validation'], 'NOT_RUN')

    def test_initializer_must_match_original_source_and_destination(self):
        for mappings in [self.recipe['module_files'][:1],
                         [self.recipe['module_files'][0], self.mapping('release-1.0/other/__init__.py', 'toolkit/__init__.py')],
                         [self.recipe['module_files'][0], self.mapping('release-1.0/toolkit/__init__.py', 'elsewhere/__init__.py')],
                         [self.recipe['module_files'][0] | {'path': 'readers.py'}, self.recipe['module_files'][1]]]:
            with self.subTest(mappings=mappings):
                self.recipe['module_files'] = mappings
                self.blocked('QUEST_SELECT_PACKAGE_INITIALIZER')

    def test_nested_archive_mapping_retains_full_chain(self):
        lead = next(row for row in self.leads if row['name'] == 'amount')
        self.recipe['components'] = [lead['definition_id']]
        self.recipe['module_files'] = [self.mapping('nested/measure.py', 'nested/measure.py'),
                                       self.mapping('nested/__init__.py', 'nested/__init__.py')]
        data = b'Nested fixture notice.\n'
        self.recipe['assets'] = [{'file_id': self.source['file_id'], 'member_chain': [
            {'path': 'release-1.0/inner.zip', 'sha256': sha(self.files['release-1.0/inner.zip'])},
            {'path': 'NOTICE', 'sha256': sha(data)}], 'path': 'NOTICE', 'bytes': len(data), 'sha256': sha(data), 'role': 'notice'}]
        self.entry.write_text('from nested.measure import amount\nprint(amount())\n')
        self.assertEqual(self.build()[0], 0)
        receipt = json.loads((self.out/'receipt.json').read_bytes())
        self.assertTrue(all(len(row['origin']['member_chain']) == 2 for row in receipt['source_files']))
        with ZipFile(self.out/'product.pyz') as archive:
            self.assertEqual(archive.read('NOTICE'), data)

    def test_changed_container_blocks_before_publication(self):
        mirror = self.work/'mirror'; mirror.mkdir()
        (mirror/'release.zip').write_bytes((self.pool/'release.zip').read_bytes() + b'changed')
        code, result = self.build(source=mirror)
        self.assertEqual((code, result['reason']), (2, 'SOURCE_VERSION_MISMATCH'))
        self.assertFalse(self.out.exists())

    def test_changed_missing_and_wrong_size_assets_block(self):
        original = copy.deepcopy(self.recipe['assets'][0])
        variants = [({'sha256': '0'*64}, 'QUEST_SOURCE_BINDING'),
                    ({'bytes': original['bytes']+1}, 'QUEST_SOURCE_BINDING'),
                    ({'member_chain': [{'path': 'absent.json', 'sha256': original['sha256']}]}, 'QUEST_SOURCE_MEMBER'),
                    ({'member_chain': [{'path': original['member_chain'][0]['path'], 'sha256': '0'*64}]}, 'QUEST_SOURCE_BINDING')]
        for change, reason in variants:
            with self.subTest(change=change):
                self.recipe['assets'][0] = original | change
                self.blocked(reason)

    def test_product_path_collisions_block(self):
        original = copy.deepcopy(self.recipe)
        for assets, mappings, reason in [
            ([original['assets'][0] | {'path': 'toolkit'}], original['module_files'], 'QUEST_PRODUCT_PATH_COLLISION'),
            ([original['assets'][0] | {'path': 'DATA'}, original['assets'][1] | {'path': 'data'}], original['module_files'], 'QUEST_PRODUCT_PATH_COLLISION'),
            (original['assets'], [original['module_files'][0] | {'path': 'toolkit.py'}, original['module_files'][1]], 'QUEST_IMPORT_PATH_COLLISION')]:
            with self.subTest(reason=reason, mappings=mappings):
                self.recipe['assets'] = assets; self.recipe['module_files'] = mappings
                self.blocked(reason)

    def test_assets_cannot_hide_importable_source(self):
        source = self.asset('release-1.0/toolkit/readers.py', 'readers.txt')
        self.recipe['assets'] = [source]
        self.blocked('QUEST_ASSET_EXECUTABLE')
        for suffix in ['.py', '.PY', '.pyc', '.so', '.pyd', '.pth', '.whl']:
            with self.subTest(suffix=suffix):
                self.recipe['assets'] = [source | {'path': 'payload' + suffix}]
                with self.assertRaisesRegex(Blocked, '^QUEST_ASSET_EXECUTABLE$'):
                    creation_quest._recipe(canonical(self.recipe))

    def test_source_declarations_reject_unsafe_paths_and_limits(self):
        original = copy.deepcopy(self.recipe)
        for path in ['../outside', '/absolute', 'a//b', 'a/../b', 'a\\b', '.env', '.git/data', 'keys/private.pem']:
            with self.subTest(path=path):
                self.recipe = copy.deepcopy(original); self.recipe['assets'][0]['path'] = path
                with self.assertRaises(Blocked):
                    creation_quest._recipe(canonical(self.recipe))
        for field, value in [('module_files', original['module_files']*17), ('assets', original['assets']*33)]:
            with self.subTest(field=field):
                with self.assertRaises(Blocked):
                    creation_quest._recipe(canonical(original | {field: value}))
        self.recipe = copy.deepcopy(original)
        self.recipe['assets'][0]['bytes'] = True
        with self.assertRaises(Blocked):
            creation_quest._recipe(canonical(self.recipe))
        self.recipe['assets'][0]['bytes'] = 2_000_001
        with self.assertRaises(Blocked):
            creation_quest._recipe(canonical(self.recipe))
        with self.assertRaises(Blocked):
            quest_sources.chain([{'path': 'x.zip', 'sha256': '0'*64}]*4)

    def test_whole_indexed_python_modules_only(self):
        for original in ['release-1.0/stub.pyi', 'release-1.0/guide.md']:
            with self.subTest(original=original):
                self.recipe['module_files'].append(self.mapping(original, 'extra.py'))
                self.blocked('QUEST_WHOLE_MODULE_REQUIRED')
                self.recipe['module_files'].pop()

    def test_combined_budget_includes_resources_and_entry(self):
        self.recipe_file.write_bytes(canonical(self.recipe))
        files_without_entry = len(self.module) + len(self.files['release-1.0/toolkit/__init__.py'])
        files_without_entry += sum(row['bytes'] for row in self.recipe['assets'])
        with mock.patch.object(creation_quest, 'APP_BYTES', files_without_entry):
            with self.assertRaisesRegex(Blocked, '^QUEST_PRODUCT_SIZE$'):
                creation_quest.build(Workbench(self.home), self.quest, self.recipe_file, self.entry, self.pool, self.out)
        self.assertFalse(self.out.exists())
        with mock.patch.object(creation_quest, 'APP_BYTES', len(self.module)):
            with self.assertRaisesRegex(Blocked, '^QUEST_PRODUCT_SIZE$'):
                creation_quest.build(Workbench(self.home), self.quest, self.recipe_file, self.entry, self.pool, self.out)
        self.assertFalse(self.out.exists())

    def test_same_mapping_produces_same_archive_independent_of_recipe_order(self):
        self.assertEqual(self.build()[0], 0)
        self.recipe['module_files'].reverse(); self.recipe['assets'].reverse()
        second = self.work/'second'
        self.assertEqual(self.build(out=second)[0], 0)
        self.assertEqual((self.out/'product.pyz').read_bytes(), (second/'product.pyz').read_bytes())

    def test_assessment_rejects_inconsistent_asset_provenance(self):
        from forge_core.quest_assessment import _receipt
        self.assertEqual(self.build()[0], 0)
        original = json.loads((self.out/'receipt.json').read_bytes())
        for change in [{'sha256': '0'*64}, {'origin': {'bad': 'source'}}]:
            receipt = copy.deepcopy(original)
            row = next(row for row in receipt['source_files'] if row['role'] == 'resource')
            row.update(change)
            with self.assertRaisesRegex(Blocked, '^QUEST_ASSESS_SOURCE_FILES$'):
                _receipt(canonical(receipt), self.recipe_file.read_bytes(), self.recipe)
        receipt = copy.deepcopy(original)
        row = next(row for row in receipt['source_files'] if row['role'] == 'module')
        row['origin']['member_chain'][-1]['sha256'] = '0'*64
        with self.assertRaisesRegex(Blocked, '^QUEST_ASSESS_SOURCE_FILES$'):
            _receipt(canonical(receipt), self.recipe_file.read_bytes(), self.recipe)
        for invalid in ([], {}, None, 1):
            receipt = copy.deepcopy(original)
            receipt['definitions'][0]['source_id'] = invalid
            with self.assertRaisesRegex(Blocked, '^QUEST_ASSESS_SOURCE_FILES$'):
                _receipt(canonical(receipt), self.recipe_file.read_bytes(), self.recipe)


class ArchiveSourceControls(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def reader(self, data):
        (self.root/'source.zip').write_bytes(data)
        source = {'provider': 'synthetic', 'file_id': 'fixture', 'path': 'source.zip',
                  'size': len(data), 'sha256': sha(data), 'source_url': 'fixture://fixture'}
        return quest_sources.Sources({'sources': [{'source': source, 'segments': []}]}, self.root)

    def test_selected_unsafe_archive_entries_are_refused(self):
        data = b'payload'; variants = []
        for mode in ['duplicate', 'symlink', 'encrypted', 'unsupported', 'ratio', 'nul']:
            buffer = BytesIO()
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                with ZipFile(buffer, 'w') as archive:
                    item = ZipInfo('data.txt_suffix' if mode == 'nul' else 'data.txt')
                    if mode == 'symlink': item.external_attr = (stat.S_IFLNK | 0o777) << 16
                    if mode == 'ratio': item.compress_type = ZIP_DEFLATED
                    archive.writestr(item, b'x'*500_000 if mode == 'ratio' else data)
                    if mode == 'duplicate': archive.writestr('data.txt', data)
            payload = bytearray(buffer.getvalue())
            if mode == 'nul':
                payload = payload.replace(b'data.txt_suffix', b'data.txt\x00suffix')
            if mode in ('encrypted', 'unsupported'):
                central = payload.index(b'PK\x01\x02')
                struct.pack_into('<H', payload, 6 if mode == 'encrypted' else 8, 1 if mode == 'encrypted' else 99)
                struct.pack_into('<H', payload, central + (8 if mode == 'encrypted' else 10), 1 if mode == 'encrypted' else 99)
            variants.append((mode, bytes(payload)))
        for mode, payload in variants:
            with self.subTest(mode=mode):
                with self.assertRaises(Blocked):
                    self.reader(payload).bytes('fixture', [{'path': 'data.txt', 'sha256': sha(data)}])

    def test_source_mutation_and_symlinks_are_refused(self):
        buffer = BytesIO()
        with ZipFile(buffer, 'w') as archive: archive.writestr('data.txt', b'payload')
        source = self.reader(buffer.getvalue())
        self.assertEqual(source.bytes('fixture', [{'path': 'data.txt', 'sha256': sha(b'payload')}]), b'payload')
        (self.root/'source.zip').write_bytes(buffer.getvalue() + b'changed')
        with self.assertRaisesRegex(Blocked, '^SOURCE_VERSION_MISMATCH$'): source.verify_unchanged()
        target = self.root/'target.zip'; target.write_bytes(buffer.getvalue())
        (self.root/'source.zip').unlink(); (self.root/'source.zip').symlink_to(target)
        with self.assertRaisesRegex(Blocked, '^SYMLINK_PATH$'): source.bytes('fixture', [])
