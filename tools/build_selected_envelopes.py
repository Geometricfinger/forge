#!/usr/bin/env python3
"""Verify six approved source copies and build the frozen selected-file envelopes.

No network calls, imports of discovered code, or complete repository downloads.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', required=True, type=Path)
    parser.add_argument('--contracts', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    source_root = args.source_root.resolve(strict=True)
    contracts = args.contracts.resolve(strict=True)
    output = args.out.resolve()
    if output.exists() or output.is_relative_to(source_root) or output.is_relative_to(contracts):
        raise ValueError('Use a new output directory outside source and contract directories')
    seals = json.loads((contracts / 'FROZEN_HASHES.json').read_text())
    for name, expected in seals.items():
        if digest((contracts / name).read_bytes()) != expected:
            raise ValueError('Frozen contract changed')
    manifest = json.loads((contracts / 'SOURCE_MANIFEST.json').read_text())
    envelopes = json.loads((contracts / 'CAPSULE_MANIFEST.json').read_text())['files']
    blobs: dict[str, bytes] = {}
    for record in manifest['files']:
        relative = PurePosixPath(record['path'])
        if relative.is_absolute() or '..' in relative.parts or '\\' in record['path']:
            raise ValueError('Unsafe source path')
        target = source_root.joinpath(*relative.parts)
        cursor = target
        while cursor != source_root:
            if cursor.is_symlink():
                raise ValueError('Linked sources are not accepted')
            cursor = cursor.parent
        if not target.resolve(strict=True).is_relative_to(source_root):
            raise ValueError('Source leaves approved root')
        data = target.read_bytes()
        blob_sha = hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()
        if len(data) != record['bytes'] or digest(data) != record['sha256'] or blob_sha != record['git_blob_sha1']:
            raise ValueError('Source version mismatch: ' + record['path'])
        blobs[record['path']] = data
    prepared: list[tuple[str, bytes]] = []
    for envelope in envelopes:
        memory = io.BytesIO()
        with zipfile.ZipFile(memory, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name in envelope['selected_paths']:
                info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, blobs[name])
        data = memory.getvalue()
        if digest(data) != envelope['sha256'] or len(data) != envelope['size']:
            raise ValueError('Envelope bytes differ from frozen run; do not rewrite the expected digest')
        prepared.append((envelope['path'], data))
    output.mkdir(parents=True, exist_ok=False)
    for name, data in prepared:
        with (output / name).open('xb') as stream:
            stream.write(data)
    print(json.dumps({'status': 'EXACT_SELECTED_ENVELOPES_REPRODUCED', 'source_files': len(blobs), 'envelopes': len(prepared), 'upstream_code_executed': False}))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
