"""Strict JSON admission, including limits used by large corpus reports."""
import json
import math
import random
import struct
import unittest

from forge_core.common import Blocked, loads


class JSONAdmissionTests(unittest.TestCase):
    def assert_blocked(self, payload, reason):
        with self.assertRaises(Blocked) as caught:
            loads(payload)
        self.assertEqual(str(caught.exception), reason)

    def test_valid_nested_values_and_distinct_object_keys(self):
        payload = '{"left":{"name":1},"right":{"name":2},"values":[null,true,false,"NaN",-13,1.25]}'
        expected = json.loads(payload)
        for value in (payload, payload.encode(), bytearray(payload.encode())):
            with self.subTest(type=type(value).__name__):
                self.assertEqual(loads(value), expected)

    def test_decoded_duplicate_keys_are_rejected(self):
        for payload in (b'{"a":1,"a":2}', b'{"a":1,"\\u0061":2}',
                        b'{"":0,"":1}', b'{"\\u0000":0,"\\u0000":1}'):
            with self.subTest(payload=payload):
                self.assert_blocked(payload, 'DUPLICATE_JSON_KEY')

    def test_nested_and_array_duplicate_keys_are_rejected(self):
        for payload in (b'{"outer":{"x":0,"x":1}}',
                        b'[{"x":0,"x":1}]',
                        b'{"outer":[{"x":0,"\\u0078":1}]}'):
            with self.subTest(payload=payload):
                self.assert_blocked(payload, 'DUPLICATE_JSON_KEY')

    def test_explicit_nonfinite_values_are_rejected_at_every_position(self):
        for token in ('NaN', 'Infinity', '-Infinity'):
            for payload in (token, '[' + token + ']', '{"a":[{"b":' + token + '}]}'):
                with self.subTest(payload=payload):
                    self.assert_blocked(payload, 'NONFINITE_JSON')

    def test_exponent_overflow_is_rejected_at_every_position(self):
        for token in ('1e9999', '-1e9999', '1.7976931348623159e308'):
            for payload in (token, '[' + token + ']', '{"a":[{"b":' + token + '}]}'):
                with self.subTest(payload=payload):
                    self.assert_blocked(payload, 'NONFINITE_JSON')

    def test_combined_invalid_values_keep_error_priority(self):
        self.assert_blocked(b'{"a":1e999,"a":0}', 'DUPLICATE_JSON_KEY')
        self.assert_blocked(b'{"a":NaN,"a":0}', 'NONFINITE_JSON')

    def test_signed_zero_underflow_and_extreme_finite_values_are_exact(self):
        for token in ('0.0', '-0.0', '1e-5000', '-1e-5000',
                      '1.7976931348623157e308', '-1.7976931348623157e308',
                      '4.9406564584124654e-324', '1.0000000000000002'):
            with self.subTest(token=token):
                expected = json.loads(token)
                actual = loads(token)
                self.assertIs(type(actual), float)
                self.assertEqual(struct.pack('!d', actual), struct.pack('!d', expected))

    def test_large_integers_are_not_converted_to_float(self):
        token = '9' * 200
        value = loads(token)
        self.assertIs(type(value), int)
        self.assertEqual(value, int(token))

    def test_decimal_boundary_differential(self):
        rng = random.Random(87471)
        for _ in range(200):
            token = f'{rng.choice(["", "-"])}{rng.randint(1,9999)}.{rng.randint(0,9999):04d}e{rng.randint(-500,500)}'
            expected = json.loads(token)
            with self.subTest(token=token):
                if math.isfinite(expected):
                    self.assertEqual(struct.pack('!d', loads(token)), struct.pack('!d', expected))
                else:
                    self.assert_blocked(token, 'NONFINITE_JSON')

    def test_malformed_utf8_is_a_controlled_json_failure(self):
        for payload in (b'"\xff"', b'{"value":"\xc0\xaf"}', b'"\xe2\x82"'):
            for value in (payload, bytearray(payload)):
                with self.subTest(payload=payload, type=type(value).__name__):
                    self.assert_blocked(value, 'INVALID_JSON')

    def test_malformed_json_is_rejected(self):
        for payload in ('', '{', '[0,]', '{"a":1,}', '01', 'true false', '"unterminated'):
            with self.subTest(payload=payload):
                self.assert_blocked(payload, 'INVALID_JSON')

    def test_deeply_nested_arrays_and_objects_remain_rejected(self):
        for payload in ('[' * 5000 + '0' + ']' * 5000,
                        '{"a":' * 5000 + '0' + '}' * 5000):
            with self.subTest(kind=payload[0]):
                self.assert_blocked(payload, 'INVALID_JSON')

    def test_valid_bounded_nested_report_preserves_values(self):
        payload = '{"a":[' * 50 + '-0.0' + ']}' * 50
        value = loads(payload)
        for _ in range(50):
            value = value['a'][0]
        self.assertEqual(struct.pack('!d', value), struct.pack('!d', -0.0))

    def test_wrong_input_types_keep_type_error_contract(self):
        for value in (None, 1, 1.0, {}, []):
            with self.subTest(type=type(value).__name__), self.assertRaises(TypeError):
                loads(value)


if __name__ == '__main__':
    unittest.main()
