"""A bounded, local improvement controller for data-only FORGE search profiles.

Models may propose profiles through JSON. They cannot supply commands, replace code,
edit the frozen acceptance contract, or mark their own evaluation successful here.
Same-user filesystem access remains trusted; this is not an authenticated service.
"""
from __future__ import annotations
from pathlib import Path
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import argparse, copy, gzip, json, os, signal, sqlite3, subprocess, sys, tempfile
import threading, time, uuid
from safeio import (Blocked,canonical,digest,strict_json,text,integer,shape,sha_string,
                    relative,no_links,read,write_new,atomic_json,file_binding,check_binding)

ROOT=Path(__file__).resolve().parent
VERSION='0.2.0'
CORE=['forge_cycle.py','probe_worker.py','safeio.py','prepare_runtime.py']
TASK_TIMEOUT=15
MAX_RESULT=8_000_000
MAX_CANDIDATES=8
PINNED_ADDON_FILES={'tool/mission_probe.py': '954a70b593b3f50d56d51f3149a8a9060ecb4e4d547261bd86cc8ed053bf680d', 'contracts/hound_binding.json': '7c8690eda2df3ede9ae744ce63b1b3c1fd819c6903c9dc67c1cb4c9fab2e2350'}


def validate_mission(m):
    shape(m,{'schema','id','requirement','allowed_api_roots','max_candidates','max_added_targets','minimum_gain'},'MISSION_SCHEMA')
    integer(m['schema'],'MISSION_SCHEMA',1,1);text(m['id'],'MISSION_ID',80);text(m['requirement'],'REQUIREMENT')
    integer(m['max_candidates'],'CANDIDATE_BUDGET',1,MAX_CANDIDATES)
    integer(m['max_added_targets'],'TARGET_BUDGET',1,32)
    integer(m['minimum_gain'],'MINIMUM_GAIN',1,1000)
    if (not isinstance(m['allowed_api_roots'],list) or not m['allowed_api_roots']
        or len(m['allowed_api_roots'])>30 or len(set(m['allowed_api_roots']))!=len(m['allowed_api_roots'])):
        raise Blocked('API_ROOTS')
    for item in m['allowed_api_roots']:
        if not isinstance(item,str) or not item.isidentifier():raise Blocked('API_ROOT')
    return m


def validate_contract(c):
    shape(c,{'schema','cases'},'CONTRACT_SCHEMA');integer(c['schema'],'CONTRACT_SCHEMA',1,1)
    if not isinstance(c['cases'],list) or not 1<=len(c['cases'])<=300:raise Blocked('CASE_COUNT')
    ids=set();positive=False;negative=False
    for r in c['cases']:
        shape(r,{'id','code','expected'},'CASE_SCHEMA');text(r['id'],'CASE_ID',100)
        if r['id'] in ids:raise Blocked('DUPLICATE_CASE')
        ids.add(r['id']);text(r['code'],'CASE_CODE',50_000)
        if not isinstance(r['expected'],list) or len(r['expected'])>20:raise Blocked('EXPECTED_MATCHES')
        found=set()
        for e in r['expected']:
            shape(e,{'scope','name','api','capability'},'EXPECTED_SCHEMA')
            if e['scope'] not in {'function','script'}:raise Blocked('EXPECTED_SCOPE')
            for k in ('name','api','capability'):text(e[k],'EXPECTED_FIELD',250)
            if e['scope']=='script' and e['name']!='<module>':raise Blocked('SCRIPT_NAME')
            key=canonical(e)
            if key in found:raise Blocked('DUPLICATE_EXPECTATION')
            found.add(key)
        positive|=bool(r['expected']);negative|=not r['expected']
    if not positive or not negative:raise Blocked('POSITIVE_AND_NEGATIVE_CONTROLS_REQUIRED')
    return c


def validate_sources(s):
    shape(s,{'schema','sources'},'SOURCE_MANIFEST');integer(s['schema'],'SOURCE_SCHEMA',1,1)
    if not isinstance(s['sources'],list) or not 1<=len(s['sources'])<=1000:raise Blocked('SOURCE_COUNT')
    ids=set();paths=set()
    for r in s['sources']:
        shape(r,{'source_id','path','sha256'},'SOURCE_ROW');text(r['source_id'],'SOURCE_ID',2000)
        p=relative(r['path']);sha_string(r['sha256'])
        if p.suffix!='.py':raise Blocked('PYTHON_SOURCE_REQUIRED')
        if r['source_id'] in ids or r['path'] in paths:raise Blocked('DUPLICATE_SOURCE')
        ids.add(r['source_id']);paths.add(r['path'])
    return s


