"""Reviewed selections become ordinary recipes without executing source."""
import copy
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from zipfile import ZipFile

from forge_core import creation_quest, quest_preparation
from forge_core.common import Blocked, canonical, sha
from forge_core.engine import ROOT
from forge_core.workspace import Workbench


class QuestPreparationTests(unittest.TestCase):
    @classmethod
    def cli(cls, *args):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'forge.py'), '--home', str(cls.home),
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
        cls.home = cls.root / 'home'
        cls.pool = cls.root / 'pool'
        (cls.pool / 'plainpkg').mkdir(parents=True)
        cls.marker = cls.root / 'must-not-exist'
        cls.initializer = ('from pathlib import Path\nPath(' + repr(str(cls.marker))
                           + ').write_text("source was executed")\n').encode()
        (cls.pool / 'plainpkg/__init__.py').write_bytes(cls.initializer)
        (cls.pool / 'plainpkg/reader.py').write_bytes(
            b'from .tools import clean\ndef normalize(value): return clean(value)\n')
        (cls.pool / 'plainpkg/tools.py').write_bytes(b'def clean(value): return value.strip()\n')
        cls.files = {
            'release-1.0/samplepkg/__init__.py': cls.initializer,
            'release-1.0/samplepkg/sub/__init__.py': b'',
            'release-1.0/samplepkg/sub/reader.py': (
                b'# coding: latin-1\r\nfrom ..tools import clean\r\n'
                b'from importlib.resources import files\r\nLABEL = "caf\xe9"\r\n'
                b'def read_label():\r\n'
                b'    return clean(files("samplepkg").joinpath("labels.json").read_text())\r\n'),
            'release-1.0/samplepkg/tools.py': b'def clean(value): return value.strip()\n',
            'release-1.0/samplepkg/other_tools.py': b'def clean(value): return value.upper()\n',
            'release-1.0/samplepkg/ambiguous.py': (
                b'if choose_first:\n    from .tools import clean\n'
                b'else:\n    from .other_tools import clean\n'
                b'def clean_ambiguous(value): return clean(value)\n'),
            'release-1.0/samplepkg/labels.json': b'{"label":"steady"}\n',
            'release-1.0/other/__init__.py': b'OTHER = True\n',
            'release-1.0/LICENSE': b'Synthetic fixture notice.\n',
        }
        nested = BytesIO()
        with ZipFile(nested, 'w') as archive:
            archive.writestr('nestedpkg/__init__.py', b'VALUE = 3\n')
            archive.writestr('nestedpkg/reader.py', b'from . import VALUE\ndef amount(): return VALUE\n')
        cls.files['release-1.0/inner.zip'] = nested.getvalue()
        with ZipFile(cls.pool / 'release.zip', 'w') as archive:
            for path, data in cls.files.items():
                archive.writestr(path, data)
        code, result = cls.cli('corpus-run', '--source-root', cls.pool, '--run-id', 'parts')
        if code:
            raise AssertionError(result)
        cls.report = json.loads((cls.home / 'corpora/parts/corpus.json').read_bytes())
        cls.segments = {segment['path']: (source['source'], segment)
                        for source in cls.report['sources'] for segment in source['segments']}
        cls.quest = cls.root / 'quest/quest.json'
        code, result = cls.cli('quest-start', '--run-id', 'parts', '--scout', '--out', cls.quest.parent)
        if code:
            raise AssertionError(result)
        cls.leads = json.loads(cls.quest.read_bytes())['leads']

    def setUp(self):
        self.work = self.root / self._testMethodName
        self.work.mkdir()
        self.out = self.work / 'prepared'
        self.recipe_file = self.work / 'recipe.json'
        self.recipe = {
            'schema': 1, 'audience': 'Fixture operator', 'purpose': 'Read the selected package label.',
            'acceptance': ['Preserve the expected label and record identifier.'],
            'components': [self.lead('read_label')['definition_id']],
            'module_files': [self.mapping('release-1.0/samplepkg/sub/reader.py', 'samplepkg/sub/reader.py')],
            'evaluation_plan': {'schema': 1, 'cases': [
                {'id': 'label-01', 'acceptance': 0, 'needs': [],
                 'expected': {'case_id': 'fixture ', 'label': 'steady'}}]},
        }

    def lead(self, name):
        return next(row for row in self.leads if row['name'] == name)

    def mapping(self, original, destination):
        return {'source_id': self.segments[original][1]['source_id'], 'path': destination}

    def asset(self, original, destination, role):
        source, _ = self.segments['release-1.0/samplepkg/sub/reader.py']
        data = self.files[original]
        return {'file_id': source['file_id'], 'member_chain': [{'path': original, 'sha256': sha(data)}],
                'path': destination, 'bytes': len(data), 'sha256': sha(data), 'role': role}

    def prepare(self, *, source=None, out=None, quest=None):
        self.recipe_file.write_bytes(canonical(self.recipe))
        return self.cli('quest-prepare', '--quest', quest or self.quest, '--recipe', self.recipe_file,
                        '--source-root', source or self.pool, '--out', out or self.out)

    def test_preparation_adds_original_initializers_and_retains_review_work(self):
        original_recipe = copy.deepcopy(self.recipe)
        source_bytes = (self.pool / 'release.zip').read_bytes()
        code, result = self.prepare()
        self.assertEqual(code, 0, result)
        self.assertEqual(result['status'], 'RECIPE_PREPARED_REVIEW_REQUIRED')
        self.assertEqual((result['added_initializers'], result['modules']), (2, 3))
        prepared = json.loads((self.out / 'recipe.prepared.json').read_bytes())
        self.assertEqual({k: v for k, v in prepared.items() if k != 'module_files'},
                         {k: v for k, v in original_recipe.items() if k != 'module_files'})
        self.assertEqual(json.loads(self.recipe_file.read_bytes()), original_recipe)
        self.assertEqual((self.pool / 'release.zip').read_bytes(), source_bytes)
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.out / 'product.pyz').exists())
        report = json.loads((self.out / 'preparation.json').read_bytes())
        self.assertEqual(report['prepared_recipe_sha256'], sha((self.out / 'recipe.prepared.json').read_bytes()))
        self.assertEqual(report['source_preflight']['report_sha256'], sha((self.out / 'source-preflight.json').read_bytes()))
        self.assertEqual({row['original_path'] for row in report['added_initializers']},
                         {'release-1.0/samplepkg/__init__.py', 'release-1.0/samplepkg/sub/__init__.py'})
        self.assertFalse(report['dependency_closure_complete'])
        self.assertFalse(report['entry_point_reviewed'])
        self.assertFalse(report['release_approved'])
        self.assertEqual(report['reuse_review']['status'], 'BUILDER_REVIEW_REQUIRED')
        self.assertEqual(report['reuse_review']['selected_notices'], [])
        candidates = report['support_review']['candidates']
        self.assertEqual([row['path'] for row in candidates], ['release-1.0/samplepkg/tools.py'])
        self.assertTrue(candidates[0]['selection_required'])
        self.assertNotIn(candidates[0]['source_id'], {row['source_id'] for row in prepared['module_files']})
        preflight = json.loads((self.out / 'source-preflight.json').read_bytes())
        self.assertEqual(preflight['scope'], 'SELECTED_MODULES_ONLY')
        self.assertFalse(preflight['entry_point_reviewed'])
        self.assertNotIn('__main__.py', {row['path'] for row in preflight['files']})
        self.assertTrue({'MISSING_LOCAL_IMPORT', 'MISSING_PACKAGE_RESOURCE'}
                        <= {row['code'] for row in preflight['issues']})

    def test_prepared_and_equivalent_manual_recipe_build_identical_archives(self):
        self.recipe['module_files'].append(self.mapping('release-1.0/samplepkg/tools.py', 'samplepkg/tools.py'))
        self.recipe['assets'] = [
            self.asset('release-1.0/samplepkg/labels.json', 'samplepkg/labels.json', 'resource'),
            self.asset('release-1.0/LICENSE', 'licenses/samplepkg/LICENSE', 'notice'),
        ]
        code, result = self.prepare()
        self.assertEqual(code, 0, result)
        manual = copy.deepcopy(self.recipe)
        manual['module_files'].extend([
            self.mapping('release-1.0/samplepkg/__init__.py', 'samplepkg/__init__.py'),
            self.mapping('release-1.0/samplepkg/sub/__init__.py', 'samplepkg/sub/__init__.py'),
        ])
        manual_path = self.work / 'manual.json'
        manual_path.write_bytes(canonical(manual))
        entry = self.work / 'entry.py'
        entry.write_bytes(b'from samplepkg.sub.reader import read_label\nprint(read_label())\n')
        products = []
        for name, recipe in [('manual', manual_path), ('prepared', self.out / 'recipe.prepared.json')]:
            product = self.work / (name + '-product')
            code, result = self.cli('quest-build', '--quest', self.quest, '--recipe', recipe,
                                    '--entry', entry, '--source-root', self.pool, '--out', product)
            self.assertEqual(code, 0, result)
            products.append((product / 'product.pyz').read_bytes())
        self.assertEqual(products[0], products[1])
        with ZipFile(BytesIO(products[0])) as archive:
            self.assertEqual(archive.read('samplepkg/sub/reader.py'),
                             self.files['release-1.0/samplepkg/sub/reader.py'])
            self.assertEqual(archive.read('samplepkg/__init__.py'), self.initializer)
        report = json.loads((self.out / 'preparation.json').read_bytes())
        self.assertEqual(report['reuse_review']['selected_notices'], ['licenses/samplepkg/LICENSE'])
        self.assertFalse(report['reuse_review']['notice_presence_is_rights_clearance'])
        self.assertEqual(report['support_review']['candidates'], [])
        self.assertFalse(self.marker.exists())

    def test_plain_module_keeps_its_existing_path(self):
        self.recipe['components'] = [self.lead('normalize')['definition_id']]
        del self.recipe['module_files']
        code, result = self.prepare()
        self.assertEqual(code, 0, result)
        prepared = json.loads((self.out / 'recipe.prepared.json').read_bytes())
        self.assertEqual({row['path'] for row in prepared['module_files']},
                         {'plainpkg/reader.py', 'plainpkg/__init__.py'})
        self.assertEqual(result['added_initializers'], 1)

    def test_explicit_support_module_gets_its_own_original_initializer(self):
        self.recipe['support_modules'] = ['plainpkg/tools.py']
        code, result = self.prepare()
        self.assertEqual(code, 0, result)
        prepared = json.loads((self.out / 'recipe.prepared.json').read_bytes())
        self.assertEqual(prepared['support_modules'], ['plainpkg/tools.py'])
        self.assertTrue({'plainpkg/tools.py', 'plainpkg/__init__.py'}
                        <= {row['path'] for row in prepared['module_files']})
        self.assertEqual(result['added_initializers'], 3)

    def test_nested_archive_and_renamed_package_preserve_original_chain(self):
        self.recipe['components'] = [self.lead('amount')['definition_id']]
        self.recipe['module_files'] = [self.mapping('nestedpkg/reader.py', 'renamed/reader.py')]
        code, result = self.prepare()
        self.assertEqual(code, 0, result)
        report = json.loads((self.out / 'preparation.json').read_bytes())
        self.assertEqual({row['path'] for row in report['source_files']},
                         {'renamed/reader.py', 'renamed/__init__.py'})
        for row in report['source_files']:
            self.assertEqual(len(row['origin']['member_chain']), 2)
            self.assertEqual(row['origin']['member_chain'][0]['path'], 'release-1.0/inner.zip')

    def test_archived_destination_remains_a_builder_decision(self):
        del self.recipe['module_files']
        code, result = self.prepare()
        self.assertEqual((code, result['reason']), (2, 'QUEST_MODULE_MAPPING_REQUIRED'))
        self.assertFalse(self.out.exists())

    def test_wrong_initializer_and_flattened_package_are_not_repaired(self):
        variants = [
            [self.mapping('release-1.0/samplepkg/sub/reader.py', 'samplepkg/sub/reader.py'),
             self.mapping('release-1.0/other/__init__.py', 'samplepkg/__init__.py')],
            [self.mapping('release-1.0/samplepkg/sub/reader.py', 'reader.py')],
        ]
        for mappings in variants:
            with self.subTest(mappings=mappings):
                self.recipe['module_files'] = mappings
                code, result = self.prepare()
                self.assertEqual((code, result['reason']), (2, 'QUEST_SELECT_PACKAGE_INITIALIZER'))
                self.assertFalse(self.out.exists())

    def test_existing_unindexed_initializer_is_not_copied(self):
        manifest = self.work / 'manifest.json'
        path = 'plainpkg/reader.py'
        data = (self.pool / path).read_bytes()
        manifest.write_bytes(canonical({'schema': 1, 'files': [
            {'provider': 'synthetic', 'file_id': path, 'path': path, 'size': len(data),
             'sha256': sha(data), 'source_url': 'fixture://' + path}]}))
        code, result = self.cli('corpus-run', '--source-root', self.pool, '--manifest', manifest, '--run-id', 'unindexed')
        self.assertEqual(code, 0, result)
        quest = self.work / 'other-quest/quest.json'
        code, result = self.cli('quest-start', '--run-id', 'unindexed', '--scout', '--out', quest.parent)
        self.assertEqual(code, 0, result)
        self.recipe['components'] = [json.loads(quest.read_bytes())['leads'][0]['definition_id']]
        del self.recipe['module_files']
        code, result = self.prepare(quest=quest)
        self.assertEqual((code, result['reason']), (2, 'QUEST_SELECT_PACKAGE_INITIALIZER'))
        self.assertFalse(self.out.exists())

    def test_changed_container_and_symlink_source_are_refused(self):
        mirror = self.work / 'mirror'
        mirror.mkdir()
        (mirror / 'release.zip').write_bytes((self.pool / 'release.zip').read_bytes() + b'changed')
        code, result = self.prepare(source=mirror)
        self.assertEqual((code, result['reason']), (2, 'SOURCE_VERSION_MISMATCH'))
        self.assertFalse(self.out.exists())
        linked = self.work / 'linked'
        linked.symlink_to(self.pool, target_is_directory=True)
        code, result = self.prepare(source=linked)
        self.assertEqual((code, result['reason']), (2, 'SYMLINK_PATH'))
        self.assertFalse(self.out.exists())

    def test_output_separation_and_no_overwrite(self):
        inside = self.pool / 'must-not-be-created'
        code, result = self.prepare(out=inside)
        self.assertEqual(code, 2, result)
        self.assertFalse(inside.exists())
        self.out.mkdir()
        sentinel = self.out / 'sentinel'
        sentinel.write_bytes(b'preserve')
        code, result = self.prepare()
        self.assertEqual(code, 2, result)
        self.assertEqual(list(self.out.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b'preserve')

    def test_initializer_completion_respects_total_module_limit(self):
        self.recipe['support_modules'] = ['plainpkg/tools.py']
        self.recipe_file.write_bytes(canonical(self.recipe))
        with mock.patch.object(creation_quest, 'COMPONENT_LIMIT', 3):
            with self.assertRaisesRegex(Blocked, '^QUEST_COMPONENT_SELECTION$'):
                quest_preparation.prepare(Workbench(self.home), self.quest, self.recipe_file, self.pool, self.out)
        self.assertFalse(self.out.exists())

    def test_ambiguous_import_context_is_not_a_support_candidate(self):
        self.recipe['components'] = [self.lead('clean_ambiguous')['definition_id']]
        self.recipe['module_files'] = [self.mapping('release-1.0/samplepkg/ambiguous.py', 'samplepkg/ambiguous.py')]
        code, result = self.prepare()
        self.assertEqual(code, 0, result)
        report = json.loads((self.out / 'preparation.json').read_bytes())
        self.assertEqual(report['support_review']['candidates'], [])
        self.assertFalse(report['support_review']['dependency_closure_complete'])

    def test_tampered_quest_is_rejected_before_publication(self):
        quest = json.loads(self.quest.read_bytes())
        quest['leads'] = []
        changed = self.work / 'changed-quest.json'
        changed.write_bytes(canonical(quest))
        code, result = self.prepare(quest=changed)
        self.assertEqual((code, result['reason']), (2, 'QUEST_BINDING'))
        self.assertFalse(self.out.exists())

    def test_repeated_preparation_has_identical_data_bytes(self):
        code, result = self.prepare()
        self.assertEqual(code, 0, result)
        second = self.work / 'second'
        code, result = self.prepare(out=second)
        self.assertEqual(code, 0, result)
        for path in self.out.iterdir():
            self.assertEqual(path.read_bytes(), (second / path.name).read_bytes())
