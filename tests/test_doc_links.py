"""Documentation links use only literal Markdown references and the saved corpus."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

from forge_core import corpus, doc_links
from forge_core.common import Blocked, sha
from forge_core.workspace import initialize
import test_corpus as fixture
from test_mixed_intake import zipped


ROOT = Path(__file__).resolve().parents[1]
EMITTER = b'class Writer:\n    def emit(self):\n        return 1\n'
READ = b'def read():\n    return 1\n'


class DocLinks(unittest.TestCase):
    setUp = fixture.CorpusTests.setUp
    tearDown = fixture.CorpusTests.tearDown
    make = fixture.CorpusTests.make
    runit = fixture.CorpusTests.runit

    def scan(self, files, **options):
        self.files = files
        self.make()
        return self.runit(**options)

    def test_sections_package_methods_and_inspectable_identity(self):
        report = self.scan([('src/pkg/io.py', EMITTER), ('src/pkg/__init__.py', READ)])
        raw = (b'`pkg.read`\r\n## Output ###\r\n`pkg.io.Writer.emit` and `Writer.emit()` use `pkg.read`.\r\n'
               b'## Output\r\n`io.Writer.emit` and `wrong.io.Writer.emit`.\r\n')
        result = doc_links.resolve(report, raw)
        preamble, first, second = result['sections']
        self.assertEqual(result['document_sha256'], sha(raw))
        self.assertEqual((preamble['heading'], preamble['heading_line']), (None, None))
        self.assertEqual((first['heading'], first['heading_line'], second['heading_line']), ('Output', 2, 4))
        emit, shortened, read = first['links']
        self.assertEqual((emit['doc_line'], emit['state'], shortened['state'], read['state']),
                         (3, 'linked', 'linked', 'linked'))
        self.assertEqual(emit['candidates'], shortened['candidates'])
        hit, = emit['candidates']
        self.assertEqual((hit['path'], hit['name'], hit['start_line'], hit['end_line']),
                         ('src/pkg/io.py', 'Writer.emit', 2, 3))
        self.assertEqual((hit['source_sha256'], hit['container_sha256']), (sha(EMITTER), sha(EMITTER)))
        self.assertEqual(corpus.inspect_definition(report, hit['definition_id'], self.src)['source_text'],
                         '    def emit(self):\n        return 1\n')
        self.assertEqual(first['linked_definition_ids'], [hit['definition_id'], read['candidates'][0]['definition_id']])
        self.assertEqual([v['state'] for v in second['links']], ['linked', 'unresolved'])

    def test_matching_precedes_limits_and_never_discards_a_qualifier(self):
        report = self.scan([('one/io.py', READ), ('two/io.py', READ), ('red.io.py', READ)])
        raw = b'`io.read` `one.io.read` `red.io.read`'
        broad, exact, wrong = doc_links.resolve(report, raw)['sections'][0]['links']
        self.assertEqual((broad['state'], broad['candidate_count']), ('ambiguous', 2))
        self.assertEqual((exact['state'], wrong['state']), ('linked', 'unresolved'))
        limited = doc_links.resolve(report, raw, limit=1)['sections'][0]['links']
        self.assertEqual((limited[0]['state'], limited[0]['candidate_count'], len(limited[0]['candidates'])),
                         ('lookup_truncated', 2, 1))
        self.assertEqual(limited[1], exact)

    def test_incomplete_coverage_withholds_uniqueness_and_absence(self):
        report = self.scan([('one/io.py', READ), ('two/io.py', READ)], max_containers=1)
        raw = b'`io.read` `io.missing`'
        for legacy in (False, True):
            if legacy:
                del report['coverage']['definitions']
            section, = doc_links.resolve(report, raw)['sections']
            self.assertEqual([link['state'] for link in section['links']], ['coverage_unknown'] * 2)
            self.assertEqual([link['candidate_count'] for link in section['links']], [1, 0])
            self.assertEqual(section['linked_definition_ids'], [])

    def test_only_python_files_and_explicit_qualified_spans(self):
        archive = zipped([('lib/io.py', READ)])
        report = self.scan([('sources.zip', archive), ('tests/test_io.py', EMITTER),
                            ('notes.md', b'## sample.py\n```python\n' + READ + b'```\n')])
        raw = (b'# Input\n````python\n```\n`bad.name`\n````\n'
               b'~~~\n# Other\n`bad.name`\n~~~\n    `bad.name`\n> `bad.name`\n'
               b'<!--\n`bad.name`\n-->\nA ``literal `bad.name` example`` and `read` and package.io.read.\n'
               b'Use ``lib.io.read``, `sample.read`, and `test_io.Writer.emit`.\n')
        section, = doc_links.resolve(report, raw)['sections']
        linked, example, test = section['links']
        self.assertEqual((section['heading'], linked['state'], example['state'], test['state']),
                         ('Input', 'linked', 'unresolved', 'unresolved'))
        hit, = linked['candidates']
        self.assertEqual((hit['container'], hit['path'], hit['member_chain'][-1]['sha256']),
                         ('sources.zip', 'lib/io.py', sha(READ)))
        tests = doc_links.resolve(report, raw, include_tests=True)['sections'][0]['links']
        self.assertEqual(tests[2]['state'], 'linked')

    def test_document_and_result_bounds(self):
        report = self.scan([('io.py', READ)])
        for raw in (b'\xff', b'a' * (doc_links.MAX_DOCUMENT_BYTES + 1),
                    b'`io.read`\n' * (doc_links.MAX_REFERENCES + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(Blocked):
                doc_links.resolve(report, raw)
        for limit in (0, 21, True):
            with self.subTest(limit=limit), self.assertRaises(Blocked):
                doc_links.resolve(report, b'', limit=limit)

    def test_cli_uses_verified_report_bytes_and_rejects_changed_snapshot(self):
        self.w = initialize(self.root / 'cli-home')
        self.out = self.w.home / 'corpora' / 'guide'
        self.scan([('io.py', READ)])
        document = self.root / 'guide.md'
        document.write_bytes(b'`io.read`\n')
        cmd = [sys.executable, '-B', str(ROOT / 'forge.py'), '--home', str(self.w.home),
               'corpus-doc-links', '--run-id', 'guide', '--document', str(document)]
        result = json.loads(subprocess.check_output(cmd))
        saved = self.out / 'corpus.json'
        self.assertEqual(result['run_id'], 'guide')
        self.assertEqual(result['corpus_report_sha256'], sha(saved.read_bytes()))
        self.assertEqual(result['sections'][0]['links'][0]['state'], 'linked')
        saved.write_bytes(saved.read_bytes() + b'\n')
        failed = subprocess.run(cmd, capture_output=True)
        self.assertEqual((failed.returncode, json.loads(failed.stdout)['reason']), (2, 'CORPUS_RECEIPT'))