def probe_module(runtime):
    # Only the verified, shipped previous add-on is loadable. No proposal selects code.
    addon=Path(runtime['addon'])
    for name,expected in PINNED_ADDON_FILES.items():
        if digest(read(addon/name,500_000))!=expected:raise Blocked('ADDON_BINDING_MISMATCH')
    if {p.name for p in (addon/'tool').glob('*.py')} != {'mission_probe.py','run_supervised_hunt.py','test_addon.py'}:
        raise Blocked('UNEXPECTED_ADDON_MODULE')
    if str(addon/'tool') not in sys.path:sys.path.insert(0,str(addon/'tool'))
    import mission_probe
    if Path(mission_probe.__file__).resolve()!=addon/'tool/mission_probe.py':raise Blocked('DIFFERENT_ADDON_LOADED')
    mission_probe.load_hound(Path(runtime['hound']))
    return mission_probe


def runtime_binding(runtime):
    addon=Path(runtime['addon']);hound=Path(runtime['hound'])
    paths=[ROOT/p for p in CORE]
    paths += list((ROOT/'tests').glob('*.py'))+[ROOT/'run_tests.py',ROOT/'run_demo.py']
    paths += list((addon/'tool').glob('*.py'))+list((addon/'contracts').glob('*'))
    paths += list(hound.glob('*.py'))+list((hound/'tests').glob('*.py'))+list((hound/'tests').glob('*.json'))
    paths += list((hound/'profiles').rglob('*.json'))
    return file_binding([p for p in paths if p.is_file()])


def observation_keys(result):
    return sorted({canonical({'scope':f['scope_kind'],'name':f.get('qualified_name','<module>'),
                              'api':f['resolved_api'],'capability':f['capability']}).decode()
                   for f in result['findings']})


def source_bytes(spec,row):
    p=Path(spec['source_root'])/row['path'];data=read(p,250_000)
    if digest(data)!=row['sha256']:raise Blocked('SOURCE_VERSION_MISMATCH')
    return data


DDL='''
CREATE TABLE metadata(k TEXT PRIMARY KEY,v BLOB NOT NULL);
CREATE TABLE candidates(id TEXT PRIMARY KEY,seq INTEGER UNIQUE NOT NULL,profile BLOB NOT NULL,
 profile_sha256 TEXT NOT NULL,proposal BLOB NOT NULL,state TEXT NOT NULL,verdict BLOB);
CREATE TABLE tasks(id TEXT PRIMARY KEY,candidate TEXT NOT NULL REFERENCES candidates(id),kind TEXT NOT NULL,
 input_id TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,token TEXT,expires REAL,
 result BLOB,result_sha256 TEXT,error TEXT,UNIQUE(candidate,kind,input_id));
CREATE TABLE events(seq INTEGER PRIMARY KEY,at REAL NOT NULL,kind TEXT NOT NULL,payload BLOB NOT NULL,
 previous TEXT NOT NULL,sha256 TEXT NOT NULL);
'''

