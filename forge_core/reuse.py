"""Source-bound requirement comparisons. Inspection is not runtime qualification.

All records are append-only on the supported API, hash-checked, and local to the
operator's trusted workspace. This is not an authenticated independent evaluator.
"""
from __future__ import annotations
import copy, html, re
from pathlib import Path
from .common import Blocked, canonical, sha, loads, read, write, text, integer, safe_path, hash40, path_in_repo

SCHEMA = '''
CREATE TABLE IF NOT EXISTS reuse_case(id TEXT PRIMARY KEY,body BLOB NOT NULL,digest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reuse_evidence(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,case_id TEXT NOT NULL,body BLOB NOT NULL,digest TEXT NOT NULL,previous TEXT);
CREATE INDEX IF NOT EXISTS reuse_case_evidence ON reuse_evidence(case_id,seq);
CREATE TABLE IF NOT EXISTS reuse_head(case_id TEXT PRIMARY KEY,head TEXT);
'''
KINDS = {'observed_api', 'review', 'behavior_test'}
API = re.compile(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+')
IDENT = re.compile(r'[a-z][a-z0-9_-]{0,63}')

def digest(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise Blocked('DIGEST_REQUIRED')
    return value

def fields(obj, exact):
    if not isinstance(obj, dict) or set(obj) != set(exact):
        raise Blocked('REUSE_RECORD_FIELDS')

def validate_contract(value):
    fields(value, {'schema','title','objective','requirements'})
    if type(value['schema']) is not int or value['schema'] != 1: raise Blocked('REUSE_SCHEMA')
    text(value['title'],160); text(value['objective'],1200)
    rows=value['requirements']
    if not isinstance(rows,list) or not 1<=len(rows)<=20: raise Blocked('REQUIREMENT_COUNT')
    seen=set()
    for r in rows:
        if not isinstance(r,dict):raise Blocked('REQUIREMENT_RECORD')
        kind=r.get('kind')
        if kind not in KINDS:raise Blocked('REQUIREMENT_KIND')
        fields(r,{'id','label','kind','required'} | ({'apis'} if kind=='observed_api' else set()))
        name=r['id']
        if not isinstance(name,str) or not IDENT.fullmatch(name) or name in seen:raise Blocked('REQUIREMENT_ID')
        seen.add(name);text(r['label'],240)
        if type(r['required']) is not bool:raise Blocked('REQUIRED_BOOLEAN')
        if kind=='observed_api':
            apis=r['apis']
            if not isinstance(apis,list) or not 1<=len(apis)<=10 or any(not isinstance(a,str) or len(a)>200 or not API.fullmatch(a) for a in apis) or len(apis)!=len(set(apis)):
                raise Blocked('EXACT_API_TARGETS_REQUIRED')
    if not any(r['required'] for r in rows):raise Blocked('REQUIRED_CRITERION_MISSING')
    return copy.deepcopy(value)

def candidates_from(snapshot):
    """Convert observed symbols to candidates; no fake functions for script code."""
    candidates=[];keys=set()
    for source in snapshot['candidates']:
        if any(source.get(k) is not False for k in ('runtime_verified','reuse_approved','release_approved')):
            raise Blocked('UNSUPPORTED_SOURCE_AUTHORITY')
        sid=text(source['source_id'],1000);h=digest(source['source_sha256']);profile=digest(source['profile_sha256'])
        hash40(source['commit']);path_in_repo(source['path']);groups={};seen=set()
        for f in source['findings']:
            if not isinstance(f,dict) or f.get('source_id')!=sid or f.get('source_sha256')!=h or f.get('registry_sha256')!=profile:raise Blocked('FINDING_SOURCE_BINDING')
            if f.get('runtime_verified') is not False or f.get('release_approved') is not False:raise Blocked('UNSUPPORTED_FINDING_AUTHORITY')
            if f.get('status')!='DECLARED_API_CALL_CANDIDATE':raise Blocked('UNSUPPORTED_FINDING_STATUS')
            fid=text(f.get('finding_id'),150)
            if fid in seen:raise Blocked('DUPLICATE_FINDING')
            seen.add(fid)
            scope=f.get('scope_kind')
            if scope=='function':
                name=text(f.get('qualified_name'),300);lines=f.get('function_lines')
                if not isinstance(lines,list) or len(lines)!=2:raise Blocked('FUNCTION_RANGE')
                integer(lines[0],1,1000000);integer(lines[1],lines[0],1000000)
            elif scope in {'module','script'}:
                if f.get('qualified_name') not in (None,'<module>') or f.get('function_lines'):raise Blocked('FABRICATED_FUNCTION_IDENTITY')
                name=None;lines=None
            else:raise Blocked('FINDING_SCOPE')
            api=f.get('resolved_api')
            if not isinstance(api,str) or not API.fullmatch(api):raise Blocked('FINDING_API')
            evidence=f.get('evidence')
            if not isinstance(evidence,list) or not evidence:raise Blocked('FINDING_EVIDENCE')
            for e in evidence:
                start=e.get('line_start');end=e.get('line_end');integer(start,1,1000000);integer(end,start,1000000)
                if lines and not (lines[0]<=start<=end<=lines[1]):raise Blocked('EVIDENCE_RANGE')
            group=(scope,name,tuple(lines) if lines else None)
            groups.setdefault(group,[]).append(copy.deepcopy(f))
        if not groups:groups[('source',None,None)]=[]
        for (scope,name,lines),findings in sorted(groups.items(),key=lambda x:str(x[0])):
            key='rc_'+sha(canonical([sid,h,scope,name,lines]))
            if key in keys:raise Blocked('DUPLICATE_CANDIDATE')
            keys.add(key)
            candidates.append({'key':key,'source_id':sid,'source_sha256':h,'profile_sha256':profile,
                'repository':source['repository'],'commit':source['commit'],'path':source['path'],
                'scope_kind':scope,'qualified_name':name,'function_lines':list(lines) if lines else None,
                'findings':findings,'declared_license':source.get('declared_license'),
                'rights_status':source['rights_status'],'provenance':source['provenance'],
                'runtime_verified':False,'reuse_approved':False})
    if not candidates:raise Blocked('NO_SOURCE_CANDIDATES')
    if len(candidates)>500:raise Blocked('CANDIDATE_COUNT_LIMIT')
    return sorted(candidates,key=lambda c:c['key'])


def source_lock(snapshot):
    return {'mission':snapshot['id'],'profile_sha256':sha(canonical(snapshot['profile'])),
        'candidates':candidates_from(snapshot)}

class Casebook:
    def __init__(self,store):
        self.store=store
        # Execute statements separately: sqlite.executescript commits transactions implicitly.
        with store.tx() as c:
            for statement in SCHEMA.split(';'):
                if statement.strip():c.execute(statement)
    def create(self,mid,contract):
        contract=validate_contract(contract);snapshot=self.store.snapshot(mid);lock=source_lock(snapshot)
        body={'schema':1,'mission':mid,'contract':contract,'source_lock':lock,'candidates':lock['candidates'],
            'collection_status_at_creation':snapshot['status'],'collection_gaps_at_creation':snapshot['gaps'],
            'release_approved':False,'independent_evaluation':False}
        data=canonical(body);h=sha(data);cid='reuse_'+h
        with self.store.tx() as c:
            row=c.execute('SELECT body,digest FROM reuse_case WHERE id=?',(cid,)).fetchone()
            if row and (row['body']!=data or row['digest']!=h):raise Blocked('CASE_CHANGED')
            c.execute('INSERT OR IGNORE INTO reuse_case VALUES(?,?,?)',(cid,data,h))
            if row is None:c.execute('INSERT INTO reuse_head VALUES(?,NULL)',(cid,))
        return {'id':cid,**body}
    def get(self,cid):
        if not isinstance(cid,str) or not re.fullmatch('reuse_[0-9a-f]{64}',cid):raise Blocked('REUSE_CASE_ID')
        with self.store.tx() as c:r=c.execute('SELECT * FROM reuse_case WHERE id=?',(cid,)).fetchone()
        if r is None:raise Blocked('REUSE_CASE_NOT_FOUND')
        if sha(r['body'])!=r['digest'] or cid!='reuse_'+r['digest']:raise Blocked('CASE_CHANGED')
        return {'id':cid,**loads(r['body'])}
    def list(self):
        with self.store.tx() as c:ids=[r[0] for r in c.execute('SELECT id FROM reuse_case ORDER BY rowid DESC')]
        return [{'id':i,'title':self.get(i)['contract']['title'],'mission':self.get(i)['mission']} for i in ids]
    def is_current(self,case):
        try:return source_lock(self.store.snapshot(case['mission']))==case['source_lock']
        except Blocked:return False
    def _history(self,c,cid):
        previous=None;out=[]
        raw_case=c.execute('SELECT body,digest FROM reuse_case WHERE id=?',(cid,)).fetchone()
        if raw_case is None or sha(raw_case['body'])!=raw_case['digest']:raise Blocked('CASE_CHANGED')
        case=loads(raw_case['body'])
        for r in c.execute('SELECT * FROM reuse_evidence WHERE case_id=? ORDER BY seq',(cid,)):
            if sha(r['body'])!=r['digest'] or r['id']!='ev_'+r['digest'] or r['previous']!=previous:raise Blocked('EVIDENCE_CHANGED')
            body=loads(r['body'])
            if body.get('previous')!=previous or body.get('case_id')!=cid:raise Blocked('EVIDENCE_CHAIN')
            if body.get('kind') not in {'operator_review','fixed_trial'}:raise Blocked('EVIDENCE_KIND')
            if body['kind']=='fixed_trial':
                from .reuse_trial import validate_receipt
                validate_receipt(case,body['payload'])
            out.append({'id':r['id'],**body});previous=r['id']
        head=c.execute('SELECT head FROM reuse_head WHERE case_id=?',(cid,)).fetchone()
        if head is None or head['head']!=previous:raise Blocked('EVIDENCE_TAIL_CHANGED')
        return out
    def history(self,cid):
        self.get(cid)
        with self.store.tx() as c:return self._history(c,cid)
    def record(self,cid,note):
        fields(note,{'candidate','requirement','verdict','note','actor','supersedes'})
        case=self.get(cid)
        if not self.is_current(case):raise Blocked('STALE_SOURCE_REVIEW_REQUIRED')
        candidate=next((r for r in case['candidates'] if r['key']==note['candidate']),None)
        req=next((r for r in case['contract']['requirements'] if r['id']==note['requirement']),None)
        if candidate is None or req is None:raise Blocked('EVIDENCE_TARGET')
        if req['kind']!='review':raise Blocked('OPERATOR_CANNOT_ASSERT_EXECUTION_OR_SYNTAX')
        if note['verdict'] not in {'SUPPORTED','CONTRADICTED','UNKNOWN'}:raise Blocked('EVIDENCE_VERDICT')
        text(note['note'],2000);text(note['actor'],120)
        if note['supersedes'] is not None:text(note['supersedes'],100)
        with self.store.tx() as c:
            hist=self._history(c,cid)
            # A retry may repeat an old event; it never makes it the active revision again.
            for e in hist:
                if e['kind']=='operator_review' and e['payload']==note:return e
            matching=[e for e in hist if e['kind']=='operator_review' and e['payload']['candidate']==note['candidate'] and e['payload']['requirement']==note['requirement']]
            head=matching[-1]['id'] if matching else None
            if note['supersedes']!=head:raise Blocked('STALE_EVIDENCE_HEAD')
            return self._append(c,cid,hist,'operator_review',note)
    def _append(self,c,cid,hist,kind,payload):
        previous=hist[-1]['id'] if hist else None
        body={'case_id':cid,'previous':previous,'kind':kind,'payload':payload}
        data=canonical(body);h=sha(data);eid='ev_'+h
        c.execute('INSERT INTO reuse_evidence(id,case_id,body,digest,previous) VALUES(?,?,?,?,?)',(eid,cid,data,h,previous))
        c.execute('UPDATE reuse_head SET head=? WHERE case_id=?',(eid,cid))
        return {'id':eid,**body}
    def attach_fixed_trial(self,cid):
        """No caller-supplied command, function, source body, test or result is accepted."""
        from .reuse_trial import run_for_case
        case=self.get(cid)
        if not self.is_current(case):raise Blocked('STALE_SOURCE_REVIEW_REQUIRED')
        receipt=run_for_case(case)
        with self.store.tx() as c:
            hist=self._history(c,cid)
            for e in hist:
                if e['kind']=='fixed_trial' and e['payload']==receipt:return e
            return self._append(c,cid,hist,'fixed_trial',receipt)
    def compare(self,cid):
        case=self.get(cid);hist=self.history(cid);current=self.is_current(case);rows=[]
        for candidate in case['candidates']:
            checks=[]
            for req in case['contract']['requirements']:
                state='UNKNOWN';basis='No supporting evidence recorded.';refs=[]
                if req['kind']=='observed_api':
                    matched=[f for f in candidate['findings'] if f['resolved_api'] in req['apis']]
                    if matched:state='OBSERVED';basis='Declared API call observed; runtime behavior not established.';refs=[f['finding_id'] for f in matched]
                    else:basis='Not observed by this profile. Absence is not established.'
                elif req['kind']=='review':
                    evidence=[e for e in hist if e['kind']=='operator_review' and e['payload']['candidate']==candidate['key'] and e['payload']['requirement']==req['id']]
                    if evidence:
                        e=evidence[-1];state={'SUPPORTED':'REVIEWED_SUPPORT','CONTRADICTED':'CONTRADICTED','UNKNOWN':'UNKNOWN'}[e['payload']['verdict']]
                        basis=e['payload']['note'];refs=[e['id']]
                else:
                    executions=[(e,r) for e in hist if e['kind']=='fixed_trial' for r in e['payload']['results'] if r['candidate']==candidate['key'] and r['test_id']==req['id']]
                    if executions:
                        e,r=executions[-1];state='TEST_PASSED' if r['passed'] else 'TEST_FAILED';basis='Executed fixed first-party test; limited to recorded fixtures and runtime.';refs=[e['id']]
                checks.append({**req,'state':state,'basis':basis,'evidence_ids':refs})
            needed=[r for r in checks if r['required']]
            verdict='CONTRADICTED' if any(r['state'] in {'CONTRADICTED','TEST_FAILED'} for r in needed) else ('NEEDS_EVIDENCE' if any(r['state']=='UNKNOWN' for r in needed) else 'READY_FOR_INTEGRATION_REVIEW')
            if not current:verdict='STALE_SOURCE_REVIEW_REQUIRED'
            rows.append({'candidate':candidate,'requirements':checks,'verdict':verdict,'runtime_verified':False,'release_approved':False,
                'next_steps':[r['label'] for r in needed if r['state'] in {'UNKNOWN','TEST_FAILED','CONTRADICTED'}]+['Review intended reuse rights, dependency environment, and integration behavior.']})
        return {'schema':1,'case_id':cid,'title':case['contract']['title'],'objective':case['contract']['objective'],
            'contract':case['contract'],'status':'CURRENT_REVIEW' if current else 'STALE_SOURCE_REVIEW_REQUIRED','rows':rows,
            'collection_status':self.store.snapshot(case['mission'])['status'],'evidence':hist,
            'source_lock_sha256':sha(canonical(case['source_lock'])),'release_approved':False,'reuse_approved':False,
            'independent_evaluation':False,'limits':['API observations are not behavior guarantees.','Operator reviews are attributed statements, not independent validation.','Fixed tests cover recorded cases only.','Hashes check local consistency, not authenticity against a privileged actor.']}
    def export(self,cid,out):
        case=self.get(cid);report=self.compare(cid);out=safe_path(out)
        package_root=Path(__file__).resolve().parents[1]
        if out.exists() or out.is_relative_to(package_root) or out.is_relative_to(self.store.home/'engine'):
            raise Blocked('NEW_REPORT_DIRECTORY_REQUIRED')
        out.mkdir(parents=True,mode=0o700)
        plan=['# Integration plan',report['title'],'','This is a review packet, not permission to execute or deploy.','']
        for row in report['rows']:
            candidate=row['candidate'];plan+=['## '+(candidate['qualified_name'] or 'Source-scoped candidate'),row['verdict'],candidate['source_id'],
            'Observed SHA-256: '+candidate['source_sha256'],'']+['- '+s for s in row['next_steps']]+['']
        values={'comparison.json':canonical(report),'SOURCE_LOCK.json':canonical(case['source_lock']),
            'contract.json':canonical(case['contract']),'Integration_Plan.md':'\n'.join(plan).encode(),
            'Review.html':render(report).encode()}
        for n,b in values.items():write(out/n,b,new=True)
        receipt={'schema':1,'files':{n:sha(b) for n,b in values.items()},'case_id':cid,'source_bodies_included':False,'release_approved':False}
        write(out/'receipt.json',canonical(receipt),new=True);return receipt


def render(report):
    e=lambda v:html.escape(str(v))
    cards=[]
    for row in report['rows']:
        c=row['candidate'];trs=''.join('<tr><td>'+e(x['label'])+'</td><td>'+e(x['state'])+'</td><td>'+e(x['basis'])+'</td></tr>' for x in row['requirements'])
        cards.append('<article><h2>'+e(c['qualified_name'] or c['path'])+'</h2><p class="state">'+e(row['verdict'])+'</p><p>'+e(c['source_id'])+'</p><p>SHA-256: '+e(c['source_sha256'])+'</p><table><thead><tr><th>Requirement</th><th>Evidence state</th><th>Basis</th></tr></thead><tbody>'+trs+'</tbody></table></article>')
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FORGE Reuse Review</title><style>body{font:16px system-ui;margin:3% auto;max-width:1120px;padding:20px;background:#0f1720;color:#e9eef4}h1{font-size:40px}article{background:#1c2835;padding:24px;margin:24px 0;border-radius:14px}p{overflow-wrap:anywhere}.state{font-weight:700;color:#a9e5d6}table{width:100%;border-collapse:collapse}td,th{text-align:left;vertical-align:top;padding:12px;border-bottom:1px solid #485767}small{color:#b2c0cd}@media(max-width:650px){body{padding:8px}article{padding:12px}td,th{padding:6px}}</style><h1>'+e(report['title'])+'</h1><p>'+e(report['objective'])+'</p><p>'+e(report['status'])+'</p>'+''.join(cards)+'<small>Evidence review only. No release or reuse approval. Static observations, operator statements and fixed test results remain separate.</small></html>'


def verify_packet(folder):
    """Check portable packet completeness and local hashes, not claim authenticity."""
    root=safe_path(folder)
    names={'comparison.json','SOURCE_LOCK.json','contract.json','Integration_Plan.md','Review.html'}
    if not root.is_dir() or {p.name for p in root.iterdir()}!=names|{'receipt.json'}:raise Blocked('PACKET_FILE_SET')
    rec=loads(read(root/'receipt.json'));fields(rec,{'schema','files','case_id','source_bodies_included','release_approved'})
    if type(rec['schema']) is not int or rec['schema']!=1 or rec['source_bodies_included'] is not False or rec['release_approved'] is not False:raise Blocked('PACKET_AUTHORITY')
    if not isinstance(rec['files'],dict) or set(rec['files'])!=names:raise Blocked('PACKET_MANIFEST')
    for n,h in rec['files'].items():
        if sha(read(root/n))!=digest(h):raise Blocked('PACKET_CHANGED')
    report=loads(read(root/'comparison.json'));contract=validate_contract(loads(read(root/'contract.json')));lock=loads(read(root/'SOURCE_LOCK.json'))
    if report.get('case_id')!=rec['case_id'] or report.get('contract')!=contract or report.get('source_lock_sha256')!=sha(canonical(lock)):raise Blocked('PACKET_BINDING')
    if any(report.get(k) is not False for k in ['release_approved','reuse_approved','independent_evaluation']):raise Blocked('PACKET_AUTHORITY')
    return {'status':'PACKET_INTEGRITY_CONFIRMED_REVIEW_REQUIRED','case_id':rec['case_id'],'files_verified':6,'release_approved':False,'independent_evaluation':False}
