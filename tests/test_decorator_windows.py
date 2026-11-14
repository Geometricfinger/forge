"""Complete declaration windows follow lexical decorator statements, not AST expressions."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from forge_core import corpus
from forge_core.common import Blocked, canonical, sha
from forge_core.mixed_intake import inspect_container
from test_large_modules import GuardlessWorkbench, RecordingAnalyzer, padded


def definition(data, name='use', filename='module.py'):
    result = inspect_container(data, filename)
    return next(d for s in result['segments'] for d in s['definitions'] if d['name'] == name)


class DecoratorWindowTests(unittest.TestCase):
    def assert_window(self, data, expected, name='use'):
        row = definition(data, name)
        window = row['source_window']
        self.assertEqual(window['status'], 'AVAILABLE')
        self.assertTrue(window['complete'])
        self.assertEqual(data[window['byte_start']:window['byte_end']], expected)
        self.assertEqual(window['sha256'], sha(expected))
        self.assertEqual(window['module_sha256'], sha(data))
        return row

    def test_parenthesized_decorator_includes_opening_statement(self):
        body = '@(\n    decorate\n)\ndef use(value):\n    return value\n'
        for newline in ('\n', '\r\n', '\r'):
            with self.subTest(newline=repr(newline)):
                expected = body.replace('\n', newline).encode()
                data = ('# π\n'.replace('\n', newline)).encode() + expected
                row = self.assert_window(data, expected)
                self.assertEqual(row['start_line'], 2)
                self.assertEqual(row['definition_line'], 5)

    def test_backslash_continuation_preserves_at_sign(self):
        body = '@\\\n decorate\ndef use(value):\n    return value\n'
        for newline in ('\n', '\r\n', '\r'):
            with self.subTest(newline=repr(newline)):
                expected = body.replace('\n', newline).encode()
                self.assert_window(b'# before' + newline.encode() + expected, expected)

    def test_multiple_decorators_and_intervening_comments_remain_whole(self):
        expected = (b'@(\n    decorate\n)\n# between decorators\n'
                    b'@other(\n    "@ignored in a string",\n)\n'
                    b'# before definition\ndef use():\n    return 1\n')
        self.assert_window(b'PREFIX = "before"\n' + expected + b'\nAFTER = 2\n', expected)

    def test_nested_async_keeps_indent_and_multiline_decorator(self):
        expected = (b'    @(\n        decorate\n    )\n'
                    b'    async def use(value):\n        return value\n')
        data = b'def outer():\n' + expected + b'    return use\n'
        row = self.assert_window(data, expected, 'outer.use')
        self.assertEqual(row['start_line'], 2)

    def test_at_sign_in_expression_or_string_is_not_a_decorator_start(self):
        prefix = (b'product = left @ right\n'
                  b'label = """@(\n    unrelated\n)"""\n')
        expected = b'@(\n    decorate\n)\ndef use():\n    return 3'
        self.assert_window(prefix + expected, expected)

    def test_class_decorator_is_not_attached_to_undecorated_method(self):
        prefix = b'@(\n    class_decorator\n)\nclass Example:\n'
        expected = b'    def use(self):\n        return self\n'
        self.assert_window(prefix + expected, expected, 'Example.use')

    def test_utf8_decorator_and_comment_only_lines_preserve_original_bytes(self):
        expected = ('@(\n    # no expression yet\n    décorateur\n)\n'
                    'def use():\n    return "雪"\n').encode()
        self.assert_window('# é\n'.encode() + expected, expected)


class DecoratorInspectionTests(unittest.TestCase):
    def test_large_module_search_and_inspection_return_exact_complete_declaration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'
            source.mkdir()
            expected = ('@(\r\n    decorate\r\n)\r\n'
                        'async def use(value):\r\n    return value\r\n').encode()
            data = padded(b'raise AssertionError("MUST_NOT_EXECUTE")\r\n' + expected)
            path = source / 'module.py'
            path.write_bytes(data)
            manifest = root / 'manifest.json'
            manifest.write_bytes(canonical({'schema': 1, 'files': [{
                'provider': 'synthetic', 'file_id': 'source', 'path': 'module.py',
                'size': len(data), 'sha256': sha(data), 'source_url': 'fixture://source'}]}))
            workbench = GuardlessWorkbench(root / 'work')
            report = corpus.run(workbench, source, manifest, workbench.home / 'corpora/run',
                                analyzer=RecordingAnalyzer())
            row = corpus.search(report, 'use')['results'][0]
            result = corpus.inspect_definition(report, row['definition_id'], source)
            self.assertEqual(result['status'], 'COMPLETE_DEFINITION_SOURCE')
            self.assertEqual(result['source_text'].encode(), expected)
            self.assertEqual(result['source_window']['sha256'], sha(expected))
            self.assertEqual(path.read_bytes(), data)
            self.assertFalse(result['source_code_executed'])
            self.assertNotIn('return value', json.dumps(report))

            # A formerly truncated window must not become current verified evidence.
            stale = copy.deepcopy(report)
            old = stale['sources'][0]['segments'][0]['definitions'][0]
            window = old['source_window']
            window['byte_start'] += len(b'@(\r\n')
            window['bytes'] -= len(b'@(\r\n')
            window['sha256'] = sha(expected[len(b'@(\r\n'):])
            with self.assertRaises(Blocked) as raised:
                corpus.inspect_definition(stale, row['definition_id'], source)
            self.assertEqual(str(raised.exception), 'DEFINITION_SOURCE_BINDING')


if __name__ == '__main__':
    unittest.main()