class Store:
    def __init__(self,job,clock=time.time):
        self.job=no_links(job);self.path=self.job/'ledger.sqlite';self.clock=clock
        if not self.path.is_file():raise Blocked('JOB_NOT_INITIALIZED')
        no_links(self.path)
        if self.path.stat().st_nlink!=1:raise Blocked('LEDGER_HARDLINK')
        self.spec=strict_json(read(self.job/'spec.json',5_000_000))
        self.seal=strict_json(read(self.job/'seal.json',2_000_000))
        if digest(canonical(self.spec))!=self.seal.get('spec_sha256'):raise Blocked('SPEC_BINDING_CHANGED')
        self.binding=self.seal['code_bindings']
    @contextmanager
    def transaction(self):
        for extra in ['', '-journal','-wal','-shm']:
            p=no_links(str(self.path)+extra)
            if p.exists() and p.stat().st_nlink!=1:raise Blocked('LEDGER_HARDLINK')
        c=sqlite3.connect(self.path,timeout=10,isolation_level=None)
        c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA synchronous=FULL')
        try:
            c.execute('BEGIN IMMEDIATE');yield c;c.execute('COMMIT')
        except BaseException:
            if c.in_transaction:c.execute('ROLLBACK')
            raise
        finally:c.close()
    def event(self,c,kind,payload):
        prev=c.execute('SELECT sha256 FROM events ORDER BY seq DESC LIMIT 1').fetchone()
        prev=prev[0] if prev else '0'*64
        seq=c.execute('SELECT COUNT(*) FROM events').fetchone()[0]+1
        at=self.clock();body=canonical({'seq':seq,'at':at,'kind':kind,'payload':payload,'previous':prev})
        c.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',(seq,at,kind,canonical(payload),prev,digest(body)))
    def guard(self,sources=True):
        if digest(read(self.job/'spec.json',5_000_000))!=self.seal['spec_sha256']:raise Blocked('SPEC_CHANGED')
        check_binding(self.binding)
        if sources:
            for row in self.spec['sources']['sources']:source_bytes(self.spec,row)
        with self.transaction() as c:
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise Blocked('LEDGER_INTEGRITY')
            if c.execute('SELECT v FROM metadata WHERE k="spec_hash"').fetchone()[0]!=self.seal['spec_sha256']:
                raise Blocked('LEDGER_BINDING')
            prev='0'*64;seq=0
            for row in c.execute('SELECT * FROM events ORDER BY seq'):
                seq+=1;obj={'seq':seq,'at':row['at'],'kind':row['kind'],'payload':strict_json(row['payload']),'previous':prev}
                if row['seq']!=seq or row['previous']!=prev or row['sha256']!=digest(canonical(obj)):
                    raise Blocked('EVENT_CHAIN')
                prev=row['sha256']
            for row in c.execute('SELECT * FROM candidates'):
                if row['profile_sha256']!=digest(row['profile']):raise Blocked('PROFILE_CHANGED')
                if row['state'] not in {'QUEUED','EVALUATED'}:raise Blocked('CANDIDATE_STATE')
            for row in c.execute('SELECT * FROM tasks'):
                if type(row['attempts']) is not int or not 0<=row['attempts']<=2:raise Blocked('ATTEMPT_LEDGER')
                if row['status'] not in {'QUEUED','LEASED','DONE','FAILED'}:raise Blocked('TASK_STATE')
                if row['status']=='DONE' and (row['result'] is None or digest(row['result'])!=row['result_sha256']):
                    raise Blocked('RESULT_DIGEST')
        return True
    def add_tasks(self,c,candidate):
        for kind,rows,field in [('case',self.spec['contract']['cases'],'id'),('source',self.spec['sources']['sources'],'source_id')]:
            for row in rows:
                key=digest(canonical([self.seal['spec_sha256'],candidate,kind,row[field]]))
                c.execute('INSERT INTO tasks(id,candidate,kind,input_id,status) VALUES(?,?,?,?,"QUEUED")',(key,candidate,kind,row[field]))
    def submit(self,proposal):
        self.guard();self.finalize()
        shape(proposal,{'schema','baseline_profile_sha256','reason','add_targets','source_references'},'PROPOSAL_SCHEMA')
        integer(proposal['schema'],'PROPOSAL_SCHEMA',1,1);text(proposal['reason'],'PROPOSAL_REASON',4000)
        base=self.spec['baseline_profile'];base_hash=digest(canonical(base))
        if proposal['baseline_profile_sha256']!=base_hash:raise Blocked('STALE_PROPOSAL')
        refs=proposal['source_references'];known={r['source_id'] for r in self.spec['sources']['sources']}
        if not isinstance(refs,list) or not refs or len(refs)>20 or len(set(refs))!=len(refs) or not set(refs)<=known:
            raise Blocked('UNBOUND_SOURCE_REFERENCES')
        additions=proposal['add_targets'];mission=self.spec['mission']
        if not isinstance(additions,list) or not 1<=len(additions)<=mission['max_added_targets']:raise Blocked('TARGET_BUDGET')
        candidate=copy.deepcopy(base);candidate['targets']+=additions
        probe_module(self.spec['runtime']).validate_profile(candidate)
        if any(t['api'].split('.')[0] not in mission['allowed_api_roots'] for t in additions):raise Blocked('TARGET_SCOPE')
        proposal_hash=digest(canonical(proposal));cid='candidate_'+digest(canonical(candidate))[:24]
        with self.transaction() as c:
            b=c.execute('SELECT * FROM candidates WHERE id="baseline"').fetchone()
            if b['state']!='EVALUATED':raise Blocked('BASELINE_NOT_EVALUATED')
            bv=strict_json(b['verdict'])
            if bv['correct_cases']==bv['case_count']:raise Blocked('BASELINE_ALREADY_SATISFIES_MISSION')
            existing=c.execute('SELECT * FROM candidates WHERE id=?',(cid,)).fetchone()
            if existing:return {'id':cid,'duplicate':True}
            for row in c.execute('SELECT verdict FROM candidates WHERE verdict IS NOT NULL AND id!="baseline"'):
                if strict_json(row[0])['status']=='READY_FOR_ADOPTION_REVIEW':raise Blocked('MISSION_ALREADY_SATISFIED')
            if c.execute('SELECT COUNT(*) FROM candidates WHERE id!="baseline" AND state!="EVALUATED"').fetchone()[0]:
                raise Blocked('EVALUATE_CURRENT_CANDIDATE_FIRST')
            seq=c.execute('SELECT COUNT(*) FROM candidates').fetchone()[0]
            if seq>mission['max_candidates']:raise Blocked('CANDIDATE_BUDGET_EXHAUSTED')
            c.execute('INSERT INTO candidates VALUES(?,?,?,?,?,"QUEUED",NULL)',(cid,seq,canonical(candidate),digest(canonical(candidate)),canonical(proposal)))
            self.add_tasks(c,cid);self.event(c,'CANDIDATE_BUILT',{'candidate':cid,'proposal_sha256':proposal_hash,'added_targets':len(additions)})
        return {'id':cid,'duplicate':False,'execution_authority':'STATIC_PROFILE_INSPECTION_ONLY'}
    def claim(self,lease_seconds=60):
        if type(lease_seconds) not in (int,float) or not 0<lease_seconds<=120:raise Blocked('LEASE_LIMIT')
        with self.transaction() as c:
            now=self.clock()
            for row in c.execute('SELECT * FROM tasks WHERE status="LEASED" AND expires<=?',(now,)).fetchall():
                state='QUEUED' if row['attempts']<2 else 'FAILED'
                c.execute('UPDATE tasks SET status=?,token=NULL,expires=NULL,error="EXPIRED" WHERE id=?',(state,row['id']))
                self.event(c,'LEASE_EXPIRED',{'task':row['id'],'next_status':state})
            row=c.execute('SELECT t.*,p.profile FROM tasks t JOIN candidates p ON t.candidate=p.id WHERE t.status="QUEUED" ORDER BY p.seq,t.kind,t.input_id LIMIT 1').fetchone()
            if row is None:return None
            token=uuid.uuid4().hex
            c.execute('UPDATE tasks SET status="LEASED",attempts=attempts+1,token=?,expires=? WHERE id=?',(token,now+lease_seconds,row['id']))
            self.event(c,'CLAIM',{'task':row['id'],'candidate':row['candidate'],'attempt':row['attempts']+1})
            return {**dict(row),'token':token,'expires':now+lease_seconds,'attempts':row['attempts']+1,'profile':strict_json(row['profile'])}
    def heartbeat(self,task,lease_seconds=60):
        if type(lease_seconds) not in (int,float) or not 0<lease_seconds<=120:raise Blocked('LEASE_LIMIT')
        with self.transaction() as c:
            cur=c.execute('UPDATE tasks SET expires=? WHERE id=? AND status="LEASED" AND token=? AND expires>?',
                          (self.clock()+lease_seconds,task['id'],task['token'],self.clock()))
            if cur.rowcount!=1:raise Blocked('STALE_WORKER')
    def finish(self,task,result):
        payload=canonical(result)
        if len(payload)>MAX_RESULT:raise Blocked('RESULT_SIZE')
        with self.transaction() as c:
            cur=c.execute('UPDATE tasks SET status="DONE",result=?,result_sha256=?,token=NULL,expires=NULL,error=NULL WHERE id=? AND status="LEASED" AND token=? AND expires>?',
                          (payload,digest(payload),task['id'],task['token'],self.clock()))
            if cur.rowcount!=1:raise Blocked('STALE_WORKER')
            self.event(c,'TASK_COMPLETED',{'task':task['id'],'result_sha256':digest(payload)})
    def fail(self,task,kind):
        with self.transaction() as c:
            row=c.execute('SELECT * FROM tasks WHERE id=? AND status="LEASED" AND token=? AND expires>?',
                          (task['id'],task['token'],self.clock())).fetchone()
            if row is None:raise Blocked('STALE_WORKER')
            state='QUEUED' if row['attempts']<2 else 'FAILED'
            c.execute('UPDATE tasks SET status=?,token=NULL,expires=NULL,error=? WHERE id=?',(state,kind,task['id']))
            self.event(c,'TASK_FAILED',{'task':task['id'],'error_type':kind,'next_status':state})
    def results(self,c,cid):
        rows=c.execute('SELECT * FROM tasks WHERE candidate=?',(cid,)).fetchall()
        expected={(k,r[f]) for k,rs,f in [('case',self.spec['contract']['cases'],'id'),('source',self.spec['sources']['sources'],'source_id')] for r in rs}
        if {(r['kind'],r['input_id']) for r in rows}!=expected or len(rows)!=len(expected):raise Blocked('TASK_COVERAGE')
        if any(r['status']!='DONE' for r in rows):return None
        result={}
        for row in rows:
            if digest(row['result'])!=row['result_sha256']:raise Blocked('RESULT_DIGEST')
            result[(row['kind'],row['input_id'])]=strict_json(row['result'])
        return result
    def finalize(self):
        self.guard()
        with self.transaction() as c:
            baseline=None
            for candidate in c.execute('SELECT * FROM candidates ORDER BY seq').fetchall():
                cid=candidate['id'];results=self.results(c,cid)
                if results is None:continue
                if cid=='baseline':baseline=results
                elif baseline is None:raise Blocked('BASELINE_INCOMPLETE')
                profile=strict_json(candidate['profile'])
                # Verify source/profile/evidence contracts again before any verdict.
                valid={}
                probe=probe_module(self.spec['runtime'])
                for case in self.spec['contract']['cases']:
                    res=results[('case',case['id'])]
                    probe.validate_result(res,case['code'].encode(),'fixture:'+case['id'],profile,Path(self.spec['runtime']['hound']))
                    valid[case['id']]=observation_keys(res)==sorted(canonical(e).decode() for e in case['expected'])
                for row in self.spec['sources']['sources']:
                    probe.validate_result(results[('source',row['source_id'])],source_bytes(self.spec,row),row['source_id'],profile,Path(self.spec['runtime']['hound']))
                good={k for k,v in valid.items() if v};failed=sorted(set(valid)-good)
                regressions=[];lost=[];gain=0
                if cid!='baseline':
                    bgood={r['id'] for r in self.spec['contract']['cases'] if observation_keys(baseline[('case',r['id'])])==sorted(canonical(e).decode() for e in r['expected'])}
                    regressions=sorted(bgood-good);gain=len(good)-len(bgood)
                    for row in self.spec['sources']['sources']:
                        key=('source',row['source_id'])
                        if not set(observation_keys(baseline[key]))<=set(observation_keys(results[key])):lost.append(row['source_id'])
                if cid=='baseline':status='BASELINE_MEASURED'
                elif regressions or lost or gain<self.spec['mission']['minimum_gain']:status='REJECTED'
                elif failed:status='IMPROVED_BUT_INCOMPLETE'
                else:status='READY_FOR_ADOPTION_REVIEW'
                verdict={'status':status,'case_count':len(valid),'correct_cases':len(good),'failed_case_ids':failed,
                         'required_regressions':regressions,'lost_source_observations':lost,'net_correct_case_gain':gain,
                         'sources_inspected':len(self.spec['sources']['sources']),
                         'observations':sum(len(v['findings']) for k,v in results.items() if k[0]=='source'),
                         'profile_sha256':candidate['profile_sha256'],'contract_sha256':digest(canonical(self.spec['contract'])),
                         'release_approved':False,'adopted':False,'independent_evaluation':False,
                         'source_code_executed':False,'model_calls':0}
                if candidate['state']=='EVALUATED':
                    if canonical(verdict)!=candidate['verdict']:raise Blocked('VERDICT_CHANGED')
                else:
                    c.execute('UPDATE candidates SET state="EVALUATED",verdict=? WHERE id=?',(canonical(verdict),cid))
                    self.event(c,'EVALUATED',{'candidate':cid,'verdict':verdict})
        return True
    def status(self):
        self.finalize()
        with self.transaction() as c:
            candidates=[{'id':r['id'],'sequence':r['seq'],'state':r['state'],'verdict':strict_json(r['verdict']) if r['verdict'] else None}
                        for r in c.execute('SELECT * FROM candidates ORDER BY seq')]
            counts={r[0]:r[1] for r in c.execute('SELECT status,COUNT(*) FROM tasks GROUP BY status')}
            completed=counts.get('DONE',0);attempts=c.execute('SELECT SUM(attempts) FROM tasks').fetchone()[0]
            ready=[r for r in candidates if r['verdict'] and r['verdict']['status']=='READY_FOR_ADOPTION_REVIEW']
            if counts.get('FAILED'):state='BLOCKED_TASK_FAILURE'
            elif counts.get('LEASED') or counts.get('QUEUED'):state='WORK_REMAINS'
            elif ready:state='READY_FOR_ADOPTION_REVIEW'
            elif candidates[0]['verdict'] and candidates[0]['verdict']['correct_cases']==candidates[0]['verdict']['case_count']:state='BASELINE_ALREADY_SATISFIES_MISSION'
            elif len(candidates)-1>=self.spec['mission']['max_candidates']:state='BUDGET_EXHAUSTED'
            else:state='AWAITING_PROFILE_PROPOSAL'
            return {'schema':1,'controller_version':VERSION,'status':state,'tasks':counts,'completed_tasks':completed,'attempts':attempts,
                    'candidates':candidates,'mission_id':self.spec['mission']['id'],'release_approved':False,'adopted':False,
                    'independent_evaluation':False,'network_requests':0,'model_calls':0,
                    'spec_sha256':self.seal['spec_sha256'],'queued_proposals_are_not_ai_agents':True}
    def packet(self):
        status=self.status()
        if status['status']!='AWAITING_PROFILE_PROPOSAL':raise Blocked('NOT_AWAITING_PROPOSAL')
        # Do not export the frozen evaluator fixture source to the proposal interface.
        return {'schema':1,'status':'REQUEST_FOR_EXTERNAL_AGENT_NOT_DISPATCHED','mission':self.spec['mission'],
                'baseline_profile':self.spec['baseline_profile'],'baseline_profile_sha256':digest(canonical(self.spec['baseline_profile'])),
                'source_references':self.spec['sources']['sources'],'prior_verdicts':[r['verdict'] for r in status['candidates']],
                'allowed_proposal_fields':['schema','baseline_profile_sha256','reason','add_targets','source_references'],
                'forbidden':['commands','source rewriting','test editing','deployment','credential access'],
                'claims':'References are investigation leads, not proof of the proposed capability label.',
                'model_calls':0,'release_approved':False}


