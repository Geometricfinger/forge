"""Real CLI checks for read-only behavioral planning and evidence review."""
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from forge_core.behavior_example import prepare, recorded_evidence
from forge_core.common import canonical, loads, sha
from forge_core.workspace import ROOT, initialize


def inventory(root):
    """Include empty directories and links as well as every file's exact bytes."""
    result = {}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[name] = ('link', os.readlink(path))
        elif path.is_dir():
            result[name] = ('directory',)
        elif path.is_file():
            result[name] = ('file', sha(path.read_bytes()))
        else:
            result[name] = ('other',)
    return result


class BehaviorCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name).resolve()
        cls.workbench = initialize(cls.root / 'home')
        cls.context = prepare(cls.workbench, cls.root / 'example', 'behavior-cli')
        recorded_evidence(cls.context)
        cls.other = prepare(cls.workbench, cls.root / 'other-example', 'other-corpus')

    def setUp(self):
        self.outputs = self.root / 'outputs' / self._testMethodName
        self.outputs.mkdir(parents=True)

    def args(self, kind, *, run_id='behavior-cli', contract=None, bundle=None, registry=None):
        folder = self.context['out']
        contract = contract or folder / 'contract.json'
        if kind == 'template':
            return ['behavior-result-template', '--contract', contract]
        result = ['opportunity-plan' if kind == 'plan' else 'opportunity-review',
                  '--bundle', bundle or folder / 'bundle.json',
                  '--registry', registry or folder / 'registry.json', '--corpus-run', run_id]
        if kind == 'plan':
            selected = self.context['selection']
            result += ['--hypothesis', selected['hypothesis'], '--mechanism', selected['mechanism'],
                       '--corpus', run_id, '--candidate', selected['candidate_id']]
        elif kind == 'behavior':
            result += ['--behavior-contract', contract,
                       '--behavior-evidence', folder / 'pass-evidence.json',
                       '--artifact-root', self.context['artifact_root']]
        return result

    def invoke(self, args, out, *, home=None):
        command = [sys.executable, '-B', str(ROOT / 'forge.py'), '--home',
                   str(self.workbench.home if home is None else home), *map(str, args),
                   '--out', str(out)]
        result = subprocess.run(command, capture_output=True, timeout=60,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(result.stderr, b'', result.stderr.decode(errors='replace'))
        return result.returncode, loads(result.stdout)

    def assert_refused_unchanged(self, args, out, *, home=None, reason=None):
        before = inventory(self.root)
        code, result = self.invoke(args, out, home=home)
        self.assertEqual(inventory(self.root), before, 'Rejected command changed file membership or bytes')
        self.assertEqual(code, 2, result)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertFalse(result['release_approved'])
        if reason is not None:
            self.assertEqual(result['reason'], reason)
        return result

    def test_all_review_commands_refuse_original_source_outputs(self):
        for kind in ('plan', 'review', 'behavior', 'template'):
            with self.subTest(command=kind):
                out = self.context['source_root'] / 'new-directory' / (kind + '.json')
                self.assert_refused_unchanged(self.args(kind), out, reason='BEHAVIOR_SEPARATE_OUTPUT')

    def test_all_review_commands_refuse_analyzer_runtime_outputs(self):
        for kind in ('plan', 'review', 'behavior', 'template'):
            with self.subTest(command=kind):
                out = self.workbench.home / 'engine' / 'runtime' / 'new-directory' / (kind + '.json')
                self.assert_refused_unchanged(self.args(kind), out, reason='BEHAVIOR_SEPARATE_OUTPUT')

    def test_all_review_commands_refuse_corpus_store_outputs(self):
        for kind in ('plan', 'review', 'behavior', 'template'):
            with self.subTest(command=kind):
                out = self.workbench.home / 'corpora' / 'new-directory' / (kind + '.json')
                self.assert_refused_unchanged(self.args(kind), out, reason='BEHAVIOR_SEPARATE_OUTPUT')

    def test_unselected_requested_corpus_source_is_also_protected(self):
        for kind in ('plan', 'review', 'behavior'):
            with self.subTest(command=kind):
                args = self.args(kind) + ['--corpus-run', 'other-corpus']
                out = self.other['source_root'] / 'new-directory' / (kind + '.json')
                self.assert_refused_unchanged(args, out, reason='BEHAVIOR_SEPARATE_OUTPUT')

    def test_external_outputs_succeed_without_changing_inputs_or_workspace(self):
        protected = (self.workbench.home, self.context['out'], self.other['out'])
        before = {str(path): inventory(path) for path in protected}
        for kind in ('plan', 'review', 'behavior', 'template'):
            with self.subTest(command=kind):
                out = self.outputs / (kind + '.json')
                code, result = self.invoke(self.args(kind), out)
                self.assertEqual(code, 0, result)
                self.assertEqual(loads(out.read_bytes()), result)
                self.assertEqual({str(path): inventory(path) for path in protected}, before)
                if kind == 'plan':
                    self.assertEqual(result['status'], 'DRAFT_REQUIRES_OPERATOR_INPUT')
                    self.assertFalse(result['authority']['execution_authorized'])
                elif kind == 'behavior':
                    layer = result['behavioral_review']
                    self.assertEqual(layer['status'], 'REPORTED_PASS')
                    self.assertFalse(layer['authority']['source_code_executed'])
                    self.assertEqual({key: value for key, value in result.items()
                                      if key != 'behavioral_review'}, self.context['transfer_report'])
                elif kind == 'template':
                    self.assertEqual(result['contract_sha256'], sha(canonical(self.context['contract'])))
                    self.assertEqual([row['id'] for row in result['cases']],
                                     [row['id'] for row in self.context['contract']['cases']])
                    self.assertTrue(all(row['outcome'] == 'NOT_RUN' and row['observations'] == {}
                                        for row in result['cases']))
                else:
                    self.assertEqual(result, self.context['transfer_report'])

    def test_result_template_requires_existing_workspace(self):
        home = self.outputs / 'absent-home'
        self.assert_refused_unchanged(self.args('template'), self.outputs / 'template.json', home=home,
                                     reason='BEHAVIOR_REQUIRES_EXISTING_CORPUS')
        self.assertFalse(home.exists())

    def test_result_template_refuses_missing_and_invalid_target_corpus(self):
        for name in ('absent-corpus', '../outside'):
            with self.subTest(corpus=name):
                contract = copy.deepcopy(self.context['contract'])
                contract['target']['code_lead']['corpus'] = name
                contract['target']['code_lead_sha256'] = sha(canonical(contract['target']['code_lead']))
                path = self.outputs / 'other-contract.json'
                path.write_bytes(canonical(contract))
                self.assert_refused_unchanged(self.args('template', contract=path),
                                             self.outputs / 'template.json')

    def test_missing_selected_corpus_does_not_change_existing_or_create_new_workspace(self):
        for kind in ('plan', 'review', 'behavior'):
            for existing in (True, False):
                with self.subTest(command=kind, existing_home=existing):
                    home = self.workbench.home if existing else self.outputs / 'absent-home'
                    self.assert_refused_unchanged(self.args(kind, run_id='absent-corpus'),
                                                 self.outputs / (kind + '.json'), home=home)
                    if not existing:
                        self.assertFalse(home.exists())

    def test_invalid_declarations_do_not_initialize_workspace(self):
        bad = self.outputs / 'invalid.json'
        for contents in (b'{', b'{}'):
            bad.write_bytes(contents)
            for field in ('bundle', 'registry'):
                with self.subTest(contents=contents, field=field):
                    home = self.outputs / 'absent-home'
                    self.assert_refused_unchanged(self.args('review', **{field: bad}),
                                                 self.outputs / 'review.json', home=home)
                    self.assertFalse(home.exists())

    def test_bad_behavior_contract_does_not_change_or_create_workspace(self):
        bad = self.outputs / 'invalid-contract.json'
        for contents in (b'{', b'{}'):
            bad.write_bytes(contents)
            for kind in ('behavior', 'template'):
                for existing in (True, False):
                    with self.subTest(contents=contents, command=kind, existing_home=existing):
                        home = self.workbench.home if existing else self.outputs / 'absent-home'
                        self.assert_refused_unchanged(self.args(kind, contract=bad),
                                                     self.outputs / (kind + '.json'), home=home)
                        if not existing:
                            self.assertFalse(home.exists())

    def test_changed_corpus_binding_blocks_outputs_without_further_writes(self):
        path = self.workbench.home / 'corpora' / 'behavior-cli' / 'binding.json'
        saved = path.read_bytes()
        binding = loads(saved)
        binding['manifest_sha256'] = '0' * 64
        path.write_bytes(canonical(binding))
        try:
            for kind in ('plan', 'review', 'behavior', 'template'):
                with self.subTest(command=kind):
                    self.assert_refused_unchanged(self.args(kind), self.outputs / (kind + '.json'),
                                                 reason='BEHAVIOR_CORPUS_BINDING')
        finally:
            path.write_bytes(saved)

    def test_source_symlink_alias_is_refused_without_writes(self):
        alias = self.outputs / 'source-alias'
        alias.symlink_to(self.context['source_root'], target_is_directory=True)
        for kind in ('plan', 'review', 'behavior', 'template'):
            with self.subTest(command=kind):
                self.assert_refused_unchanged(self.args(kind), alias / (kind + '.json'),
                                             reason='SYMLINK_PATH')

    def test_existing_output_is_not_overwritten(self):
        out = self.outputs / 'existing.json'
        out.write_bytes(b'preserve these existing bytes\n')
        for kind in ('plan', 'review', 'behavior', 'template'):
            with self.subTest(command=kind):
                self.assert_refused_unchanged(self.args(kind), out, reason='OUTPUT_CONFLICT')


if __name__ == '__main__':
    unittest.main()
