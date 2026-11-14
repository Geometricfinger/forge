#!/usr/bin/env python3
"""Bounded local continuation of a source-bound FORGE research mission.

No model, network, source execution, shell, deployment, or original-file rewrite.
The caller supplies the judgment. The console provides ask / inspect / status /
cancel, pinned to an already-completed mission and its original question budget.
A trusted local account is assumed. Hashes are not independent attestations.
"""
from __future__ import annotations
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

MAX_RECORD_BYTES = 32_000_000
AUTHORITY = {'execute': False, 'deploy': False, 'modify_baseline': False,
             'market_validated': False, 'novelty_established': False}

from . import core, runner, bridge


class Supervisor:
    def __init__(self, manifest, source_root, work, forge_root):
        self.c, self.runner, self.bridge = core, runner, bridge
        self.manifest_path = self.c.no_links(manifest)
        self.sources = self.c.no_links(source_root)
        self.work = self.c.no_links(work)
        self.forge = self.c.no_links(forge_root)
        self.wrapper_sha = self.c.sha(Path(__file__).read_bytes())

    def _read(self, path):
        p = self.c.no_links(path)
        if not p.is_file() or p.stat().st_nlink != 1 or p.stat().st_size > MAX_RECORD_BYTES:
            raise self.c.Blocked('INVALID_OR_MISSING_RECORD')
        return self.c.strict_loads(p.read_bytes())

    def _context(self):
        m = self.c.check_manifest(self._read(self.manifest_path))
        expected = {'tool': self.runner.tool_binding(), 'forge': self.runner.verify_base(self.forge),
                    'manifest': self.c.digest(m), 'source_root': str(self.sources)}
        if self._read(self.work / 'binding.json') != expected:
            raise self.c.Blocked('MISSION_BINDING_CHANGED')
        for s in m['sources']:
            self.c.read_source(self.sources, s)
        checkpoints = self.c.Checkpoints(self.work / 'checkpoints', self.c.digest(expected))
        report = self._read(self.work / 'report.json')
        if checkpoints.get('assemble') != report:
            raise self.c.Blocked('REPORT_CHECKPOINT_MISMATCH')
        if report.get('status') not in ('READY_FOR_SUPERVISOR_REVIEW', 'INCOMPLETE_SOURCE_EVIDENCE'):
            raise self.c.Blocked('MISSION_NOT_COMPLETED')
        source_results = []
        for s in m['sources']:
            cached = checkpoints.get('source:' + s['id'])
            if not isinstance(cached, dict) or cached.get('source') != s:
                raise self.c.Blocked('SOURCE_CHECKPOINT_MISMATCH')
            path = cached.get('forge_report_path')
            if path and self.c.no_links(path) != self.work / 'corpora' / s['id'] / 'corpus.json':
                raise self.c.Blocked('FOREIGN_CORPUS_PATH')
            source_results.append(cached)
        context = {'mission': self.c.digest(expected), 'report': self.c.digest(report),
                   'source_records': self.c.digest(source_results), 'supervisor': self.wrapper_sha}
        return m, source_results, context

    def _db(self, context):
        directory = self.c.no_links(self.work / 'supervision')
        directory.mkdir(mode=0o700, exist_ok=True)
        for name in ('ledger.sqlite', 'ledger.sqlite-journal', 'ledger.sqlite-wal', 'ledger.sqlite-shm'):
            p = self.c.no_links(directory / name)
            if p.exists() and (not p.is_file() or p.stat().st_nlink != 1):
                raise self.c.Blocked('SHARED_SUPERVISION_LEDGER')
        db = sqlite3.connect(directory / 'ledger.sqlite', isolation_level=None, timeout=0)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, action TEXT NOT NULL, request TEXT NOT NULL, state TEXT NOT NULL, result TEXT, result_sha TEXT)')
            value = self.c.canonical(context).decode()
            old = db.execute("SELECT value FROM meta WHERE key='context'").fetchone()
            if old and old[0] != value:
                raise self.c.Blocked('SUPERVISION_BINDING_CHANGED')
            db.execute("INSERT OR IGNORE INTO meta VALUES ('context', ?)", (value,))
            db.execute("INSERT OR IGNORE INTO meta VALUES ('canceled', 'false')")
            db.execute('COMMIT')
            return db
        except BaseException:
            db.close()
            raise

    def _check_request(self, request, manifest, source_results):
        c = self.c
        if not isinstance(request, dict) or 'action' not in request:
            raise c.Blocked('REQUEST_FIELDS')
        action = request['action']
        allowed = {'ask': {'action', 'request_id', 'question'},
                   'inspect': {'action', 'request_id', 'source_id', 'evidence_id', 'max_chars'},
                   'status': {'action'}, 'cancel': {'action'}}
        if not isinstance(action, str) or action not in allowed or set(request) != allowed[action]:
            raise c.Blocked('REQUEST_FIELDS')
        if action in ('status', 'cancel'):
            return None
        rid = request['request_id']
        if not isinstance(rid, str) or re.fullmatch('[A-Za-z0-9_-]{1,80}', rid) is None:
            raise c.Blocked('REQUEST_ID')
        if action == 'ask':
            q = request['question']
            if not isinstance(q, dict) or q.get('id') != rid:
                raise c.Blocked('QUESTION_ID_MUST_MATCH_REQUEST')
            # Validate the added question using the existing schema. The ledger
            # separately counts all reserved questions, including failed work.
            v = dict(manifest, questions=[*manifest['questions'], q])
            c.check_manifest(v)
            self.bridge.validate_questions([q], self.forge)
            return None
        c.check_int(request['max_chars'], 16, 4096)
        if not isinstance(request['source_id'], str) or not isinstance(request['evidence_id'], str):
            raise c.Blocked('EVIDENCE_ID')
        matches = [r for s in source_results if s['source']['id'] == request['source_id']
                   for r in [*s.get('records', []), *s.get('references', [])]
                   if r.get('id') == request['evidence_id']]
        if len(matches) != 1:
            raise c.Blocked('UNKNOWN_OR_AMBIGUOUS_EVIDENCE')
        return matches[0]

    def _project(self, full):
        fields = ('id', 'query', 'intent', 'status', 'source_gaps', 'warnings',
                  'next_action', 'runtime_validation', 'code_generated', 'market_validated', 'novelty_established')
        result = {k: full[k] for k in fields if k in full}
        result['runtime_validation'] = 'NOT_RUN'
        result['results'] = []
        result['references'] = []
        for r in full.get('results', [])[:5]:
            keep = {k: r[k] for k in ('id', 'source_id', 'source_sha256', 'origin', 'capture', 'name', 'path', 'start_line', 'end_line', 'record_kind', 'channels') if k in r}
            keep['excerpt'] = r.get('snippet', '')[:192]
            keep['excerpt_truncated'] = len(r.get('snippet', '')) > 192
            result['results'].append(keep)
        for r in full.get('references', [])[:5]:
            keep = {k: r[k] for k in ('id', 'source_id', 'source_sha256', 'origin', 'capture', 'kind', 'title', 'path', 'start_line', 'end_line') if k in r}
            keep['excerpt'] = r.get('text', '')[:192]
            keep['excerpt_truncated'] = len(r.get('text', '')) > 192
            result['references'].append(keep)
        return result

    def execute(self, request):
        c = self.c
        with c.locked(self.work):
            m, sources, context = self._context()
            evidence = self._check_request(request, m, sources)
            db = self._db(context)
            try:
                action = request['action']
                if action in ('status', 'cancel'):
                    if action == 'cancel':
                        db.execute("UPDATE meta SET value='true' WHERE key='canceled'")
                    count = db.execute("SELECT COUNT(*) FROM requests WHERE action='ask'").fetchone()[0]
                    return {'status': 'SUPERVISION_STATUS', 'initial_questions': len(m['questions']),
                            'reserved_followups': count,
                            'remaining_questions': max(0, m['budgets']['max_questions'] - len(m['questions']) - count),
                            'canceled': db.execute("SELECT value FROM meta WHERE key='canceled'").fetchone()[0] == 'true',
                            'parent_report_sha256': context['report'], 'authority': dict(AUTHORITY), 'model_calls': 0}
                rid = request['request_id']
                request_json = c.canonical(request).decode()
                old = db.execute('SELECT request,state,result,result_sha FROM requests WHERE id=?', (rid,)).fetchone()
                if old:
                    if old[0] != request_json:
                        raise c.Blocked('REQUEST_ID_CONFLICT')
                    if old[1] == 'RESERVED':
                        return self._envelope(rid, 'INTERRUPTED_REQUIRES_REVIEW', None, True, context)
                    if old[2] is None or c.sha(old[2].encode()) != old[3]:
                        raise c.Blocked('RESULT_LEDGER_CHANGED')
                    saved = c.strict_loads(old[2]); saved['replayed'] = True
                    return saved
                if (self.work / 'CANCEL.json').exists() or db.execute("SELECT value FROM meta WHERE key='canceled'").fetchone()[0] == 'true':
                    raise c.Blocked('SUPERVISION_CANCELED')
                db.execute('BEGIN IMMEDIATE')
                used = db.execute('SELECT COUNT(*) FROM requests WHERE action=?', (action,)).fetchone()[0]
                cap = m['budgets']['max_questions'] - len(m['questions']) if action == 'ask' else m['budgets']['max_questions']
                if used >= cap:
                    raise c.Blocked('SUPERVISOR_BUDGET_EXHAUSTED')
                db.execute('INSERT INTO requests VALUES (?,?,?,\'RESERVED\',NULL,NULL)', (rid, action, request_json))
                db.execute('COMMIT')
                try:
                    if action == 'ask':
                        full = self.bridge.investigate(request['question'], sources, self.forge)
                        result = self._project(full)
                    else:
                        text = evidence.get('text', '')
                        result = {k: evidence[k] for k in ('id', 'source_id', 'source_sha256', 'origin', 'kind', 'capture', 'name', 'path', 'start_line', 'end_line', 'record_kind', 'text_sha256', 'source_window') if k in evidence}
                        result.update(text=text[:request['max_chars']], total_chars=len(text),
                                      truncated=len(text) > request['max_chars'], runtime_validation='NOT_RUN')
                        full = result
                    if self._context()[2] != context:
                        raise c.Blocked('SOURCE_OR_CONTEXT_CHANGED_DURING_REQUEST')
                    response = self._envelope(rid, 'COMPLETED', result, False, context)
                    if len(c.canonical(response)) > m['budgets']['packet_bytes']:
                        raise c.Blocked('SUPERVISOR_RESPONSE_LIMIT')
                    c.atomic_new(self.work / 'supervision' / (rid + '.json'), c.canonical({'request': request, 'result': full, 'context': context}))
                except Exception as ex:
                    # Preserve failure and consumption without source text or
                    # arbitrary exception messages leaking to the caller.
                    response = self._envelope(rid, 'FAILED', None, False, context)
                    response['reason'] = str(ex) if isinstance(ex, c.Blocked) else type(ex).__name__
                encoded = c.canonical(response).decode()
                db.execute('UPDATE requests SET state=?,result=?,result_sha=? WHERE id=?',
                           (response['state'], encoded, c.sha(encoded.encode()), rid))
                return response
            finally:
                db.close()

    @staticmethod
    def _envelope(rid, state, result, replayed, context):
        return {'request_id': rid, 'state': state, 'replayed': replayed, 'result': result,
                'parent_report_sha256': context['report'], 'untrusted_content': True,
                'authority': dict(AUTHORITY), 'model_calls': 0, 'release_approved': False}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest', type=Path, required=True)
    ap.add_argument('--source-root', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--forge-root', type=Path, required=True)
    ap.add_argument('--request', type=Path, help='JSON request; stdin when omitted. No executable commands.')
    a = ap.parse_args()
    session = Supervisor(a.manifest, a.source_root, a.work, a.forge_root)
    if a.request:
        with a.request.open('rb') as stream: data = stream.read(16_385)
    else:
        data = sys.stdin.buffer.read(16_385)
    if len(data) > 16_384:
        raise session.c.Blocked('REQUEST_SIZE')
    result = session.execute(session.c.strict_loads(data))
    print(session.c.canonical(result).decode())
    return 0 if result.get('state') in (None, 'COMPLETED') else 2

