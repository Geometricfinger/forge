"""Packaging warnings use exact owned bytes without importing the product."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from forge_core import quest_preflight as preflight
from forge_core.common import Blocked, canonical, sha


class QuestPreflightTests(unittest.TestCase):
    def review(self, modules, assets=()):
        facts = [preflight.inspect_module(source.encode() if isinstance(source, str) else source, path)
                 for path, source in sorted(modules.items())]
        return preflight.review(facts, assets, stdlib={'json', 'sys', 'importlib', 'pkgutil'}, builtin={'sys'})

    def codes(self, report):
        return [row['code'] for row in report['issues']]

    def test_complete_local_imports_and_resource_are_unflagged(self):
        report = self.review({'alpha/__init__.py': '', 'alpha/maths.py': 'def twice(n): return n*2\n',
                              'alpha/service.py': 'from .maths import twice\nfrom importlib.resources import files\n'
                                                  'def factor(): return files(__package__).joinpath("data/factor.json").read_text()\n',
                              '__main__.py': 'from alpha.service import factor\n'}, ['alpha/data/factor.json'])
        self.assertEqual(report['status'], 'NO_ISSUES_OBSERVED')
        self.assertEqual(self.codes(report), [])
        self.assertFalse(report['source_code_executed'])
        self.assertEqual(report['runtime_validation'], 'NOT_RUN')

    def test_missing_transitive_relative_and_absolute_modules(self):
        report = self.review({'alpha/__init__.py': '',
                              'alpha/service.py': 'from .maths import twice\nimport alpha.config\n'})
        self.assertEqual({row['module'] for row in report['issues']}, {'alpha.maths', 'alpha.config'})
        self.assertEqual(set(self.codes(report)), {'MISSING_LOCAL_IMPORT'})

    def test_unavailable_external_dependency_is_not_called_local(self):
        report = self.review({'part.py': 'import external_package\nimport json, sys\n'})
        self.assertEqual(self.codes(report), ['EXTERNAL_IMPORT_UNVERIFIED'])
        self.assertEqual(report['issues'][0]['module'], 'external_package')

    def test_relative_import_beyond_mapped_package(self):
        report = self.review({'alpha/service.py': 'from ..other import value\n'})
        self.assertEqual(self.codes(report), ['RELATIVE_IMPORT_OUTSIDE_PACKAGE'])

    def test_nested_relative_import_resolves_selected_module(self):
        report = self.review({'alpha/maths.py': '', 'alpha/sub/service.py': 'from ..maths import twice\n'})
        self.assertEqual(self.codes(report), [])

    def test_imported_attribute_is_left_for_runtime_review(self):
        report = self.review({'alpha/__init__.py': 'VALUE = 1\n', 'alpha/service.py': 'from . import VALUE\n'})
        self.assertEqual(self.codes(report), [])
        self.assertTrue(any('imported attributes' in item for item in report['limitations']))

    def test_missing_package_resource_reports_exact_path(self):
        report = self.review({'alpha/__init__.py': '', 'alpha/config.py':
                              'from importlib.resources import files\n'
                              'def factor(): return files(__package__).joinpath("data/factor.json").read_text()\n'})
        self.assertEqual(self.codes(report), ['MISSING_PACKAGE_RESOURCE'])
        self.assertEqual(report['issues'][0]['resource'], 'data/factor.json')

    def test_resource_alias_keyword_join_and_slash_forms(self):
        for statement in ('from importlib.resources import files as fetch\np = fetch(package="alpha").joinpath("data", "factor.json")',
                          'import importlib.resources as r\np = r.files(anchor="alpha") / "data" / "factor.json"',
                          'from importlib import resources as r\np = r.files("alpha").joinpath("data").joinpath("factor.json")',
                          'import importlib.resources\np = importlib.resources.files("alpha").joinpath("data/factor.json")'):
            with self.subTest(statement=statement):
                report = self.review({'alpha/__init__.py': '', 'part.py': statement}, ['alpha/data/factor.json'])
                self.assertEqual(self.codes(report), [])

    def test_legacy_resource_functions_check_literal_paths(self):
        for statement in ('from pkgutil import get_data\np = get_data("alpha", "factor.json")',
                          'from importlib.resources import read_text\np = read_text(package="alpha", resource="factor.json")'):
            with self.subTest(statement=statement):
                report = self.review({'alpha/__init__.py': '', 'part.py': statement})
                self.assertEqual(self.codes(report), ['MISSING_PACKAGE_RESOURCE'])

    def test_module_anchor_or_shadowed_resource_api_is_unresolved(self):
        for modules in ({'part.py': '', '__main__.py': 'from importlib.resources import files\np = files("part") / "data"\n'},
                        {'alpha/__init__.py': '', 'importlib.py': '', '__main__.py':
                         'from importlib.resources import files\np = files("alpha") / "data"\n'}):
            report = self.review(modules)
            self.assertIn('RESOURCE_REFERENCE_UNRESOLVED', self.codes(report))
            self.assertNotIn('MISSING_PACKAGE_RESOURCE', self.codes(report))

    def test_dynamic_or_unsafe_resource_paths_remain_unresolved(self):
        for expression in ('files("alpha").joinpath(name)', 'files(package).joinpath("value.json")',
                           'files("alpha") / "/value.json"', 'files("alpha").joinpath("../value.json")'):
            with self.subTest(expression=expression):
                report = self.review({'alpha/__init__.py': '', 'part.py':
                                      'from importlib.resources import files\np = ' + expression})
                self.assertIn('RESOURCE_REFERENCE_UNRESOLVED', self.codes(report))
                self.assertNotIn('MISSING_PACKAGE_RESOURCE', self.codes(report))

    def test_rebound_resource_alias_is_not_treated_as_standard_library(self):
        variants = ('files = lambda x: x\n', 'def thing(files): pass\n',
                    'try: pass\nexcept Exception as files: pass\n', 'from external_package import *\n')
        for extra in variants:
            with self.subTest(extra=extra):
                facts = preflight.inspect_module(('from importlib.resources import files\n' + extra +
                                                  'x = files("alpha").joinpath("absent")\n').encode(), 'part.py')
                self.assertEqual(facts['resources'], [])

    def test_namespace_resources_warn_even_when_bytes_are_present(self):
        report = self.review({'loose/first.py': 'def first(): return 1\n', '__main__.py':
                              'from importlib.resources import files\np = files("loose").joinpath("data/info.json")\n'},
                             ['loose/data/info.json'])
        self.assertIn('ZIP_NAMESPACE_RESOURCE', self.codes(report))
        self.assertNotIn('MISSING_PACKAGE_RESOURCE', self.codes(report))

    def test_namespace_imports_without_resource_calls_are_unflagged(self):
        report = self.review({'loose/first.py': '', '__main__.py': 'import loose.first\n'})
        self.assertEqual(self.codes(report), [])

    def test_eager_cycle_is_a_warning_but_delayed_cycle_is_not(self):
        eager = {'part/__init__.py': '', 'part/a.py': 'from .b import right\nLEFT=1\n',
                 'part/b.py': 'from .a import LEFT\ndef right(): return LEFT+1\n'}
        report = self.review(eager)
        cycle = next(row for row in report['issues'] if row['code'] == 'EAGER_IMPORT_CYCLE')
        self.assertEqual(cycle['modules'], ['part.a', 'part.b'])
        eager['part/a.py'] = 'def left():\n    from .b import right\n    return right()+1\n'
        self.assertNotIn('EAGER_IMPORT_CYCLE', self.codes(self.review(eager)))

    def test_class_body_imports_remain_eager(self):
        report = self.review({'a.py': 'class A:\n    import b\n', 'b.py': 'import a\n'})
        self.assertIn('EAGER_IMPORT_CYCLE', self.codes(report))

    def test_standard_library_shadow_is_only_top_level_and_not_builtin(self):
        report = self.review({'json.py': '', 'sys.py': '', 'alpha/json.py': '', '__main__.py': ''})
        rows = [row for row in report['issues'] if row['code'] == 'STDLIB_NAME_SHADOW']
        self.assertEqual([row['path'] for row in rows], ['json.py'])

    def test_raw_utf16_is_rejected_without_rewriting_source(self):
        data = 'def useful(): return 1\n'.encode('utf-16')
        report = self.review({'part.py': data})
        self.assertEqual(self.codes(report), ['PYTHON_BYTES_NOT_COMPILABLE'])
        self.assertEqual(report['files'][0]['sha256'], sha(data))
        self.assertEqual(report['files'][0]['raw_compilation'], 'REJECTED')

    def test_raw_latin1_bom_and_crlf_are_accepted(self):
        report = self.review({'latin.py': b'# coding: latin-1\r\nVALUE = "caf\xe9"\r\n',
                              'bom.py': b'\xef\xbb\xbfVALUE = 1\n'})
        self.assertEqual(self.codes(report), [])
        self.assertEqual({row['raw_compilation'] for row in report['files']}, {'ACCEPTED'})

    def test_compiler_rejects_semantics_beyond_ast_parsing(self):
        report = self.review({'part.py': 'return 1\n'})
        self.assertEqual(self.codes(report), ['PYTHON_BYTES_NOT_COMPILABLE'])

    def test_nfkc_mapping_and_normalized_collision_are_visible(self):
        report = self.review({'\U0001d542.py': '', 'K.py': ''})
        self.assertIn('MODULE_NAME_NORMALIZATION', self.codes(report))
        self.assertIn('NORMALIZED_MODULE_COLLISION', self.codes(report))
        missing = self.review({'\u212a.py': '', '__main__.py': 'import K\n'})
        self.assertIn('MISSING_LOCAL_IMPORT', self.codes(missing))

    def test_reference_limits_are_visible(self):
        report = self.review({'part.py': '\n'.join('import external' + str(i) for i in range(257))})
        self.assertEqual(report['status'], 'INCOMPLETE')
        self.assertIn('PREFLIGHT_REFERENCE_LIMIT', self.codes(report))

    def test_issue_limit_stays_incomplete(self):
        fact = preflight.inspect_module(b'import unknown\n', 'part.py')
        facts = []
        for i in range(513):
            row = copy.deepcopy(fact); row['path'] = 'part' + str(i) + '.py'; facts.append(row)
        report = preflight.review(facts, [])
        self.assertEqual(len(report['issues']), 512)
        self.assertEqual(report['omitted_issues'], 1)
        self.assertEqual(report['status'], 'INCOMPLETE')

    def test_compiler_resource_errors_are_incomplete(self):
        with mock.patch.object(preflight, 'node_count', return_value=60_001):
            report = self.review({'part.py': ''})
        self.assertEqual(report['status'], 'INCOMPLETE')
        self.assertEqual(report['files'][0]['raw_compilation'], 'NOT_CHECKED')

    def test_worker_failure_and_malformed_binding_do_not_look_clean(self):
        for response in (Blocked('TOOL_TIMEOUT'), b'{}'):
            with self.subTest(response=response), mock.patch.object(preflight, 'run_fixed') as run:
                if isinstance(response, Exception):
                    run.side_effect = response
                else:
                    run.return_value = response
                report = preflight.run(Path('.'), {}, {}, b'pass\n')
            self.assertEqual(report['status'], 'INCOMPLETE')
            self.assertEqual(self.codes(report), ['PREFLIGHT_WORKER_UNAVAILABLE'])

    def test_fixed_worker_never_imports_or_executes_owned_product(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            marker = home/'must-not-exist'
            source = ('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("executed")\n').encode()
            report = preflight.run(home, {'part.py': source}, {}, b'import part\n')
            self.assertFalse(marker.exists())
            self.assertEqual(report['status'], 'NO_ISSUES_OBSERVED')
            self.assertEqual({row['raw_compilation'] for row in report['files']}, {'ACCEPTED'})
            self.assertEqual(preflight.summary(report, canonical(report))['report_sha256'], sha(canonical(report)))

    def test_facts_and_warnings_are_deterministic(self):
        modules = {'alpha/__init__.py': '', 'alpha/service.py': 'from .missing import something\n'}
        self.assertEqual(canonical(self.review(modules)), canonical(self.review(dict(reversed(list(modules.items()))))))
