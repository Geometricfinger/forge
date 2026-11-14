#!/usr/bin/env python3
"""Pre-publish gate: fail if the repository contains secrets, personal paths or denylisted terms.

Checks every file in the working tree (text and binary-ish files, decoded leniently)
and, with --history, every blob and commit message/author in git history.

Denylisted terms (names, products, hostnames, ...) are NOT stored in the repository.
Supply them in a file outside the repo, one case-insensitive regex per line ('#' comments allowed):

    FORGE_DENYLIST=/path/outside/repo/denylist.txt python3 tools/prepublish_check.py --history
    python3 tools/prepublish_check.py --denylist /path/outside/repo/denylist.txt

Known intentional matches are listed in tools/prepublish_allowlist.json (path + exact substring).
Only findings wholly inside an approved substring are exempt, never the rest of the line.
Exit code 0 = clean, 1 = findings, 2 = usage error. Matched values are never printed in full.

This is accidental-disclosure linting: pattern matching catches common mistakes but cannot
prove a tree is free of secrets and is not a DLP boundary (see docs/THREAT_MODEL.md).
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {'.git'}
ALLOWLIST_PATH = 'tools/prepublish_allowlist.json'
SECRET_FIELDS = r'(?:password|passwd|secret|client[_-]?secret|api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|session[_-]?(?:key|token|secret)|token|cookie)'

BUILTIN = {
    'private_key': r'-----BEGIN [A-Z ]*PRIVATE KEY-----',
    'aws_access_key': r'\b(AKIA|ASIA)[0-9A-Z]{16}\b',
    'github_token': r'\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{6,}',
    'slack_token': r'\bxox[abprs]-[A-Za-z0-9-]{10,}',
    'stripe_key': r'\b[sr]k_(live|test)_[A-Za-z0-9]{8,}',
    'openai_style_key': r'\bsk-(proj-)?[A-Za-z0-9_-]{32,}',
    'google_api_key': r'\bAIza[0-9A-Za-z_-]{30,}',
    'jwt': r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}',
    'bearer_token': r'(?i)\bauthorization["\']?\s*[:=]\s*["\']?(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}',
    'assigned_secret': r'(?i)(?<![A-Za-z0-9_])(?:["\']' + SECRET_FIELDS + r'["\']|\b' + SECRET_FIELDS + r'\b)\s*[:=]\s*["\'][^"\'\r\n]{8,}["\']',
    'cookie_token': r'(?i)\b(?:cookie|set-cookie)["\']?\s*[:=]\s*["\']?[A-Za-z0-9._~+/=-]{12,}',
    'credential_url_parameter': r'(?i)[?&](?:access_token|token|api_key|auth|sig|signature|password|secret|x-amz-signature|x-goog-signature)=[^&#\s"\'<>]{6,}',
    'url_credentials': r'(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@"\']+:[^/\s@"\']+@',
    'env_file_line': r'(?m)^(?:export\s+)?[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)=\S{6,}',
    # Keep the detection examples out of the scanner's own source bytes.
    'home_path': r'(?<![A-Za-z0-9_])(/' + r'Users/[A-Za-z][^/\s"\']*|/' + r'home/[a-z][a-z0-9_-]*|/' + r'Volumes/[^/\s"\']+|[A-Za-z]:\\Users\\[^\\\s"\']+)',
    'drive_link': r'(?i)(drive|docs)\.google\.com/',
    'email': r'(?i)\b[a-z0-9._%+-]+@(?!example\.(?:com|org|net|invalid)\b)[a-z0-9.-]+\.(?:com|net|org|io|app|dev|ai|co|us|me)\b',
}
SENSITIVE_FILE = re.compile(r'(?i)(?:^|/)(?:\.env(?:\.[^/]*)?|\.netrc|\.npmrc|\.pypirc|id_rsa|id_ed25519|credentials(?:\.json|\.ini)?|secrets?\.(?:json|ya?ml|toml|ini)|service[-_]account[^/]*\.json)$')


def load_denylist(path):
    if not path:
        return {}
    p = Path(path).expanduser().resolve()
    if p.is_relative_to(ROOT):
        raise ValueError('DENYLIST_MUST_BE_EXTERNAL')
    pats = {}
    for i, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
        line = line.strip()
        if line and not line.startswith('#'):
            pats['denylist_line_%d' % i] = '(?i)' + line
    return pats


def _policy_json(text):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError('DUPLICATE_POLICY_KEY')
            out[key] = value
        return out
    return json.loads(text, object_pairs_hook=pairs)


def _policy_entries(text):
    value = _policy_json(text)
    if (type(value) is not dict or not {'entries'} <= set(value) <= {'entries', 'note'}
            or ('note' in value and type(value['note']) is not str)
            or type(value['entries']) is not list or len(value['entries']) > 128):
        raise ValueError('ALLOWLIST_SCHEMA')
    seen = set()
    for entry in value['entries']:
        if type(entry) is not dict:
            raise ValueError('ALLOWLIST_ENTRY')
        literal = set(entry) == {'path', 'contains', 'reason'}
        historical = set(entry) == {'path', 'match_sha256', 'rule', 'history_only', 'reason'}
        if not (literal or historical):
            raise ValueError('ALLOWLIST_FIELDS')
        path = entry['path']
        if (type(path) is not str or not path or len(path) > 512
                or path.startswith('/') or '\\' in path
                or any(p in ('', '.', '..') for p in path.split('/'))
                or any(ord(c) < 32 for c in path)
                or (':' in path and path != 'git:commit-metadata')
                or type(entry['reason']) is not str or not entry['reason'].strip()
                or len(entry['reason']) > 1024):
            raise ValueError('ALLOWLIST_ENTRY')
        if literal:
            content = entry['contains']
            if type(content) is not str or not content or len(content) > 4096 or any(c in content for c in '\r\n\x00'):
                raise ValueError('ALLOWLIST_LITERAL')
            identity = (path, content)
        else:
            digest = entry['match_sha256']
            if (entry['history_only'] is not True or entry['rule'] != 'email'
                    or type(digest) is not str or re.fullmatch('[0-9a-f]{64}', digest) is None):
                raise ValueError('ALLOWLIST_HISTORY_SELECTOR')
            identity = (path, digest, 'email', True)
        if identity in seen:
            raise ValueError('DUPLICATE_ALLOWLIST_ENTRY')
        seen.add(identity)
    return value['entries']


def load_allowlist():
    p = ROOT / ALLOWLIST_PATH
    return _policy_entries(p.read_text()) if p.exists() else []


def allowed(path, line, match, allow, *, history=False, rule=None):
    for entry in allow:
        if entry.get('history_only'):
            # These digests select exact existing public-history identities.
            # They are not confidentiality or credential-authentication claims.
            if (history and rule == 'email' and entry['rule'] == 'email'
                    and entry['path'] == path and
                    hashlib.sha256(match.group(0).encode('utf-8')).hexdigest() == entry['match_sha256']):
                return True
            continue
        literal = entry.get('contains', '')
        if entry['path'] != path or not literal:
            continue
        start = line.find(literal)
        while start != -1:
            if start <= match.start() and match.end() <= start + len(literal):
                return True
            start = line.find(literal, start + 1)
    return False


def redact(s):
    return '[redacted: %d chars]' % len(s)


def safe_label(label, patterns):
    spans = sorted((m.start(), m.end()) for rx in patterns.values() for m in rx.finditer(label))
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    out = []; cursor = 0
    for start, end in merged:
        out.extend((label[cursor:start], '[redacted]')); cursor = end
    out.append(label[cursor:])
    result = ''.join(out)
    return result if len(result) <= 512 else '[long path: redacted]'


def _allowlist_value_spans(text, allow, history):
    """Only exempt approved JSON contains values, never their row or file."""
    try:
        entries = _policy_entries(text)
    except (ValueError, TypeError):
        return []
    literals = [entry for entry in entries if 'contains' in entry]
    fields = list(re.finditer(r'"contains"\s*:\s*("(?:[^"\\]|\\.)*")', text))
    if len(fields) != len(literals):
        return []
    spans = []
    for field, old in zip(fields, literals):
        if json.loads(field.group(1)) != old['contains']:
            return []
        approved = any(entry.get('contains') == old['contains'] and entry['path'] == old['path']
                       and not entry.get('history_only') for entry in allow)
        if not approved and history:
            match = re.fullmatch(BUILTIN['email'], old['contains'])
            approved = match is not None and allowed(old['path'], old['contains'], match, allow,
                                                     history=True, rule='email')
        if approved:
            spans.append(field.span(1))
    return spans


def scan_text(label, text, patterns, allow, findings, path_for_allow=None, *, history=False):
    spans = _allowlist_value_spans(text, allow, history) if path_for_allow == ALLOWLIST_PATH else []
    offset = 0
    for n, raw_line in enumerate(text.splitlines(keepends=True), 1):
        line = raw_line.rstrip('\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029')
        for name, rx in patterns.items():
            for m in rx.finditer(line):
                if path_for_allow and allowed(path_for_allow, line, m, allow, history=history, rule=name):
                    continue
                if any(start <= offset + m.start() and offset + m.end() <= end for start, end in spans):
                    continue
                findings.append({'where': safe_label(label, patterns), 'line': n, 'rule': name, 'match': redact(m.group(0))})
        offset += len(raw_line)


def scan_bytes(label, data, patterns, allow, findings, path_for_allow=None, *, history=False):
    # Retain the raw UTF-8 view as well as ordinary UTF-16/32 text layouts.
    # This is disclosure linting, not decoding of arbitrary binary containers.
    views = [data.decode('utf-8', errors='replace')]
    if data.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
        views.append(data.decode('utf-32', errors='replace'))
    elif data.startswith((b'\xff\xfe', b'\xfe\xff')):
        views.append(data.decode('utf-16', errors='replace'))
    elif len(data) >= 8 and data.count(b'\x00') >= len(data) // 4:
        for encoding, stride, slots in (
            ('utf-32le', 4, (1, 2, 3)), ('utf-32be', 4, (0, 1, 2)),
            ('utf-16le', 2, (1,)), ('utf-16be', 2, (0,)),
        ):
            if all(data[slot::stride].count(b'\x00') * 5 >= len(data[slot::stride]) * 4 for slot in slots):
                views.append(data.decode(encoding, errors='replace'))
    for text in dict.fromkeys(views):
        scan_text(label, text, patterns, allow, findings, path_for_allow, history=history)


def tree_files():
    for p in sorted(ROOT.rglob('*')):
        rel = p.relative_to(ROOT)
        if (p.is_file() or p.is_symlink()) and not (set(rel.parts) & SKIP_DIRS):
            yield rel.as_posix(), p


def scan_name(rel, patterns, findings, label=None):
    label = safe_label(label or rel, patterns)
    for name, rx in patterns.items():
        if rx.search(rel):
            findings.append({'where': label, 'line': 0, 'rule': name + ' (file name)', 'match': '[redacted]'})
    if SENSITIVE_FILE.search(rel):
        findings.append({'where': label, 'line': 0, 'rule': 'sensitive_file_name', 'match': ''})


def _main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--denylist', default=os.environ.get('FORGE_DENYLIST'))
    ap.add_argument('--history', action='store_true', help='also scan all git history (blobs, messages, authors)')
    ap.add_argument('--require-denylist', action='store_true', help='fail if no denylist was supplied')
    a = ap.parse_args()
    if a.require_denylist and not a.denylist:
        print(json.dumps({'status': 'PREPUBLICATION_SCAN_ERROR', 'reason': 'DENYLIST_REQUIRED'})); return 2
    try:
        patterns = {k: re.compile(v) for k, v in {**BUILTIN, **load_denylist(a.denylist)}.items()}
        allow = load_allowlist()
    except (OSError, ValueError, TypeError, re.error):
        print(json.dumps({'status': 'PREPUBLICATION_SCAN_ERROR', 'reason': 'INVALID_OR_UNREADABLE_POLICY'})); return 2
    findings = []; files = 0
    for rel, p in tree_files():
        files += 1
        scan_name(rel, patterns, findings)
        if p.is_symlink():
            findings.append({'where': safe_label(rel, patterns), 'line': 0, 'rule': 'symlink_entry', 'match': ''})
            continue
        if rel.split('/')[-1].startswith('._') or rel.endswith(('.DS_Store', '.pyc')) or '__pycache__' in rel:
            findings.append({'where': safe_label(rel, patterns), 'line': 0, 'rule': 'junk_file', 'match': ''})
        scan_bytes(rel, p.read_bytes(), patterns, allow, findings, rel)
    history = 'not scanned'
    if a.history and (ROOT / '.git').exists():
        git = lambda *x: subprocess.run(['git', '-C', str(ROOT), *x], capture_output=True, text=True, errors='replace', check=True).stdout
        meta = git('log', '--all', 'HEAD', '--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x00%x01')
        # Match the complete finding before applying identity exceptions. Removing
        # substrings first could conceal a different, longer email address.
        scan_text('git:commit-metadata', meta, patterns, allow, findings, 'git:commit-metadata', history=True)
        # rev-list --objects supplies only one path per blob. A reused blob must
        # be checked under every historical path before applying path exceptions.
        objects = {}
        for commit in git('rev-list', '--all', 'HEAD').splitlines():
            tree = subprocess.run(['git', '-C', str(ROOT), 'ls-tree', '-rz', commit],
                                  capture_output=True, check=True).stdout
            for item in tree.split(b'\0'):
                if not item:
                    continue
                meta, path = item.split(b'\t', 1)
                _, kind, sha = meta.decode('ascii').split()
                if kind == 'blob':
                    objects.setdefault(sha, set()).add(path.decode('utf-8', errors='replace'))
        for sha, paths in sorted(objects.items()):
            data = subprocess.run(['git', '-C', str(ROOT), 'cat-file', 'blob', sha], capture_output=True, check=True).stdout
            for path in sorted(paths):
                label = 'git:' + path + '@' + sha[:10]
                scan_name(path, patterns, findings, label)
                scan_bytes(label, data, patterns, allow, findings, path, history=True)
        history = 'scanned %d blobs' % len(objects)
    report = {'files_scanned': files, 'history': history, 'denylist_terms': sum(1 for k in patterns if k.startswith('denylist_')),
              'findings': len(findings), 'details': findings[:200]}
    print(json.dumps(report, indent=2))
    return 1 if findings else 0


def main():
    try:
        return _main()
    except (OSError, ValueError, TypeError, re.error, subprocess.CalledProcessError):
        # Exceptions can contain private filenames, policy terms or raw data.
        # This diagnostic is deliberately safe to publish.
        print(json.dumps({'status': 'PREPUBLICATION_SCAN_ERROR', 'reason': 'INVALID_OR_UNREADABLE_SCAN_INPUT'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
