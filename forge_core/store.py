"""Transactional, single-provider mission queue. No remote writes or model execution."""
from __future__ import annotations
from contextlib import contextmanager
import secrets,sqlite3,time
from pathlib import Path
from .common import *
from . import policy
DDL='''
CREATE TABLE meta(k TEXT PRIMARY KEY,v BLOB NOT NULL);
CREATE TABLE mission(id TEXT PRIMARY KEY,spec BLOB NOT NULL,spec_hash TEXT NOT NULL,created REAL NOT NULL,requests INTEGER NOT NULL DEFAULT 0,source_bytes INTEGER NOT NULL DEFAULT 0,cancelled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE task(id TEXT PRIMARY KEY,mission TEXT NOT NULL REFERENCES mission(id),kind TEXT NOT NULL,payload BLOB NOT NULL,state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,token TEXT,expires REAL,not_before REAL NOT NULL DEFAULT 0,response_hash TEXT,result BLOB,error TEXT,UNIQUE(mission,kind,payload));
CREATE TABLE event(seq INTEGER PRIMARY KEY,mission TEXT,at REAL NOT NULL,kind TEXT NOT NULL,payload BLOB NOT NULL);
CREATE TABLE candidate(id TEXT PRIMARY KEY,mission TEXT NOT NULL,source_id TEXT NOT NULL,result BLOB NOT NULL,result_hash TEXT NOT NULL,UNIQUE(mission,source_id));
CREATE TABLE provider(id TEXT PRIMARY KEY,cooldown REAL NOT NULL DEFAULT 0,hold TEXT);
INSERT INTO provider VALUES('github',0,NULL);
'''
class Store:
    def __init__(self,home,clock=time.time):
        self.home=safe_path(home);self.path=self.home/'missions.sqlite';self.clock=clock
        if not self.path.is_file():raise Blocked('WORKSPACE_NOT_INITIALIZED')
        self.guard_files()
    @classmethod
    def create(cls,home,binding,clock=time.time):
        home=safe_path(home)
        if home.exists():raise Blocked('NEW_WORKSPACE_REQUIRED')
        home.mkdir(parents=True,mode=0o700)
        c=sqlite3.connect(home/'missions.sqlite')
        try:c.executescript(DDL);c.execute('INSERT INTO meta VALUES(?,?)',('binding',canonical(binding)));c.commit()
        finally:c.close()
        (home/'missions.sqlite').chmod(0o600)
        return cls(home,clock)
    def guard_files(self):
        for suffix in ['','-wal','-shm','-journal']:
            p=safe_path(str(self.path)+suffix)
            if p.exists() and (not p.is_file() or p.stat().st_nlink!=1):raise Blocked('SHARED_OR_INVALID_LEDGER')
    @contextmanager
    def tx(self):
        self.guard_files();c=sqlite3.connect(self.path,timeout=10,isolation_level=None);c.row_factory=sqlite3.Row
        try:
            c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA synchronous=FULL');c.execute('BEGIN IMMEDIATE')
            yield c;c.execute('COMMIT')
        except BaseException:
            if c.in_transaction:c.rollback()
            raise
        finally:c.close()
    def binding(self):
        with self.tx() as c:return loads(c.execute('SELECT v FROM meta WHERE k="binding"').fetchone()[0])
    def event(self,c,mid,kind,obj):c.execute('INSERT INTO event(mission,at,kind,payload) VALUES(?,?,?,?)',(mid,self.clock(),kind,canonical(obj)))
    def add_task(self,c,mid,kind,p):
        policy.endpoint(kind,p);data=canonical(p);tid='t_'+sha(canonical([mid,kind,p]))[:32]
        c.execute('INSERT OR IGNORE INTO task(id,mission,kind,payload,state) VALUES(?,?,?,?,"READY")',(tid,mid,kind,data));return tid
    def create_mission(self,spec):
        spec=policy.validate(spec);mid='m_'+secrets.token_hex(12)
        with self.tx() as c:
            c.execute('INSERT INTO mission(id,spec,spec_hash,created) VALUES(?,?,?,?)',(mid,canonical(spec),sha(canonical(spec)),self.clock()))
            for q in spec['queries']:self.add_task(c,mid,'search',{'query':q,'page':1})
            self.event(c,mid,'MISSION_CREATED',{'template':spec['template'],'budget':spec['limits'],'profile_sha256':sha(canonical(spec['profile']))})
        return mid
    def spec(self,c,mid):
        row=c.execute('SELECT * FROM mission WHERE id=?',(mid,)).fetchone()
        if row is None:raise Blocked('MISSION_NOT_FOUND')
        if sha(row['spec'])!=row['spec_hash']:raise Blocked('MISSION_CHANGED')
        return row,policy.validate(loads(row['spec']))
    def check_task(self,row):
        expected='t_'+sha(canonical([row['mission'],row['kind'],loads(row['payload'])]))[:32]
        if row['id']!=expected:raise Blocked('TASK_PAYLOAD_CHANGED')
    def claim(self,mid):
        with self.tx() as c:
            m,s=self.spec(c,mid);now=self.clock();limit=s['limits']
            if m['cancelled']:return None
            provider=c.execute('SELECT cooldown,hold FROM provider WHERE id="github"').fetchone()
            if provider['hold']:return None
            cooldown=provider['cooldown']
            if cooldown>now:return None
            if c.execute('SELECT 1 FROM task WHERE state="LEASED" AND expires>?',(now,)).fetchone():return None
            c.execute('UPDATE task SET state="BLOCKED",error="ATTEMPTS_EXHAUSTED" WHERE mission=? AND state="LEASED" AND expires<=? AND attempts>=?',(mid,now,limit['max_attempts']))
            if m['requests']>=limit['max_requests']:return None
            row=c.execute('SELECT * FROM task WHERE mission=? AND attempts<? AND not_before<=? AND (state="READY" OR (state="LEASED" AND expires<=?)) ORDER BY rowid LIMIT 1',(mid,limit['max_attempts'],now,now)).fetchone()
            if not row:return None
            self.check_task(row)
            token=secrets.token_urlsafe(32);expires=now+300
            c.execute('UPDATE task SET state="LEASED",token=?,expires=?,attempts=attempts+1 WHERE id=?',(token,expires,row['id']))
            c.execute('UPDATE mission SET requests=requests+1 WHERE id=?',(mid,))
            self.event(c,mid,'REQUEST_CLAIMED',{'task':row['id'],'kind':row['kind'],'attempt':row['attempts']+1})
            p=loads(row['payload'])
            return {'id':row['id'],'mission':mid,'kind':row['kind'],'payload':p,'url':policy.url_for(row['kind'],p),'method':'GET','token':token,'expires':expires,'max_response_bytes':limit['max_response_bytes']}
    def owned(self,c,packet,allow_done=False):
        r=c.execute('SELECT * FROM task WHERE id=?',(packet['id'],)).fetchone()
        if r is None or r['mission']!=packet['mission'] or r['kind']!=packet['kind'] or loads(r['payload'])!=packet['payload']:raise Blocked('REQUEST_BINDING')
        self.check_task(r)
        if not isinstance(packet.get('token'),str) or not secrets.compare_digest(r['token'] or '',packet['token']):raise Blocked('STALE_CLAIM')
        m,s=self.spec(c,r['mission'])
        if m['cancelled']:raise Blocked('MISSION_CANCELLED')
        if not (allow_done and r['state']=='DONE') and (r['state']!='LEASED' or r['expires']<=self.clock()):raise Blocked('STALE_CLAIM')
        return r,m,s
    def fail(self,packet,reason,retry=False,delay=0,global_wait=False):
        if not isinstance(reason,str) or not re.fullmatch('[A-Z_0-9:]{1,100}',reason):raise Blocked('ERROR_LABEL')
        if type(delay) not in (int,float) or not math.isfinite(delay) or not 0<=delay<=86400:raise Blocked('RETRY_DELAY')
        with self.tx() as c:
            r,m,s=self.owned(c,packet);state='READY' if retry and r['attempts']<s['limits']['max_attempts'] else 'BLOCKED'
            c.execute('UPDATE task SET state=?,expires=NULL,not_before=?,error=? WHERE id=?',(state,self.clock()+delay,reason,r['id']))
            if global_wait:c.execute('UPDATE provider SET cooldown=max(cooldown,?) WHERE id="github"',(self.clock()+delay,))
            self.event(c,r['mission'],'REQUEST_PAUSED' if state=='READY' else 'REQUEST_BLOCKED',{'task':r['id'],'reason':reason,'not_before':self.clock()+delay})
    def hold_provider(self,packet,reason):
        with self.tx() as c:
            r,m,s=self.owned(c,packet)
            c.execute('UPDATE provider SET hold=? WHERE id="github"',(reason,))
            c.execute('UPDATE task SET state="BLOCKED",error=?,expires=NULL WHERE id=?',(reason,r['id']))
            self.event(c,r['mission'],'PROVIDER_REVIEW_REQUIRED',{'reason':reason,'task':r['id']})
    def complete(self,packet,response_hash,result,children=(),candidate=None,source_bytes=0,cooldown=0):
        with self.tx() as c:
            r,m,s=self.owned(c,packet,allow_done=True)
            if r['state']=='DONE':
                if r['response_hash']!=response_hash:raise Blocked('CHANGED_REPLAY')
                return False
            if m['source_bytes']+source_bytes>s['limits']['max_total_source_bytes']:raise Blocked('MISSION_SOURCE_BUDGET')
            if candidate:
                b=canonical(candidate);cid='c_'+sha(canonical([m['id'],candidate['source_id']]))[:32]
                c.execute('INSERT INTO candidate VALUES(?,?,?,?,?)',(cid,m['id'],candidate['source_id'],b,sha(b)))
            for k,p in children:self.add_task(c,r['mission'],k,p)
            c.execute('UPDATE task SET state="DONE",response_hash=?,result=?,expires=NULL,error=NULL WHERE id=?',(response_hash,canonical(result),r['id']))
            c.execute('UPDATE mission SET source_bytes=source_bytes+? WHERE id=?',(source_bytes,m['id']))
            if cooldown:c.execute('UPDATE provider SET cooldown=max(cooldown,?) WHERE id="github"',(cooldown,))
            self.event(c,r['mission'],'REQUEST_COMPLETE',{'task':r['id'],'kind':r['kind'],'response_sha256':response_hash,'outcome':result.get('outcome'),'new_leads':len(children)})
            return True
    def cancel(self,mid):
        with self.tx() as c:
            self.spec(c,mid)
            c.execute('UPDATE mission SET cancelled=1 WHERE id=?',(mid,))
            c.execute("UPDATE task SET state='BLOCKED',error='MISSION_CANCELLED',expires=NULL WHERE mission=? AND state IN ('READY','LEASED')",(mid,))
            self.event(c,mid,'CANCELLED',{})
    def task_count(self,mid,kind):
        with self.tx() as c:return c.execute('SELECT COUNT(*) FROM task WHERE mission=? AND kind=?',(mid,kind)).fetchone()[0]
    def snapshot(self,mid=None):
        with self.tx() as c:
            mids=[mid] if mid else [r[0] for r in c.execute('SELECT id FROM mission ORDER BY created DESC')]
            out=[]
            for m_id in mids:
                m,s=self.spec(c,m_id);rows=[dict(r) for r in c.execute('SELECT id,kind,state,attempts,not_before,error,result FROM task WHERE mission=? ORDER BY rowid',(m_id,))]
                counts={k:sum(r['state']==k for r in rows) for k in ('READY','LEASED','DONE','BLOCKED')}
                notes=[]
                for r in rows:
                    r['result']=loads(r['result']) if r['result'] else None
                    if r['result'] and r['result'].get('gap'):notes.append(r['result']['gap'])
                cand=[]
                for r in c.execute('SELECT id,result,result_hash FROM candidate WHERE mission=? ORDER BY id',(m_id,)):
                    if sha(r['result'])!=r['result_hash']:raise Blocked('CANDIDATE_CHANGED')
                    cand.append({'id':r['id'],**loads(r['result'])})
                wait=max([c.execute('SELECT cooldown FROM provider WHERE id="github"').fetchone()[0]]+[r['not_before'] for r in rows if r['state']=='READY'])
                if m['cancelled']:state='CANCELLED'
                elif c.execute('SELECT hold FROM provider WHERE id="github"').fetchone()[0] and (counts['READY'] or counts['LEASED']):state='PROVIDER_REVIEW_REQUIRED'
                elif counts['READY'] or counts['LEASED']:
                    state='BUDGET_EXHAUSTED' if m['requests']>=s['limits']['max_requests'] and not counts['LEASED'] else ('RATE_WAIT' if wait>self.clock() else 'WORK_REMAINS')
                elif counts['BLOCKED'] or notes:state='COMPLETED_WITH_GAPS'
                elif not any(x['findings'] for x in cand):state='COMPLETED_NO_MATCH'
                else:state='COMPLETED_FOR_BOUNDED_MISSION'
                events=[{'sequence':r['seq'],'at':r['at'],'kind':r['kind'],**loads(r['payload'])} for r in c.execute('SELECT * FROM event WHERE mission=? ORDER BY seq',(m_id,))]
                out.append({'id':m_id,'title':s['title'],'public_brief':s['public_brief'],'profile':s['profile'],'status':state,'counts':counts,'requests_used':m['requests'],'request_budget':s['limits']['max_requests'],'source_bytes':m['source_bytes'],'next_allowed_at':wait,'tasks':rows,'candidates':cand,'events':events,'gaps':notes,'release_approved':False,'reuse_approved':False,'independent_evaluation':False,'scope':'Bounded repository sampling; not complete Internet or repository coverage.'})
            return out[0] if mid else out