def initialize(job,runtime,source_root,mission,contract,sources,profile):
    validate_mission(mission);validate_contract(contract);validate_sources(sources)
    runtime=copy.deepcopy(runtime)
    for name in ('addon','hound'):
        runtime[name]=str(no_links(runtime[name]).resolve())
    probe_module(runtime).validate_profile(profile)
    source_root=no_links(source_root).resolve();job=no_links(job).resolve()
    protected=[source_root,ROOT,Path(runtime['addon']),Path(runtime['hound'])]
    if job.exists() or any(job.is_relative_to(p) or p.is_relative_to(job) for p in protected):raise Blocked('SEPARATE_NEW_JOB_REQUIRED')
    spec={'schema':1,'mission':mission,'contract':contract,'sources':sources,'baseline_profile':profile,
          'source_root':str(source_root),'runtime':runtime}
    for row in sources['sources']:source_bytes(spec,row)
    bindings=runtime_binding(runtime)
    job.mkdir(parents=True,mode=0o700)
    write_new(job/'spec.json',canonical(spec));write_new(job/'seal.json',canonical({'spec_sha256':digest(canonical(spec)),'code_bindings':bindings}))
    conn=sqlite3.connect(job/'ledger.sqlite')
    try:conn.executescript(DDL);conn.commit()
    finally:conn.close()
    store=Store(job)
    with store.transaction() as c:
        c.execute('INSERT INTO metadata VALUES("spec_hash",?)',(store.seal['spec_sha256'],))
        c.execute('INSERT INTO candidates VALUES("baseline",0,?,?,?,"QUEUED",NULL)',(canonical(profile),digest(canonical(profile)),canonical({'type':'original_baseline'})))
        store.add_tasks(c,'baseline');store.event(c,'INITIALIZED',{'mission':mission['id'],'contract_sha256':digest(canonical(contract))})
    return store


