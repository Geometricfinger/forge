"""Bounded-memory publication of canonical metadata and compressed records.

The on-disk JSON format is unchanged. Each output is replaced only after its
complete stream has been written and synchronized; its digest covers those
exact bytes. These are trusted local filesystem consistency controls.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zlib

from .common import Blocked, safe_path


def json_text_chunks(value, **options):
    """Buffer encoder fragments without retaining a complete JSON string."""
    encoder = json.JSONEncoder(**options)
    pieces = []
    size = 0
    for piece in encoder.iterencode(value):
        pieces.append(piece)
        size += len(piece)
        if size >= 65_536:
            yield ''.join(pieces)
            pieces = []
            size = 0
    if pieces:
        yield ''.join(pieces)


def canonical_chunks(value):
    """Use the same JSON settings as common.canonical, without a full copy."""
    for chunk in json_text_chunks(value, sort_keys=True, separators=(',', ':'),
                                  ensure_ascii=True, allow_nan=False):
        yield chunk.encode('ascii')


def gzip_chunks(chunks, *, level=9):
    """One deterministic gzip member; no complete uncompressed cart in RAM."""
    compressor = zlib.compressobj(level=level, wbits=31)
    for chunk in chunks:
        encoded = compressor.compress(chunk)
        if encoded:
            yield encoded
    yield compressor.flush()


def write_chunks(path, chunks, *, new=False, limit=None):
    """Atomically write byte chunks and return their SHA-256 and byte count."""
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (new or not path.is_file() or path.stat().st_nlink != 1):
        raise Blocked('OUTPUT_CONFLICT')
    digest = hashlib.sha256()
    size = 0
    fd, temporary = tempfile.mkstemp(prefix='.forge-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            for chunk in chunks:
                if not isinstance(chunk, bytes):
                    raise TypeError('Publication chunks must be bytes')
                size += len(chunk)
                if limit is not None and size > limit:
                    raise Blocked('CORPUS_REPORT_SIZE_LIMIT')
                stream.write(chunk)
                digest.update(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if new:
            os.link(temporary, path)
            os.unlink(temporary)
        else:
            os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {'sha256': digest.hexdigest(), 'bytes': size}
