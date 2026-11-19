#!/usr/bin/env python3
"""Verify runner package hashes and, in a Forge checkout, inventory membership."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = 'FILE_HASHES.json'


def tracked_files(root):
    output = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z', '--cached', '--', '.'])
    return {os.fsdecode(name) for name in output.split(b'\0') if name} - {MANIFEST}


def file_hash(root, name):
    path = root / name
    if (name == MANIFEST or Path(name).is_absolute() or path.resolve() != path
            or not path.is_relative_to(root) or not path.is_file()):
        raise ValueError(f'Expected a regular package file: {name}')
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check(root=ROOT, *, refresh=False):
    manifest = root / MANIFEST
    if manifest.is_symlink():
        raise ValueError('Package manifest is a symlink')
    checkout = (root.parent / '.git').exists()
    expected = json.loads(manifest.read_bytes())
    if not isinstance(expected, dict):
        raise ValueError('Expected a package file-to-hash mapping')
    if checkout:
        tracked = tracked_files(root)
    if refresh:
        if not checkout:
            raise ValueError('Refresh requires a Forge Git checkout')
        hashes = {name: file_hash(root, name) for name in sorted(tracked)}
        changed_contracts = [name for name in expected if name.startswith('contracts/')
                             and expected[name] != hashes.get(name)]
        if changed_contracts:
            raise ValueError('Historical contract differs from its recorded digest: '
                             + ', '.join(sorted(changed_contracts)))
        # Publish only after every tracked file was successfully hashed.
        fd, temporary = tempfile.mkstemp(dir=root)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
                stream.write(json.dumps(hashes, indent=2) + '\n')
            os.chmod(temporary, manifest.stat().st_mode & 0o777)
            os.replace(temporary, manifest)
        finally:
            Path(temporary).unlink(missing_ok=True)
        expected = hashes
    bad = []
    for name, digest in expected.items():
        try:
            if file_hash(root, name) == digest:
                continue
        except (OSError, ValueError):
            pass
        bad.append(name)
    unlisted = sorted(tracked - expected.keys()) if checkout else []
    removed = sorted(expected.keys() - tracked) if checkout else []
    result = {'manifest_files': len(expected), 'mismatches': sorted(bad),
              'unlisted_files': unlisted, 'untracked_manifest_entries': removed,
              'inventory_scope': 'GIT_CHECKOUT' if checkout else 'MANIFEST_ENTRIES_ONLY',
              'consistent': not (bad or unlisted or removed), 'release_approved': False}
    print(json.dumps(result, indent=2))
    return 0 if result['consistent'] else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true', help='refresh hashes of tracked runner files')
    try:
        sys.exit(check(refresh=parser.parse_args().refresh))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'Runner package check failed: {exc}', file=sys.stderr)
        sys.exit(2)
