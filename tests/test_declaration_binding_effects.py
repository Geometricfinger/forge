"""Declaration-time bindings use static symbol tables as an independent oracle."""
import ast
from types import SimpleNamespace
import symtable
import unittest

from forge_core import corpus, structure
from test_research_retrieval import report_from


def described(code, name='outer'):
    module, definitions = structure.describe(ast.parse(code))
    return module, next(value for (qualified, _), value in definitions.items() if qualified == name)


def last_read(context, name):
    return next(row for row in reversed(context['references']) if row['name'] == name)


class DeclarationBindingEffectTests(unittest.TestCase):
    def test_defaults_decorators_and_class_headers_bind_enclosing_scope(self):
        declarations = (
            '    def inner(arg=(token := value)): return arg\n',
            '    def inner(*, arg=(token := value)): return arg\n',
            '    @(token := value)\n    def inner(): return 1\n',
            '    class Inner((token := value)): pass\n',
            '    class Inner(metaclass=(token := value)): pass\n',
            '    def inner(arg: (token := value)): return arg\n',
            '    def inner() -> (token := value): return 1\n',
        )
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                code = 'token = 99\ndef outer(value):\n' + declaration + '    return token\n'
                table = next(t for t in symtable.symtable(code, 'fixture.py', 'exec').get_children()
                             if t.get_name() == 'outer')
                self.assertTrue(table.lookup('token').is_local())
                self.assertTrue(table.lookup('token').is_assigned())
                _, meta = described(code)
                reference = last_read(meta['binding_context'], 'token')
                self.assertEqual(reference['candidates'][0]['scope'], 'outer')
                self.assertFalse(reference['candidates'][0]['binding_effect_proven'])
                self.assertIn('token', meta['shadowed_roots'])

    def test_nested_body_assignment_is_not_an_enclosing_binding(self):
        code = ('token = 99\ndef outer(value):\n'
                '    def inner():\n        token = value\n        return token\n'
                '    return token\n')
        _, meta = described(code)
        self.assertEqual(last_read(meta['binding_context'], 'token')['candidates'][0]['scope'], '<module>')
        self.assertNotIn('token', meta['shadowed_roots'])

    def test_lambda_body_in_default_is_not_an_enclosing_binding(self):
        code = ('token = 99\ndef outer(value):\n'
                '    def inner(callback=lambda: (token := value)): return callback\n'
                '    return token\n')
        _, meta = described(code)
        self.assertEqual(last_read(meta['binding_context'], 'token')['candidates'][0]['scope'], '<module>')
        self.assertNotIn('token', meta['shadowed_roots'])
        self.assertIn('LAMBDA_BODY_NOT_CAPTURED', meta['binding_context']['scope_omissions'])

    def test_lambda_default_in_declaration_binds_enclosing_scope(self):
        code = ('token = 99\ndef outer(value):\n'
                '    def inner(callback=lambda arg=(token := value): arg): return callback\n'
                '    return token\n')
        table = next(t for t in symtable.symtable(code, 'fixture.py', 'exec').get_children()
                     if t.get_name() == 'outer')
        self.assertTrue(table.lookup('token').is_local())
        _, meta = described(code)
        self.assertEqual(last_read(meta['binding_context'], 'token')['candidates'][0]['scope'], 'outer')
        self.assertIn('token', meta['shadowed_roots'])

    def test_comprehension_walrus_keeps_binding_effect_uncertain(self):
        code = ('token = 99\ndef outer(values):\n'
                '    def inner(arg=[(token := value) for value in values]): return arg\n'
                '    return token\n')
        _, meta = described(code)
        reference = last_read(meta['binding_context'], 'token')
        self.assertEqual(reference['status'], 'COMPREHENSION_BINDING_EFFECT_UNRESOLVED')
        self.assertEqual(reference['candidates'][0]['scope'], 'outer')
        self.assertIn('token', meta['shadowed_roots'])

    def test_global_declaration_remains_module_scope(self):
        code = ('token = 99\ndef outer(value):\n    global token\n'
                '    def inner(arg=(token := value)): return arg\n    return token\n')
        table = next(t for t in symtable.symtable(code, 'fixture.py', 'exec').get_children()
                     if t.get_name() == 'outer')
        self.assertTrue(table.lookup('token').is_global())
        _, meta = described(code)
        self.assertEqual(last_read(meta['binding_context'], 'token')['candidates'][0]['scope'], '<module>')

    def test_nonlocal_declaration_remains_enclosing_function_scope(self):
        code = ('def enclosing(token):\n    def outer(value):\n        nonlocal token\n'
                '        def inner(arg=(token := value)): return arg\n        return token\n'
                '    return outer\n')
        _, meta = described(code, 'enclosing.outer')
        self.assertEqual(last_read(meta['binding_context'], 'token')['candidates'][0]['scope'], 'enclosing')

    def test_class_body_default_binding_does_not_become_method_closure(self):
        code = ('token = 99\nclass Owner:\n'
                '    def inner(arg=(token := 1)): return arg\n    observed = token\n'
                '    def method(self): return token\n')
        module, meta = described(code, 'Owner.method')
        owner = module['class_definitions'][0]
        self.assertEqual(last_read(owner['binding_context'], 'token')['candidates'][0]['scope'], 'Owner')
        self.assertEqual(last_read(meta['binding_context'], 'token')['candidates'][0]['scope'], '<module>')

    def test_postponed_annotations_do_not_add_enclosing_reads_or_writes(self):
        # ast.parse retains annotation syntax even where future-annotation
        # compilation would reject assignment expressions. Never infer effects.
        code = ('from __future__ import annotations\ntoken = 99\ndef outer(value):\n'
                '    def inner(arg: (token := value)): return arg\n    return token\n')
        _, meta = described(code)
        self.assertEqual(last_read(meta['binding_context'], 'token')['candidates'][0]['scope'], '<module>')
        self.assertNotIn('token', meta['shadowed_roots'])
        _, inner = described(code, 'outer.inner')
        self.assertIn('POSTPONED_ANNOTATION_EXPRESSION_NOT_EVALUATED',
                      inner['declaration_context']['scope_omissions'])

    def test_postponed_annotations_still_capture_default_effects(self):
        code = ('from __future__ import annotations\ntoken = 99\ndef outer(value):\n'
                '    def inner(arg: token = (token := value)): return arg\n    return token\n')
        _, meta = described(code)
        self.assertEqual(last_read(meta['binding_context'], 'token')['candidates'][0]['scope'], 'outer')

    def test_generic_declaration_effects_remain_explicitly_uncertain(self):
        # Supported on 3.11 too: an AST metadata contract, not a claim that this
        # generated syntax compiles on all Python versions.
        tree = ast.parse('token = 99\ndef outer(value):\n'
                         '    def inner(arg=(token := value)): return arg\n    return token\n')
        tree.body[1].body[0].type_params = [SimpleNamespace(name='T', bound=None, default_value=None)]
        _, definitions = structure.describe(tree)
        meta = definitions[('outer', 2)]
        reference = last_read(meta['binding_context'], 'token')
        self.assertEqual(reference['status'], 'DECLARATION_BINDING_EFFECT_UNRESOLVED')
        self.assertEqual(reference['candidates'][0]['scope'], 'outer')
        self.assertIn('GENERIC_DECLARATION_SCOPE_NOT_RESOLVED', meta['binding_context']['scope_omissions'])

    def test_default_binding_withholds_false_import_call_lead(self):
        code = ('import helpers as token\ndef outer(value):\n'
                '    def inner(arg=(token := value)): return arg\n    return token()\n')
        module, meta = described(code)
        self.assertEqual(structure.declared_apis(meta, module), [])
        report = report_from([('fixture.py', code)])
        row = next(r for r in corpus.rows(report) if r['name'] == 'outer')
        context = corpus.dependency_context(report, row['definition_id'])
        call = next(r for r in context['references'] if r['call'] == 'token')
        self.assertEqual(call['status'], 'AMBIGUOUS_OR_REBOUND_IMPORT')
        self.assertEqual(call['candidates'], [])

    def test_module_declaration_binding_marks_rebound_import(self):
        code = ('import helpers as token\ndef bind(arg=(token := None)): return arg\n'
                'def outer(): return token()\n')
        module, meta = described(code)
        self.assertIn('token', module['shadowed_roots'])
        self.assertEqual(structure.declared_apis(meta, module), [])

    def test_binding_capture_limit_cannot_infer_through_omitted_default(self):
        code = ('token = 99\ndef outer(value):\n' +
                ''.join('    item_%s = 0\n' % i for i in range(130)) +
                '    def inner(arg=(token := value)): return arg\n    return token\n')
        _, meta = described(code)
        reference = last_read(meta['binding_context'], 'token')
        self.assertEqual(reference['status'], 'BINDING_CAPTURE_TRUNCATED')
        self.assertEqual(reference['candidates'], [])
        self.assertTrue(meta['binding_context']['truncated'])
        # Import-lead shadow guards must still see syntactic writes beyond the
        # bounded detailed binding context, as they do for direct assignments.
        self.assertIn('token', meta['shadowed_roots'])


if __name__ == '__main__':
    unittest.main()
