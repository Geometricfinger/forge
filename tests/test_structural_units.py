"""Complete-unit boundaries, shared context and honest resource omissions.

All source is synthetic text. The independent oracle parses text with stdlib
AST; it never compiles, imports or executes an inspected program.
"""
import ast
import copy
import unittest
from unittest.mock import patch

from forge_core.common import Blocked, sha
from forge_core.mixed_intake import inspect_container
from forge_core.structural_units import admitted_trees, plan


def padded(code):
    return code + '#' + '.' * max(0, 250_001 - len(code.encode('utf-8')) - 2) + '\n'


class StructuralUnitTests(unittest.TestCase):
    def segment(self, code, **kwargs):
        result = inspect_container(code.encode('utf-8'), 'module.py', **kwargs)
        self.assertEqual(len(result['segments']), 1, result['inventory'])
        return result, result['segments'][0]

    def test_compound_clauses_and_semicolons_are_indivisible(self):
        samples = [
            'if ok:\n x=1\nelif maybe:\n x=2\nelse:\n x=3\n',
            'try:\n call()\nexcept ValueError:\n recover()\nelse:\n done()\nfinally:\n clean()\n',
            'for x in xs:\n use(x)\nelse:\n end()\n',
            'while ok:\n step()\nelse:\n stopped()\n',
            'match x:\n case 1:\n  y=2\n case _:\n  y=3\n',
            'try:\n use()\nexcept* ValueError:\n recover()\n',
            'x=1; y=2\n',
        ]
        for code in samples:
            with self.subTest(code=code):
                planned = plan(code, 60_000)
                self.assertEqual(planned['metadata']['units_total'], 1)
                trees = list(admitted_trees(code, planned))
                self.assertEqual(ast.dump(trees[0], include_attributes=True),
                                 ast.dump(ast.parse(code), include_attributes=True))

    def test_token_boundaries_preserve_ast_and_original_newlines(self):
        samples = [
            '@(\n decorate\n)\n@other\nasync def a():\n return 1\n\ndef b(): return 2\n',
            '# leading\nx=(\n 1,\n 2\n)\n# gap\ndef f():\n s="""\nclass X:\n pass\n"""\n return s\n# trailing\n',
            'value="é"\n@dec("λ")\ndef α(β="é"):\n return β\ndef last(): return "雪"\n',
            '@dec \\\n (thing)\ndef f():\n return 1\nx=1+\\\n 2\n',
            'if flag:\n class C:\n  def f(self):\n   def g():\n    return x\n   return g()\ndef other():\n return 1\n',
        ]
        for code in samples:
            for newline in ['\n', '\r\n', '\r']:
                actual = code.replace('\n', newline)
                with self.subTest(code=code, newline=repr(newline)):
                    planned = plan(actual, 60_000)
                    body = [node for tree in admitted_trees(actual, planned) for node in tree.body]
                    rebuilt = ast.Module(body=body, type_ignores=[])
                    self.assertEqual(ast.dump(rebuilt, include_attributes=True),
                                     ast.dump(ast.parse(actual), include_attributes=True))
                    raw = actual.encode('utf-8')
                    restored = b''.join(raw[u['byte_start']:u['byte_end']]
                                        for u in planned['metadata']['units'])
                    self.assertEqual(restored, raw)
                    for unit in planned['metadata']['units']:
                        selected = raw[unit['byte_start']:unit['byte_end']]
                        self.assertEqual(unit['sha256'], sha(selected))
                        self.assertEqual(unit['bytes'], len(selected))

    def test_complete_units_share_original_module_and_declaration_context(self):
        code = '''from __future__ import annotations
import hashlib as digest
SCALE = 2
def outer(value):
    def inner(arg=(local := value)):
        return digest.sha256(arg).digest(), local
    return inner(value), local
class Base:
    def run(self, value):
        return value * SCALE
class Child(Base):
    def run(self, value):
        return super().run(value)
def later(value: Base = SCALE):
    return outer(value)
'''
        _, ordinary = self.segment(code)
        _, partitioned = self.segment(padded(code))
        self.assertEqual(ordinary['module_structure'], partitioned['module_structure'])
        self.assertEqual(ordinary['imports'], partitioned['imports'])
        self.assertEqual(ordinary['script_present'], partitioned['script_present'])
        for old, new in zip(ordinary['definitions'], partitioned['definitions']):
            for key in ('name', 'start_line', 'definition_line', 'end_line', 'parameters', 'structure'):
                self.assertEqual(old[key], new[key], (old['name'], key))
        self.assertTrue(partitioned['structural_partition']['module_context_complete'])
        inner = next(d for d in partitioned['definitions'] if d['name'] == 'outer.inner')
        self.assertTrue(any(r['name'] == 'hashlib.sha256' for r in inner['structure']['declared_api_leads']))

    def test_later_binding_withholds_import_leads_across_unit_boundaries(self):
        code = 'import hashlib as digest\ndef use(value):\n return digest.sha256(value)\ndigest = replacement\n'
        _, segment = self.segment(padded(code))
        self.assertEqual(segment['definitions'][0]['structure']['declared_api_leads'], [])
        self.assertIn('digest', segment['module_structure']['shadowed_roots'])

    def test_large_path_never_parses_the_whole_module(self):
        code = padded('def first():\n return 1\ndef second():\n return 2\n')
        code += 'def last():\n return 3\n'
        original = ast.parse
        inputs = []
        def observe(source, *args, **kwargs):
            inputs.append(source)
            return original(source, *args, **kwargs)
        with patch('forge_core.structural_units.ast.parse', side_effect=observe):
            _, segment = self.segment(code)
        self.assertEqual([d['name'] for d in segment['definitions']], ['first', 'second', 'last'])
        self.assertNotIn(code, inputs)
        self.assertTrue(inputs)
        self.assertTrue(all(len(source.encode('utf-8')) < len(code.encode('utf-8')) for source in inputs))

    def test_ordinary_admitted_module_keeps_single_parse_path(self):
        with patch('forge_core.structural_units.plan', side_effect=AssertionError('Unexpected partition')):
            _, segment = self.segment('def value():\n return 7\n')
        self.assertNotIn('structural_partition', segment)

    def test_oversized_atomic_units_are_omitted_whole(self):
        bodies = ['def giant():\n', 'class Giant:\n', 'if active:\n', 'try:\n']
        for header in bodies:
            body = header + ''.join(' value_%d = %d\n' % (i, i) for i in range(30))
            body += ' def hidden():\n  return 1\n'
            if header == 'try:\n':
                body += 'except Exception:\n pass\n'
            code = body + 'def neighbor():\n return 2\n'
            with self.subTest(header=header):
                result, segment = self.segment(code, limits={'max_nodes': 25})
                self.assertEqual([d['name'] for d in segment['definitions']], ['neighbor'])
                self.assertFalse(segment['module_structure']['structural_context_complete'])
                meta = segment['structural_partition']
                self.assertEqual((meta['units_analyzed'], meta['units_omitted']), (1, 1))
                self.assertTrue(meta['syntax_validation_complete'])
                omitted = [u for u in meta['units'] if u['status'] == 'AST_UNIT_LIMIT']
                self.assertEqual(len(omitted), 1)
                self.assertEqual(code.encode()[omitted[0]['byte_start']:omitted[0]['byte_end']], body.encode())
                self.assertEqual(omitted[0]['node_count_lower_bound'], 26)
                self.assertIn('AST_UNIT_LIMIT', {r['status'] for r in result['gaps']})

    def test_missing_unit_poisoning_reaches_local_and_declaration_bindings(self):
        code = ('import hashlib as digest\n' +
                'if conditional:\n' + ''.join(' digest = value_%d\n' % i for i in range(30)) +
                'def use(value=digest):\n local = value\n return digest.sha256(local)\n')
        _, segment = self.segment(code, limits={'max_nodes': 35})
        detail = segment['definitions'][0]['structure']
        self.assertEqual(detail['declared_api_leads'], [])
        refs = detail['binding_context']['references']
        self.assertTrue(refs)
        self.assertTrue(all(not ref['candidates'] for ref in refs))
        self.assertIn('MODULE_STRUCTURE_INCOMPLETE', detail['binding_context']['scope_omissions'])
        self.assertIn('MODULE_STRUCTURE_INCOMPLETE', detail['declaration_context']['scope_omissions'])

    def test_envelope_limit_preserves_functions_but_marks_shared_context_unknown(self):
        code = ''.join('value_%d = %d\n' % (i, i) for i in range(20))
        code += 'def use():\n return value_0\n'
        result, segment = self.segment(code, limits={'max_nodes': 20})
        self.assertEqual([d['name'] for d in segment['definitions']], ['use'])
        meta = segment['structural_partition']
        self.assertEqual(meta['units_omitted'], 0)
        self.assertTrue(meta['envelope_limited'])
        self.assertFalse(meta['module_context_complete'])
        self.assertIsNone(meta['envelope_node_count'])
        refs = segment['definitions'][0]['structure']['binding_context']['references']
        self.assertEqual(refs[0]['candidates'], [])
        self.assertIn('MODULE_CONTEXT_LIMIT', {r['status'] for r in result['gaps']})

    def test_unit_count_budget_records_one_bounded_unvalidated_tail(self):
        code = ''.join('def f%d():\n return %d\n' % (i, i) for i in range(5))
        planned = plan(code, 60_000, max_units=2)
        meta = planned['metadata']
        self.assertEqual((meta['units_total'], meta['units_analyzed'], meta['units_omitted']), (5, 2, 3))
        self.assertEqual(len(meta['units']), 3)
        self.assertEqual(meta['units'][-1]['status'], 'STRUCTURAL_UNIT_COUNT_LIMIT')
        self.assertEqual(meta['units'][-1]['unit_count'], 3)
        self.assertFalse(meta['syntax_validation_complete'])
        self.assertFalse(meta['module_context_complete'])
        self.assertEqual([n.name for t in admitted_trees(code, planned) for n in t.body], ['f0', 'f1'])
        self.assertEqual(b''.join(code.encode()[u['byte_start']:u['byte_end']] for u in meta['units']), code.encode())

    def test_known_syntax_or_lexical_errors_refuse_all_prefix_definitions(self):
        prefix = 'def good():\n return 1\n'
        for suffix in ['def broken(:\n', 'invalid = ;\n', 'text = """unterminated\n', 'else:\n pass\n']:
            for code in [prefix + suffix, padded(prefix) + suffix]:
                with self.subTest(suffix=suffix, large=len(code) > 250_000):
                    result = inspect_container(code.encode(), 'module.py')
                    self.assertFalse(result['segments'])
                    self.assertIn('PYTHON_SYNTAX_ERROR', {g['status'] for g in result['gaps']})

    def test_second_pass_rejects_changed_unit_and_record_identity(self):
        code = 'def first():\n return 1\ndef last():\n return 2\n'
        planned = plan(code, 60_000)
        with self.assertRaisesRegex(Blocked, '^STRUCTURAL_UNIT_BINDING$'):
            list(admitted_trees(code.replace('return 1', 'return 3'), planned))
        altered = copy.deepcopy(planned)
        altered['units'][0]['node_count'] += 1
        with self.assertRaisesRegex(Blocked, '^STRUCTURAL_UNIT_BINDING$'):
            list(admitted_trees(code, altered))

    def test_plain_text_classification_uses_bounded_partition_path(self):
        code = padded('def useful():\n return 1\n') + 'def second():\n return 2\n'
        original = ast.parse
        inputs = []
        def observe(source, *args, **kwargs):
            inputs.append(source)
            return original(source, *args, **kwargs)
        with patch('forge_core.structural_units.ast.parse', side_effect=observe):
            result = inspect_container(code.encode(), 'module.txt')
        self.assertEqual(len(result['segments']), 1)
        self.assertNotIn(code, inputs)
        prose = inspect_container(b'This prose is not Python source.', 'notes.txt')
        self.assertFalse(prose['segments'])
        self.assertIn('CONTEXT_ONLY', {r['status'] for r in prose['inventory']})

    def test_invalid_internal_budget_types_are_rejected(self):
        for value in [False, 0, -1, '10']:
            with self.subTest(value=value):
                with self.assertRaises(Blocked):
                    plan('pass\n', value)
                with self.assertRaises(Blocked):
                    plan('pass\n', 100, max_units=value)
        with self.assertRaises(Blocked):
            plan('pass\n', 100, max_units=2049)


if __name__ == '__main__':
    unittest.main()
