#!/usr/bin/env python3
"""Verify a downloaded Forge CI evidence ZIP without extracting or executing it.

Checks the expected commit, optional provider archive digest, exact membership,
provenance hashes, checksum list and qualification-status binding. This proves
internal consistency against the supplied identities, not independent execution,
scientific validity, safety or release approval. Failure evidence can be intact.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

MAX_ARCHIVE_BYTES = 64_000_000
MAX_FILES = 10_000
MAX_MEMBER_BYTES = 32_000_000
MAX_EXPANDED_BYTES = 256_000_000


class EvidenceError(ValueError):
    pass


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _hex(value, length):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{%d}' % length, value) is not None


def _name(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise EvidenceError('UNSAFE_PATH')
    p = PurePosixPath(value)
    if (p.is_absolute() or p.as_posix() != value or any(x in ('.', '..') for x in p.parts)
            or '\\' in value or ':' in value or any(ord(x) < 32 or ord(x) == 127 for x in value)):
        raise EvidenceError('UNSAFE_PATH')
    return value


def _json(data):
    def pairs(rows):
        out = {}
        for k, v in rows:
            if k in out:
                raise EvidenceError('DUPLICATE_JSON_KEY')
            out[k] = v
        return out
    def bad_constant(_):
        raise EvidenceError('NONFINITE_JSON')
    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=bad_constant)
    except (ValueError, UnicodeError, RecursionError) as ex:
        if isinstance(ex, EvidenceError):
            raise
        raise EvidenceError('INVALID_JSON') from ex


def verify(path, expected_commit, artifact_sha256=None):
    """Read only an explicit ZIP. Never follow archive paths onto the filesystem."""
    if not _hex(expected_commit, 40):
        raise EvidenceError('EXPECTED_COMMIT_REQUIRED')
    if artifact_sha256 is not None and not _hex(artifact_sha256, 64):
        raise EvidenceError('INVALID_ARCHIVE_DIGEST')
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise EvidenceError('ARCHIVE_FILE_OR_SIZE')
    # Keep one file handle for digest and all ZIP reads; no path re-open between them.
    with path.open('rb') as stream:
        archive_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
        if artifact_sha256 is not None and archive_hash != artifact_sha256:
            raise EvidenceError('ARCHIVE_DIGEST_MISMATCH')
        stream.seek(0)
        with zipfile.ZipFile(stream) as z:
            infos = z.infolist()
            if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_EXPANDED_BYTES:
                raise EvidenceError('ARCHIVE_LIMIT')
            members = {}
            paths = set()
            for info in infos:
                name = _name(info.filename[:-1] if info.is_dir() else info.filename)
                if name in paths:
                    raise EvidenceError('DUPLICATE_ARCHIVE_PATH')
                paths.add(name)
                mode = stat.S_IFMT(info.external_attr >> 16)
                if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or info.flag_bits & 1:
                    raise EvidenceError('UNSAFE_ARCHIVE_MEMBER')
                if info.file_size > MAX_MEMBER_BYTES:
                    raise EvidenceError('MEMBER_LIMIT')
                if not info.is_dir():
                    members[name] = info
            if not {'provenance.json', 'SHA256SUMS.txt'} <= set(members):
                raise EvidenceError('MANIFEST_MISSING')
            provenance = _json(z.read(members['provenance.json']))
            if not isinstance(provenance, dict) or provenance.get('commit_sha') != expected_commit:
                raise EvidenceError('COMMIT_MISMATCH')
            declared = provenance.get('files')
            if not isinstance(declared, dict) or not declared or len(declared) > MAX_FILES:
                raise EvidenceError('FILE_MANIFEST')
            for name, digest in declared.items():
                _name(name)
                if name in ('provenance.json', 'SHA256SUMS.txt') or not _hex(digest, 64):
                    raise EvidenceError('FILE_MANIFEST')
            wanted = set(declared) | {'provenance.json', 'SHA256SUMS.txt'}
            if set(members) != wanted:
                raise EvidenceError('FILE_SET_MISMATCH: missing=%d extra=%d' %
                                    (len(wanted - set(members)), len(set(members) - wanted)))
            actual = {name: _sha(z.read(info)) for name, info in members.items()}
            if any(actual[name] != digest for name, digest in declared.items()):
                raise EvidenceError('FILE_HASH_MISMATCH')
            checksums = {}
            for line in z.read(members['SHA256SUMS.txt']).decode('utf-8').splitlines():
                if not re.match(r'^[0-9a-f]{64}  ', line):
                    raise EvidenceError('CHECKSUM_FORMAT')
                digest, name = line[:64], line[66:]
                name = _name(name[2:] if name.startswith('./') else name)
                if name in checksums:
                    raise EvidenceError('DUPLICATE_CHECKSUM')
                checksums[name] = digest
            if checksums != {n: h for n, h in actual.items() if n != 'SHA256SUMS.txt'}:
                raise EvidenceError('CHECKSUM_MANIFEST_MISMATCH')
            report_name = 'qualification/verification.json'
            if report_name in members:
                report = _json(z.read(members[report_name]))
                if (not isinstance(report, dict) or provenance.get('verification_sha256') != actual[report_name]
                        or provenance.get('verification_status') != report.get('status')):
                    raise EvidenceError('QUALIFICATION_BINDING')
                status = report.get('status')
            else:
                if provenance.get('verification_status') != 'MISSING' or provenance.get('verification_sha256') is not None:
                    raise EvidenceError('QUALIFICATION_BINDING')
                status = 'MISSING'
    return {'status': 'EVIDENCE_INTEGRITY_VERIFIED', 'commit_sha': expected_commit,
            'artifact_sha256': archive_hash, 'provider_digest_checked': artifact_sha256 is not None,
            'files_verified': len(members), 'declared_files': len(declared),
            'hidden_files_verified': sum(any(p.startswith('.') for p in PurePosixPath(n).parts) for n in members),
            'job_status': provenance.get('job_status'), 'qualification_status': status,
            'reported_qualification_passed': provenance.get('job_status') == 'success' and status == 'PASSED_FOR_LOCAL_DECLARED_SCOPE',
            'independent_execution_verified': False, 'release_approved': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--artifact-sha256')
    args = parser.parse_args()
    try:
        result = verify(args.archive, args.commit, args.artifact_sha256)
    except (EvidenceError, OSError, zipfile.BadZipFile, UnicodeError, RuntimeError) as ex:
        result = {'status': 'BLOCKED', 'reason': str(ex) if isinstance(ex, EvidenceError) else type(ex).__name__,
                  'release_approved': False}
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result['status'] == 'EVIDENCE_INTEGRITY_VERIFIED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
