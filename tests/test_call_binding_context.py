"""Call leads must agree with lexical bindings, without executing fixture code.

Expected targets are explicit source relationships. The same fixtures exercise
ordinary parsing and actual >250 kB structural partitions; symbol-table checks
independently distinguish globals, closures and parameters where relevant.
"""
import copy
import symtable
import unittest

from forge_core import corpus
from test_research_retrieval import report_from


def padded(code):
    return code + '#' + '.' * max(0, 250_001 - len(code.encode()) - 2) + '\n'


class CallBindingContextTests(unittest.TestCase):
    def contexts(self, code, name):
        cases = []
        for large in (False, True):
            with self.subTest(large=large):
                report = report_from([('module.py', padded(code) if large else code)])
                segment = report['sources'][0]['segments'][0]
                if large:
                    self.assertTrue(segment['structural_partition']['module_context_complete'])
                definition = next(d for d in segment['definitions'] if d['name'] == name)
                context = corpus.dependency_context(report, definition['definition_id'])
                self.assertFalse(context['source_code_executed'])
                self.assertFalse(context['dependency_closure_complete'])
                cases.append((context, definition['structure']))
        return cases

    def assert_targets(self, context, call, names):
        reference = next(r for r in context['references'] if r['call'] == call)
        self.assertEqual([d['name'] for d in reference['candidates']], names)
        self.assertFalse(reference['callee_execution_proven'])
        return reference

    def test_enclosing_parameter_does_not_link_module_function(self):
        code = ('def helper(): return 1\ndef outer(helper):\n'
                '    def use(): return helper()\n    return use\n')
        outer = next(t for t in symtable.symtable(code, 'module.py', 'exec').get_children()
                     if t.get_name() == 'outer')
        self.assertTrue(outer.lookup('helper').is_parameter())
        self.assertTrue(outer.get_children()[0].lookup('helper').is_free())
        for context, _ in self.contexts(code, 'outer.use'):
            self.assert_targets(context, 'helper', [])

    def test_enclosing_assignment_does_not_link_module_function(self):
        code = ('def helper(): return 1\ndef outer(value):\n    helper = value\n'
                '    def use(): return helper()\n    return use\n')
        for context, _ in self.contexts(code, 'outer.use'):
            self.assert_targets(context, 'helper', [])

    def test_global_declaration_selects_module_function(self):
        code = ('def helper(): return 1\ndef outer():\n    def helper(): return 2\n'
                '    def use():\n        global helper\n        return helper()\n    return use\n')
        outer = next(t for t in symtable.symtable(code, 'module.py', 'exec').get_children()
                     if t.get_name() == 'outer')
        use = next(t for t in outer.get_children() if t.get_name() == 'use')
        self.assertTrue(use.lookup('helper').is_global())
        for context, _ in self.contexts(code, 'outer.use'):
            self.assert_targets(context, 'helper', ['helper'])

    def test_enclosing_global_blocks_still_outer_function(self):
        code = ('def helper(): return 1\ndef outer():\n    def helper(): return 2\n'
                '    def middle():\n        global helper\n'
                '        def use(): return helper()\n        return use\n    return middle\n')
        for context, _ in self.contexts(code, 'outer.middle.use'):
            self.assert_targets(context, 'helper', ['helper'])

    def test_nonlocal_parameter_does_not_link_module_function(self):
        code = ('def helper(): return 1\ndef outer(helper):\n    def use():\n'
                '        nonlocal helper\n        return helper()\n    return use\n')
        for context, _ in self.contexts(code, 'outer.use'):
            self.assert_targets(context, 'helper', [])

    def test_nonlocal_function_keeps_enclosing_definition(self):
        code = ('def helper(): return 1\ndef outer():\n    def helper(): return 2\n'
                '    def use():\n        nonlocal helper\n        return helper()\n    return use\n')
        for context, _ in self.contexts(code, 'outer.use'):
            self.assert_targets(context, 'helper', ['outer.helper'])

    def test_missing_nonlocal_is_unresolved_even_when_ast_accepts_it(self):
        code = 'def helper(): return 1\ndef use():\n    nonlocal helper\n    return helper()\n'
        for context, _ in self.contexts(code, 'use'):
            reference = self.assert_targets(context, 'helper', [])
            self.assertEqual(reference['status'], 'NONLOCAL_BINDING_NOT_CAPTURED')

    def test_reassigned_nested_definition_is_ambiguous(self):
        code = ('def use(value):\n    def helper(): return 1\n'
                '    helper = value\n    return helper()\n')
        for context, _ in self.contexts(code, 'use'):
            reference = self.assert_targets(context, 'helper', [])
            self.assertEqual(reference['status'], 'AMBIGUOUS_OR_REBOUND_BINDING')

    def test_reassigned_module_definition_is_ambiguous(self):
        code = 'def helper(): return 1\nhelper = replacement\ndef use(): return helper()\n'
        for context, _ in self.contexts(code, 'use'):
            self.assert_targets(context, 'helper', [])

    def test_multiple_definition_bindings_remain_ambiguous(self):
        code = 'def helper(): return 1\ndef helper(): return 2\ndef use(): return helper()\n'
        for context, _ in self.contexts(code, 'use'):
            self.assert_targets(context, 'helper', [])

    def test_positive_module_and_closure_definitions_are_preserved(self):
        fixtures = [
            ('def helper(): return 1\ndef use(): return helper()\n', 'use', 'helper'),
            ('def outer():\n    def helper(): return 1\n    def use(): return helper()\n'
             '    return use\n', 'outer.use', 'outer.helper'),
            ('def use():\n    def helper(): return 1\n    return helper()\n', 'use', 'use.helper'),
        ]
        for code, name, expected in fixtures:
            for context, _ in self.contexts(code, name):
                self.assert_targets(context, 'helper', [expected])

    def test_decorated_definition_matches_definition_line(self):
        code = '@decorate\ndef helper(): return 1\ndef use(): return helper()\n'
        for context, _ in self.contexts(code, 'use'):
            reference = self.assert_targets(context, 'helper', ['helper'])
            self.assertEqual(reference['candidates'][0]['start_line'], 1)

    def test_local_function_can_shadow_unrelated_module_import(self):
        code = ('from external import helper\ndef outer():\n    def helper(): return 1\n'
                '    def use(): return helper()\n    return use\n')
        for context, meta in self.contexts(code, 'outer.use'):
            self.assert_targets(context, 'helper', ['outer.helper'])
            self.assertEqual(meta['declared_api_leads'], [])

    def test_enclosing_import_is_used_instead_of_module_alias(self):
        code = ('from wrong import helper\ndef outer():\n    from actual import helper\n'
                '    def use(): return helper()\n    return use\n')
        for _, meta in self.contexts(code, 'outer.use'):
            self.assertEqual([r['name'] for r in meta['declared_api_leads']], ['actual.helper'])

    def test_enclosing_parameter_withholds_imported_api_lead(self):
        code = ('from external import helper\ndef outer(helper):\n'
                '    def use(): return helper()\n    return use\n')
        for context, meta in self.contexts(code, 'outer.use'):
            self.assert_targets(context, 'helper', [])
            self.assertEqual(meta['declared_api_leads'], [])

    def test_exception_target_withholds_imported_api_lead(self):
        code = ('from external import helper\ndef use():\n    try: pass\n'
                '    except Exception as helper:\n        return helper()\n')
        for context, meta in self.contexts(code, 'use'):
            self.assert_targets(context, 'helper', [])
            self.assertEqual(meta['declared_api_leads'], [])

    def test_pattern_capture_withholds_imported_api_lead(self):
        code = ('from external import helper\ndef use(value):\n    match value:\n'
                '        case {"key": helper}:\n            return helper()\n')
        for context, meta in self.contexts(code, 'use'):
            self.assert_targets(context, 'helper', [])
            self.assertEqual(meta['declared_api_leads'], [])

    def test_same_line_comprehension_call_does_not_borrow_neighbor_binding(self):
        code = ('def helper(): return 1\ndef use(values):\n'
                '    return helper(), [helper() for helper in values]\n')
        for context, _ in self.contexts(code, 'use'):
            calls = [r for r in context['references'] if r['call'] == 'helper']
            self.assertEqual([r['name'] for r in calls[0]['candidates']], ['helper'])
            self.assertEqual(calls[1]['candidates'], [])
            self.assertEqual(calls[1]['status'], 'COMPREHENSION_SCOPE_UNRESOLVED')

    def test_call_root_after_read_budget_stays_unresolved(self):
        code = ('def helper(): return 1\ndef use(value):\n' +
                '    value\n' * 130 + '    return helper()\n')
        for context, _ in self.contexts(code, 'use'):
            reference = self.assert_targets(context, 'helper', [])
            self.assertEqual(reference['status'], 'BINDING_CAPTURE_TRUNCATED')

    def test_call_root_cannot_infer_through_truncated_enclosing_bindings(self):
        code = ('def helper(): return 1\ndef outer(value):\n' +
                ''.join('    item_%d = 0\n' % i for i in range(130)) +
                '    helper = value\n    def use(): return helper()\n    return use\n')
        for context, _ in self.contexts(code, 'outer.use'):
            reference = self.assert_targets(context, 'helper', [])
            self.assertEqual(reference['status'], 'BINDING_CAPTURE_TRUNCATED')

    def test_wildcard_import_withholds_ordinary_function_candidate(self):
        code = 'from external import *\ndef helper(): return 1\ndef use(): return helper()\n'
        for context, _ in self.contexts(code, 'use'):
            reference = self.assert_targets(context, 'helper', [])
            self.assertEqual(reference['status'], 'WILDCARD_IMPORT_BINDING_UNRESOLVED')

    def test_reassigned_self_does_not_link_method(self):
        code = ('class Owner:\n    def helper(self): return 1\n'
                '    def use(self, value):\n        self = value\n        return self.helper()\n')
        for context, _ in self.contexts(code, 'Owner.use'):
            self.assert_targets(context, 'self.helper', [])

    def test_existing_self_and_super_candidates_remain_available(self):
        code = ('class Base:\n    def helper(self): return 1\nclass Owner(Base):\n'
                '    def helper(self): return super().helper()\n'
                '    def use(self): return self.helper()\n')
        for context, _ in self.contexts(code, 'Owner.use'):
            self.assert_targets(context, 'self.helper', ['Owner.helper'])
        for context, _ in self.contexts(code, 'Owner.helper'):
            self.assert_targets(context, 'super().helper', ['Base.helper'])

    def test_legacy_call_positions_request_reindex_without_mutation(self):
        report = report_from([('module.py', 'def helper(): return 1\ndef use(): return helper()\n')])
        definition = next(d for d in report['sources'][0]['segments'][0]['definitions'] if d['name'] == 'use')
        for call in definition['structure']['calls']:
            call.pop('root_position', None)
        before = copy.deepcopy(report)
        context = corpus.dependency_context(report, definition['definition_id'])
        reference = self.assert_targets(context, 'helper', [])
        self.assertEqual(reference['status'], 'CALL_ROOT_BINDING_NOT_CAPTURED_REINDEX_REQUIRED')
        self.assertEqual(report, before)

    def test_global_writes_do_not_reuse_original_module_callable(self):
        for effect in ('helper = other', 'del helper', 'import external as helper',
                       'from external import helper'):
            code = ('def helper(): return 1\ndef use(other):\n    global helper\n'
                    '    ' + effect + '\n    return helper()\n')
            for context, meta in self.contexts(code, 'use'):
                reference = self.assert_targets(context, 'helper', [])
                self.assertEqual(reference['status'], 'REDIRECTED_BINDING_EFFECT_UNRESOLVED')
                self.assertEqual(meta['declared_api_leads'], [])

    def test_nonlocal_writes_do_not_reuse_original_enclosing_callable(self):
        for effect in ('helper = other', 'del helper', 'import external as helper',
                       'from external import helper'):
            code = ('def outer():\n    def helper(): return 1\n    def use(other):\n'
                    '        nonlocal helper\n        ' + effect + '\n'
                    '        return helper()\n    return use\n')
            for context, meta in self.contexts(code, 'outer.use'):
                reference = self.assert_targets(context, 'helper', [])
                self.assertEqual(reference['status'], 'REDIRECTED_BINDING_EFFECT_UNRESOLVED')
                self.assertEqual(meta['declared_api_leads'], [])

    def test_enclosing_global_writes_withhold_original_module_callable(self):
        for effect in ('helper = other', 'del helper', 'import external as helper',
                       'from external import helper'):
            code = ('def helper(): return 1\ndef outer(other):\n    global helper\n'
                    '    ' + effect + '\n    def use(): return helper()\n    return use\n')
            for context, meta in self.contexts(code, 'outer.use'):
                reference = self.assert_targets(context, 'helper', [])
                self.assertEqual(reference['status'], 'REDIRECTED_BINDING_EFFECT_UNRESOLVED')
                self.assertEqual(meta['declared_api_leads'], [])

    def test_enclosing_nonlocal_writes_withhold_original_callable(self):
        for effect in ('helper = other', 'del helper', 'import external as helper',
                       'from external import helper'):
            code = ('def outer(other):\n    def helper(): return 1\n    def middle():\n'
                    '        nonlocal helper\n        ' + effect + '\n'
                    '        def use(): return helper()\n        return use\n    return middle\n')
            for context, meta in self.contexts(code, 'outer.middle.use'):
                reference = self.assert_targets(context, 'helper', [])
                self.assertEqual(reference['status'], 'REDIRECTED_BINDING_EFFECT_UNRESOLVED')
                self.assertEqual(meta['declared_api_leads'], [])

    def test_nearer_binding_stops_before_unrelated_enclosing_redirect(self):
        prefix = 'def helper(): return 1\ndef outer(other):\n    global helper\n    helper = other\n'
        code = prefix + ('    def use():\n        def helper(): return 2\n'
                         '        return helper()\n    return use\n')
        for context, _ in self.contexts(code, 'outer.use'):
            self.assert_targets(context, 'helper', ['outer.use.helper'])
        code = prefix + '    def use(helper): return helper()\n    return use\n'
        for context, _ in self.contexts(code, 'outer.use'):
            self.assert_targets(context, 'helper', [])
            binding = next(r for r in context['binding_context']['references'] if r['name'] == 'helper')
            self.assertEqual(binding['status'], 'LEXICAL_BINDING_CANDIDATE')
            self.assertEqual(binding['candidates'][0]['kind'], 'parameter')

    def test_redirect_cannot_bypass_own_truncated_binding_capture(self):
        assignments = ''.join('    item_%d = 0\n' % i for i in range(130))
        code = ('def helper(): return 1\ndef use(other):\n    global helper\n' +
                assignments + '    helper = other\n    return helper()\n')
        for context, _ in self.contexts(code, 'use'):
            reference = self.assert_targets(context, 'helper', [])
            self.assertEqual(reference['status'], 'BINDING_CAPTURE_TRUNCATED')


if __name__ == '__main__':
    unittest.main()
