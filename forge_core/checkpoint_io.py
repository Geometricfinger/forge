"""Bounded internal checkpoint storage with legacy raw-JSON compatibility.

The wrapper and its result digest keep their original canonical JSON contract.
New files use an explicit .json.gz suffix; an existing .json is read and updated
in place. Two representations of one checkpoint are a conflict, not a priority
rule. These are local consistency checks, not execution authentication.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import zlib

from .common import Blocked, canonical, loads, read, safe_path, sha
from .streaming import gzip_chunks, write_chunks


MAX_CHECKPOINT_BYTES = 20_000_000
CACHE_COMPRESSION_LEVEL = 1


@dataclass(frozen=True)
class EncodedResult:
    data: bytes
    sha256: str

    @property
    def byte_count(self):
        return len(self.data)


@dataclass(frozen=True)
class LoadedResult:
    result: dict
    result_sha256: str
    result_bytes: int


def encode_result(result):
    """Encode once using the C-backed canonical encoder, then hash those bytes."""
    data = canonical(result)
    return EncodedResult(data, sha(data))


def _paths(path):
    path = Path(path)
    if path.name.endswith('.json.gz'):
        raw = path.with_suffix('')
    elif path.suffix == '.json':
        raw = path
    else:
        raise Blocked('CORPUS_CHECKPOINT_FORMAT')
    raw = safe_path(raw)
    compressed = safe_path(raw.with_suffix('.json.gz'))
    if raw.exists() and compressed.exists():
        raise Blocked('CORPUS_CHECKPOINT_FORMAT_CONFLICT')
    return raw, compressed


def resolve(path):
    """Choose an existing representation, or the new explicit gzip path."""
    raw, compressed = _paths(path)
    return raw if raw.exists() else compressed


def _decoded(path):
    stored = read(path, MAX_CHECKPOINT_BYTES)
    if not path.name.endswith('.json.gz'):
        return stored
    try:
        decoder = zlib.decompressobj(wbits=31)
        # One positive bound admits at most one byte beyond the decoded ceiling.
        # No flush: its length argument would not cap total decompressed output.
        decoded = decoder.decompress(stored, MAX_CHECKPOINT_BYTES + 1)
    except zlib.error as error:
        raise Blocked('CORPUS_CHECKPOINT_COMPRESSION') from error
    if len(decoded) > MAX_CHECKPOINT_BYTES:
        raise Blocked('CORPUS_CHECKPOINT_SIZE')
    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise Blocked('CORPUS_CHECKPOINT_COMPRESSION')
    return decoded


def load(path, binding):
    """Validate one strict, size-bounded wrapper and reuse its result digest."""
    raw, compressed = _paths(path)
    path = compressed if str(path).endswith('.json.gz') else raw
    obj = loads(_decoded(path))
    if (not isinstance(obj, dict)
            or set(obj) != {'binding', 'result_sha256', 'result'}
            or obj['binding'] != binding):
        raise Blocked('CORPUS_CHECKPOINT_INVALID')
    encoded = encode_result(obj['result'])
    if encoded.sha256 != obj['result_sha256']:
        raise Blocked('CORPUS_CHECKPOINT_INVALID')
    if (not isinstance(obj['result'], dict)
            or obj['result'].get('upstream_code_executed') is not False):
        raise Blocked('CORPUS_CHECKPOINT_AUTHORITY')
    return LoadedResult(obj['result'], encoded.sha256, encoded.byte_count)


def write_checkpoint(path, binding, encoded, *, new=False):
    """Atomically publish the exact canonical wrapper without re-encoding it."""
    raw, compressed = _paths(path)
    path = compressed if str(path).endswith('.json.gz') else raw
    peer = raw if path == compressed else compressed
    if peer.exists():
        raise Blocked('CORPUS_CHECKPOINT_FORMAT_CONFLICT')
    # Canonical key order is binding, result, result_sha256.
    prefix = b'{"binding":' + canonical(binding) + b',"result":'
    suffix = b',"result_sha256":' + canonical(encoded.sha256) + b'}'
    if len(prefix) + encoded.byte_count + len(suffix) > MAX_CHECKPOINT_BYTES:
        raise Blocked('CORPUS_CHECKPOINT_SIZE')
    chunks = (prefix, encoded.data, suffix)
    if path == compressed:
        chunks = gzip_chunks(chunks, level=CACHE_COMPRESSION_LEVEL)

    def bounded():
        size = 0
        for chunk in chunks:
            size += len(chunk)
            if size > MAX_CHECKPOINT_BYTES:
                raise Blocked('CORPUS_CHECKPOINT_SIZE')
            yield chunk

    return write_chunks(path, bounded(), new=new)
