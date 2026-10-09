"""Additive Atlas hunt. Read-only catalog + approved, hash-matching sources.

Writes a NEW result directory only. Does not migrate the Atlas, fetch remotely,
execute target code, or assign an AI model. Run on static approved snapshots.
"""
from __future__ import annotations
import argparse
import ast
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path,PurePosixPath
import sqlite3
import stat
import subprocess
import sys
import zipfile
import hound
import script_scope

MAX_SOURCE=256_000
MAX_ARCHIVE=10_000_000
MAX_CATALOG=64_000_000


def strict_json(data):
    def pairs(rows):
        d={}
        for k,v in rows:
            if k in d:raise ValueError('DUPLICATE_JSON_KEY')
            d[k]=v
        return d
    def finite_float(text):
        value=float(text)
        if not math.isfinite(value):raise ValueError('NONFINITE_JSON')
        return value
    return json.loads(data,object_pairs_hook=pairs,parse_float=finite_float,
                      parse_constant=lambda _:(_ for _ in ()).throw(ValueError('NONFINITE_JSON')))


def bounded_read(path:Path,limit:int)->bytes:
    if path.is_symlink() or not path.is_file():raise ValueError('REGULAR_FILE_REQUIRED')
    st=path.stat()
    if st.st_size>limit:raise ValueError('BYTE_LIMIT')
    with path.open('rb') as stream:data=stream.read(limit+1)
    after=path.stat()
    if len(data)>limit or (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
        raise ValueError('SOURCE_CHANGED_OR_OVERSIZED')
    return data


def source_path(root:Path,rel:str)->Path:
    if not isinstance(rel,str) or not rel or '\\' in rel or ':' in rel or any(ord(c)<32 for c in rel):raise ValueError('INVALID_RELATIVE_PATH')
    parts=rel.split('/')
    if rel.startswith('/') or any(x in {'','..','.'} for x in parts):raise ValueError('PATH_ESCAPE')
    current=root
    for part in parts:
        current=current/part
        if current.is_symlink():raise ValueError('SYMLINK_REFUSED')
    if not current.resolve().is_relative_to(root.resolve()):raise ValueError('PATH_ESCAPE')
    return current


def read_locator(root:Path,loc:dict,source:dict)->bytes:
    if not isinstance(loc,dict) or set(loc)-{'path','member','archive_sha256'}:raise ValueError('INVALID_LOCATOR')
    path=source_path(root,loc['path'])
    if 'member' not in loc:
        data=bounded_read(path,MAX_SOURCE)
    else:
        # Archive member is read in memory, never extracted. Entire archive hash is checked.
        raw=bounded_read(path,MAX_ARCHIVE)
        expected=source.get('modified_time','')
        if not expected.startswith('archive-sha256:') or hound.sha(raw)!=expected.split(':',1)[1]:raise ValueError('ARCHIVE_VERSION_MISMATCH')
        if 'archive_sha256' in loc and loc['archive_sha256']!=hound.sha(raw):raise ValueError('ARCHIVE_VERSION_MISMATCH')
        name=loc['member'];source_path(Path('/approved-placeholder'),name)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos=z.infolist()
            if len(infos)>3000 or sum(i.file_size for i in infos)>64_000_000:raise ValueError('ARCHIVE_LIMIT')
            names=[i.filename for i in infos]
            if len(set(names))!=len(names):raise ValueError('DUPLICATE_ARCHIVE_ENTRY')
            i=z.getinfo(name)
            if i.flag_bits&1 or stat.S_ISLNK(i.external_attr>>16) or i.file_size>MAX_SOURCE:raise ValueError('UNSAFE_MEMBER')
            with z.open(i) as stream:data=stream.read(MAX_SOURCE+1)
            if len(data)>MAX_SOURCE or len(data)!=i.file_size:raise ValueError('MEMBER_SIZE')
    if hound.sha(data)!=source['sha256']:raise ValueError('SOURCE_VERSION_MISMATCH')
    return data


def inspect_isolated(data:bytes,source_id:str,timeout:float=6)->dict:
    # Isolated Python process + deadline. This is NOT a hardened hostile-code sandbox.
    request={'source_id':source_id,'source_hex':data.hex()}
    proc=subprocess.run([sys.executable,'-I','-c',
        "import sys;sys.path.insert(0,sys.argv.pop(1));import hound;raise SystemExit(hound.main())",
        str(Path(__file__).resolve().parent),'--worker'],
        input=hound.canonical(request),capture_output=True,timeout=timeout,check=False,
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    if proc.returncode!=0:raise ValueError('PARSER_WORKER_FAILED')
    if len(proc.stdout)>8_000_000:raise ValueError('RESULT_SIZE_LIMIT')
    r=strict_json(proc.stdout)
    if r.get('source_id')!=source_id or r.get('source_sha256')!=hound.sha(data):raise ValueError('RESULT_BINDING')
    return r


def load_catalog(path:Path)->tuple[dict,list,str]:
    digest=hound.sha(bounded_read(path,MAX_CATALOG))
    # This command is for an exported static Atlas. Never ignore live WAL data.
    if any(Path(str(path)+suffix).exists() for suffix in ('-wal','-shm','-journal')):raise ValueError('EXPORT_STATIC_ATLAS_FIRST')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as c:
        c.execute('PRAGMA query_only=ON');c.execute('PRAGMA trusted_schema=OFF')
        rows=c.execute('SELECT id,current_observation,active,payload FROM sources').fetchall()
        sources={}
        for sid,obs,active,payload in rows:
            s=strict_json(payload)
            if s.get('source_id')!=sid or s.get('observation_id')!=obs:raise ValueError('SOURCE_BINDING')
            s['_active']=bool(active);sources[sid]=s
        cards=[]
        for cid,sid,oid,payload in c.execute('SELECT id,source_id,observation_id,payload FROM cards'):
            r=strict_json(payload)
            if r.get('card_id')!=cid or r.get('source_id')!=sid or r.get('observation_id')!=oid:raise ValueError('CARD_BINDING')
            cards.append(r)
    return sources,cards,digest


def resolve_atlas_card(data:bytes, finding:dict, eligible:list[dict])->dict:
    """Bind exact source bytes and definition identity, permitting decorator-inclusive cards.

    The scanner starts at ``def``; the Atlas may start at the earliest decorator.
    No fuzzy range or name-only match is accepted. Legacy cards without a name
    are supported only for an exact definition range. No original code executes.
    """
    if hound.sha(data) != finding.get('source_sha256'):
        raise ValueError('SOURCE_VERSION_MISMATCH')
    relevant = [c for c in eligible if c.get('source_id') == finding.get('source_id')]
    if not isinstance(finding.get('function_lines'), list) or len(finding['function_lines']) != 2:
        raise ValueError('UNRESOLVED_ATLAS_CARD')
    name = finding.get('qualified_name')
    if not isinstance(name, str) or not name:
        raise ValueError('UNRESOLVED_ATLAS_CARD')
    exact = [c for c in relevant
             if [c.get('source_range', {}).get('start'), c.get('source_range', {}).get('end')]
                == finding['function_lines']
             and (c.get('qualified_name') is None or c['qualified_name'] == name)]
    if len(exact) == 1:
        return exact[0]
    if exact:
        raise ValueError('UNRESOLVED_ATLAS_CARD')
    if len(data) > MAX_SOURCE:
        raise ValueError('BYTE_LIMIT')
    tree = ast.parse(data, filename='<source-range-verification>')
    if sum(1 for _ in ast.walk(tree)) > hound.base.MAX_NODES:
        raise ValueError('AST_NODE_LIMIT')
    definitions = [node for symbol, node in hound.base.functions(tree)
                   if symbol == name and [node.lineno, node.end_lineno] == finding['function_lines']]
    if len(definitions) != 1 or not definitions[0].decorator_list:
        raise ValueError('UNRESOLVED_ATLAS_CARD')
    node = definitions[0]
    start = min(d.lineno for d in node.decorator_list)
    matches = [c for c in relevant if c.get('qualified_name') == name
               and c.get('source_range') == {'start': start, 'end': node.end_lineno}]
    if len(matches) != 1:
        raise ValueError('UNRESOLVED_ATLAS_CARD')
    return matches[0]


def hunt(catalog:Path,root:Path,locators:dict,out:Path,*,profiles:list[str]|None=None,max_sources:int|None=None)->dict:
    if out.exists():raise ValueError('NEW_OUTPUT_DIRECTORY_REQUIRED')
    selected=set(profiles or hound.PROFILE_IDS)
    if not selected or selected-set(hound.PROFILE_IDS):raise ValueError('UNKNOWN_PROFILE')
    if max_sources is not None and (type(max_sources) is not int or max_sources<1):raise ValueError('INVALID_SOURCE_BUDGET')
    root=root.resolve()
    if out.resolve().is_relative_to(root):raise ValueError('OUTPUT_MUST_BE_OUTSIDE_SOURCE_ROOT')
    sources,cards,cat_hash=load_catalog(catalog)
    if not isinstance(locators,dict) or set(locators)-set(sources):raise ValueError('LOCATOR_UNKNOWN_SOURCE')
    selected_sources=set(sorted(locators)[:max_sources] if max_sources is not None else locators)
    bysource={}
    for card in cards:bysource.setdefault(card['source_id'],[]).append(card)
    findings=[];coverage=[];functions=0
    def inspect_one(item):
        sid,s=item
        mapped=[];function_count=0
        record={'source_id':sid,'observation_id':s['observation_id'],'display_path':s.get('display_path'),
            'source_sha256':s['sha256'],'original_body_retained':False}
        if not s['_active']:record['status']='INACTIVE_SOURCE';return record,[],0
        if sid not in locators:record['status']='SOURCE_NOT_SUPPLIED';return record,[],0
        if sid not in selected_sources:record['status']='DEFERRED_BY_SOURCE_BUDGET';return record,[],0
        try:
            data=read_locator(root,locators[sid],s);r=inspect_isolated(data,sid)
            # Map by exact definition range AND current observation; avoids inventing replacement IDs.
            eligible=[c for c in bysource.get(sid,[]) if c['observation_id']==s['observation_id']]
            mapped=[]
            for f in r['findings']:
                if f['profile_id'] not in selected:continue
                card=resolve_atlas_card(data,f,eligible)
                f.update(card_id=card['card_id'],function_id=card['function_id'],
                    observation_id=s['observation_id'],project_id=s['project_id'],
                    source_location=s.get('url'),display_path=s.get('display_path'),
                    is_test=('/tests/' in '/'+s.get('relative_path','') or PurePosixPath(s.get('relative_path','')).name.startswith('test_')))
                f['finding_id']='finding_'+hound.sha(hound.canonical(f));mapped.append(f)
            record['_script_records']=[f for f in script_scope.bind(r.get('script_findings',[]),s,data,hound.PROFILE_IDS) if f['profile_id'] in selected]
            # Re-read only this authorized locator to detect changes during parser execution.
            read_locator(root,locators[sid],s)
            function_count=r['functions_inspected'];record.update(status='STATIC_INSPECTED',findings=len(mapped))
        except (ValueError,OSError,SyntaxError,KeyError,zipfile.BadZipFile,subprocess.TimeoutExpired) as exc:
            record.pop('_script_records',None)
            mapped=[];function_count=0
            record.update(status='NOT_INSPECTED',reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
        return record,mapped,function_count
    script_findings=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for record,mapped,count in pool.map(inspect_one,sorted(sources.items())):
            script_findings.extend(record.pop('_script_records',[]))
            coverage.append(record);findings.extend(mapped);functions+=count
    if hound.sha(bounded_read(catalog,MAX_CATALOG))!=cat_hash:raise ValueError('CATALOG_CHANGED')
    stats=dict(Counter(x['status'] for x in coverage));complete=bool(coverage) and all(x['status']=='STATIC_INSPECTED' for x in coverage)
    report={'schema_version':1,'script_findings':script_findings,'script_finding_count':len(script_findings),'engine':hound.VERSION,'engine_sha256':hound.engine_digest(),
        'adapter_revision':'0.2.1-decorator-binding',
        'adapter_sha256':hound.sha(Path(__file__).read_bytes()),'catalog_sha256':cat_hash,'atlas_source_count':len(sources),'atlas_card_count':len(cards),'selected_profiles':sorted(selected),
        'sources':stats,'functions_inspected':functions,'finding_count':len(findings),
        'findings_by_profile':dict(Counter(x['profile_id'] for x in findings)),
        'coverage':'COMPLETE_FOR_SUPPLIED_ATLAS' if complete else 'PARTIAL_FOR_SUPPLIED_ATLAS',
        'full_drive_coverage':False,'runtime_functions_executed':0,'independent_models_called':0,
        'original_code_retained':False,'catalog_unchanged':True,'source_coverage':coverage,'findings':findings,
        'limitations':['Static local value flow, not full semantics or data-flow proof.',
            'Historical observations, not current deployed source. No correctness, licensing or novelty certification.',
            'No memory-isolated sandbox; use reviewed static source snapshots only.']}
    out.mkdir(parents=True)
    (out/'hunt.json').write_bytes(json.dumps(report,indent=2,allow_nan=False).encode())
    (out/'findings.ndjson.gz').write_bytes(gzip.compress(b''.join(hound.canonical(f)+b'\n' for f in findings),mtime=0))
    (out/'script_findings.ndjson.gz').write_bytes(gzip.compress(b''.join(hound.canonical(f)+b'\n' for f in script_findings),mtime=0))
    (out/'analyst_jobs.json').write_bytes(json.dumps([{
        'job_id':'review_'+f['finding_id'],'role':'specialist_analyst','state':'READY_NOT_RUN',
        'binding':{k:f[k] for k in ['finding_id','card_id','observation_id','source_sha256']},
        'required_output':{'existing_behavior':'source evidence required','useful_difference':'relative to named baseline',
            'missing_context':'explicit list','validation_plan':'immutable acceptance tests','decision':'review, defer or reject'},
        'permissions':['read approved evidence','propose new work; no source writes or execution']
    } for f in findings],indent=2).encode())
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog',type=Path,required=True);p.add_argument('--source-root',type=Path,required=True)
    p.add_argument('--locators',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--profile',action='append',choices=hound.PROFILE_IDS)
    p.add_argument('--max-sources',type=int,default=None)
    a=p.parse_args()
    try:
        r=hunt(a.catalog,a.source_root,strict_json(bounded_read(a.locators,2_000_000)),a.out,profiles=a.profile,max_sources=a.max_sources)
        print(json.dumps({k:r[k] for k in ['coverage','sources','functions_inspected','finding_count','findings_by_profile']}))
        return 0 if r['coverage']=='COMPLETE_FOR_SUPPLIED_ATLAS' else 2
    except (ValueError,OSError,sqlite3.Error) as exc:
        print(json.dumps({'status':'BLOCKED','reason':str(exc)}));return 2

if __name__=='__main__':raise SystemExit(main())
