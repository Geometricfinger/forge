"""Reviewed first-party examples for the fixed JSON-record reuse trial.

These are not downloaded code. The strict candidate reuses FORGE's existing JSON
validation and serialization helpers. It is Python-representation specific, not
RFC 8785/JCS, not a signature, and not a universal cross-language canonicalizer.
"""
import hashlib
import json
from forge_core.common import Blocked, canonical, loads


def simple_json_fingerprint(raw: bytes) -> str:
    """Deliberately limited comparison candidate, not a historical production bug."""
    value = json.loads(raw)
    return hashlib.sha256(repr(value).encode('utf-8')).hexdigest()


def strict_json_fingerprint(raw: bytes) -> str:
    """Return a content digest of a bounded, strict JSON object using FORGE helpers.

    Dictionary ordering is normalized. Number spellings, Unicode normalization,
    input versions and application meaning are NOT generally normalized.
    """
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 100_000:
        raise Blocked('JSON_RECORD_BYTE_LIMIT')
    value = loads(raw)
    if not isinstance(value, dict):
        raise Blocked('JSON_OBJECT_REQUIRED')
    return hashlib.sha256(canonical(value)).hexdigest()
