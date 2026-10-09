"""Resumable read-only Atlas hunt with version-bound per-source checkpoints.

Only the job directory is written. A checkpoint is a consistency record on a
trusted host, NOT an authenticated attestation. No original source bodies are
stored. Exact bytes are reacquired and verified even when a cache hit exists.
"""
from __future__ import annotations
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
import argparse
import gzip
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import time
import threading
import uuid
import zipfile
import hound
import atlas_hunt as atlas
import script_scope

VERSION='0.5'
CACHE_SCHEMA=1
MAX_CACHE_BYTES=8_000_000


def fingerprint():
    names=['script_scope.py','library_rules.py','hound.py','flow_core.py','baseline_sniffers.py','profiles/baseline.json','atlas_hunt.py','persistent_hunt.py']
    return hound.sha(hound.canonical({name:hound.sha((Path(__file__).parent/name).read_bytes()) for name in names}))


def atomic_bytes(path:Path,raw:bytes):
    if path.is_symlink():raise ValueError('OUTPUT_SYMLINK_REFUSED')
    pending=path.with_name(path.name+'.'+uuid.uuid4().hex+'.pending')
    try:
        fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(pending,path)
        if os.name=='posix':
            fd=os.open(path.parent,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
    finally:
        if pending.exists():pending.unlink()


def atomic_json(path:Path,value):
    atomic_bytes(path,hound.canonical(value))


@contextmanager
def exclusive_job(root:Path):
    if root.is_symlink():raise ValueError('JOB_DIRECTORY_SYMLINK_REFUSED')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=root/'.job.lock'
    if path.is_symlink():raise ValueError('JOB_LOCK_SYMLINK_REFUSED')
    fd=os.open(path,os.O_RDWR|os.O_CREAT,0o600)
    with os.fdopen(fd,'r+b') as stream:
        if os.name=='posix':
            import fcntl
            try:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('JOB_BUSY') from None
            try:yield
            finally:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
        else:
            # Windows path specified and guarded, not natively qualified here.
            import msvcrt
            stream.write(b'0');stream.flush();stream.seek(0)
            try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise ValueError('JOB_BUSY') from None
            try:yield
            finally:
                stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)


class SnapshotReader:
    """Memoize each verified source container for one bounded invocation only."""
    def __init__(self,root):self.root=root;self.containers={};self.read_bytes=0
    def raw(self,rel,limit):
        if rel not in self.containers:
            raw=atlas.bounded_read(atlas.source_path(self.root,rel),limit)
            self.containers[rel]=(raw,hound.sha(raw),limit);self.read_bytes+=len(raw)
        raw,digest,_=self.containers[rel]
        if len(raw)>limit:raise ValueError('BYTE_LIMIT')
        return raw,digest
    def read(self,loc,source):
        if not isinstance(loc,dict) or set(loc)-{'path','member','archive_sha256'}:raise ValueError('INVALID_LOCATOR')
        raw,digest=self.raw(loc['path'],atlas.MAX_ARCHIVE if 'member' in loc else atlas.MAX_SOURCE)
        if 'member' in loc:
            if source.get('modified_time')!='archive-sha256:'+digest:raise ValueError('ARCHIVE_VERSION_MISMATCH')
            if loc.get('archive_sha256',digest)!=digest:raise ValueError('ARCHIVE_VERSION_MISMATCH')
            member=loc['member'];atlas.source_path(Path('/approved-placeholder'),member)
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                infos=z.infolist()
                if len(infos)>3000 or sum(x.file_size for x in infos)>64_000_000:raise ValueError('ARCHIVE_LIMIT')
                if len({x.filename for x in infos})!=len(infos):raise ValueError('DUPLICATE_ARCHIVE_ENTRY')
                info=z.getinfo(member)
                if info.flag_bits&1 or stat.S_ISLNK(info.external_attr>>16) or info.file_size>atlas.MAX_SOURCE:raise ValueError('UNSAFE_MEMBER')
                with z.open(info) as stream:raw=stream.read(atlas.MAX_SOURCE+1)
                if len(raw)>atlas.MAX_SOURCE or len(raw)!=info.file_size:raise ValueError('MEMBER_SIZE')
        if hound.sha(raw)!=source['sha256']:raise ValueError('SOURCE_VERSION_MISMATCH')
        return raw
    def verify_unchanged(self):
        for rel,(_,digest,limit) in self.containers.items():
            if hound.sha(atlas.bounded_read(atlas.source_path(self.root,rel),limit))!=digest:
                raise ValueError('SOURCE_CONTAINER_CHANGED')


