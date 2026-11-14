"""Independent byte-preservation and corruption controls for source transport."""
import copy
import unittest

from forge_core.common import Blocked, sha
from forge_core import source_blocks


class SourceBlockTests(unittest.TestCase):
    def fixture(self):
        data = b'first:123\nsecond:456\nthird:789\nlast:000\n'
        result = source_blocks.deconstruct(data, max_block_bytes=10)
        self.assertGreater(len(result['blocks']), 2)
        return data, result

    def assert_exact_partition(self, data, result, cap):
        manifest, blocks = result['manifest'], result['blocks']
        self.assertEqual(manifest['schema'], 1)
        self.assertEqual(manifest['source_sha256'], sha(data))
        self.assertEqual(manifest['source_bytes'], len(data))
        self.assertEqual(manifest['max_block_bytes'], cap)
        self.assertEqual(len(manifest['blocks']), len(blocks))
        position = 0
        for index, (record, body) in enumerate(zip(manifest['blocks'], blocks)):
            self.assertEqual(record['index'], index)
            self.assertEqual(record['start'], position)
            self.assertEqual(record['end'], position + len(body))
            self.assertEqual(record['size'], len(body))
            self.assertEqual(record['sha256'], sha(body))
            self.assertLessEqual(len(body), cap)
            self.assertEqual(body, data[record['start']:record['end']])
            position += len(body)
        self.assertEqual(position, len(data))
        self.assertEqual(b''.join(blocks), data)
        self.assertEqual(source_blocks.reconstruct(manifest, blocks), data)

    def test_contiguous_partition_reconstructs_exact_original(self):
        data = bytes(range(256)) * 7
        self.assert_exact_partition(data, source_blocks.deconstruct(data, max_block_bytes=137), 137)

    def test_empty_source_round_trip(self):
        self.assert_exact_partition(b'', source_blocks.deconstruct(b''), 250_000)

    def test_encodings_newlines_and_binary_bytes_are_not_normalized(self):
        text = 'def café():\r\n    return "雪"\r\n'
        cases = [text.encode('utf-8'), text.encode('utf-8-sig'),
                 text.encode('utf-16'), text.encode('utf-16-be'),
                 'café\rnext\nlast\r\n'.encode('latin-1'),
                 b'\x00\xff\xfe\x80\r\n\x00\x01']
        for data in cases:
            with self.subTest(prefix=data[:8]):
                self.assert_exact_partition(data, source_blocks.deconstruct(data, max_block_bytes=7), 7)

    def test_long_physical_line_is_preserved_across_size_boundaries(self):
        data = b'#' + b'x' * 600_000
        result = source_blocks.deconstruct(data)
        self.assertEqual(len(result['blocks']), 3)
        self.assert_exact_partition(data, result, 250_000)

    def test_preferred_boundaries_preserve_bytes_and_bound_every_block(self):
        data = b'alpha\r\nbeta\n' + b'x' * 300 + b'\nend'
        result = source_blocks.deconstruct(data, max_block_bytes=80,
                                           preferred_boundaries=(7, 12, 312))
        self.assert_exact_partition(data, result, 80)

    def test_invalid_input_budgets_and_boundaries_are_rejected(self):
        for cap in (True, False, 0, -1, 250_001, 2.5, '10'):
            with self.subTest(cap=cap), self.assertRaises(Blocked):
                source_blocks.deconstruct(b'abc', max_block_bytes=cap)
        for boundary in (-1, 0, 3, 4, True, 1.5, '1'):
            with self.subTest(boundary=boundary), self.assertRaises(Blocked):
                source_blocks.deconstruct(b'abc', preferred_boundaries=(boundary,))
        for data in ('source', None, 123):
            with self.subTest(data=data), self.assertRaises(Blocked):
                source_blocks.deconstruct(data)

    def test_source_byte_budget_remains_bounded(self):
        self.assertEqual(source_blocks.MAX_SOURCE_BYTES, 8_000_000)
        with self.assertRaises(Blocked):
            source_blocks.deconstruct(b'x' * (source_blocks.MAX_SOURCE_BYTES + 1))

    def test_missing_block_is_rejected(self):
        _, result = self.fixture()
        with self.assertRaises(Blocked):
            source_blocks.reconstruct(result['manifest'], result['blocks'][:-1])

    def test_reordered_blocks_are_rejected(self):
        _, result = self.fixture()
        blocks = list(result['blocks'])
        blocks[0], blocks[1] = blocks[1], blocks[0]
        with self.assertRaises(Blocked):
            source_blocks.reconstruct(result['manifest'], blocks)

    def test_duplicate_block_is_rejected(self):
        _, result = self.fixture()
        blocks = list(result['blocks'])
        blocks[1] = blocks[0]
        with self.assertRaises(Blocked):
            source_blocks.reconstruct(result['manifest'], blocks)
        with self.assertRaises(Blocked):
            source_blocks.reconstruct(result['manifest'], result['blocks'] + result['blocks'][:1])

    def test_same_size_byte_tampering_is_rejected(self):
        _, result = self.fixture()
        blocks = list(result['blocks'])
        blocks[0] = b'X' + blocks[0][1:]
        with self.assertRaises(Blocked):
            source_blocks.reconstruct(result['manifest'], blocks)

    def test_wrong_hashes_sizes_and_offsets_are_rejected(self):
        _, result = self.fixture()
        changes = [('source_sha256', '0' * 64), ('source_bytes', 1),
                   ('max_block_bytes', 1), ('schema', 2)]
        for key, value in changes:
            manifest = copy.deepcopy(result['manifest'])
            manifest[key] = value
            with self.subTest(field=key), self.assertRaises(Blocked):
                source_blocks.reconstruct(manifest, result['blocks'])
        block_changes = [('sha256', '0' * 64), ('size', 1), ('start', 0),
                         ('end', 1), ('index', 0)]
        for key, value in block_changes:
            manifest = copy.deepcopy(result['manifest'])
            manifest['blocks'][1][key] = value
            with self.subTest(block_field=key), self.assertRaises(Blocked):
                source_blocks.reconstruct(manifest, result['blocks'])

    def test_rehashed_overlap_is_not_a_valid_partition(self):
        data, result = self.fixture()
        manifest = copy.deepcopy(result['manifest'])
        blocks = list(result['blocks'])
        record = manifest['blocks'][1]
        record['start'] -= 1
        blocks[1] = data[record['start']:record['end']]
        record['size'] = len(blocks[1])
        record['sha256'] = sha(blocks[1])
        manifest['max_block_bytes'] = 20
        with self.assertRaises(Blocked):
            source_blocks.reconstruct(manifest, blocks)

    def test_malformed_manifest_or_block_type_is_rejected(self):
        _, result = self.fixture()
        for manifest in (None, [], {}, {'schema': 1, 'blocks': 'wrong'}):
            with self.subTest(manifest=manifest), self.assertRaises(Blocked):
                source_blocks.reconstruct(manifest, result['blocks'])
        blocks = list(result['blocks'])
        blocks[0] = blocks[0].decode()
        with self.assertRaises(Blocked):
            source_blocks.reconstruct(result['manifest'], blocks)


if __name__ == '__main__':
    unittest.main()