def execute_task(store,task,cancel=None):
    store.guard();spec=store.spec
    if task['kind']=='case':
        row=next(r for r in spec['contract']['cases'] if r['id']==task['input_id']);data=row['code'].encode();sid='fixture:'+row['id']
    else:
        row=next(r for r in spec['sources']['sources'] if r['source_id']==task['input_id']);data=source_bytes(spec,row);sid=row['source_id']
    packet=canonical({'source_hex':data.hex(),'source_id':sid,'profile':task['profile']})
    if len(packet)>900_000:raise Blocked('PACKET_LIMIT')
    runtime=spec['runtime'];cmd=[sys.executable,'-I','-B',str(ROOT/'probe_worker.py'),'--addon',runtime['addon'],'--hound',runtime['hound']]
    with tempfile.TemporaryDirectory(prefix='scan-',dir=store.job) as temp:
        env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'LANG':'C.UTF-8','HOME':temp,'TMPDIR':temp,'PYTHONDONTWRITEBYTECODE':'1'}
        output=Path(temp)/'output.json';error=Path(temp)/'error.json'
        with output.open('wb') as out,error.open('wb') as err:
            proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=out,stderr=err,env=env,start_new_session=(os.name=='posix'))
            start=time.monotonic();renew=start
            try:
                proc.stdin.write(packet);proc.stdin.close()
                while proc.poll() is None:
                    if cancel is not None and cancel.is_set():raise Blocked('RUN_INTERRUPTED')
                    if time.monotonic()-start>TASK_TIMEOUT:raise Blocked('TASK_TIMEOUT')
                    if output.stat().st_size>MAX_RESULT or error.stat().st_size>100_000:raise Blocked('WORKER_OUTPUT_LIMIT')
                    if time.monotonic()-renew>1:
                        store.heartbeat(task);renew=time.monotonic()
                    time.sleep(.02)
                if proc.returncode!=0:raise Blocked('PARSER_BLOCKED')
            finally:
                if proc.poll() is None:
                    if os.name=='posix':os.killpg(proc.pid,signal.SIGKILL)
                    else:proc.kill()
                proc.wait()
        result=strict_json(read(output,MAX_RESULT))
    probe_module(runtime).validate_result(result,data,sid,task['profile'],Path(runtime['hound']))
    store.guard()
    return result


