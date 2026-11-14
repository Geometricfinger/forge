#!/usr/bin/env python3
"""Check bounded CI evidence for accidental disclosure before staging an upload.

This is pattern-based publication lint, not a guarantee that data contains no
secret and not an execution sandbox. Inputs are never imported or executed.
Only exact original bytes that passed this check enter the new output folder.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import struct
import subprocess
import sys
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import prepublish_check as privacy
from tools import verify_ci_evidence as integrity

MAX_FILES = 10_000
MAX_FILE_BYTES = 32_000_000
MAX_INPUT_BYTES = 256_000_000
MAX_SCAN_BYTES = 512_000_000
MAX_DEPTH = 4
MAX_SQLITE_ROWS = 100_000
MAX_JSON_VALUES = 4_000_000
EXCLUDED = ('qualification', 'home')
CREDENTIAL_NAMES = {'.env', '.netrc', '.npmrc', '.pypirc', '.git-credentials',
                    'credentials', 'credentials.json', 'credentials.ini',
                    'cookies.txt', 'cookies.sqlite', 'id_rsa', 'id_dsa',
                    'id_ecdsa', 'id_ed25519', 'access_token', 'refresh_token',
                    'auth_token', '.token', 'token.txt', 'token.json'}


class PublicationError(ValueError):
    """The message is a fixed public code, never an input value or path."""


def require(condition, code):
    if not condition:
        raise PublicationError(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def path_checked(value):
    path = Path(value).absolute()
    require('..' not in path.parts, 'PATH_TRAVERSAL')
    for item in (*reversed(path.parents), path):
        require(not item.is_symlink(), 'SYMLINK_PATH')
    require(all(not p.exists() or p.is_dir() for p in path.parents), 'PATH_ANCESTOR')
    return path


def overlap(left, right):
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def safe_name(value):
    try:
        return integrity._name(value)
    except integrity.EvidenceError as exc:
        raise PublicationError('UNSAFE_MEMBER_NAME') from exc


def read_regular(path):
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_size <= MAX_FILE_BYTES, 'FILE_TYPE_OR_SIZE')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        actual = os.fstat(stream.fileno())
        require((before.st_dev, before.st_ino, before.st_size) ==
                (actual.st_dev, actual.st_ino, actual.st_size), 'INPUT_CHANGED')
        data = stream.read(MAX_FILE_BYTES + 1)
        after = os.fstat(stream.fileno())
    require(len(data) == before.st_size and (actual.st_size, actual.st_mtime_ns) ==
            (after.st_size, after.st_mtime_ns), 'INPUT_CHANGED')
    return data


def collect(source):
    files = {}; total = 0
    for current, dirs, names in os.walk(source, followlinks=False):
        folder = Path(current)
        for name in list(dirs):
            path = folder / name
            rel = path.relative_to(source).parts
            require(not path.is_symlink(), 'SYMLINK_MEMBER')
            if rel == EXCLUDED:
                dirs.remove(name)
            else:
                safe_name(path.relative_to(source).as_posix())
        for name in names:
            path = folder / name
            rel = safe_name(path.relative_to(source).as_posix())
            data = read_regular(path); total += len(data)
            require(len(files) < MAX_FILES and total <= MAX_INPUT_BYTES, 'INPUT_LIMIT')
            files[rel] = data
    require(files, 'EMPTY_EVIDENCE')
    return files


def verify_files(files, expected_commit):
    """The same exact-set/hash/status contract as the downloadable ZIP verifier."""
    require(integrity._hex(expected_commit, 40), 'EXPECTED_COMMIT_REQUIRED')
    require({'provenance.json', 'SHA256SUMS.txt'} <= files.keys(), 'MANIFEST_MISSING')
    try:
        provenance = integrity._json(files['provenance.json'])
        require(isinstance(provenance, dict) and provenance.get('commit_sha') == expected_commit,
                'COMMIT_MISMATCH')
        declared = provenance.get('files')
        require(isinstance(declared, dict) and 0 < len(declared) <= MAX_FILES, 'FILE_MANIFEST')
        for name, digest in declared.items():
            safe_name(name)
            require(name not in ('provenance.json', 'SHA256SUMS.txt') and integrity._hex(digest, 64),
                    'FILE_MANIFEST')
        require(set(files) == set(declared) | {'provenance.json', 'SHA256SUMS.txt'}, 'FILE_SET_MISMATCH')
        actual = {name: sha(data) for name, data in files.items()}
        require(all(actual[name] == digest for name, digest in declared.items()), 'FILE_HASH_MISMATCH')
        sums = {}
        for line in files['SHA256SUMS.txt'].decode('utf-8').splitlines():
            require(re.match(r'^[0-9a-f]{64}  ', line) is not None, 'CHECKSUM_FORMAT')
            name = safe_name(line[68:] if line[66:].startswith('./') else line[66:])
            require(name not in sums, 'DUPLICATE_CHECKSUM')
            sums[name] = line[:64]
        require(sums == {n: h for n, h in actual.items() if n != 'SHA256SUMS.txt'}, 'CHECKSUM_MISMATCH')
        report_name = 'qualification/verification.json'
        if report_name in files:
            report = integrity._json(files[report_name])
            require(isinstance(report, dict) and provenance.get('verification_sha256') == actual[report_name]
                    and provenance.get('verification_status') == report.get('status'), 'QUALIFICATION_BINDING')
            status = report.get('status')
        else:
            require(provenance.get('verification_status') == 'MISSING'
                    and provenance.get('verification_sha256') is None, 'QUALIFICATION_BINDING')
            status = 'MISSING'
    except (integrity.EvidenceError, UnicodeError) as exc:
        raise PublicationError('INVALID_MANIFEST') from exc
    require(type(status) is str, 'QUALIFICATION_STATUS')
    public_status = status if status in {'PASSED_FOR_LOCAL_DECLARED_SCOPE', 'FAILED',
                                        'FAILED_QUALIFICATION', 'BLOCKED', 'MISSING'} else 'OTHER'
    return {'commit_sha': expected_commit, 'files_verified': len(files),
            'qualification_status': public_status,
            'reported_qualification_passed': provenance.get('job_status') == 'success'
            and status == 'PASSED_FOR_LOCAL_DECLARED_SCOPE'}


def source_exceptions(repo):
    """Map exact current tracked source bytes to its reviewed literal exceptions."""
    result = {}
    paths = subprocess.run(['git', '-C', str(repo), 'ls-files', '-z'], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
    tracked = set(paths.decode('utf-8').split('\0'))
    allow = privacy.load_allowlist()
    for entry in allow:
        name = entry['path']
        if name not in tracked or not entry.get('contains') or entry.get('history_only'):
            continue
        path = repo / safe_name(name)
        data = read_regular(path_checked(path))
        result.setdefault(sha(data), []).append((name, entry))
    return result


class Scanner:
    def __init__(self, patterns, exceptions=None, ci_runner_paths=False):
        self.patterns = patterns
        self.exceptions = exceptions or {}
        self.ci_runner_paths = ci_runner_paths
        self.counts = Counter(); self.rules = Counter(); self.details = []
        self.bytes = 0; self.payloads = 0; self.json_values = 0; self.sqlite_rows = 0

    def finding(self, rule, label):
        self.rules[rule] += 1
        if len(self.details) < 100:
            self.details.append({'rule': rule, 'input_id': sha(label.encode('utf-8'))})

    def text(self, label, text, allowances=()):
        for line in text.splitlines():
            for rule, pattern in self.patterns.items():
                for match in pattern.finditer(line):
                    if (rule == 'home_path' and self.ci_runner_paths
                            and match.group(0) == '/home/' + 'runner'
                            and (match.end() == len(line) or line[match.end()] in '/\"\' \t\r\n\0')):
                        self.counts['ci_runner_path_matches'] += 1
                        continue
                    if any(privacy.allowed(path, line, match, [entry]) for path, entry in allowances):
                        self.counts['source_fixture_matches'] += 1
                        continue
                    self.finding(rule, label)

    def member_name(self, label, name):
        safe_name(name)
        for part in PurePosixPath(name).parts:
            lower = part.lower()
            if (lower in CREDENTIAL_NAMES or lower.startswith('.env.') or
                    lower.endswith(('.pem', '.key', '.p12', '.pfx', '.jks', '.kdbx')) or
                    lower in ('.ssh', '.aws', '.gnupg', '.git')):
                self.finding('credential_file_name', label)
        self.text(label, name)

    def account(self, size):
        self.payloads += 1; self.bytes += size
        require(self.payloads <= MAX_FILES and self.bytes <= MAX_SCAN_BYTES and size <= MAX_FILE_BYTES,
                'SCAN_LIMIT')

    def json_text(self, label, data, name):
        suffix = PurePosixPath(name).suffix.lower()
        if suffix not in ('.json', '.ipynb', '.har', '.jsonl', '.ndjson', '.cast'):
            return
        records = [data] if suffix in ('.json', '.ipynb', '.har') else data.splitlines()
        for raw in records:
            if not raw.strip():
                continue
            try:
                value = integrity._json(raw)
            except integrity.EvidenceError as exc:
                raise PublicationError('UNREADABLE_JSON_CONTENT') from exc
            # The raw text was already scanned. Only escaped string literals
            # need a second content scan; this keeps multi-million-node corpus
            # reports bounded without missing escaped keys or values.
            try:
                text = raw.decode('utf-16') if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else raw.decode('utf-8')
            except UnicodeError as exc:
                raise PublicationError('UNREADABLE_JSON_CONTENT') from exc
            escaped = {json.loads(match.group(0)) for match in re.finditer(r'"(?:[^"\\]|\\.)*"', text)
                       if '\\' in match.group(0)}
            pending = [(value, 0)]
            while pending:
                item, depth = pending.pop(); self.json_values += 1
                require(depth <= 64 and self.json_values <= MAX_JSON_VALUES, 'JSON_SCAN_LIMIT')
                if isinstance(item, str):
                    if item in escaped:
                        self.text(label, item)
                elif isinstance(item, dict):
                    for key, child in item.items():
                        if key in escaped:
                            self.text(label, key)
                        if isinstance(child, str) and (key in escaped or child in escaped):
                            self.text(label, json.dumps(key) + ':' + json.dumps(child, ensure_ascii=False))
                        pending.append((child, depth + 1))
                elif isinstance(item, list):
                    pending.extend((child, depth + 1) for child in item)

    def sqlite(self, label, data, depth):
        self.counts['sqlite_databases'] += 1
        connection = sqlite3.connect(':memory:')
        steps = 0
        def progress():
            nonlocal steps
            steps += 1
            return int(steps > 10_000)
        try:
            connection.deserialize(data)
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_FILE_BYTES)
            connection.execute('PRAGMA trusted_schema=OFF')
            connection.execute('PRAGMA query_only=ON')
            connection.set_progress_handler(progress, 1000)
            # Ordinary and shadow tables store bytes. Views and virtual-table
            # programs are not evaluated; the whole raw database is also scanned.
            tables = connection.execute('PRAGMA table_list').fetchall()
            for schema, name, kind, *_ in tables:
                if schema != 'main' or kind not in ('table', 'shadow'):
                    continue
                quoted = '"' + name.replace('"', '""') + '"'
                columns = connection.execute('PRAGMA table_xinfo(' + quoted + ')').fetchall()
                column_names = [c[1] for c in columns if c[6] in (0, 3)]
                stored = ['"' + name.replace('"', '""') + '"' for name in column_names]
                if not column_names:
                    continue
                for row in connection.execute('SELECT ' + ','.join(stored) + ' FROM ' + quoted):
                    self.sqlite_rows += 1
                    require(self.sqlite_rows <= MAX_SQLITE_ROWS, 'SQLITE_ROW_LIMIT')
                    decoded_row = {}
                    for column, value in zip(column_names, row):
                        if isinstance(value, str):
                            self.text(label, value)
                            decoded_row[column.lower()] = value
                            self.text(label, json.dumps(column) + ':' + json.dumps(value, ensure_ascii=False))
                        elif isinstance(value, bytes):
                            self.account(len(value))
                            decoded = value.decode('utf-8', 'replace')
                            self.text(label + '/sqlite-value', decoded)
                            decoded_row[column.lower()] = decoded
                            self.text(label, json.dumps(column) + ':' + json.dumps(decoded, ensure_ascii=False))
                    for key_name in ('key', 'name', 'setting', 'config_key', 'parameter'):
                        for value_name in ('value', 'config_value', 'secret', 'token', 'password'):
                            if key_name in decoded_row and value_name in decoded_row:
                                self.text(label, json.dumps(decoded_row[key_name]) + ':' +
                                          json.dumps(decoded_row[value_name], ensure_ascii=False))
            self.counts['sqlite_rows'] = self.sqlite_rows
        except sqlite3.Error as exc:
            raise PublicationError('UNREADABLE_SQLITE_CONTENT') from exc
        finally:
            connection.close()

    def gzip(self, label, name, data, depth):
        self.counts['gzip_containers'] += 1
        remaining = data; parts = []; total = 0; members = 0
        while remaining:
            require(remaining.startswith(b'\x1f\x8b\x08') and len(remaining) >= 18, 'UNREADABLE_GZIP')
            flags = remaining[3]; require(not flags & 0xe0, 'UNREADABLE_GZIP')
            offset = 10
            if flags & 4:
                require(len(remaining) >= offset + 2, 'UNREADABLE_GZIP')
                length = int.from_bytes(remaining[offset:offset + 2], 'little'); offset += 2
                require(offset + length <= len(remaining), 'UNREADABLE_GZIP')
                self.text(label, remaining[offset:offset + length].decode('utf-8', 'replace')); offset += length
            for bit in (8, 16):
                if flags & bit:
                    end = remaining.find(b'\0', offset, offset + 65536)
                    require(end >= offset, 'UNREADABLE_GZIP')
                    self.text(label, remaining[offset:end].decode('utf-8', 'replace')); offset = end + 1
            if flags & 2:
                offset += 2
            require(offset <= len(remaining) - 8, 'UNREADABLE_GZIP')
            decoder = zlib.decompressobj(31)
            try:
                part = decoder.decompress(remaining, MAX_FILE_BYTES - total + 1)
            except zlib.error as exc:
                raise PublicationError('UNREADABLE_GZIP') from exc
            total += len(part); members += 1
            require(total <= MAX_FILE_BYTES and decoder.eof and members <= MAX_FILES, 'GZIP_LIMIT')
            parts.append(part); remaining = decoder.unused_data
            if remaining and not remaining.strip(b'\0'):
                remaining = b''
        child = name[:-3] if name.lower().endswith('.gz') else name + '.decoded'
        self.scan(label + '/gzip', child, b''.join(parts), depth + 1)

    def zip(self, label, data, depth):
        self.counts['zip_containers'] += 1
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                require(len(infos) <= MAX_FILES, 'ZIP_LIMIT')
                paths = set(); intervals = []
                for info in infos:
                    name = info.filename[:-1] if info.is_dir() else info.filename
                    self.member_name(label + '/zip-name', name)
                    require(name not in paths, 'DUPLICATE_ZIP_MEMBER'); paths.add(name)
                    mode = stat.S_IFMT(info.external_attr >> 16)
                    require(mode in (0, stat.S_IFREG, stat.S_IFDIR) and not info.flag_bits & 1,
                            'UNSAFE_ZIP_MEMBER')
                    require(info.file_size <= MAX_FILE_BYTES, 'ZIP_MEMBER_LIMIT')
                    offset = info.header_offset
                    require(offset >= 0 and data[offset:offset + 4] == b'PK\x03\x04'
                            and offset + 30 <= len(data), 'UNREADABLE_ZIP')
                    fn, extra = struct.unpack_from('<HH', data, offset + 26)
                    start = offset + 30 + fn + extra; end = start + info.compress_size
                    require(end <= len(data), 'UNREADABLE_ZIP'); intervals.append((start, end))
                    if not info.is_dir():
                        body = archive.read(info)
                        require(len(body) == info.file_size, 'ZIP_MEMBER_SIZE')
                        self.scan(label + '/' + name, name, body, depth + 1)
                # Scan names, comments, extras, prefix/trailer and gaps, but not
                # meaningless compressed bytes (which are scanned after decoding).
                cursor = 0
                for start, end in sorted(intervals):
                    require(start >= cursor, 'OVERLAPPING_ZIP_MEMBERS')
                    self.text(label + '/zip-metadata', data[cursor:start].decode('utf-8', 'replace'))
                    cursor = end
                self.text(label + '/zip-metadata', data[cursor:].decode('utf-8', 'replace'))
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, zlib.error) as exc:
            raise PublicationError('UNREADABLE_ZIP') from exc

    def scan(self, label, name, data, depth=0):
        require(depth <= MAX_DEPTH, 'NESTING_LIMIT'); self.account(len(data))
        self.member_name(label, name)
        if data.startswith(b'\x1f\x8b') or name.lower().endswith('.gz'):
            self.gzip(label, name, data, depth)
            return
        if (data.startswith((b'PK\x03\x04', b'PK\x05\x06')) or name.lower().endswith('.zip')
                or zipfile.is_zipfile(io.BytesIO(data))):
            self.zip(label, data, depth)
            return
        allowances = [(p, e) for p, e in self.exceptions.get(sha(data), [])
                      if name == p or name.endswith('/' + p)]
        if data.startswith(b'SQLite format 3\0'):
            self.text(label, data.decode('utf-8', 'replace'))
            self.sqlite(label, data, depth)
            return
        if data.startswith((b'\xff\xfe', b'\xfe\xff')):
            try:
                text = data.decode('utf-16')
            except UnicodeError as exc:
                raise PublicationError('UNREADABLE_TEXT_ENCODING') from exc
        else:
            try:
                text = data.decode('utf-8')
            except UnicodeError as exc:
                raise PublicationError('UNSUPPORTED_BINARY_CONTENT') from exc
        require('\0' not in text, 'UNSUPPORTED_BINARY_CONTENT')
        self.text(label, text, allowances)
        self.json_text(label, data, name)

    def report(self):
        return {'logical_payloads_scanned': self.payloads, 'logical_bytes_scanned': self.bytes,
                'container_counts': dict(sorted(self.counts.items())),
                'findings': sum(self.rules.values()), 'rules': dict(sorted(self.rules.items())),
                'details': self.details}


def scan_files(files, *, repo=ROOT, denylist=None, ci_runner_paths=False):
    require(denylist is None or not path_checked(denylist).is_relative_to(repo), 'EXTERNAL_DENYLIST_REQUIRED')
    patterns = {key: re.compile(pattern) for key, pattern in
                {**privacy.BUILTIN, **privacy.load_denylist(denylist)}.items()}
    scanner = Scanner(patterns, source_exceptions(repo), ci_runner_paths)
    for name, data in sorted(files.items()):
        scanner.scan(name, name, data)
    return scanner.report()


def prepare(source, out, expected_commit, *, repo=ROOT, denylist=None, ci_runner_paths=False):
    source, out, repo = path_checked(source), path_checked(out), path_checked(repo)
    require(source.is_dir() and not out.exists(), 'NEW_OUTPUT_REQUIRED')
    require(not overlap(source, out) and not overlap(repo, out) and not overlap(repo, source),
            'SEPARATE_DIRECTORIES_REQUIRED')
    files = collect(source)
    checked = verify_files(files, expected_commit)
    report = scan_files(files, repo=repo, denylist=denylist, ci_runner_paths=ci_runner_paths)
    if report['findings']:
        return {'status': 'PUBLICATION_BLOCKED', 'reason': 'DISCLOSURE_PATTERNS', **report,
                'secret_free_guarantee': False}
    require(collect(source) == files, 'INPUT_CHANGED')
    out.mkdir(parents=True, mode=0o700)
    for name, data in sorted(files.items()):
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with target.open('xb') as stream:
            stream.write(data)
        target.chmod(0o600)
    require(collect(out) == files, 'STAGED_BYTES_CHANGED')
    return {'status': 'PUBLIC_EVIDENCE_PREPARED', **checked, **report,
            'staged_bytes': sum(len(data) for data in files.values()),
            'exact_bytes_preserved': True, 'secret_free_guarantee': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--expected-commit', required=True)
    parser.add_argument('--denylist', default=os.environ.get('FORGE_DENYLIST'))
    parser.add_argument('--ci-runner-paths', action='store_true',
                        help='allow only the generic Ubuntu CI runner home path')
    args = parser.parse_args(argv)
    try:
        result = prepare(args.source, args.out, args.expected_commit, denylist=args.denylist,
                         ci_runner_paths=args.ci_runner_paths)
    except Exception as exc:
        result = {'status': 'PUBLICATION_BLOCKED',
                  'reason': str(exc) if isinstance(exc, PublicationError) else type(exc).__name__,
                  'secret_free_guarantee': False}
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result['status'] == 'PUBLIC_EVIDENCE_PREPARED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
