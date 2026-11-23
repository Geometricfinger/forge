"""Public CLI creation from purpose or discovered components, using owned fixtures."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from zipfile import ZipFile

from forge_core import creation_quest
from forge_core.common import Blocked, sha
from forge_core.engine import ROOT


class CreationQuestTests(unittest.TestCase):
    @classmethod
    def cli(cls, *args):
        command = [sys.executable, '-B', str(ROOT / 'forge.py'), '--home', str(cls.home), *map(str, args)]
        result = subprocess.run(command, capture_output=True, timeout=90,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        if result.stderr:
            raise AssertionError(result.stderr.decode(errors='replace'))
        return result.returncode, json.loads(result.stdout)

    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.root = Path(temp.name).resolve()
        cls.home = cls.root / 'home'
        cls.source = cls.root / 'pool'
        package = cls.source / 'helpers'
        package.mkdir(parents=True)
        (package / '__init__.py').write_text('SCALE = 2\n')
        cls.unit = package / 'measure.py'
        cls.unit.write_text('from . import SCALE\ndef double(value):\n    return value * SCALE\n')
        (cls.source / 'decoy.py').write_text("raise RuntimeError('must never be imported')\ndef decoy():\n    pass\n")
        code, report = cls.cli('corpus-run', '--source-root', cls.source, '--run-id', 'sample')
        if code:
            raise AssertionError(report)
        cls.quest = cls.root / 'idea' / 'quest.json'
        code, result = cls.cli('quest-start', '--run-id', 'sample', '--idea', 'double value', '--out', cls.quest.parent)
        if code:
            raise AssertionError(result)
        lead = next(row for row in json.loads(cls.quest.read_bytes())['leads'] if row['name'] == 'double')
        cls.recipe = {'schema': 1, 'audience': 'Operators', 'purpose': 'Double a measurement.',
                      'acceptance': ['Input 6 prints 12.'], 'components': [lead['definition_id']],
                      'support_modules': ['helpers/__init__.py']}

    def setUp(self):
        self.work = self.root / self._testMethodName
        self.work.mkdir()
        self.entry = self.work / 'main.py'
        self.entry.write_text("from helpers.measure import double\nimport sys\nprint(double(int(sys.argv[1])))\n")
        self.recipe_file = self.work / 'recipe.json'
        self.recipe_file.write_text(json.dumps(self.recipe))
        self.out = self.work / 'product'

    def build(self, *extra, quest=None, source=None):
        return self.cli('quest-build', '--quest', quest or self.quest, '--recipe', self.recipe_file,
                        '--entry', self.entry, '--source-root', source or self.source, '--out', self.out, *extra)

    def test_public_build_and_python_entry_point(self):
        check = self.work / 'check.txt'
        check.write_bytes(b'Input 6 returns 12.\n')
        code, result = self.build('--check-file', check)
        self.assertEqual(code, 0, result)
        product = self.out / 'product.pyz'
        receipt = json.loads((self.out / 'receipt.json').read_bytes())
        self.assertEqual(receipt['product_sha256'], sha(product.read_bytes()))
        self.assertEqual(receipt['acceptance_file'], {'sha256': sha(check.read_bytes()), 'bytes': check.stat().st_size})
        self.assertEqual(receipt['runtime_validation'], 'NOT_RUN')
        self.assertIs(receipt['source_code_executed'], False)
        with ZipFile(product) as artifact:
            self.assertEqual(artifact.read('helpers/measure.py'), self.unit.read_bytes())
            self.assertEqual(artifact.read('__main__.py'), self.entry.read_bytes())
            self.assertNotIn('decoy.py', artifact.namelist())
            self.assertNotIn('check.txt', artifact.namelist())
        # Only the checked-in test's synthetic program is executed.
        completed = subprocess.run([sys.executable, '-I', str(product), '6'], cwd=self.work,
                                   capture_output=True, check=True, timeout=10)
        self.assertEqual(completed.stdout, b'12\n')
        self.assertEqual(self.build()[0], 2)
        self.assertEqual(receipt['product_sha256'], sha(product.read_bytes()))

    def test_scout_to_packaged_candidate(self):
        dest = self.work / 'scout'
        code, result = self.cli('quest-start', '--run-id', 'sample', '--scout', '--out', dest)
        self.assertEqual(code, 0, result)
        quest = json.loads((dest / 'quest.json').read_bytes())
        self.assertEqual(quest['mode'], 'scout')
        self.assertEqual({row['name'] for row in quest['leads']}, {'double', 'decoy'})
        self.assertEqual(self.build(quest=dest / 'quest.json')[0], 0)

    def test_incomplete_recipe(self):
        self.recipe_file.write_text(json.dumps(self.recipe | {'acceptance': []}))
        self.assertEqual(self.build()[1]['reason'], 'QUEST_ACCEPTANCE')
        self.assertFalse(self.out.exists())

    def test_changed_selected_source_fails_before_publication(self):
        mirror = self.work / 'mirror'
        (mirror / 'helpers').mkdir(parents=True)
        (mirror / 'helpers/measure.py').write_text('def double(value): return value\n')
        self.assertEqual(self.build(source=mirror)[1]['reason'], 'SOURCE_VERSION_MISMATCH')
        self.assertFalse(self.out.exists())

    def test_unselected_initializer_fails(self):
        self.recipe_file.write_text(json.dumps(self.recipe | {'support_modules': []}))
        self.assertEqual(self.build()[1]['reason'], 'QUEST_SELECT_PACKAGE_INITIALIZER')
        self.assertFalse(self.out.exists())

    def test_unsupported_entry_encoding(self):
        self.entry.write_bytes(b'# coding: no-such-encoding\nprint(1)\n')
        self.assertEqual(self.build()[1]['reason'], 'QUEST_ENTRY_SYNTAX')
        self.assertFalse(self.out.exists())

    def test_tampered_quest(self):
        changed = self.work / 'edited.json'
        quest = json.loads(self.quest.read_bytes())
        for update in ({'idea': ['double']}, {'run_id': []}, {'leads': []}, {'corpus_sha256': 'bad'}):
            changed.write_text(json.dumps(quest | update))
            self.assertEqual(self.build(quest=changed)[1]['reason'], 'QUEST_BINDING')
            self.assertFalse(self.out.exists())

    def test_no_output_in_corpus(self):
        dest = self.source / 'scout'
        code, _ = self.cli('quest-start', '--run-id', 'sample', '--scout', '--out', dest)
        self.assertEqual(code, 2)
        self.assertFalse(dest.exists())

    def test_failed_stage_leaves_no_output(self):
        original = creation_quest.write

        def fail_on_receipt(path, data, **kwargs):
            if path.name == 'receipt.json':
                raise OSError('injected write failure')
            original(path, data, **kwargs)

        before = set(self.work.iterdir())
        with mock.patch.object(creation_quest, 'write', side_effect=fail_on_receipt):
            with self.assertRaises(OSError):
                creation_quest._publish(self.out, {'product.pyz': b'test', 'receipt.json': b'test'})
        self.assertEqual(set(self.work.iterdir()), before)

    def test_concurrent_publication_keeps_one_complete_output(self):
        def publish(marker):
            try:
                creation_quest._publish(self.out, {'product.pyz': marker, 'receipt.json': marker})
            except Blocked:
                return False
            return True

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(publish, [b'a', b'b'])), [False, True])
        self.assertEqual((self.out / 'product.pyz').read_bytes(), (self.out / 'receipt.json').read_bytes())