def run_jobs(store,max_tasks=100,workers=2,cancel=None):
    integer(max_tasks,'TASK_BUDGET',0,5000);integer(workers,'WORKER_BUDGET',1,3)
    store.guard();cancel=cancel or threading.Event();lock=threading.Lock();claimed=0;errors=[]
    def work():
        nonlocal claimed
        while not cancel.is_set():
            with lock:
                if claimed>=max_tasks:return
                task=store.claim()
                if task is None:return
                claimed+=1
            try:
                result=execute_task(store,task,cancel);store.finish(task,result)
            except Exception as e:
                try:store.fail(task,type(e).__name__+(':'+str(e) if isinstance(e,Blocked) else ''))
                except Blocked:pass
                with lock:errors.append({'task':task['id'],'type':type(e).__name__})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(work) for _ in range(workers)]
        for f in futures:f.result()
    result=store.status();result['this_run']={'claims':claimed,'errors':errors,'interrupted':cancel.is_set(),'parser_slots':workers}
    atomic_json(store.job/'last_run.json',result)
    return result


def export_review(store,out):
    status=store.status();out=no_links(out).resolve()
    for p in (store.job,ROOT,Path(store.spec['source_root']),Path(store.spec['runtime']['hound']),Path(store.spec['runtime']['addon'])):
        if out.is_relative_to(p) or p.is_relative_to(out):raise Blocked('SEPARATE_REVIEW_EXPORT_REQUIRED')
    if out.exists():raise Blocked('NEW_EXPORT_REQUIRED')
    out.mkdir(parents=True,mode=0o700)
    write_new(out/'summary.json',canonical(status))
    with store.transaction() as c:
        for candidate in c.execute('SELECT * FROM candidates ORDER BY seq').fetchall():
            d=out/candidate['id'];d.mkdir()
            write_new(d/'profile.json',candidate['profile']);write_new(d/'proposal.json',candidate['proposal'])
            if candidate['verdict']:write_new(d/'evaluation.json',candidate['verdict'])
            rows=c.execute('SELECT * FROM tasks WHERE candidate=? AND status="DONE" ORDER BY kind,input_id',(candidate['id'],)).fetchall()
            receipts=[]
            with (d/'findings.ndjson.gz').open('xb') as raw:
                with gzip.GzipFile(fileobj=raw,mode='wb',mtime=0,filename='') as cart:
                    for r in rows:
                        result=strict_json(r['result'])
                        receipts.append({'kind':r['kind'],'input_id':r['input_id'],'result_sha256':r['result_sha256']})
                        if r['kind']=='source':
                            for f in result['findings']:cart.write(canonical(f)+b'\n')
            write_new(d/'task_receipts.json',canonical(receipts))
        events=[{'seq':r['seq'],'at':r['at'],'kind':r['kind'],'payload':strict_json(r['payload']),'previous':r['previous'],'sha256':r['sha256']}
                for r in c.execute('SELECT * FROM events ORDER BY seq')]
        write_new(out/'events.json',canonical(events))
    # Export the frozen tests separately for reproducibility, not as worker instructions.
    write_new(out/'acceptance_contract.json',canonical(store.spec['contract']))
    write_new(out/'mission.json',canonical(store.spec['mission']))
    write_new(out/'source_manifest.json',canonical(store.spec['sources']))
    return status


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('init');a.add_argument('--job',type=Path,required=True);a.add_argument('--runtime',type=Path,required=True);a.add_argument('--source-root',type=Path,required=True)
    for k in ('mission','contract','sources','profile'):a.add_argument('--'+k,type=Path,required=True)
    for name in ('run','status','request','submit','export'):
        a=sub.add_parser(name);a.add_argument('--job',type=Path,required=True)
        if name=='run':a.add_argument('--max-tasks',type=int,default=100);a.add_argument('--workers',type=int,default=2)
        if name in ('request','export'):a.add_argument('--out',type=Path,required=True)
        if name=='submit':a.add_argument('--proposal',type=Path,required=True)
    a=p.parse_args()
    try:
        if a.command=='init':
            obj=lambda path:strict_json(read(path))
            result=initialize(a.job,obj(a.runtime),a.source_root,obj(a.mission),obj(a.contract),obj(a.sources),obj(a.profile)).status()
        else:
            store=Store(a.job)
            if a.command=='run':
                cancel=threading.Event()
                signal.signal(signal.SIGTERM,lambda *_:cancel.set());signal.signal(signal.SIGINT,lambda *_:cancel.set())
                result=run_jobs(store,a.max_tasks,a.workers,cancel)
            elif a.command=='status':result=store.status()
            elif a.command=='request':result=store.packet();write_new(a.out,canonical(result))
            elif a.command=='submit':result=store.submit(strict_json(read(a.proposal,100_000)))
            else:result=export_review(store,a.out)
        print(json.dumps(result,sort_keys=True))
        return 2 if result.get('status') in {'WORK_REMAINS','BLOCKED_TASK_FAILURE','BUDGET_EXHAUSTED'} else 0
    except (ValueError,OSError,KeyError,TypeError,sqlite3.Error,StopIteration) as e:
        print(json.dumps({'status':'BLOCKED','reason':str(e) if isinstance(e,Blocked) else type(e).__name__,'release_approved':False}))
        return 2
if __name__=='__main__':raise SystemExit(main())
