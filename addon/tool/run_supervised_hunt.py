"""Execute three bounded parser roles on supplied exact sources, not Internet agents."""
from pathlib import Path, PurePosixPath
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import gzip
import json
import os
import threading
import time
import mission_probe as probe


def validate_manifest(obj):
    if not isinstance(obj, dict) or obj.get('schema') != 1 or not isinstance(obj.get('sources'), list) or not 1 <= len(obj['sources']) <= 1000:
        raise ValueError('MANIFEST_SCHEMA')
    seen=set()
    for r in obj['sources']:
        if not isinstance(r,dict) or not isinstance(r.get('path'),str) or not isinstance(r.get('source_id'),str):
            raise ValueError('SOURCE_ROW')
        path=PurePosixPath(r['path'])
        if path.is_absolute() or '..' in path.parts or '\\' in r['path'] or not path.parts or path.suffix!='.py':
            raise ValueError('SOURCE_PATH')
        if r['source_id'] in seen:
            raise ValueError('DUPLICATE_SOURCE')
        seen.add(r['source_id'])
        if not isinstance(r.get('sha256'),str) or not probe.re.fullmatch('[0-9a-f]{64}',r['sha256']):
            raise ValueError('SOURCE_DIGEST')
    return obj


def run(hound,source_root,manifest,profiles,out):
    hound=Path(hound).resolve();source_root=Path(source_root).resolve();out=Path(out)
    manifest=validate_manifest(manifest)
    for p in profiles:probe.validate_profile(p)
    if not 1<=len(profiles)<=3 or len({p['id'] for p in profiles})!=len(profiles):
        raise ValueError('WORKER_BUDGET_OR_DUPLICATE_ROLE')
    if out.exists() or out.is_symlink() or out.resolve().is_relative_to(source_root) or out.resolve().is_relative_to(hound):
        raise ValueError('NEW_SEPARATE_OUTPUT_REQUIRED')
    probe.load_hound(hound)
    sources=[]
    for row in manifest['sources']:
        path=source_root/row['path']
        # Reject symlink components before following a source path.
        current=source_root
        for component in PurePosixPath(row['path']).parts:
            current=current/component
            if current.is_symlink():raise ValueError('SOURCE_LINK')
        if not path.resolve().is_relative_to(source_root):raise ValueError('SOURCE_ESCAPE')
        data=probe.read_regular(path,probe.MAX_SOURCE)
        if probe.sha(data)!=row['sha256']:raise ValueError('SOURCE_VERSION_MISMATCH')
        sources.append((row,data,path.stat().st_mtime_ns))
    out.mkdir(parents=True)
    for p in profiles:(out/p['id']).mkdir()
    lock=threading.Lock();running=0;max_running=0;events=[]
    def role(profile):
        nonlocal running,max_running
        role_rows=[]
        for index,(row,data,mtime) in enumerate(sources):
            started=time.monotonic()
            with lock:
                running+=1;max_running=max(max_running,running)
            result=None;status='STATIC_INSPECTED';error=None
            try:
                result=probe.isolated_scan(data,row['source_id'],profile,hound)
            except Exception as exc:
                status='BLOCKED';error=type(exc).__name__
            finally:
                with lock:running-=1
            record={'role':profile['id'],'source_id':row['source_id'],'path':row['path'],'source_sha256':row['sha256'],
                    'status':status,'elapsed_seconds':round(time.monotonic()-started,6),'error_type':error}
            if result is not None:
                record['result']=result
            (out/profile['id']/f'{index:04d}.json').write_bytes(probe.canonical(record))
            role_rows.append(record)
        return role_rows
    started=datetime.now(timezone.utc).isoformat()
    with ThreadPoolExecutor(max_workers=len(profiles)) as pool:
        runs=list(pool.map(role,profiles))
    for row,data,mtime in sources:
        p=source_root/row['path']
        if probe.sha(probe.read_regular(p,probe.MAX_SOURCE))!=row['sha256'] or p.stat().st_mtime_ns!=mtime:
            raise ValueError('SOURCE_CHANGED')
    flat=[r for role_rows in runs for r in role_rows]
    findings=[f for r in flat for f in r.get('result',{}).get('findings',[])]
    summary={'schema':1,'started_utc':started,'finished_utc':datetime.now(timezone.utc).isoformat(),
             'status':'COMPLETE_SUPPLIED_SCOPE' if all(r['status']=='STATIC_INSPECTED' for r in flat) else 'PARTIAL_BLOCKED',
             'source_files':len(sources),'role_scans':len(flat),'roles':[p['id'] for p in profiles],
             'max_concurrent_parser_slots':max_running,'observations':len(findings),
             'unique_functions':len({(f['source_id'],f.get('qualified_name')) for f in findings if f['scope_kind']=='function'}),
             'source_preserved':True,'upstream_code_executed':False,'model_calls':0,'review_mode':'one human reviewer; ordinary parser workers',
             'release_approved':False,'source_results':flat}
    (out/'summary.json').write_bytes(probe.canonical(summary))
    with (out/'findings.ndjson.gz').open('xb') as raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as z:
            for f in sorted(findings,key=lambda f:f['finding_id']):z.write(probe.canonical(f)+b'\n')
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--hound',type=Path,required=True);p.add_argument('--source-root',type=Path,required=True)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--profiles',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    try:
        manifest=probe.strict_json(probe.read_regular(a.manifest,2_000_000))
        profiles=[probe.strict_json(probe.read_regular(x,100_000)) for x in sorted(a.profiles.glob('*.json'))]
        s=run(a.hound,a.source_root,manifest,profiles,a.out)
        print(json.dumps({k:s[k] for k in ['status','source_files','role_scans','observations','unique_functions','max_concurrent_parser_slots']}))
        return 0 if s['status']=='COMPLETE_SUPPLIED_SCOPE' else 2
    except (ValueError,OSError,KeyError,TypeError):
        print('{"status":"BLOCKED","release_approved":false}')
        return 2
if __name__=='__main__':raise SystemExit(main())
