"""Fixed integration requirements. External numeric vectors are a separate table."""
import json,unittest,math
from pathlib import Path
from unittest.mock import patch
from forge_core.common import Blocked,canonical,loads
from forge_core import interop

class InteropContractTests(unittest.TestCase):
    def test_ascii(self):self.assertEqual(interop.normalize_record(b'{ "b":2, "a":1 }'),b'{"a":1,"b":2}')
    def test_utf16_order(self):self.assertEqual(interop.normalize_record('{"\\ufb33":1,"\\ud83d\\ude00":2}'.encode()),'{"😀":2,"דּ":1}'.encode())
    def test_integral_float(self):self.assertEqual(interop.normalize_record(b'{"x":1.0}'),b'{"x":1}')
    def test_negative_zero(self):self.assertEqual(interop.normalize_record(b'{"x":-0.0}'),b'{"x":0}')
    def test_short_exponent(self):self.assertEqual(interop.normalize_record(b'{"x":1e-7}'),b'{"x":1e-7}')
    def test_small_decimal(self):self.assertEqual(interop.normalize_record(b'{"x":1e-6}'),b'{"x":0.000001}')
    def test_duplicate(self):
        with self.assertRaisesRegex(Blocked,'DUPLICATE'):interop.normalize_record(b'{"a":1,"a":2}')
    def test_escaped_duplicate(self):
        with self.assertRaisesRegex(Blocked,'DUPLICATE'):interop.normalize_record(b'{"a":1,"\\u0061":2}')
    def test_nested_duplicate(self):
        with self.assertRaisesRegex(Blocked,'DUPLICATE'):interop.normalize_record(b'{"b":{"a":1,"a":2}}')
    def test_nonfinite(self):
        for n in ['NaN','Infinity','-Infinity','1e400']:
            with self.subTest(n=n),self.assertRaises(Blocked):interop.normalize_record(('{"x":'+n+'}').encode())
    def test_unsafe_integer(self):
        for n in ['9007199254740992','-9007199254740992','9007199254740993.0','9.007199254740993e15']:
            with self.subTest(n=n),self.assertRaisesRegex(Blocked,'UNSAFE_INTEGER'):interop.normalize_record(('{"x":'+n+'}').encode())
    def test_safe_boundary(self):self.assertEqual(interop.normalize_record(b'{"x":9007199254740991}'),b'{"x":9007199254740991}')
    def test_large_identifier_string(self):self.assertEqual(interop.normalize_record(b'{"id":"9007199254740993"}'),b'{"id":"9007199254740993"}')
    def test_underflow(self):
        with self.assertRaisesRegex(Blocked,'UNDERFLOW'):interop.normalize_record(b'{"x":1e-400}')
    def test_small_subnormal(self):self.assertEqual(interop.normalize_record(b'{"x":5e-324}'),b'{"x":5e-324}')
    def test_unpaired_surrogate_value(self):
        with self.assertRaises(Blocked):interop.normalize_record(b'{"x":"\\ud800"}')
    def test_unpaired_surrogate_key(self):
        with self.assertRaises(Blocked):interop.normalize_record(b'{"\\ud800":1}')
    def test_invalid_utf8(self):
        with self.assertRaises(Blocked):interop.normalize_record(b'{"x":"\xff"}')
    def test_bom(self):
        with self.assertRaises(Blocked):interop.normalize_record(b'\xef\xbb\xbf{}')
    def test_root_must_object(self):
        for b in [b'null',b'1',b'true',b'[]',b'"x"']:
            with self.subTest(value=b),self.assertRaisesRegex(Blocked,'OBJECT_REQUIRED'):interop.normalize_record(b)
    def test_input_type(self):
        for x in ['{}',{},None,True]:
            with self.subTest(x=x),self.assertRaises(Blocked):interop.normalize_record(x)
    def test_malformed(self):
        for x in [b'',b'{',b'{}{}',b'{"a":}',b'{/*x*/}',b'{"a":01}']:
            with self.subTest(x=x),self.assertRaises(Blocked):interop.normalize_record(x)
    def test_depth(self):
        b=b'{"a":'+b'['*70+b'0'+b']'*70+b'}'
        with self.assertRaisesRegex(Blocked,'DEPTH'):interop.normalize_record(b)
    def test_size(self):
        with self.assertRaisesRegex(Blocked,'SIZE'):interop.normalize_record(b' '*1000001)
    def test_input_not_changed(self):
        b=b'{ "x":1 }';before=b[:];interop.normalize_record(b);self.assertEqual(b,before)
    def test_array_order_not_normalized(self):self.assertNotEqual(interop.fingerprint(b'{"a":[1,2]}')['canonical_sha256'],interop.fingerprint(b'{"a":[2,1]}')['canonical_sha256'])
    def test_unicode_not_normalized(self):self.assertNotEqual(interop.fingerprint('{"a":"é"}'.encode())['canonical_sha256'],interop.fingerprint('{"a":"é"}'.encode())['canonical_sha256'])
    def test_formatting_same_canonical_not_same_source(self):
        a=interop.fingerprint(b'{"b":2,"a":1}');b=interop.fingerprint(b'{ "a":1.0,"b":2.0 }');self.assertEqual(a['canonical_sha256'],b['canonical_sha256']);self.assertNotEqual(a['source_sha256'],b['source_sha256'])
    def test_receipt_no_auth_claim(self):
        r=interop.fingerprint(b'{}');self.assertFalse(r['authenticated']);self.assertFalse(r['schema_validated']);self.assertFalse(r['release_approved'])
    def test_receipt_verify(self):self.assertTrue(interop.verify_record(b'{"a":1}',interop.fingerprint(b'{"a":1}'))['matched'])
    def test_receipt_reformatted(self):
        v=interop.verify_record(b'{ "a":1.0 }',interop.fingerprint(b'{"a":1}'));self.assertTrue(v['matched']);self.assertFalse(v['raw_bytes_equal'])
    def test_receipt_tamper(self):
        r=interop.fingerprint(b'{}');r['canonical_sha256']='0'*64
        with self.assertRaises(Blocked):interop.verify_record(b'{}',r)
    def test_receipt_schema(self):
        for field,value in [('authenticated',True),('release_approved',True),('scheme','other'),('canonical_bytes',True)]:
            r=interop.fingerprint(b'{}');r[field]=value
            with self.subTest(field=field),self.assertRaises(Blocked):interop.verify_record(b'{}',r)
    def test_no_legacy_changes(self):
        self.assertEqual(canonical({'x':1.0}),b'{"x":1.0}');self.assertEqual(canonical({'a':'é'}),b'{"a":"\\u00e9"}')
    def test_component_pin_gate(self):
        with patch('forge_core.interop.PINS',{'missing.py':'0'*64}),self.assertRaises(Blocked):interop.normalize_record(b'{}')
    def test_contract_loaded(self):self.assertEqual(interop.contract()['implementation_id'],'bundled_rfc8785_record_v1')

if __name__=='__main__':unittest.main()
