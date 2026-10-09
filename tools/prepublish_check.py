#!/usr/bin/env python3
"""Pre-publish gate: fail if the repository contains secrets, personal paths or denylisted terms.

Checks every file in the working tree (text and binary-ish files, decoded leniently)
and, with --history, every blob and commit message/author in git history.

Denylisted terms (names, products, hostnames, ...) are NOT stored in the repository.
Supply them in a file outside the repo, one case-insensitive regex per line ('#' comments allowed):

    FORGE_DENYLIST=/path/outside/repo/denylist.txt python3 tools/prepublish_check.py --history
    python3 tools/prepublish_check.py --denylist /path/outside/repo/denylist.txt

Known intentional matches are listed in tools/prepublish_allowlist.json (path + exact substring).
Exit code 0 = clean, 1 = findings, 2 = usage error. Matched values are never printed in full.

This is accidental-disclosure linting: pattern matching catches common mistakes but cannot
prove a tree is free of secrets and is not a DLP boundary (see docs/THREAT_MODEL.md).
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {'.git'}
SELF = {'tools/prepublish_check.py', 'tools/prepublish_allowlist.json'}

BUILTIN = {
    'private_key': r'-----BEGIN [A-Z ]*PRIVATE KEY-----',
    'aws_access_key': r'\b(AKIA|ASIA)[0-9A-Z]{16}\b',
    'github_token': r'\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{6,}',
    'slack_token': r'\bxox[abprs]-[A-Za-z0-9-]{10,}',
    'stripe_key': r'\b[sr]k_(live|test)_[A-Za-z0-9]{8,}',
    'openai_style_key': r'\bsk-(proj-)?[A-Za-z0-9_-]{32,}',
    'google_api_key': r'\bAIza[0-9A-Za-z_-]{30,}',
    'jwt': r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}',
    'bearer_token': r'(?i)authorization:\s*bearer\s+[A-Za-z0-9._~+/-]{20,}',
    'assigned_secret': r'(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)\b\s*[:=]\s*["\'][^"\'\s]{8,}["\']',
    'url_credentials': r'(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@"\']+:[^/\s@"\']+@',
    'env_file_line': r'(?m)^(?:export\s+)?[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)=\S{6,}',
    'home_path': r'(?<![A-Za-z0-9_])(/Users/[A-Za-z][^/\s"\']*|/home/[a-z][a-z0-9_-]*|/Volumes/[^/\s"\']+|[A-Za-z]:\\Users\\[^\\\s"\']+)',
    'drive_link': r'(?i)(drive|docs)\.google\.com/',
    'email': r'(?i)\b[a-z0-9._%+-]+@(?!example\.(?:com|org|net|invalid)\b)[a-z0-9.-]+\.(?:com|net|org|io|app|dev|ai|co|us|me)\b',
}


def load_denylist(path):
    if not path:
        return {}
    p = Path(path).expanduser().resolve()
    if p.is_relative_to(ROOT):
        sys.exit('The denylist must live outside the repository so it is never published.')
    pats = {}
    for i, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
        line = line.strip()
        if line and not line.startswith('#'):
            pats['denylist_line_%d' % i] = '(?i)' + line
    return pats


def load_allowlist():
    p = ROOT / 'tools/prepublish_allowlist.json'
    return json.loads(p.read_text())['entries'] if p.exists() else []


def allowed(path, line, allow):
    return any(e['path'] == path and e['contains'] in line for e in allow)


def redact(s):
    s = s.strip()
    return (s[:3] + '…' + '(%d chars)' % len(s)) if len(s) > 3 else '…'


def scan_text(label, text, patterns, allow, findings, path_for_allow=None):
    for n, line in enumerate(text.splitlines(), 1):
        for name, rx in patterns.items():
            for m in rx.finditer(line):
                if path_for_allow and allowed(path_for_allow, line, allow):
                    continue
                findings.append({'where': label, 'line': n, 'rule': name, 'match': redact(m.group(0))})


def tree_files():
    for p in sorted(ROOT.rglob('*')):
        rel = p.relative_to(ROOT)
        if p.is_file() and not (set(rel.parts) & SKIP_DIRS):
            yield rel.as_posix(), p


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--denylist', default=os.environ.get('FORGE_DENYLIST'))
    ap.add_argument('--history', action='store_true', help='also scan all git history (blobs, messages, authors)')
    ap.add_argument('--require-denylist', action='store_true', help='fail if no denylist was supplied')
    a = ap.parse_args()
    if a.require_denylist and not a.denylist:
        print('No denylist supplied (set FORGE_DENYLIST or pass --denylist).'); return 2
    patterns = {k: re.compile(v) for k, v in {**BUILTIN, **load_denylist(a.denylist)}.items()}
    allow = load_allowlist(); findings = []; files = 0
    for rel, p in tree_files():
        files += 1
        name_hit = [k for k, rx in patterns.items() if rx.search(rel)]
        for k in name_hit:
            findings.append({'where': rel, 'line': 0, 'rule': k + ' (file name)', 'match': redact(rel)})
        if rel.split('/')[-1].startswith('._') or rel.endswith(('.DS_Store', '.pyc')) or '__pycache__' in rel:
            findings.append({'where': rel, 'line': 0, 'rule': 'junk_file', 'match': ''})
        if rel in SELF:
            continue
        text = p.read_bytes().decode('utf-8', errors='replace')
        scan_text(rel, text, patterns, allow, findings, rel)
    history = 'not scanned'
    if a.history and (ROOT / '.git').exists():
        git = lambda *x: subprocess.run(['git', '-C', str(ROOT), *x], capture_output=True, text=True, errors='replace', check=True).stdout
        meta = git('log', '--all', '--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x00%x01')
        scan_text('git:commit-metadata', meta, patterns, allow, findings)
        objs = git('rev-list', '--all', '--objects').splitlines(); blobs = 0
        for row in objs:
            parts = row.split(' ', 1)
            if len(parts) != 2:
                continue
            sha, path = parts
            if git('cat-file', '-t', sha).strip() != 'blob':
                continue
            blobs += 1
            if path in SELF:
                continue
            data = subprocess.run(['git', '-C', str(ROOT), 'cat-file', 'blob', sha], capture_output=True, check=True).stdout
            scan_text('git:' + path + '@' + sha[:10], data.decode('utf-8', errors='replace'), patterns, allow, findings, path)
        history = 'scanned %d blobs' % blobs
    report = {'files_scanned': files, 'history': history, 'denylist_terms': sum(1 for k in patterns if k.startswith('denylist_')),
              'findings': len(findings), 'details': findings[:200]}
    print(json.dumps(report, indent=2))
    return 1 if findings else 0


if __name__ == '__main__':
    raise SystemExit(main())
