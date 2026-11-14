"""Fixed-trial CLI boundaries, including a controlled worker-failure receipt."""
import contextlib
import copy
import io
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from forge_core.common import Blocked, loads, sha
from forge_core.workspace import ROOT, Workbench


def inventory(root):
    """Catch added directories/links as well as changed or missing file bytes."""
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


def invoke(args, *, home=None, environment=None):
    command = [sys.executable, '-B', str(ROOT / 'forge.py')]
    if home is not None:
        command += ['--home', str(home)]
    command += list(map(str, args))
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.update(environment or {})
    completed = subprocess.run(command, capture_output=True, timeout=90, env=env)
    if completed.stderr:
        raise AssertionError(completed.stderr.decode(errors='replace'))
    return completed.returncode, loads(completed.stdout)


class ReviewedEvaluatorCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from forge_core import reviewed_evaluator
        cls.evaluator = reviewed_evaluator
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name).resolve()
        cls.home = cls.root / 'home'
        cls.prepared = cls.root / 'prepared'
        code, cls.preparation = invoke(['reviewed-trial-prepare', '--out', cls.prepared], home=cls.home)
        if code != 0 or cls.preparation['status'] != 'REVIEWED_TRIAL_PREPARED':
            raise AssertionError(cls.preparation)
        cls.frozen_preparation = inventory(cls.prepared)
        # A corpus unrelated to the fixed trial still owns its original source
        # tree. Recording it must not let later evaluator outputs modify it.
        cls.unrelated_source = cls.root / 'unrelated-source'
        cls.unrelated_source.mkdir()
        (cls.unrelated_source / 'sample.py').write_bytes(b'def preserved(value):\n    return value + 1\n')
        cls.frozen_unrelated_source = inventory(cls.unrelated_source)
        workbench = Workbench(cls.home)
        workbench.guard()
        cls.home_binding = workbench.store.binding()
        code, indexed = invoke(['corpus-run', '--source-root', cls.unrelated_source,
                                '--run-id', 'reviewed-cli-unrelated', '--max-containers', '1'],
                               home=cls.home)
        if code != 0 or indexed['status'] != 'COMPLETED_FOR_SELECTED_CONTAINERS':
            raise AssertionError(indexed)
        workbench.guard()
        if workbench.store.binding() != cls.home_binding or inventory(cls.unrelated_source) != cls.frozen_unrelated_source:
            raise AssertionError('Indexing changed source bytes or the workspace code binding')
        cls.result = cls.root / 'completed'
        code, cls.completed = invoke(['reviewed-trial-run', '--prepared', cls.prepared,
                                      '--out', cls.result], home=cls.home)
        cls.completed_exit = code
        if code != 0 or cls.completed['status'] != 'REVIEWED_TRIAL_COMPLETED':
            raise AssertionError(cls.completed)
        # Exercise the real failure-packet writer without invoking a subject.
        # The CLI exposes no custom worker or failure-injection option.
        cls.failed_result = cls.root / 'execution-failed'
        with mock.patch.object(reviewed_evaluator, 'run_bounded', side_effect=Blocked('TOOL_TIMEOUT')):
            cls.failed = reviewed_evaluator.run(Workbench(cls.home), cls.prepared, cls.failed_result)
        if cls.failed['status'] != 'REVIEWED_TRIAL_EXECUTION_FAILED':
            raise AssertionError(cls.failed)

    def setUp(self):
        self.outputs = self.root / 'test-outputs' / self._testMethodName
        self.outputs.mkdir(parents=True)

    def assert_refused_unchanged(self, args, *, home=None, reason=None, environment=None):
        before = inventory(self.root)
        code, value = invoke(args, home=home, environment=environment)
        self.assertEqual(inventory(self.root), before, 'Refusal changed file membership or content')
        self.assertEqual(code, 2, value)
        self.assertEqual(value['status'], 'BLOCKED')
        self.assertFalse(value['release_approved'])
        if reason is not None:
            self.assertEqual(value['reason'], reason)
        return value

    def run_args(self, out, *, prepared=None):
        return ['reviewed-trial-run', '--prepared', self.prepared if prepared is None else prepared,
                '--out', out]

    def verify_args(self, *, prepared=None, result=None):
        return ['reviewed-trial-verify', '--prepared', self.prepared if prepared is None else prepared,
                '--result', self.result if result is None else result]

    def invoke_in_process(self, args, *, home=None):
        argv = [str(ROOT / 'forge.py')]
        if home is not None:
            argv += ['--home', str(home)]
        argv += list(map(str, args))
        output = io.StringIO()
        with mock.patch.object(sys, 'argv', argv), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(str(ROOT / 'forge.py'), run_name='__main__')
        return stopped.exception.code, loads(output.getvalue())

    def test_prepare_creates_external_packet_and_existing_home_without_running_trial(self):
        self.assertEqual(self.preparation['status'], 'REVIEWED_TRIAL_PREPARED')
        self.assertIs(self.preparation['executed'], False)
        self.assertTrue((self.home / 'missions.sqlite').is_file())
        self.assertTrue((self.prepared / 'packet.json').is_file())
        self.assertEqual(inventory(self.prepared), self.frozen_preparation)

    def test_completed_execution_exits_zero_even_when_baseline_does_not_match(self):
        self.assertEqual(self.completed_exit, 0)
        self.assertEqual(self.completed['results'],
                         {'baseline': 'REPORTED_FAIL', 'candidate': 'REPORTED_PASS'})
        self.assertEqual(inventory(self.prepared), self.frozen_preparation)

    def test_complete_packet_verifies_without_home_or_input_writes(self):
        environment = {'HOME': str(self.outputs / 'absent-default-home')}
        before = inventory(self.root)
        code, first = invoke(self.verify_args(), environment=environment)
        self.assertEqual(code, 0, first)
        self.assertEqual(first['status'], 'REVIEWED_TRIAL_VERIFIED')
        self.assertEqual(first['qualification'], 'COMPLETED')
        self.assertIs(first['executed'], False)
        code, again = invoke(self.verify_args(), environment=environment)
        self.assertEqual(code, 0, again)
        self.assertEqual(again, first)
        self.assertEqual(inventory(self.root), before)
        self.assertFalse((self.outputs / 'absent-default-home').exists())

    def test_intact_execution_failure_is_verifiable_and_remains_failed(self):
        absent_home = self.outputs / 'absent-home'
        before = inventory(self.root)
        code, value = invoke(self.verify_args(result=self.failed_result), home=absent_home)
        self.assertEqual(code, 0, value)
        self.assertEqual(value['status'], 'REVIEWED_TRIAL_VERIFIED')
        self.assertEqual(value['qualification'], 'EXECUTION_FAILED')
        self.assertIs(value['executed'], False)
        self.assertEqual(inventory(self.root), before)
        self.assertFalse(absent_home.exists())

    def test_verification_does_not_launch_worker_or_old_execution_helpers(self):
        from forge_core import contract_trial, interop, workspace
        absent_home = self.outputs / 'absent-home'
        before = inventory(self.root)
        forbidden = AssertionError('Verification attempted experiment execution or workspace initialization')
        with contextlib.ExitStack() as stack:
            for owner, name in ((self.evaluator, 'run_bounded'), (contract_trial, 'external_vectors'),
                                (interop, 'normalize_record'), (interop, 'fingerprint'),
                                (interop, 'verify_record'), (workspace, 'initialize'),
                                (subprocess, 'Popen')):
                stack.enter_context(mock.patch.object(owner, name, side_effect=forbidden))
            code, value = self.invoke_in_process(self.verify_args(), home=absent_home)
        self.assertEqual(code, 0, value)
        self.assertEqual(value['status'], 'REVIEWED_TRIAL_VERIFIED')
        self.assertIs(value['executed'], False)
        self.assertEqual(inventory(self.root), before)

    def test_execution_failed_status_maps_to_cli_exit_two(self):
        # The saved failure packet above is real; only CLI exit mapping uses a
        # substituted API return so this test cannot launch a second experiment.
        out = self.outputs / 'unused-result'
        before = inventory(self.root)
        with mock.patch.object(self.evaluator, 'run', return_value=copy.deepcopy(self.failed)) as run:
            code, value = self.invoke_in_process(self.run_args(out), home=self.home)
        self.assertEqual(code, 2)
        self.assertEqual(value['status'], 'REVIEWED_TRIAL_EXECUTION_FAILED')
        run.assert_called_once()
        self.assertEqual(inventory(self.root), before)

    def test_prepare_refuses_home_output_overlap_before_initializing(self):
        cases = ((self.outputs / 'same', self.outputs / 'same'),
                 (self.outputs / 'home', self.outputs / 'home' / 'plan'),
                 (self.outputs / 'plan' / 'home', self.outputs / 'plan'))
        for home, out in cases:
            with self.subTest(home=home, out=out):
                self.assert_refused_unchanged(['reviewed-trial-prepare', '--out', out], home=home)
                self.assertFalse(home.exists())
                self.assertFalse(out.exists())

    def test_prepare_refuses_checkout_output_and_checkout_home_before_writes(self):
        forbidden_out = ROOT / 'reviewed-cli-forbidden-plan'
        forbidden_home = ROOT / 'reviewed-cli-forbidden-home'
        self.assertFalse(forbidden_out.exists())
        self.assertFalse(forbidden_home.exists())
        # Clean only these new test-owned paths if a regression creates them.
        self.addCleanup(shutil.rmtree, forbidden_out, True)
        self.addCleanup(shutil.rmtree, forbidden_home, True)
        home = self.outputs / 'absent-home'
        self.assert_refused_unchanged(['reviewed-trial-prepare', '--out', forbidden_out], home=home)
        self.assertFalse(forbidden_out.exists())
        self.assertFalse(home.exists())
        out = self.outputs / 'plan'
        self.assert_refused_unchanged(['reviewed-trial-prepare', '--out', out], home=forbidden_home)
        self.assertFalse(forbidden_home.exists())
        self.assertFalse(out.exists())

    def test_prepare_and_run_refuse_existing_outputs(self):
        existing = self.outputs / 'existing'
        existing.mkdir()
        (existing / 'keep.txt').write_bytes(b'preserve existing bytes\n')
        self.assert_refused_unchanged(['reviewed-trial-prepare', '--out', existing], home=self.home)
        absent_home = self.outputs / 'absent-home'
        self.assert_refused_unchanged(['reviewed-trial-prepare', '--out', existing], home=absent_home)
        self.assertFalse(absent_home.exists())
        self.assert_refused_unchanged(self.run_args(existing), home=self.home)

    def test_prepare_rejects_non_directory_ancestors_before_initializing(self):
        obstruction = self.outputs / 'ordinary-file'
        obstruction.write_bytes(b'not a directory\n')
        home = self.outputs / 'absent-home'
        out = obstruction / 'plan'
        self.assert_refused_unchanged(['reviewed-trial-prepare', '--out', out], home=home)
        self.assertFalse(home.exists())
        self.assert_refused_unchanged(['reviewed-trial-prepare', '--out', self.outputs / 'plan'],
                                     home=obstruction / 'home')

    def test_run_refuses_home_and_prepared_output_overlap(self):
        destinations = (self.home / 'new-result', self.home / 'engine' / 'new-result',
                        self.home / 'corpora' / 'new-result', self.prepared / 'new-result',
                        self.prepared, self.root)
        for out in destinations:
            with self.subTest(out=out):
                self.assert_refused_unchanged(self.run_args(out), home=self.home)

    def test_prepare_and_run_protect_an_unrelated_indexed_source(self):
        workbench = Workbench(self.home)
        workbench.guard()
        self.assertEqual(workbench.store.binding(), self.home_binding)
        self.assertEqual(inventory(self.unrelated_source), self.frozen_unrelated_source)
        for command in ('prepare', 'run'):
            with self.subTest(command=command):
                out = self.unrelated_source / 'new-directory' / command
                args = (['reviewed-trial-prepare', '--out', out] if command == 'prepare'
                        else self.run_args(out))
                self.assert_refused_unchanged(args, home=self.home)
        self.assertEqual(inventory(self.unrelated_source), self.frozen_unrelated_source)
        self.assertEqual(workbench.store.binding(), self.home_binding)
        workbench.guard()

    def test_run_refuses_checkout_output(self):
        out = ROOT / 'reviewed-cli-forbidden-result'
        self.assertFalse(out.exists())
        self.addCleanup(shutil.rmtree, out, True)
        self.assert_refused_unchanged(self.run_args(out), home=self.home)
        self.assertFalse(out.exists())

    def test_run_requires_existing_initialized_home_without_writes(self):
        for existing in (False, True):
            with self.subTest(empty_home_exists=existing):
                home = self.outputs / ('empty-home' if existing else 'absent-home')
                if existing:
                    home.mkdir()
                out = self.outputs / 'new-result'
                self.assert_refused_unchanged(self.run_args(out), home=home)
                self.assertFalse(out.exists())
                if not existing:
                    self.assertFalse(home.exists())

    def test_bad_or_missing_preparation_does_not_create_result(self):
        bad = self.outputs / 'bad-prepared'
        bad.mkdir()
        (bad / 'packet.json').write_bytes(b'{')
        for prepared in (bad, self.outputs / 'missing-prepared'):
            with self.subTest(prepared=prepared):
                out = self.outputs / 'new-result'
                self.assert_refused_unchanged(self.run_args(out, prepared=prepared), home=self.home)
                self.assertFalse(out.exists())

    def test_symlink_inputs_and_destinations_are_refused(self):
        alias = self.outputs / 'prepared-alias'
        alias.symlink_to(self.prepared, target_is_directory=True)
        result_alias = self.outputs / 'result-alias'
        result_alias.symlink_to(self.result, target_is_directory=True)
        destination = self.outputs / 'destination'
        destination.mkdir()
        destination_alias = self.outputs / 'destination-alias'
        destination_alias.symlink_to(destination, target_is_directory=True)
        home_alias = self.outputs / 'home-alias'
        home_alias.symlink_to(self.home, target_is_directory=True)
        cases = [(['reviewed-trial-prepare', '--out', destination_alias / 'plan'], self.home),
                 (['reviewed-trial-prepare', '--out', self.outputs / 'new-plan'], home_alias),
                 (self.run_args(destination_alias / 'result'), self.home),
                 (self.run_args(self.outputs / 'new-result'), home_alias),
                 (self.run_args(self.outputs / 'new-result', prepared=alias), self.home),
                 (self.verify_args(prepared=alias), None),
                 (self.verify_args(result=result_alias), None)]
        for args, home in cases:
            with self.subTest(command=args[0], path=str(args[-1])):
                self.assert_refused_unchanged(args, home=home)

    def test_verify_refuses_prepared_result_overlap_without_initializing_home(self):
        home = self.outputs / 'absent-home'
        for prepared, result in ((self.prepared, self.prepared),
                                 (self.root, self.result), (self.prepared, self.root)):
            with self.subTest(prepared=prepared, result=result):
                self.assert_refused_unchanged(self.verify_args(prepared=prepared, result=result), home=home)
                self.assertFalse(home.exists())

    def test_missing_or_malformed_result_does_not_initialize_home(self):
        home = self.outputs / 'absent-home'
        bad = self.outputs / 'bad-result'
        bad.mkdir()
        (bad / 'packet.json').write_bytes(b'{')
        for result in (bad, self.outputs / 'missing-result'):
            with self.subTest(result=result):
                self.assert_refused_unchanged(self.verify_args(result=result), home=home)
                self.assertFalse(home.exists())


if __name__ == '__main__':
    unittest.main()
