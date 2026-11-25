"""Publication identity, interrupted writes and aggregate metadata admission."""
import gzip
import html
import subprocess
import json
import tempfile
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from forge_core import corpus, streaming, bounded_io
from forge_core.common import Blocked, canonical, sha
from forge_core.streaming import canonical_chunks, gzip_chunks, write_chunks
from forge_core.bounded_io import _Capture, run_bounded
import test_corpus as helper


class StreamingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_canonical_bytes_match_existing_contract(self):
        values = [None, False, True, 0, -0.0, 1.25, 1e-20,
                  {'z': ['\u2028', 'é', '\U0001f600', '</script>', 'a' * 100_000],
                   'a': {'nested': [0, True, None], 'quote': '\"\\\n'}}]
        for value in values:
            with self.subTest(value=type(value).__name__):
                self.assertEqual(b''.join(canonical_chunks(value)), canonical(value))

    def test_nonfinite_values_are_refused(self):
        for value in (float('nan'), float('inf'), -float('inf')):
            with self.assertRaises(ValueError):
                b''.join(canonical_chunks({'value': value}))

    def test_written_digest_covers_exact_bytes(self):
        path = self.root / 'data.json'
        data = canonical({'a': list(range(1000))})
        result = write_chunks(path, (data[:17], data[17:]), new=True)
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(result, {'sha256': sha(data), 'bytes': len(data)})

    def test_interrupted_replacement_preserves_previous_file(self):
        path = self.root / 'data.json'
        path.write_bytes(b'previous')
        def interrupted():
            yield b'partial'
            raise OSError('synthetic interrupted writer')
        with self.assertRaises(OSError):
            write_chunks(path, interrupted())
        self.assertEqual(path.read_bytes(), b'previous')
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_interrupted_new_file_is_not_published(self):
        path = self.root / 'data.json'
        def interrupted():
            yield b'partial'
            raise MemoryError()
        with self.assertRaises(MemoryError):
            write_chunks(path, interrupted(), new=True)
        self.assertFalse(path.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_exact_output_limit_and_one_byte_over(self):
        path = self.root / 'data.json'
        write_chunks(path, [b'ab', b'cd'], limit=4)
        with self.assertRaisesRegex(Blocked, 'CORPUS_REPORT_SIZE_LIMIT'):
            write_chunks(path, [b'ab', b'cde'], limit=4)
        self.assertEqual(path.read_bytes(), b'abcd')

    def test_new_output_does_not_replace_existing_file(self):
        path = self.root / 'data.json'
        path.write_bytes(b'previous')
        with self.assertRaisesRegex(Blocked, 'OUTPUT_CONFLICT'):
            write_chunks(path, [b'new'], new=True)
        self.assertEqual(path.read_bytes(), b'previous')

    def test_symlink_output_refused(self):
        target = self.root / 'target'
        target.write_bytes(b'previous')
        path = self.root / 'link'
        path.symlink_to(target)
        with self.assertRaisesRegex(Blocked, 'SYMLINK_PATH'):
            write_chunks(path, [b'new'])
        self.assertEqual(target.read_bytes(), b'previous')

    def test_gzip_stream_preserves_lines_and_is_deterministic(self):
        chunks = [canonical({'n': n, 'text': 'x' * 500}) + b'\n' for n in range(120)]
        encoded = b''.join(gzip_chunks(iter(chunks)))
        self.assertEqual(gzip.decompress(encoded), b''.join(chunks))
        self.assertEqual(encoded, b''.join(gzip_chunks([b''.join(chunks)])))

    def test_reader_memory_failure_stops_fixed_child_promptly(self):
        # The child is our owned fixture. A capture allocation fails while the
        # child remains alive; the supervisor must stop it, not wait 30 seconds.
        command = [sys.executable, '-I', '-B', '-c',
                   'import sys,time;sys.stdout.write("output");sys.stdout.flush();time.sleep(30)']
        started = time.monotonic()
        with patch.object(_Capture, 'accept', side_effect=MemoryError):
            with self.assertRaisesRegex(Blocked, 'TOOL_MEMORY_LIMIT'):
                run_bounded(command, self.root, {}, timeout=3)
        self.assertLess(time.monotonic() - started, 3)


class CorpusPublicationTests(unittest.TestCase):
    setUp = helper.CorpusTests.setUp
    tearDown = helper.CorpusTests.tearDown
    make = helper.CorpusTests.make
    runit = helper.CorpusTests.runit

    def test_published_report_cart_and_review_have_same_records(self):
        report = self.runit()
        report_data = (self.out / 'corpus.json').read_bytes()
        cart = (self.out / 'capabilities.ndjson.gz').read_bytes()
        receipt = json.loads((self.out / 'receipt.json').read_bytes())
        self.assertEqual(report_data, canonical(report))
        self.assertEqual(receipt['report_sha256'], sha(report_data))
        self.assertEqual(receipt['cart_sha256'], sha(cart))
        self.assertEqual(receipt['cart_bytes'], len(cart))
        rows = list(corpus.all_rows(report))
        self.assertEqual([json.loads(line) for line in gzip.decompress(cart).splitlines()], rows)
        page = (self.out / 'Review.html').read_text()
        records = page.split('id="records">', 1)[1].split('</script>', 1)[0]
        self.assertEqual(json.loads(records), rows)
        self.assertEqual(page, corpus.render(report))

    def test_exact_metadata_budget_preserves_all_records(self):
        initial = self.runit()
        limit = sum(len(canonical(source)) for source in initial['sources'])
        with patch.object(corpus, 'MAX_REPORT_SOURCE_BYTES', limit):
            report = self.runit(max_containers=0)
        self.assertEqual(report['status'], 'COMPLETED_FOR_SELECTED_CONTAINERS')
        self.assertEqual(report['coverage']['metadata_budget']['source_bytes'], limit)
        self.assertEqual(report['summary']['definitions'], 2)

    def test_metadata_budget_stops_with_honest_partial_report(self):
        initial = self.runit()
        limit = len(canonical(initial['sources'][0]))
        calls = self.a.calls
        with patch.object(corpus, 'MAX_REPORT_SOURCE_BYTES', limit):
            report = self.runit(max_containers=0)
        self.assertEqual(report['status'], 'INCOMPLETE')
        self.assertEqual(report['summary']['definitions'], 1)
        self.assertFalse(report['coverage']['definitions']['discovery_complete'])
        self.assertEqual(report['coverage']['blocked'][0]['reason'], 'CORPUS_METADATA_BUDGET')
        self.assertEqual(self.a.calls, calls)
        self.assertEqual(len(list((self.out / 'checkpoints').glob('*.json'))), 2)
        self.assertEqual(corpus.read_report(self.out), report)

    def test_metadata_budget_marks_remaining_containers_unread(self):
        self.files.append(('third.py', helper.CODE))
        self.make()
        with patch.object(corpus, 'MAX_REPORT_SOURCE_BYTES', 0):
            report = self.runit()
        self.assertEqual(report['status'], 'INCOMPLETE')
        self.assertEqual(len(report['coverage']['blocked']), 1)
        self.assertEqual(len(report['coverage']['unread']), 2)
        self.assertEqual(report['cache']['new_containers'], 1)
        self.assertEqual(report['summary']['definitions'], 0)
        self.assertEqual(len(list((self.out / 'checkpoints').glob('*.json'))), 1)

    def test_report_limit_failure_never_publishes_success_receipt(self):
        with patch.object(corpus, 'REPORT_READ_LIMIT', 1):
            with self.assertRaisesRegex(Blocked, 'CORPUS_REPORT_SIZE_LIMIT'):
                self.runit()
        self.assertFalse((self.out / 'corpus.json').exists())
        self.assertFalse((self.out / 'receipt.json').exists())
        self.assertEqual(len(list((self.out / 'checkpoints').glob('*.json'))), 2)


    def test_large_gap_section_streams_exact_escaped_json(self):
        report = {'status': 'COMPLETED_WITH_GAPS', 'summary': {'segments': 0, 'definitions': 0},
                  'coverage': {'blocked': [], 'unread': []}, 'sources': [],
                  'gaps': [{'path': f'item_{i}</script>&\"\u2028', 'status': 'PYTHON_SYNTAX_ERROR'}
                           for i in range(3000)]}
        expected = html.escape(json.dumps({'coverage': report['coverage'], 'gaps': report['gaps']}, indent=2), quote=True)
        with patch.object(json, 'dumps', side_effect=AssertionError('Full JSON copy during render')):
            pieces = list(corpus.render_parts(report))
        page = ''.join(pieces)
        observed = page.split('<pre>', 1)[1].split('</pre>', 1)[0]
        self.assertEqual(observed, expected)
        self.assertGreater(len(expected), 65_536)
        self.assertLess(max(map(len, pieces)), 150_000)
        self.assertNotIn('</script>', observed)
        self.assertIn('&lt;/script&gt;&amp;', observed)

    def fail_review(self, path, chunks, **options):
        if Path(path).name == 'Review.html':
            def interrupted():
                yield next(iter(chunks))
                raise OSError('owned publication fault')
            return streaming.write_chunks_original(path, interrupted(), **options)
        return streaming.write_chunks_original(path, chunks, **options)

    def test_new_publication_fault_cannot_issue_receipt_and_replay_recovers(self):
        with patch.object(streaming, 'write_chunks_original', streaming.write_chunks, create=True), \
             patch.object(streaming, 'write_chunks', side_effect=self.fail_review):
            with self.assertRaisesRegex(OSError, 'owned publication fault'):
                self.runit()
        self.assertTrue((self.out / 'corpus.json').exists())
        self.assertTrue((self.out / 'capabilities.ndjson.gz').exists())
        self.assertFalse((self.out / 'Review.html').exists())
        self.assertFalse((self.out / 'receipt.json').exists())
        self.assertEqual(len(list((self.out / 'checkpoints').glob('*.json'))), 2)
        count = self.a.calls
        with patch.object(corpus, '_parser', side_effect=AssertionError('Unnecessary reparse')):
            report = self.runit(max_containers=0)
        self.assertEqual(self.a.calls, count)
        self.assertEqual(corpus.read_report(self.out), report)

    def test_replacement_fault_retains_old_receipt_and_refuses_new_report(self):
        self.runit()
        old = (self.out / 'receipt.json').read_bytes()
        with patch.object(streaming, 'write_chunks_original', streaming.write_chunks, create=True), \
             patch.object(streaming, 'write_chunks', side_effect=self.fail_review):
            with self.assertRaisesRegex(OSError, 'owned publication fault'):
                self.runit(max_containers=0)
        self.assertEqual((self.out / 'receipt.json').read_bytes(), old)
        with self.assertRaisesRegex(Blocked, '^CORPUS_RECEIPT$'):
            corpus.read_report(self.out)
        report = self.runit(max_containers=0)
        self.assertEqual(corpus.read_report(self.out), report)

    def test_input_collector_memory_error_stops_and_reaps_child(self):
        children = []
        real_popen = subprocess.Popen
        def spawn(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child
        with patch.object(bounded_io.subprocess, 'Popen', side_effect=spawn), \
             patch.object(bounded_io, 'memoryview', side_effect=MemoryError, create=True):
            with self.assertRaisesRegex(Blocked, '^TOOL_MEMORY_LIMIT$'):
                bounded_io.run_bounded([sys.executable, '-I', '-B', '-c',
                    'import sys,time;sys.stdin.buffer.read();time.sleep(30)'],
                    self.root, {}, timeout=3, input=b'owned input')
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())

if __name__ == '__main__':
    unittest.main()
