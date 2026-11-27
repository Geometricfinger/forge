"""Independent byte, admission, corruption and atomicity checks for caches."""
import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forge_core import checkpoint_io, streaming
from forge_core.common import Blocked, canonical, sha


class CheckpointIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binding = {'job': 'synthetic-job', 'source': 'synthetic-source'}
        self.result = {'upstream_code_executed': False,
                       'values': [0, -0.0, True, None, 'é😀</script>'],
                       'metadata': {'b': 2, 'a': 1}}

    def wrapper(self, result=None, binding=None):
        result = self.result if result is None else result
        return {'binding': self.binding if binding is None else binding,
                'result': result, 'result_sha256': sha(canonical(result))}

    def save_bytes(self, data, *, compressed=True):
        path = self.root / ('test.json.gz' if compressed else 'test.json')
        path.write_bytes(gzip.compress(data, mtime=0) if compressed else data)
        return path

    def test_raw_wrapper_uses_one_result_encoding_and_exact_canonical_bytes(self):
        path = self.root / 'legacy.json'
        with patch.object(checkpoint_io, 'canonical', wraps=canonical) as encoder:
            encoded = checkpoint_io.encode_result(self.result)
            receipt = checkpoint_io.write_checkpoint(path, self.binding, encoded, new=True)
        self.assertEqual(sum(call.args[0] is self.result for call in encoder.call_args_list), 1)
        expected = canonical(self.wrapper())
        self.assertEqual(path.read_bytes(), expected)
        self.assertEqual(receipt, {'sha256': sha(expected), 'bytes': len(expected)})
        self.assertEqual(encoded.byte_count, len(canonical(self.result)))

    def test_new_gzip_is_deterministic_and_decodes_to_exact_wrapper(self):
        encoded = checkpoint_io.encode_result(self.result)
        paths = [checkpoint_io.resolve(self.root / (name + '.json')) for name in ('a', 'b')]
        for path in paths:
            self.assertTrue(path.name.endswith('.json.gz'))
            checkpoint_io.write_checkpoint(path, self.binding, encoded, new=True)
            self.assertEqual(gzip.decompress(path.read_bytes()), canonical(self.wrapper()))
        self.assertEqual(paths[0].read_bytes(), paths[1].read_bytes())
        self.assertEqual(paths[0].read_bytes()[4:8], bytes(4))

    def test_legacy_noncanonical_json_is_read_and_updated_as_raw(self):
        path = self.save_bytes(json.dumps(self.wrapper(), indent=2, ensure_ascii=False).encode(), compressed=False)
        self.assertEqual(checkpoint_io.resolve(path), path)
        loaded = checkpoint_io.load(path, self.binding)
        self.assertEqual(loaded.result, self.result)
        self.assertEqual(loaded.result_sha256, sha(canonical(self.result)))
        self.assertEqual(loaded.result_bytes, len(canonical(self.result)))
        checkpoint_io.write_checkpoint(path, self.binding, checkpoint_io.encode_result(self.result))
        self.assertEqual(path.read_bytes(), canonical(self.wrapper()))
        self.assertFalse(path.with_suffix('.json.gz').exists())

    def test_read_reuses_one_canonical_result_for_digest_and_count(self):
        path = self.save_bytes(canonical(self.wrapper()))
        with patch.object(checkpoint_io, 'canonical', wraps=canonical) as encoder:
            loaded = checkpoint_io.load(path, self.binding)
        self.assertEqual(encoder.call_count, 1)
        self.assertEqual(loaded.result_bytes, len(canonical(loaded.result)))

    def test_wrapper_binding_digest_keys_and_authority_are_checked_in_both_formats(self):
        mutations = [
            ('binding', lambda obj: obj.update(binding={'job': 'different'}), 'CORPUS_CHECKPOINT_INVALID'),
            ('digest', lambda obj: obj.update(result_sha256='0' * 64), 'CORPUS_CHECKPOINT_INVALID'),
            ('extra', lambda obj: obj.update(extra=True), 'CORPUS_CHECKPOINT_INVALID'),
            ('missing', lambda obj: obj.pop('result_sha256'), 'CORPUS_CHECKPOINT_INVALID'),
            ('authority', lambda obj: obj.update(result={'upstream_code_executed': True},
                result_sha256=sha(canonical({'upstream_code_executed': True}))), 'CORPUS_CHECKPOINT_AUTHORITY'),
            ('list', lambda obj: obj.update(result=[], result_sha256=sha(canonical([]))), 'CORPUS_CHECKPOINT_AUTHORITY'),
        ]
        for compressed in (False, True):
            for name, mutate, reason in mutations:
                with self.subTest(format=compressed, mutation=name):
                    obj = self.wrapper(); mutate(obj)
                    path = self.save_bytes(canonical(obj), compressed=compressed)
                    with self.assertRaisesRegex(Blocked, '^' + reason + '$'):
                        checkpoint_io.load(path, self.binding)
                    path.unlink()

    def test_strict_json_rejects_duplicate_nonfinite_and_invalid_records(self):
        inputs = [(b'{"result":{},"result":{}}', 'DUPLICATE_JSON_KEY'),
                  (b'{"result":NaN}', 'NONFINITE_JSON'),
                  (b'{broken', 'INVALID_JSON')]
        for data, reason in inputs:
            with self.subTest(reason=reason):
                path = self.save_bytes(data)
                with self.assertRaisesRegex(Blocked, '^' + reason + '$'):
                    checkpoint_io.load(path, self.binding)

    def test_truncation_and_checksum_corruption_are_refused(self):
        data = gzip.compress(canonical(self.wrapper()), mtime=0)
        crc = bytearray(data); crc[-8] ^= 1
        length = bytearray(data); length[-1] ^= 1
        variants = [data[:n] for n in (0, 1, 2, 9, len(data) // 2, len(data) - 8, len(data) - 1)]
        variants += [bytes(crc), bytes(length), b'xx' + data[2:]]
        path = self.root / 'test.json.gz'
        for index, damaged in enumerate(variants):
            with self.subTest(variant=index):
                path.write_bytes(damaged)
                with self.assertRaisesRegex(Blocked, '^CORPUS_CHECKPOINT_COMPRESSION$'):
                    checkpoint_io.load(path, self.binding)

    def test_trailing_garbage_padding_and_concatenated_members_are_refused(self):
        data = gzip.compress(canonical(self.wrapper()), mtime=0)
        path = self.root / 'test.json.gz'
        for trailing in (b'\x00', b'garbage', gzip.compress(b'', mtime=0), data):
            with self.subTest(trailing_bytes=len(trailing)):
                path.write_bytes(data + trailing)
                with self.assertRaisesRegex(Blocked, '^CORPUS_CHECKPOINT_COMPRESSION$'):
                    checkpoint_io.load(path, self.binding)

    def test_decoded_ceiling_admits_exact_boundary_and_refuses_one_byte_more(self):
        result = {'upstream_code_executed': False, 'padding': ''}
        limit = 4096
        result['padding'] = 'x' * (limit - len(canonical(self.wrapper(result))))
        self.assertEqual(len(canonical(self.wrapper(result))), limit)
        for compressed in (False, True):
            with self.subTest(compressed=compressed), patch.object(checkpoint_io, 'MAX_CHECKPOINT_BYTES', limit):
                path = self.save_bytes(canonical(self.wrapper(result)), compressed=compressed)
                self.assertEqual(checkpoint_io.load(path, self.binding).result, result)
                before = path.read_bytes()
                larger = {**result, 'padding': result['padding'] + 'x'}
                with self.assertRaisesRegex(Blocked, '^CORPUS_CHECKPOINT_SIZE$'):
                    checkpoint_io.write_checkpoint(path, self.binding, checkpoint_io.encode_result(larger))
                self.assertEqual(path.read_bytes(), before)
                if compressed:
                    path.write_bytes(gzip.compress(canonical(self.wrapper(larger)), mtime=0))
                    with self.assertRaisesRegex(Blocked, '^CORPUS_CHECKPOINT_SIZE$'):
                        checkpoint_io.load(path, self.binding)
                path.unlink()

    def test_actual_twenty_megabyte_decode_limit_precedes_json_parsing(self):
        self.assertEqual(checkpoint_io.MAX_CHECKPOINT_BYTES, 20_000_000)
        path = self.root / 'bomb.json.gz'
        path.write_bytes(gzip.compress(b'x' * 20_000_001, compresslevel=1, mtime=0))
        with patch.object(checkpoint_io, 'loads', side_effect=AssertionError('Oversized JSON reached parser')):
            with self.assertRaisesRegex(Blocked, '^CORPUS_CHECKPOINT_SIZE$'):
                checkpoint_io.load(path, self.binding)

    def test_stored_ceiling_and_explicit_extension_are_enforced(self):
        for extension in ('.json', '.json.gz'):
            path = self.root / ('oversize' + extension); path.write_bytes(b'x' * 65)
            with patch.object(checkpoint_io, 'MAX_CHECKPOINT_BYTES', 64):
                with self.assertRaisesRegex(Blocked, '^INPUT_FILE_BOUNDS$'):
                    checkpoint_io.load(path, self.binding)
            path.unlink()
        raw = self.root / 'raw.json'; raw.write_bytes(gzip.compress(canonical(self.wrapper()), mtime=0))
        with self.assertRaisesRegex(Blocked, '^INVALID_JSON$'):
            checkpoint_io.load(raw, self.binding)
        raw.unlink()
        compressed = self.root / 'raw.json.gz'; compressed.write_bytes(canonical(self.wrapper()))
        with self.assertRaisesRegex(Blocked, '^CORPUS_CHECKPOINT_COMPRESSION$'):
            checkpoint_io.load(compressed, self.binding)

    def test_duplicate_formats_fail_closed_even_when_decoded_values_agree(self):
        raw = self.save_bytes(canonical(self.wrapper()), compressed=False)
        compressed = self.save_bytes(canonical(self.wrapper()), compressed=True)
        for path in (raw, compressed):
            with self.assertRaisesRegex(Blocked, '^CORPUS_CHECKPOINT_FORMAT_CONFLICT$'):
                checkpoint_io.resolve(path)
            with self.assertRaisesRegex(Blocked, '^CORPUS_CHECKPOINT_FORMAT_CONFLICT$'):
                checkpoint_io.load(path, self.binding)
            with self.assertRaisesRegex(Blocked, '^CORPUS_CHECKPOINT_FORMAT_CONFLICT$'):
                checkpoint_io.write_checkpoint(path, self.binding, checkpoint_io.encode_result(self.result))

    def test_writer_cannot_create_a_second_format(self):
        encoded = checkpoint_io.encode_result(self.result)
        for first, second in (('a.json', 'a.json.gz'), ('b.json.gz', 'b.json')):
            path = self.root / first
            checkpoint_io.write_checkpoint(path, self.binding, encoded, new=True)
            before = path.read_bytes()
            with self.assertRaisesRegex(Blocked, '^CORPUS_CHECKPOINT_FORMAT_CONFLICT$'):
                checkpoint_io.write_checkpoint(self.root / second, self.binding, encoded, new=True)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((self.root / second).exists())

    def test_interrupted_compression_preserves_old_bytes_and_leaves_no_new_file(self):
        encoded = checkpoint_io.encode_result(self.result)
        for existing in (False, True):
            path = self.root / ('existing.json.gz' if existing else 'new.json.gz')
            if existing:
                checkpoint_io.write_checkpoint(path, self.binding, encoded, new=True)
            before = path.read_bytes() if existing else None
            def interrupted(chunks, **kwargs):
                yield b'partial gzip header'
                raise MemoryError('synthetic compression failure')
            with patch.object(checkpoint_io, 'gzip_chunks', side_effect=interrupted):
                with self.assertRaises(MemoryError):
                    checkpoint_io.write_checkpoint(path, self.binding, encoded, new=not existing)
            self.assertEqual(path.read_bytes() if path.exists() else None, before)
            self.assertFalse(list(self.root.glob('.forge-*')))

    def test_failed_atomic_replace_preserves_previous_compressed_checkpoint(self):
        path = self.root / 'replace.json.gz'
        encoded = checkpoint_io.encode_result(self.result)
        checkpoint_io.write_checkpoint(path, self.binding, encoded, new=True)
        before = path.read_bytes()
        with patch.object(streaming.os, 'replace', side_effect=OSError('synthetic replace failure')):
            with self.assertRaises(OSError):
                checkpoint_io.write_checkpoint(path, self.binding, encoded)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(self.root.glob('.forge-*')))

    def test_existing_symlink_and_hardlink_write_guards_remain(self):
        path = self.root / 'guard.json.gz'
        encoded = checkpoint_io.encode_result(self.result)
        checkpoint_io.write_checkpoint(path, self.binding, encoded, new=True)
        before = path.read_bytes()
        with self.assertRaisesRegex(Blocked, '^OUTPUT_CONFLICT$'):
            checkpoint_io.write_checkpoint(path, self.binding, encoded, new=True)
        link = self.root / 'hardlink'; os.link(path, link)
        with self.assertRaisesRegex(Blocked, '^OUTPUT_CONFLICT$'):
            checkpoint_io.write_checkpoint(path, self.binding, encoded)
        link.unlink()
        symlink = self.root / 'link.json.gz'; symlink.symlink_to(path)
        with self.assertRaisesRegex(Blocked, '^SYMLINK_PATH$'):
            checkpoint_io.load(symlink, self.binding)
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