def inspect_bounded(data,sid,timeout=10):
    # Resource limits supplement parsing constraints. They are NOT a network/filesystem sandbox.
    bootstrap='''import sys
if sys.platform.startswith("linux"):
 import resource
 resource.setrlimit(resource.RLIMIT_AS,(768*1024*1024,768*1024*1024))
 resource.setrlimit(resource.RLIMIT_CPU,(8,8))
 resource.setrlimit(resource.RLIMIT_NOFILE,(64,64))
sys.path.insert(0,sys.argv.pop(1))
import hound
raise SystemExit(hound.main())
'''
    proc=subprocess.run([sys.executable,'-I','-B','-c',bootstrap,str(Path(__file__).parent),'--worker'],
        input=hound.canonical({'source_id':sid,'source_hex':data.hex()}),capture_output=True,timeout=timeout,
        env={'PATH':os.defpath,'LANG':'C.UTF-8'},check=False)
    if proc.returncode:raise ValueError('PARSER_FAILED_OR_RESOURCE_LIMIT')
    if len(proc.stdout)>MAX_CACHE_BYTES:raise ValueError('RESULT_SIZE_LIMIT')
    r=atlas.strict_json(proc.stdout)
    validate_result(r,sid,hound.sha(data))
    return r


def validate_result(r,sid,digest):
    if not isinstance(r,dict) or r.get('source_id')!=sid or r.get('source_sha256')!=digest:raise ValueError('RESULT_BINDING')
    if type(r.get('functions_inspected')) is not int or r['functions_inspected']<0:raise ValueError('RESULT_FUNCTION_COUNT')
    if r.get('source_body_retained') is not False or not isinstance(r.get('findings'),list):raise ValueError('RESULT_SCHEMA')
    script_scope.validate(r.get('script_findings',[]),sid,digest,hound.PROFILE_IDS)
    seen=set()
    for f in r['findings']:
        if not isinstance(f,dict):raise ValueError('FINDING_SCHEMA')
        if f.get('finding_id') in seen:raise ValueError('DUPLICATE_FINDING')
        seen.add(f.get('finding_id'))
        if f.get('source_id')!=sid or f.get('source_sha256')!=digest or f.get('runtime_verified') is not False:raise ValueError('FINDING_BINDING')
        if f.get('profile_id') not in hound.PROFILE_IDS:raise ValueError('FINDING_PROFILE')
        core={k:v for k,v in f.items() if k!='finding_id'}
        if f.get('finding_id')!='finding_'+hound.sha(hound.canonical(core)):raise ValueError('FINDING_DIGEST')


def cache_key(source,engine):
    return hound.sha(hound.canonical({'schema':CACHE_SCHEMA,'source_id':source['source_id'],
        'observation_id':source['observation_id'],'sha256':source['sha256'],'engine':engine}))


def load_checkpoint(path,key,sid,digest):
    if not path.exists():return None,'MISS'
    try:
        box=atlas.strict_json(atlas.bounded_read(path,MAX_CACHE_BYTES))
        if box['schema']!=CACHE_SCHEMA or box['key']!=key or box['result_sha256']!=hound.sha(hound.canonical(box['result'])):
            raise ValueError('CACHE_DIGEST')
        validate_result(box['result'],sid,digest)
        return box['result'],'HIT'
    except (ValueError,KeyError,TypeError,AttributeError,OSError):return None,'CORRUPT_RECOMPUTE_REQUIRED'


def save_checkpoint(path,key,result):
    atomic_json(path,{'schema':CACHE_SCHEMA,'key':key,'result':result,'result_sha256':hound.sha(hound.canonical(result))})


