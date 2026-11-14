"""Synthetic scope/declaration leads never evaluate inspected expressions."""
import ast
import json
from types import SimpleNamespace
import unittest

from forge_core import corpus, structure
from test_research_retrieval import report_from


def described(code, name):
    module, definitions = structure.describe(ast.parse(code))
    return module, next(meta for (qualified, _), meta in definitions.items() if qualified == name)


def read(context, name):
    return next(row for row in context['references'] if row['name'] == name)


class DeclarationContextTests(unittest.TestCase):
    def test_decorator_defaults_and_annotations_link_declared_imports(self):
        code = ('from helpers import decorate, make, Item, Result\n'
                '@decorate\ndef use(value: Item = make(), *, backup: Item = make()) -> Result:\n'
                '    return value\n')
        _, meta = described(code, 'use')
        expressions = meta['declaration_context']['expressions']
        self.assertEqual([row['kind'] for row in expressions],
                         ['decorator', 'positional_default', 'keyword_default',
                          'parameter_annotation', 'parameter_annotation', 'return_annotation'])
        for expression in expressions:
            for reference in expression['references']:
                self.assertEqual(reference['candidates'][0]['module'], 'helpers')
                self.assertFalse(reference['candidates'][0]['binding_effect_proven'])
        self.assertFalse(meta['declaration_context']['dependency_closure_complete'])

    def test_default_uses_outer_binding_and_body_uses_parameter(self):
        _, meta = described('value = 3\ndef use(value=value):\n    return value\n', 'use')
        default = meta['declaration_context']['expressions'][0]['references'][0]
        body = read(meta['binding_context'], 'value')
        self.assertEqual(default['candidates'][0]['scope_kind'], 'module')
        self.assertEqual(body['candidates'][0]['kind'], 'parameter')

    def test_class_declaration_preserves_bases_metaclass_and_keywords(self):
        module, _ = described('from helpers import Base, Meta, options\n'
                              'class C(Base, metaclass=Meta, **options):\n'
                              '    def use(self): return self\n', 'C.use')
        context = module['class_definitions'][0]['declaration_context']
        self.assertEqual([x['kind'] for x in context['expressions']],
                         ['class_base', 'metaclass', 'class_keyword'])
        self.assertEqual([x['references'][0]['name'] for x in context['expressions']],
                         ['Base', 'Meta', 'options'])

    def test_class_default_and_method_body_use_different_namespaces(self):
        _, meta = described('value = 1\nclass C:\n    value = 2\n'
                            '    def use(self, fallback=value):\n        return value\n', 'C.use')
        default = meta['declaration_context']['expressions'][0]['references'][0]
        body = read(meta['binding_context'], 'value')
        self.assertEqual(default['candidates'][0]['scope'], 'C')
        self.assertEqual(body['candidates'][0]['scope'], '<module>')

    def test_nested_default_does_not_capture_grandparent_class_namespace(self):
        _, meta = described('class C:\n    value = 2\n    def outer(self):\n'
                            '        def inner(x=value): return x\n        return inner\n', 'C.outer.inner')
        reference = meta['declaration_context']['expressions'][0]['references'][0]
        self.assertEqual(reference['candidates'], [])

    def test_closure_read_links_enclosing_function(self):
        _, meta = described('value = 1\ndef outer(value):\n'
                            '    def inner(): return value\n    return inner\n', 'outer.inner')
        reference = read(meta['binding_context'], 'value')
        self.assertEqual(reference['candidates'][0]['scope'], 'outer')
        self.assertEqual(reference['candidates'][0]['kind'], 'parameter')

    def test_global_read_does_not_capture_enclosing_local(self):
        _, meta = described('value = 1\ndef outer(value):\n'
                            '    def inner():\n        global value\n        return value\n'
                            '    return inner\n', 'outer.inner')
        self.assertEqual(meta['binding_context']['global_names'], ['value'])
        self.assertEqual(read(meta['binding_context'], 'value')['candidates'][0]['scope'], '<module>')

    def test_nonlocal_read_links_function_but_never_module(self):
        _, meta = described('value = 1\ndef outer(value):\n'
                            '    def inner():\n        nonlocal value\n        return value\n'
                            '    return inner\n', 'outer.inner')
        self.assertEqual(meta['binding_context']['nonlocal_names'], ['value'])
        self.assertEqual(read(meta['binding_context'], 'value')['candidates'][0]['scope'], 'outer')
        # ast.parse accepts some binding-invalid programs; they remain unresolved.
        _, invalid = described('value = 1\ndef use():\n    nonlocal value\n    return value\n', 'use')
        self.assertEqual(read(invalid['binding_context'], 'value')['candidates'], [])

    def test_enclosing_global_blocks_still_outer_closure(self):
        code = ('value = 1\ndef outer(value):\n    def middle():\n        global value\n'
                '        def inner(): return value\n        return inner\n    return middle\n')
        _, meta = described(code, 'outer.middle.inner')
        self.assertEqual(read(meta['binding_context'], 'value')['candidates'][0]['scope'], '<module>')
        _, missing = described(code.replace('value = 1\n', ''), 'outer.middle.inner')
        self.assertEqual(read(missing['binding_context'], 'value')['candidates'], [])

    def test_rebound_import_remains_ambiguous(self):
        _, meta = described('import helpers as thing\nthing = None\ndef use(): return thing\n', 'use')
        reference = read(meta['binding_context'], 'thing')
        self.assertEqual(reference['status'], 'AMBIGUOUS_OR_REBOUND_BINDING')
        self.assertEqual({x['kind'] for x in reference['candidates']}, {'import', 'assignment'})

    def test_later_import_does_not_become_resolved_default(self):
        _, meta = described('def use(x=make()): return x\nfrom helpers import make\n', 'use')
        reference = meta['declaration_context']['expressions'][0]['references'][0]
        self.assertEqual(reference['status'], 'DECLARATION_BINDING_ORDER_UNRESOLVED')

    def test_comprehension_reads_are_explicitly_unresolved(self):
        _, meta = described('items = [1]\nx = 9\ndef use(): return [x for x in items]\n', 'use')
        context = meta['binding_context']
        self.assertIn('COMPREHENSION_BINDINGS_UNRESOLVED', context['scope_omissions'])
        self.assertTrue(context['references'])
        for reference in context['references']:
            self.assertEqual(reference['status'], 'COMPREHENSION_SCOPE_UNRESOLVED')
            self.assertEqual(reference['candidates'], [])

    def test_lambda_default_capture_is_separate_from_omitted_lambda_body(self):
        _, meta = described('seed = 1\nhidden = 2\n'
                            'def use(callback=lambda value=seed: hidden): return callback\n', 'use')
        context = meta['declaration_context']
        self.assertIn('LAMBDA_BODY_NOT_CAPTURED', context['scope_omissions'])
        self.assertEqual([x['name'] for x in context['expressions'][0]['references']], ['seed'])

    def test_literal_annotation_and_default_bodies_are_not_retained(self):
        marker = 'SYNTHETIC_PRIVATE_LITERAL_7d38'
        _, meta = described('def use(x: "' + marker + '" = "' + marker + '"):\n    return x\n', 'use')
        context = meta['declaration_context']
        self.assertIn('STRING_ANNOTATION_CONTENT_NOT_RESOLVED', context['scope_omissions'])
        self.assertNotIn(marker, json.dumps(context))
        self.assertNotIn(marker, json.dumps(meta['binding_context']))

    def test_context_bounds_do_not_infer_through_missing_closer_binding(self):
        code = ('wanted = 1\ndef outer():\n' +
                ''.join('    local_' + str(i) + ' = 0\n' for i in range(130)) +
                '    wanted = 2\n    def inner(): return wanted\n    return inner\n')
        _, meta = described(code, 'outer.inner')
        reference = read(meta['binding_context'], 'wanted')
        self.assertEqual(reference['status'], 'BINDING_CAPTURE_TRUNCATED')
        self.assertEqual(reference['candidates'], [])
        self.assertTrue(meta['binding_context']['truncated'])

    def test_many_expression_references_have_one_total_budget(self):
        params = ', '.join('x' + str(i) + '=a+b+c+d' for i in range(60))
        _, meta = described('def use(' + params + '): return 0\n', 'use')
        context = meta['declaration_context']
        self.assertLessEqual(sum(len(x['references']) for x in context['expressions']), structure.MAX_CALLS)
        self.assertTrue(context['truncated'])

    def test_read_budget_does_not_discard_complete_parameter_binding(self):
        _, meta = described('def use(value):\n' + '    value\n' * 140, 'use')
        context = meta['binding_context']
        self.assertTrue(context['truncated'])
        self.assertEqual(len(context['references']), structure.MAX_CALLS)
        self.assertEqual(context['references'][0]['candidates'][0]['kind'], 'parameter')

    def test_comprehension_assignment_is_not_replaced_by_global_candidate(self):
        _, meta = described('value = 9\ndef use(items):\n'
                            '    [(value := item) for item in items]\n    return value\n', 'use')
        reference = next(row for row in reversed(meta['binding_context']['references']) if row['name'] == 'value')
        self.assertEqual(reference['status'], 'COMPREHENSION_BINDING_EFFECT_UNRESOLVED')
        self.assertEqual(reference['candidates'][0]['scope'], 'use')

    def test_wildcard_import_does_not_appear_as_resolved_binding(self):
        _, meta = described('from helpers import *\nvalue = 1\ndef use(): return value\n', 'use')
        reference = read(meta['binding_context'], 'value')
        self.assertEqual(reference['status'], 'WILDCARD_IMPORT_BINDING_UNRESOLVED')
        self.assertEqual(reference['candidates'], [])

    def test_type_parameter_scope_is_not_confused_with_module_name(self):
        # Exercise the generic-AST metadata contract on every supported runtime;
        # Python 3.11 cannot parse PEP 695 syntax itself.
        tree = ast.parse('T = 1\nclass C:\n    def use(self, x: T): return T\n')
        tree.body[1].type_params = [SimpleNamespace(name='T', bound=None, default_value=None)]
        _, definitions = structure.describe(tree)
        meta = next(value for (name, _), value in definitions.items() if name == 'C.use')
        annotation = meta['declaration_context']['expressions'][0]['references'][0]
        self.assertEqual(annotation['status'], 'TYPE_PARAMETER_SCOPE_UNRESOLVED')
        self.assertEqual(read(meta['binding_context'], 'T')['status'], 'TYPE_PARAMETER_SCOPE_UNRESOLVED')

    def test_context_exposes_explicit_segment_offsets_and_legacy_call_edges(self):
        source = ('intro\n```python\nvalue = 1\ndef helper(): return value\n'
                  'def use(x=value): return helper()\n```\n')
        report = report_from([('notes.md', source)])
        row = next(x for x in corpus.rows(report) if x['name'] == 'use')
        context = corpus.dependency_context(report, row['definition_id'])
        reference = context['declaration_context']['expressions'][0]['references'][0]
        self.assertEqual(context['additional_context_line_basis'], 'SEGMENT_LOCAL')
        self.assertEqual(context['additional_context_member_start_line'] + reference['line'] - 1, 5)
        self.assertEqual(context['references'][0]['candidates'][0]['name'], 'helper')
        self.assertFalse(context['dependency_closure_complete'])

    def test_old_structure_reports_missing_additive_context(self):
        report = report_from([('x.py', 'def use(): return 1\n')])
        definition = report['sources'][0]['segments'][0]['definitions'][0]
        definition['structure'].pop('declaration_context')
        definition['structure'].pop('binding_context')
        context = corpus.dependency_context(report, definition['definition_id'])
        self.assertEqual(context['declaration_context']['status'], 'NOT_CAPTURED_REINDEX_REQUIRED')
        self.assertEqual(context['binding_context']['status'], 'NOT_CAPTURED_REINDEX_REQUIRED')

    def test_class_declarations_are_available_without_mutating_report(self):
        report = report_from([('x.py', 'from helpers import Base\nclass C(Base):\n'
                              '    def use(self): return self\n')])
        row = next(corpus.rows(report)); before = json.dumps(report, sort_keys=True)
        context = corpus.dependency_context(report, row['definition_id'])
        self.assertEqual(context['class_declaration_context']['expressions'][0]['references'][0]['name'], 'Base')
        context['class_declaration_context']['expressions'].clear()
        context['binding_context']['references'].clear()
        self.assertEqual(json.dumps(report, sort_keys=True), before)

    def test_exception_and_pattern_binding_reads_are_local_leads(self):
        _, meta = described('def use(value):\n    try: value()\n'
                            '    except RuntimeError as problem:\n        return problem\n'
                            '    match value:\n        case {"x": found}: return found\n', 'use')
        self.assertEqual(read(meta['binding_context'], 'problem')['candidates'][0]['kind'], 'exception_target')
        self.assertEqual(read(meta['binding_context'], 'found')['candidates'][0]['kind'], 'pattern_capture')


if __name__ == '__main__':
    unittest.main()
