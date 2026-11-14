"""Missing target-module structure must not become a resolved import lead.

Synthetic source is parsed as data. The initial parser budget is lowered to
exercise whole-unit omissions cheaply; source inspection uses the real fixed
worker and original archive bytes. No fixture body is imported or executed.
"""
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from forge_core import corpus
from forge_core.common import sha
from forge_core.mixed_intake import inspect_container


class StaticAnalyzer:
    def scan(self, data, source_id, profile):
        return {'source_id': source_id, 'source_sha256': sha(data),
                'upstream_code_executed': False, 'findings': []}


def omitted_effects(name='available'):
    return 'if condition:\n' + ''.join(
        '    %s = replacement_%d\n' % (name, i) for i in range(30))


class ImportTargetPartitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def report(self, files):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_STORED) as archive:
            for name, code in files:
                archive.writestr(name, code)
        data = stream.getvalue()
        (self.root / 'source.zip').write_bytes(data)
        source = {'provider': 'synthetic', 'file_id': 'fixture',
                  'path': 'source.zip', 'size': len(data), 'sha256': sha(data),
                  'source_url': 'fixture://fixture'}
        parsed = inspect_container(data, 'source.zip', limits={'max_nodes': 25})
        with patch.object(corpus, '_parser', return_value=parsed):
            result = corpus.inspect_one(data, source, self.root, {}, StaticAnalyzer())
        return {'schema': 1, 'sources': [result],
                'source_bodies_retained': False, 'upstream_code_executed': False}, data

    def definition(self, report, name):
        return next(d for s in report['sources'][0]['segments']
                    for d in s['definitions'] if d['name'] == name)

    def reference(self, report):
        caller = self.definition(report, 'caller')
        context = corpus.dependency_context(report, caller['definition_id'])
        self.assertFalse(context['source_code_executed'])
        self.assertFalse(context['dependency_closure_complete'])
        self.assertEqual(len(context['references']), 1)
        return context['references'][0]

    def assert_partial_target(self, reference):
        self.assertEqual(reference['status'], 'TARGET_MODULE_CONTEXT_INCOMPLETE')
        self.assertEqual(reference['candidates'], [])
        self.assertFalse(reference['callee_execution_proven'])

    def test_partial_target_retains_inventory_and_exact_inspection(self):
        caller_body = 'def caller():\n    return available()\n'
        target_body = 'def available():\n    return 1\n'
        for module in ['pkg.target', '.target']:
            with self.subTest(module=module):
                caller = 'from %s import available\n' % module + caller_body
                report, original = self.report([
                    ('pkg/caller.py', caller),
                    ('pkg/target.py', target_body + omitted_effects()),
                ])
                target = next(s for s in report['sources'][0]['segments']
                              if s['path'] == 'pkg/target.py')
                self.assertFalse(target['structural_partition']['module_context_complete'])
                self.assertEqual([d['name'] for d in target['definitions']], ['available'])
                for name, body in [('caller', caller_body), ('available', target_body)]:
                    definition = self.definition(report, name)
                    inspected = corpus.inspect_definition(report, definition['definition_id'], self.root)
                    self.assertEqual(inspected['source_text'], body)
                    self.assertEqual(inspected['source_window']['sha256'], sha(body.encode()))
                    self.assertTrue(inspected['source_window']['complete'])
                    self.assertFalse(inspected['source_code_executed'])
                self.assertEqual((self.root / 'source.zip').read_bytes(), original)
                self.assert_partial_target(self.reference(report))

    def test_omitted_target_definition_is_unknown_instead_of_absent(self):
        target = omitted_effects() + '    def available():\n        return 1\n'
        report, _ = self.report([
            ('caller.py', 'from target import available\ndef caller():\n    return available()\n'),
            ('target.py', target),
        ])
        segment = next(s for s in report['sources'][0]['segments'] if s['path'] == 'target.py')
        self.assertEqual(segment['definitions'], [])
        self.assertFalse(segment['structural_partition']['module_context_complete'])
        self.assert_partial_target(self.reference(report))

    def test_partial_package_or_submodule_withholds_alternative_candidate(self):
        caller = 'from pkg import tool\ndef caller():\n    return tool.run()\n'
        package = 'class tool:\n    def run(self):\n        return 1\n'
        submodule = 'def run():\n    return 2\n'
        for partial in ['package', 'submodule']:
            with self.subTest(partial=partial):
                report, _ = self.report([
                    ('caller.py', caller),
                    ('pkg/__init__.py', package + (omitted_effects('tool') if partial == 'package' else '')),
                    ('pkg/tool.py', submodule + (omitted_effects('run') if partial == 'submodule' else '')),
                ])
                self.assert_partial_target(self.reference(report))

    def test_unrelated_partial_module_keeps_complete_target_candidate(self):
        report, _ = self.report([
            ('caller.py', 'from target import available\ndef caller():\n    return available()\n'),
            ('target.py', 'def available():\n    return 1\n'),
            ('unrelated.py', omitted_effects()),
        ])
        reference = self.reference(report)
        self.assertEqual(reference['status'], 'DECLARED_IMPORT_CANDIDATE')
        self.assertEqual([r['path'] for r in reference['candidates']], ['target.py'])
        self.assertEqual([r['name'] for r in reference['candidates']], ['available'])

    def test_complete_target_without_symbol_keeps_absence_of_snapshot_match(self):
        report, _ = self.report([
            ('caller.py', 'from target import available\ndef caller():\n    return available()\n'),
            ('target.py', 'def another():\n    return 1\n'),
        ])
        reference = self.reference(report)
        self.assertEqual(reference['status'], 'DECLARED_IMPORT_TARGET_NOT_IN_SNAPSHOT')
        self.assertEqual(reference['candidates'], [])


if __name__ == '__main__':
    unittest.main()