def run(catalog:Path,root:Path,locators:dict,out:Path,*,profiles=None,max_new_sources=40,workers=4):
    if type(max_new_sources) is not int or max_new_sources<0:raise ValueError('INVALID_SOURCE_BUDGET')
    if type(workers) is not int or not 1<=workers<=8:raise ValueError('INVALID_WORKER_COUNT')
    root=root.resolve()
    if out.resolve().is_relative_to(root) or out.resolve()==catalog.resolve():raise ValueError('OUTPUT_MUST_BE_SEPARATE')
    profiles=sorted(set(profiles or hound.PROFILE_IDS))
    if set(profiles)-set(hound.PROFILE_IDS):raise ValueError('UNKNOWN_PROFILE')
    sources,cards,catalog_hash=atlas.load_catalog(catalog)
    if not isinstance(locators,dict) or set(locators)-set(sources):raise ValueError('LOCATOR_UNKNOWN_SOURCE')
    engine=fingerprint()
    binding={'version':VERSION,'catalog_sha256':catalog_hash,'locator_sha256':hound.sha(hound.canonical(locators)),
        'profiles':profiles,'engine_sha256':engine,'source_root':str(root)}
    start=time.perf_counter()
    with exclusive_job(out):
        manifest=out/'job.json'
        if manifest.exists():
            if atlas.strict_json(atlas.bounded_read(manifest,100_000))!=binding:raise ValueError('JOB_BINDING_CHANGED_USE_NEW_DIRECTORY')
        else:atomic_json(manifest,binding)
        cache=out/'cache'
        if cache.is_symlink():raise ValueError('CACHE_DIRECTORY_SYMLINK_REFUSED')
        cache.mkdir(exist_ok=True,mode=0o700)
        state_path=out/'attempts.json'
        attempts=atlas.strict_json(atlas.bounded_read(state_path,1_000_000)) if state_path.exists() else {}
        if not isinstance(attempts,dict) or set(attempts)-set(sources) or any(type(v) is not int or not 0<=v<=2 for v in attempts.values()):
            raise ValueError('INVALID_RETRY_LEDGER')
        # Reject a foreign output link before doing any work for the new invocation.
        if (out/'findings.ndjson.gz').is_symlink():raise ValueError('OUTPUT_SYMLINK_REFUSED')
        bysource={}
        for c in cards:
            if c['observation_id']==sources[c['source_id']]['observation_id']:bysource.setdefault(c['source_id'],[]).append(c)
        reader=SnapshotReader(root);records={};script_records={};findings=[];planned=[];counts=Counter();function_count=0
        def accept(sid,s,data,result,rec):
            mapped=[]
            for f in result['findings']:
                if f['profile_id'] not in profiles:continue
                card=atlas.resolve_atlas_card(data,f,bysource.get(sid,[]))
                m={**f,'card_id':card['card_id'],'function_id':card['function_id'],'observation_id':s['observation_id'],
                    'project_id':s['project_id'],'source_location':s.get('url'),'display_path':s.get('display_path'),
                    'is_test':'/tests/' in '/'+s.get('relative_path','') or Path(s.get('relative_path','')).name.startswith('test_')}
                m.pop('finding_id',None);m['finding_id']='finding_'+hound.sha(hound.canonical(m));mapped.append(m)
            all_scripts=script_scope.bind(result.get('script_findings',[]),s,data,hound.PROFILE_IDS)
            script_records[sid]=[f for f in all_scripts if f['profile_id'] in profiles]
            rec.update(status='STATIC_INSPECTED',findings=len(mapped),script_findings=len(script_records[sid]),functions_inspected=result['functions_inspected'])
            return mapped,result['functions_inspected']
        for sid,s in sorted(sources.items()):
            rec={'source_id':sid,'observation_id':s['observation_id'],'source_sha256':s['sha256'],
                'display_path':s.get('display_path'),'original_body_retained':False}
            records[sid]=rec
            if not s['_active']:rec['status']='INACTIVE_SOURCE';continue
            if sid not in locators:rec['status']='SOURCE_NOT_SUPPLIED';continue
            try:
                data=reader.read(locators[sid],s) # Always validate fresh bytes, including hits.
                key=cache_key(s,engine);path=cache/(key+'.json')
                result,cache_status=load_checkpoint(path,key,sid,s['sha256'])
                rec['checkpoint']=cache_status
                if result is not None:
                    mapped,n=accept(sid,s,data,result,rec);findings+=mapped;function_count+=n;counts['cache_hits']+=1
                elif attempts.get(sid,0)>=2:
                    rec.update(status='NOT_INSPECTED',reason='RETRY_BUDGET_EXHAUSTED')
                elif len(planned)>=max_new_sources:
                    rec['status']='DEFERRED_BY_SOURCE_BUDGET'
                else:
                    rec['status']='PENDING_CURRENT_BATCH'
                    planned.append((sid,s,data,key,path,rec))
            except (ValueError,OSError,SyntaxError,KeyError,zipfile.BadZipFile) as exc:
                rec.update(status='NOT_INSPECTED',reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
        atomic_json(state_path,attempts)
        attempts_lock=threading.Lock()
        def execute_started(item):
            # Charge only a task that actually enters a worker, not queued futures.
            sid=item[0]
            with attempts_lock:
                attempts[sid]=attempts.get(sid,0)+1
                atomic_json(state_path,attempts)
            return inspect_bounded(item[2],sid)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures={executor.submit(execute_started,t):t for t in planned}
            for future in as_completed(futures):
                sid,s,data,key,path,rec=futures[future];counts['parser_jobs_started']+=1
                try:
                    result=future.result();mapped,n=accept(sid,s,data,result,rec)
                    save_checkpoint(path,key,result) # Persist each successful source before global reporting.
                    findings+=mapped;function_count+=n;counts['checkpoints_written']+=1
                except (ValueError,OSError,SyntaxError,KeyError,subprocess.TimeoutExpired) as exc:
                    rec.update(status='NOT_INSPECTED',reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
        reader.verify_unchanged()
        if hound.sha(atlas.bounded_read(catalog,atlas.MAX_CATALOG))!=catalog_hash:raise ValueError('CATALOG_CHANGED')
        if fingerprint()!=engine:raise ValueError('ENGINE_CHANGED_DURING_RUN')
        coverage=list(records.values());findings.sort(key=lambda f:(f['source_id'],f['qualified_name'],f['profile_id'],f['finding_id']))
        statuses=dict(Counter(x['status'] for x in coverage));complete=bool(coverage) and all(x['status']=='STATIC_INSPECTED' for x in coverage)
        scripts=sorted([f for sid in sorted(script_records) for f in script_records[sid]],key=lambda f:f['finding_id'])
        report={'schema_version':3,'script_findings':scripts,'script_finding_count':len(scripts),'engine':hound.VERSION,'engine_sha256':engine,'catalog_sha256':catalog_hash,
            'coverage':'COMPLETE_FOR_SUPPLIED_ATLAS' if complete else 'PARTIAL_FOR_SUPPLIED_ATLAS','full_drive_coverage':False,
            'atlas_source_count':len(sources),'atlas_card_count':len(cards),'selected_profiles':profiles,'sources':statuses,
            'functions_inspected':function_count,'finding_count':len(findings),'source_coverage':coverage,'findings':findings,
            'findings_by_profile':dict(Counter(f['profile_id'] for f in findings)),
            'runtime_functions_executed':0,'independent_models_called':0,'original_code_retained':False,'catalog_unchanged':True,
            'invocation':{'completed_utc':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':round(time.perf_counter()-start,6),
                'container_bytes_read':reader.read_bytes,**dict(counts)},
            'parser_limits':{'wall_seconds':10,'linux_address_space_bytes':768*1024*1024,'linux_cpu_seconds':8,
                'enforced_on_this_platform':sys.platform.startswith('linux'),'hostile_input_sandbox':False},
            'limitations':['Single trusted host; cache hashes do not authenticate execution.','Historical source observations, not a fresh Drive scan.',
                'Local static subset only; unknown methods and unsupported flow can be missed.','No original source execution, market, rights or accuracy validation.']}
        report['result_sha256']=hound.sha(hound.canonical({'coverage':coverage,'findings':findings,'script_findings':scripts}))
        atomic_json(out/'hunt.json',report)
        # Append an immutable invocation receipt; later resumes never erase this history.
        atomic_json(out/('invocation-'+uuid.uuid4().hex+'.json'),report['invocation'])
        atomic_bytes(out/'findings.ndjson.gz',gzip.compress(b''.join(hound.canonical(f)+b'\n' for f in findings),mtime=0))
        atomic_bytes(out/'script_findings.ndjson.gz',gzip.compress(b''.join(hound.canonical(f)+b'\n' for f in scripts),mtime=0))
        return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog',required=True,type=Path);p.add_argument('--source-root',required=True,type=Path)
    p.add_argument('--locators',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    p.add_argument('--max-new-sources',type=int,default=40);p.add_argument('--workers',type=int,default=4)
    p.add_argument('--profile',action='append',choices=hound.PROFILE_IDS)
    a=p.parse_args()
    try:
        r=run(a.catalog,a.source_root,atlas.strict_json(atlas.bounded_read(a.locators,2_000_000)),a.out,
            profiles=a.profile,max_new_sources=a.max_new_sources,workers=a.workers)
        print(json.dumps({k:r[k] for k in ('coverage','sources','functions_inspected','finding_count','invocation')},indent=2))
        return 0 if r['coverage']=='COMPLETE_FOR_SUPPLIED_ATLAS' else 2
    except (ValueError,OSError,sqlite3.Error) as exc:
        print(json.dumps({'status':'BLOCKED','reason':str(exc)}));return 2

if __name__=='__main__':raise SystemExit(main())
