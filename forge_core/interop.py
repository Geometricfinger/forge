"""Opt-in, pinned JCS record adapter. Existing FORGE evidence serialization is unchanged.

Only UTF-8 bytes are accepted. Parsing preserves strings and array order and
rejects inputs outside a conservative, explicitly versioned numeric domain.
A digest is not a signature, source-truth check, or license clearance.
"""
from __future__ import annotations
import hashlib, importlib, json, math, re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from .common import Blocked, read, sha, loads
ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 1_000_000
MAX_DEPTH = 64
MAX_NODES = 100_000
MAX_SAFE_INTEGER = 9007199254740991
SCHEME = 'forge-jcs-safe-number-v1:sha256'
PINS = {'forge_core/_vendor/rfc8785/_impl.py': 'd841b6d81ceefff296b528f54836b4d7612fab69fe7a48ebef8590eeb512637b', 'forge_core/_vendor/rfc8785/__init__.py': 'fa44927afd547caf7547247078bcf28863d1e69caf116d258c532b3f20ffd154', 'contracts/interop/capability.json': 'abf0c1fbcba536c8be247f76a3b543d09773dabab33a720b3ad645f50617d042', 'contracts/interop/rfc8785_numbers.json': '5aa923ceab5586ea3b309d7f25f5e59ab031f26017b801b395e4bffb6d2c35f3', 'contracts/interop/rfc8785_objects.json': 'b09c890f8bbaf48bc466aa33903a1fa4e86a07dcfbc20e1d24fcfcea146ff191', 'third_party/rfc8785/PROVENANCE.json': '9fb64cf58e98a529ce83a337c8a67232660cedf176d55ba8fdd6b67e7af37f67', 'third_party/rfc8785/LICENSE': '0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594', 'tools/interop_reference.cjs': '1479e723e00048548719ce3032a6a7562acc7c60fccde72815c171802e1852d2'}


def verify_pins():
    for name, expected in PINS.items():
        try: actual = sha(read(ROOT / name, 100_000))
        except (OSError, ValueError) as exc: raise Blocked('COMPONENT_BINDING_UNAVAILABLE') from exc
        if actual != expected: raise Blocked('COMPONENT_BINDING_CHANGED')
    return dict(PINS)


def contract():
    verify_pins()
    return loads(read(ROOT / 'contracts/interop/capability.json'))


def _depth_check(text):
    quoted = False; escaped = False; depth = 0
    for c in text:
        if quoted:
            if escaped: escaped = False
            elif c == '\\': escaped = True
            elif c == '"': quoted = False
        elif c == '"': quoted = True
        elif c in '[{':
            depth += 1
            if depth > MAX_DEPTH: raise Blocked('JSON_DEPTH_LIMIT')
        elif c in ']}': depth -= 1


def _number(token):
    if len(token) > 128: raise Blocked('NUMBER_TOKEN_LIMIT')
    try:
        exact = Decimal(token)
        if not exact.is_finite(): raise Blocked('NONFINITE_JSON')
        if exact == exact.to_integral_value() and abs(exact) > MAX_SAFE_INTEGER:
            raise Blocked('UNSAFE_INTEGER_USE_STRING')
        f = float(token)
        if not math.isfinite(f): raise Blocked('NONFINITE_JSON')
        if f == 0 and exact != 0: raise Blocked('NUMBER_UNDERFLOW')
        if f.is_integer() and abs(f) > MAX_SAFE_INTEGER:
            raise Blocked('UNSAFE_INTEGER_USE_STRING')
        return int(token) if not any(c in token for c in '.eE') else f
    except (InvalidOperation, OverflowError) as exc: raise Blocked('INVALID_NUMBER') from exc


def _pairs(rows):
    result = {}
    for k, v in rows:
        if k in result: raise Blocked('DUPLICATE_JSON_KEY')
        result[k] = v
    return result


def _reject_constant(value):
    raise Blocked('NONFINITE_JSON')


def _decode(raw):
    if type(raw) is not bytes: raise Blocked('INPUT_MUST_BE_UTF8_BYTES')
    if not raw or len(raw) > MAX_BYTES: raise Blocked('JSON_SIZE_LIMIT')
    try:
        text = raw.decode('utf-8', errors='strict')
        _depth_check(text)
        obj = json.loads(text, object_pairs_hook=_pairs, parse_int=_number,
                         parse_float=_number, parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise Blocked('INVALID_JSON_OR_UNICODE') from exc
    if type(obj) is not dict: raise Blocked('OBJECT_REQUIRED')
    pending = [obj]; count = 0
    while pending:
        value = pending.pop(); count += 1
        if count > MAX_NODES: raise Blocked('JSON_NODE_LIMIT')
        if type(value) is dict:
            pending.extend(value.keys()); pending.extend(value.values())
        elif type(value) is list: pending.extend(value)
        elif type(value) is str:
            try: value.encode('utf-8', errors='strict')
            except UnicodeError as exc: raise Blocked('INVALID_UNICODE') from exc
    return obj


def normalize_record(raw: bytes) -> bytes:
    """Canonicalize an admitted JSON object. No network, file writes or user code."""
    verify_pins()  # The vendor is not imported before its exact files are checked.
    obj = _decode(raw)
    backend = importlib.import_module('forge_core._vendor.rfc8785')
    try: data = backend.dumps(obj)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Blocked('CANONICALIZATION_FAILED') from exc
    if len(data) > MAX_BYTES: raise Blocked('CANONICAL_SIZE_LIMIT')
    return data


def fingerprint(raw: bytes) -> dict:
    data = normalize_record(raw)
    return {'schema_version':1,'scheme':SCHEME,
            'canonical_sha256':sha(data),'source_sha256':sha(raw),
            'canonical_bytes':len(data),
            'contract_sha256':PINS['contracts/interop/capability.json'],
            'component_sha256':PINS['forge_core/_vendor/rfc8785/_impl.py'],
            'authenticated':False,'schema_validated':False,'release_approved':False}


def verify_record(raw: bytes, receipt: dict) -> dict:
    expected = fingerprint(raw)
    if type(receipt) is not dict or set(receipt) != set(expected): raise Blocked('RECEIPT_FIELDS')
    for field in ('schema_version','canonical_bytes'):
        if type(receipt[field]) is not int: raise Blocked('RECEIPT_INTEGER')
    for field in ('authenticated','schema_validated','release_approved'):
        if receipt[field] is not False: raise Blocked('RECEIPT_AUTHORITY')
    for field in ('canonical_sha256','source_sha256','contract_sha256','component_sha256'):
        if type(receipt[field]) is not str or re.fullmatch('[0-9a-f]{64}',receipt[field]) is None:
            raise Blocked('RECEIPT_DIGEST')
    for field in set(expected)-{'source_sha256'}:
        if receipt[field] != expected[field]: raise Blocked('RECEIPT_MISMATCH')
    return {'matched':True,'raw_bytes_equal':receipt['source_sha256']==expected['source_sha256'],
            'scheme':SCHEME,'authenticated':False,'schema_validated':False,'release_approved':False}
