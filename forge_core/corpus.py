"""Mixed-code development corpus, metadata search, and persistent read-only runs.

The connector/operator supplies approved exact copies and original Drive IDs.
This module does not possess a Drive credential, make network requests, execute
source, alter existing Atlas observations, or turn old prose into test evidence.
"""
from __future__ import annotations
import collections,contextlib,gzip,html,os,re,sys,time
from pathlib import Path
from .common import *
from .engine import Analyzer,ROOT,run_fixed
from .mixed_intake import DEFAULTS,tokens
from .policy import validate_profile
from . import analysis_recovery

SCHEMA=1

def manifest(value):
    if not isinstance(value,dict) or type(value.get('schema')) is not int or value['schema']!=1 or not isinstance(value.get('files'),list):raise Blocked('CORPUS_MANIFEST')
    if not 1<=len(value['files'])<=500:raise Blocked('CORPUS_COUNT')
    ids=set();paths=set()
    for row in value['files']:
        if not isinstance(row,dict) or not {'file_id','path','size','sha256','source_url'}<=set(row):raise Blocked('CORPUS_SOURCE_FIELDS')
        text(row['file_id'],200);path_in_repo(row['path']);integer(row['size'],0,DEFAULTS['max_container_bytes'])
        if not isinstance(row['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',row['sha256']):raise Blocked('CORPUS_SOURCE_SHA')
        provider=row.get('provider','gdrive')
        if provider not in ('gdrive','synthetic','github'):raise Blocked('CORPUS_PROVIDER')
        expected=('fixture://'+row['file_id']) if provider=='synthetic' else ('https://drive.google.com/file/d/'+row['file_id']+'/view')
        if provider=='github':
            from .github_corpus import validate_source
            validate_source(row)
        elif row['source_url']!=expected:raise Blocked('CORPUS_SOURCE_URL')
        if row['file_id'] in ids or row['path'] in paths:raise Blocked('DUPLICATE_CORPUS_SOURCE')
        ids.add(row['file_id']);paths.add(row['path'])
    return value

@contextlib.contextmanager
def _lock(path):
    path=safe_path(path)
    if path.exists() and path.stat().st_nlink!=1:raise Blocked('SHARED_LOCK')
    with path.open('a+b') as f:
        if os.name=='posix':
            import fcntl
            try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise Blocked('CORPUS_RUNNING')
        elif os.name=='nt':
            import msvcrt
            f.seek(0);f.write(b'0');f.flush();f.seek(0)
            try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise Blocked('CORPUS_RUNNING')
        else:raise Blocked('PLATFORM_LOCK_UNQUALIFIED')
        try:yield
        finally:
            if os.name=='posix':fcntl.flock(f,fcntl.LOCK_UN)
            elif os.name=='nt':f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)

def _parser(data,name,home):
    # Raw bytes use the existing 8 MB pipe allowance without a 2x hex envelope.
    # Name is data in an explicit argv; no shell, path read or configurable limits.
    # Patch: the parser result carries each segment body as hex
    # (about 2x the source) plus structure, so >~100-file envelopes overran the
    # generic 8 MB tool-output cap although the worker itself allows 16 MB.
    from .bounded_io import MAX_PARSER_OUTPUT_BYTES
    return loads(run_fixed([sys.executable,'-I',str(ROOT/'forge_core/mixed_worker.py'),
                            '--raw-container',name],home,60,data,MAX_PARSER_OUTPUT_BYTES))

def inspect_one(data,source,home,profile,analyzer,segment_dir=None,job_binding=None):
    validation=None
    if source.get('provider')=='github':
        from .github_corpus import validate_payload
        validation=validate_payload(data,source)
    parsed=_parser(data,source['path'],home)
    if parsed.get('container_sha256')!=source['sha256'] or parsed.get('upstream_code_executed') is not False:raise Blocked('CORPUS_PARSE_BINDING')
    result={'source':source,'segments':[],'inventory':parsed['inventory'],'gaps':list(parsed['gaps']),
            'upstream_code_executed':False,'analysis_status':'PARSED','hound_errors':[]}
    if validation is not None:result['acquisition_validation']=validation
    def analyze(segment):
        segment=__import__('copy').deepcopy(segment)
        payload=bytes.fromhex(segment.pop('source_hex'))
        if sha(payload)!=segment['source_sha256']:raise Blocked('SEGMENT_HASH')
        sid='source_'+sha(canonical({'file_id':source['file_id'],'container_sha256':source['sha256'],'segment':segment['segment_local_id']}))
        segment['source_id']=sid;segment['definitions_count']=len(segment['definitions'])
        for d in segment['definitions']:
            d['definition_id']='definition_'+sha(canonical([sid,d['name'],d['start_line'],d['end_line']]))
            d['member_start_line']=segment['start_line']+d['start_line']-1
            d['member_end_line']=segment['start_line']+d['end_line']-1
        key={'job':job_binding,'source':source['sha256'],'source_id':sid,'profile':sha(canonical(profile))}
        cache=(segment_dir/(sid+'.json')) if segment_dir is not None else None
        journal=analysis_recovery.Journal(segment_dir.parent/'analysis_attempts'/sid,key) if segment_dir is not None else None
        if cache is not None and cache.exists():
            saved=_load_checkpoint(cache,key)
            if saved['segment']['source_id']!=sid or saved['segment']['source_sha256']!=sha(payload):raise Blocked('SEGMENT_CHECKPOINT_BINDING')
            if saved['segment'].get('hound_status')!='INSPECTED':raise Blocked('SEGMENT_CHECKPOINT_STATUS')
            if journal is not None:journal.cached_success(sha(canonical(saved)))
            return saved['segment']
        if journal is not None and not journal.begin():
            segment['hound']={};segment['hound_status']='BLOCKED'
            segment['hound_reason']=journal.history[-1]['reason']
            return segment
        try:
            observed=analyzer.scan(payload,sid,profile)
            if observed.get('source_sha256')!=sha(payload) or observed.get('source_id')!=sid or observed.get('upstream_code_executed') is not False:raise Blocked('HOUND_BINDING')
            segment['hound']=observed;segment['hound_status']='INSPECTED'
        except Exception as e:
            reason=str(e) if isinstance(e,Blocked) else type(e).__name__
            segment['hound']={};segment['hound_status']='BLOCKED';segment['hound_reason']=reason
            if journal is not None:journal.finish('FAILED',reason)
        if cache is not None and segment['hound_status']=='INSPECTED':
            saved={'segment':segment,'upstream_code_executed':False}
            write(cache,canonical({'binding':key,'result_sha256':sha(canonical(saved)),'result':saved}),new=True)
            if journal is not None:journal.finish('SUCCEEDED',result_sha256=sha(canonical(saved)))
        return segment
    # Two ordinary subprocess-backed parser slots; no independent AI inference.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        result['segments']=list(pool.map(analyze,parsed['segments']))
    result['hound_errors']=[{'source_id':s['source_id'],'reason':s['hound_reason']} for s in result['segments'] if s['hound_status']=='BLOCKED']
    if result['hound_errors']:result['analysis_status']='PARSED_WITH_HOUND_GAPS'
    return result

def _load_checkpoint(p,binding):
    obj=loads(read(p,20_000_000))
    if not isinstance(obj,dict) or set(obj)!={'binding','result_sha256','result'} or obj['binding']!=binding or sha(canonical(obj['result']))!=obj['result_sha256']:raise Blocked('CORPUS_CHECKPOINT_INVALID')
    if not isinstance(obj['result'],dict) or obj['result'].get('upstream_code_executed') is not False:raise Blocked('CORPUS_CHECKPOINT_AUTHORITY')
    return obj['result']

def run(workbench,source_root,manifest_file,out,profile=None,max_containers=20,analyzer=None):
    workbench.guard();source_root=safe_path(source_root);out=safe_path(out)
    if not source_root.is_dir():raise Blocked('SOURCE_ROOT')
    if out==ROOT or out.is_relative_to(ROOT) or ROOT.is_relative_to(out) or out.is_relative_to(source_root) or source_root.is_relative_to(out) or out.is_relative_to(workbench.home/'engine'):raise Blocked('CORPUS_SEPARATE_OUTPUT')
    m=manifest(loads(read(manifest_file,2_000_000)));profile=validate_profile(profile or loads(read(ROOT/'templates/corpus_profile.json')))
    integer(max_containers,0,500)
    from .workspace import code_binding
    binding={'schema':SCHEMA,'code':code_binding(),'manifest_sha256':sha(canonical(m)),'source_root':str(source_root),'profile_sha256':sha(canonical(profile))}
    if not out.exists():out.mkdir(parents=True,mode=0o700);write(out/'binding.json',canonical(binding),new=True)
    elif loads(read(out/'binding.json',2_000_000))!=binding:raise Blocked('CORPUS_BINDING_CHANGED_NEW_RUN_REQUIRED')
    analyzer=analyzer or Analyzer(workbench.home)
    results=[];unread=[];blocked=[];used=0;cached=0;start=time.monotonic()
    with _lock(out/'.running.lock'):
        for row in m['files']:
            local=safe_path(source_root/row['path'])
            if not local.is_relative_to(source_root):raise Blocked('SOURCE_ESCAPE')
            try:
                b=read(local,DEFAULTS['max_container_bytes'])
                if len(b)!=row['size'] or sha(b)!=row['sha256']:raise Blocked('SOURCE_VERSION_MISMATCH')
                checkpoint=out/'checkpoints'/('source-'+sha(canonical(row))+'.json')
                cb={'job':sha(canonical(binding)),'source':sha(canonical(row))}
                r=None;retry=False
                if checkpoint.exists():
                    r=_load_checkpoint(checkpoint,cb)
                    if r.get('source')!=row:raise Blocked('CORPUS_CHECKPOINT_SOURCE')
                    # Validate typed cache contents independently of checkpoint
                    # hashes. Rehashed missing failures are not successful analysis.
                    cached_segments=r.get('segments')
                    if not isinstance(cached_segments,list):raise Blocked('CORPUS_CHECKPOINT_SEGMENTS')
                    errors=[];seen=set()
                    for segment in cached_segments:
                        if not isinstance(segment,dict) or segment.get('hound_status') not in ('BLOCKED','INSPECTED'):raise Blocked('CORPUS_CHECKPOINT_SEGMENTS')
                        sid=segment.get('source_id')
                        if not isinstance(sid,str) or not re.fullmatch('source_[0-9a-f]{64}',sid) or sid in seen:raise Blocked('CORPUS_CHECKPOINT_SEGMENTS')
                        seen.add(sid)
                        if segment['hound_status']=='BLOCKED':
                            if not isinstance(segment.get('hound_reason'),str) or segment.get('hound')!={}:raise Blocked('CORPUS_CHECKPOINT_SEGMENTS')
                            errors.append({'source_id':sid,'reason':segment['hound_reason']})
                    if r.get('hound_errors')!=errors:raise Blocked('CORPUS_CHECKPOINT_GAP_MISMATCH')
                    for segment in cached_segments:
                        sid=segment['source_id']
                        key={'job':sha(canonical(binding)),'source':row['sha256'],'source_id':sid,'profile':sha(canonical(profile))}
                        journal=analysis_recovery.Journal(out/'analysis_attempts'/sid,key)
                        if segment['hound_status']=='INSPECTED':
                            journal.cached_success(sha(canonical({'segment':segment,'upstream_code_executed':False})))
                            continue
                        segment_cache=out/'segments'/(sid+'.json')
                        if segment_cache.exists():
                            # Successful segment publication can precede aggregate
                            # publication. Recover its exact cached result, no rerun.
                            saved=_load_checkpoint(segment_cache,key)
                            found=saved.get('segment',{})
                            if found.get('source_id')!=sid or found.get('source_sha256')!=segment['source_sha256'] or found.get('hound_status')!='INSPECTED':raise Blocked('RECOVERY_CACHE_DISAGREEMENT')
                            journal.cached_success(sha(canonical(saved)));retry=True
                        else:
                            if journal.history and journal.history[-1]['state']=='STARTED':journal.finish('FAILED','ATTEMPT_INTERRUPTED')
                            if journal.summary(sid)['retry_eligible']:retry=True
                    if not retry or used>=max_containers:cached+=1
                if r is None or (retry and used<max_containers):
                    if used>=max_containers:unread.append({'file_id':row['file_id'],'reason':'INVOCATION_BUDGET'});continue
                    used+=1;fresh=inspect_one(b,row,workbench.home,profile,analyzer,out/'segments',sha(canonical(binding)))
                    if sha(read(local,DEFAULTS['max_container_bytes']))!=row['sha256']:raise Blocked('SOURCE_CHANGED_DURING_READ')
                    if r is not None:
                        previous=read(checkpoint,20_000_000);history=out/'recovery_history'/(sha(previous)+'.json')
                        if history.exists():
                            if read(history,20_000_000)!=previous:raise Blocked('RECOVERY_HISTORY_CHANGED')
                        else:write(history,previous,new=True)
                    r=fresh
                    write(checkpoint,canonical({'binding':cb,'result_sha256':sha(canonical(r)),'result':r}),new=not checkpoint.exists())
                results.append(r)
            except (Blocked,OSError) as e:blocked.append({'file_id':row['file_id'],'reason':str(e) if isinstance(e,Blocked) else type(e).__name__})
        segments=[s for r in results for s in r['segments']]
        definitions=[d for s in segments for d in s['definitions']]
        states=collections.Counter(i['status'] for r in results for i in r['inventory'])
        gaps=[{'file_id':r['source']['file_id'],**g} for r in results for g in [*r['gaps'],*r['hound_errors']]]
        occurrences=collections.defaultdict(list)
        for s in segments:occurrences[s['source_sha256']].append(s['source_id'])
        status='INCOMPLETE' if unread or blocked else ('COMPLETED_WITH_GAPS' if gaps else 'COMPLETED_FOR_SELECTED_CONTAINERS')
        report={'schema':SCHEMA,'status':status,'corpus_manifest_sha256':binding['manifest_sha256'],'profile':profile,
                'coverage':{'selected_containers':len(m['files']),'inspected_containers':len(results),'unread':unread,'blocked':blocked,'inventory_states':dict(states),'whole_drive_scanned':False},
                'summary':{'segments':len(segments),'definitions':len(definitions),'test_definitions':sum(len(s['definitions']) for s in segments if s['test_path']),
                           'unique_segment_texts':len(occurrences),'duplicate_text_occurrences':len(segments)-len(occurrences),
                           'hound_observations':sum(len(s.get('hound',{}).get('findings',[])) for s in segments),
                           'hound_segments':sum(s['hound_status']=='INSPECTED' for s in segments)},
                'cache':{'reused_containers':cached,'new_containers':used},'sources':results,'gaps':gaps,
                'equivalent_text_groups':{k:v for k,v in occurrences.items() if len(v)>1},'elapsed_seconds':time.monotonic()-start,
                'source_bodies_retained':False,'upstream_code_executed':False,'model_calls':0,'release_approved':False,'independent_evaluation':False}
        report['recovery']=analysis_recovery.summary(out/'analysis_attempts')
        report['summary']['source_scope_records']=sum(1 for _ in source_rows(report))
        workbench.guard()  # Reject publication if the qualified tools changed mid-run.
        write(out/'corpus.json',canonical(report));cart=gzip.compress(b''.join(canonical(row)+b'\n' for row in all_rows(report)),mtime=0)
        write(out/'capabilities.ndjson.gz',cart)
        write(out/'receipt.json',canonical({'report_sha256':sha(canonical(report)),'cart_sha256':sha(cart),'cart_bytes':len(cart),'source_bodies_retained':False}))
        write(out/'Review.html',render(report).encode())
    return report

def _class_context(meta,segment):
    owner=meta.get('class_context')
    if not owner:return None
    import copy
    owner=copy.deepcopy(owner)
    owner['line_basis']='SEGMENT_LOCAL_WITH_CONTAINER_MEMBER_OFFSETS'
    for key in ('documentation_start_line','documentation_end_line','start_line','end_line'):
        owner['member_'+key]=segment['start_line']+owner[key]-1 if owner.get(key) is not None else None
    return owner

def rows(report):
    for r in report['sources']:
        source=r['source']
        for s in r['segments']:
            for d in s['definitions']:
                findings=[f for f in s.get('hound',{}).get('findings',[]) if f.get('qualified_name',f.get('function',f.get('function_name')))==d['name']]
                apis=sorted(set(f.get('resolved_api',f.get('api','')) for f in findings if f.get('resolved_api',f.get('api'))))
                yield {'record_kind':'function','definition_id':d['definition_id'],'name':d['name'],'parameters':d['parameters'],'source_id':s['source_id'],
                       'source_sha256':s['source_sha256'],'container_sha256':source['sha256'],'file_id':source['file_id'],'source_url':source['source_url'],
                       'container':source['path'],'path':s['path'],'logical_path_hint':s['logical_path_hint'],'member_chain':s['member_chain'],
                       'format':s['format'],'cell_index':s['cell_index'],'start_line':d['member_start_line'],'end_line':d['member_end_line'],
                       'test_path':s['test_path'],'documented_terms':d['declared_terms'],'observed_apis':apis,
                       'local_start_line':d['start_line'],'local_end_line':d['end_line'],
                       'term_frequencies':d.get('structure',{}).get('term_frequencies',{}),
                       'class_context':_class_context(d.get('structure',{}),s),
                       'implementation_kind':d.get('structure',{}).get('implementation_kind','NOT_CAPTURED'),
                       'method_role':d.get('structure',{}).get('method_role','UNCLASSIFIED'),
                       'attribute_reads':d.get('structure',{}).get('attribute_reads',[]),
                       'comparison_operators':sorted({op for cmp in d.get('structure',{}).get('comparisons',[]) for op in cmp['operators']}),
                       'structure_truncated':d.get('structure',{}).get('truncated',False),
                       'declared_calls':[c['name'] for c in d.get('structure',{}).get('calls',[])],
                       'declared_api_leads':[c['name'] for c in d.get('structure',{}).get('declared_api_leads',[])],
                       'context_status':'CAPTURED' if 'structure' in d else 'REINDEX_REQUIRED',
                       'search_terms':sorted(set(d['declared_terms']+tokens((s['logical_path_hint'] or s['path'])+' '+d['name']+' '+' '.join(apis)))),
                       'runtime_status':'NOT_RUN','rights_status':'NOT_REVIEWED','evidence':'STATIC_STRUCTURE_AND_DECLARATIONS'}

def source_rows(report):
    """Module/script records never masquerade as callable function definitions."""
    for r in report['sources']:
        source=r['source']
        for s in r['segments']:
            observed=[f for f in s.get('hound',{}).get('findings',[]) if f.get('scope_kind') in ('source','module','script')]
            if not observed and (s['definitions'] or not s['script_present']):continue
            apis=sorted({f['resolved_api'] for f in observed if f.get('resolved_api')})
            yield {'record_kind':'source_scope','name':'<module>','parameters':[],
                   'source_id':s['source_id'],'source_sha256':s['source_sha256'],
                   'container_sha256':source['sha256'],'file_id':source['file_id'],'source_url':source['source_url'],
                   'container':source['path'],'path':s['path'],'logical_path_hint':s['logical_path_hint'],
                   'member_chain':s['member_chain'],'format':s['format'],'cell_index':s['cell_index'],
                   'start_line':s['start_line'],'end_line':s['end_line'],'test_path':s['test_path'],
                   'documented_terms':[],'declared_imports':s['imports'],'observed_apis':apis,
                   'local_start_line':1,'local_end_line':s['end_line']-s['start_line']+1,
                   'attribute_reads':s.get('module_structure',{}).get('attribute_reads',[]),
                   'comparison_operators':sorted({op for c in s.get('module_structure',{}).get('comparisons',[]) for op in c['operators']}),
                   'structure_truncated':s.get('module_structure',{}).get('truncated',False),
                   'declared_calls':[c['name'] for c in s.get('module_structure',{}).get('calls',[])],
                   'context_status':'CAPTURED' if 'module_structure' in s else 'REINDEX_REQUIRED',
                   'search_terms':tokens((s['logical_path_hint'] or s['path'])+' '+' '.join(s['imports']+apis)),
                   'runtime_status':'NOT_RUN','rights_status':'NOT_REVIEWED','evidence':'SOURCE_STRUCTURE_AND_DECLARED_IMPORTS'}

def all_rows(report):
    yield from rows(report)
    yield from source_rows(report)

EXPAND={'align':{'alignment','align'},'alignment':{'alignment','align'},'queue':{'queue','job','jobs'},'snapshot':{'snapshot','backup'},'atomic':{'atomic','publish','publication'},'geometry':{'geometry','mesh','point','surface'},'validate':{'validate','validation','verify','verification'},'hash':{'hash','hashing','sha256','fingerprint'},'uncertainty':{'uncertainty','unknown','ambiguous'},'normalize':{'normalize','normalization'}}

def search_legacy(report,query,limit=20,include_tests=False):
    text(query,200);integer(limit,1,100)
    if type(include_tests) is not bool:raise Blocked('INCLUDE_TESTS_TYPE')
    if not isinstance(report,dict) or report.get('schema')!=SCHEMA or report.get('source_bodies_retained') is not False or report.get('upstream_code_executed') is not False:raise Blocked('CORPUS_REPORT')
    qt=tokens(query)
    if not qt:raise Blocked('QUERY_TERMS_REQUIRED')
    scored=[]
    for row in all_rows(report):
        if row['test_path'] and not include_tests:continue
        matched=[t for t in qt if (EXPAND.get(t,{t}) & set(row['search_terms']))]
        if not matched:continue
        row['matched_query_terms']=matched;row['match_count']=len(matched);row['all_query_terms_matched']=len(matched)==len(qt)
        row['symbol_query_matches']=len(set(qt)&set(tokens(row['name'])))
        row['exact_symbol_match']=tokens(row['name'].split('.')[-1])==qt
        scored.append(row)
    scored.sort(key=lambda r:(-r['match_count'],-int(r['exact_symbol_match']),-r['symbol_query_matches'],r['name'],r.get('definition_id',r['source_id'])))
    full=sum(r['all_query_terms_matched'] for r in scored)
    return {'query':query,'query_terms':qt,'total_matching_occurrences':len(scored),'full_match_count':full,'search_status':('MATCHES_FOUND' if full else 'PARTIAL_TERM_MATCHES_ONLY' if scored else 'NO_METADATA_MATCH'),'results':scored[:limit],
            'rank_meaning':'literal/explicit synonym coverage, then exact symbol/name matches; NOT confidence or code quality','runtime_validation':'NOT_RUN','release_approved':False,
            'coverage_status':report['status'],'match_absence_is_not_capability_absence':True}

def search(report, query, limit=20, include_tests=False, *, distinct=False, filters=None):
    from .retrieval import search_report
    return search_report(report, query, limit, include_tests, distinct, filters)


def dependency_context(report, definition_id):
    if not isinstance(report, dict) or report.get('schema') != SCHEMA or report.get('upstream_code_executed') is not False or report.get('source_bodies_retained') is not False:
        raise Blocked('CORPUS_REPORT')
    if not isinstance(definition_id, str) or not re.fullmatch(r'definition_[0-9a-f]{64}', definition_id):
        raise Blocked('CONTEXT_DEFINITION_ID')
    from .structure import context
    return context(report, definition_id)


def render(report):
    esc=lambda x:html.escape(str(x),quote=True)
    recovery=report.get('recovery',{})
    recovery_html=('<h2>Analysis recovery</h2><p>'+esc(recovery.get('recovered_segments',0))+' recovered; '+esc(recovery.get('retry_eligible_segments',0))+' eligible for a later bounded retry; '+esc(recovery.get('exhausted_segments',0))+' exhausted. The original failures remain in the attempt history. No source code was executed.</p>') if recovery else ''
    items=list(all_rows(report));data=canonical(items).decode().replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    return '''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FORGE mixed-source corpus</title>
<style>body{font:16px system-ui;margin:2rem auto;max-width:1120px;padding:0 1rem;background:#101820;color:#e5edf4}h1{font-size:2rem}input{width:95%;padding:1rem;margin:1rem 0}article{padding:1rem;border:1px solid #425366;margin:.7rem 0;overflow-wrap:anywhere}small{color:#aab9c8}pre{white-space:pre-wrap}button{padding:.6rem;cursor:pointer}</style>
<h1>FORGE · Mixed-source evidence</h1><p>Read-only test corpus. Static observations, not verified implementations.</p><p>'''+esc(report['status'])+' · '+esc(report['summary']['segments'])+' code segments · '+esc(report['summary']['definitions'])+''' definitions</p>'''+recovery_html+'''
<input id="q" aria-label="Search code metadata" placeholder="Search names, paths, and documented terms"><p id="count"></p><div id="results"></div>
<details><summary>Coverage and limitations</summary><pre>'''+esc(json.dumps({'coverage':report['coverage'],'gaps':report['gaps']},indent=2))+'''</pre></details><script type="application/json" id="records">'''+data+'''</script><script>
const rows=JSON.parse(document.getElementById('records').textContent),q=document.getElementById('q'),root=document.getElementById('results');
function show(){let words=q.value.toLowerCase().trim().split(/\\s+/).filter(Boolean),found=rows.filter(r=>words.every(w=>[r.name,r.path,r.logical_path_hint||'',...r.search_terms].join(' ').toLowerCase().includes(w)));root.replaceChildren();document.getElementById('count').textContent=found.length+' matching occurrences; showing at most 100. Search is lexical, not a confidence score.';for(const r of found.slice(0,100)){const a=document.createElement('article'),h=document.createElement('h3'),p=document.createElement('p'),s=document.createElement('small');h.textContent=r.name;p.textContent=r.container+' → '+r.member_chain.map(x=>x.path).join(' → ')+' · '+(r.logical_path_hint||r.path)+' · lines '+r.start_line+'–'+r.end_line;s.textContent=r.format+' | '+r.runtime_status+' | '+r.source_id;a.append(h,p,s);root.append(a)}}q.addEventListener('input',show);show();</script>'''

# Patch: a run over many envelopes can publish a corpus.json well
# past 20 MB (segment structure for thousands of definitions), which made
# corpus-search/-context/-list fail with INPUT_FILE_BOUNDS. Raise only this
# read ceiling; the generated report is trusted local output, not external input.
REPORT_READ_LIMIT=256_000_000

def read_report(folder):
    folder=safe_path(folder);data=read(folder/'corpus.json',REPORT_READ_LIMIT);receipt=loads(read(folder/'receipt.json',2000))
    if not isinstance(receipt,dict) or sha(data)!=receipt.get('report_sha256') or receipt.get('source_bodies_retained') is not False:raise Blocked('CORPUS_RECEIPT')
    report=loads(data)
    if not isinstance(report,dict) or report.get('upstream_code_executed') is not False or report.get('source_bodies_retained') is not False:raise Blocked('CORPUS_AUTHORITY')
    return report

def get(workbench,run_id):
    workbench.guard()
    if not isinstance(run_id,str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}',run_id):raise Blocked('CORPUS_RUN_ID')
    return read_report(workbench.home/'corpora'/run_id)

def list_runs(workbench):
    workbench.guard();folder=workbench.home/'corpora';items=[]
    if not folder.exists():return []
    for p in sorted(folder.iterdir()):
        if not p.is_dir() or p.is_symlink() or not (p/'corpus.json').exists():continue
        try:
            r=get(workbench,p.name);items.append({'id':p.name,'status':r['status'],'summary':r['summary'],'coverage':{k:r['coverage'][k] for k in ('selected_containers','inspected_containers','whole_drive_scanned')}})
        except (Blocked,OSError,ValueError):items.append({'id':p.name,'status':'BLOCKED_INVALID_REPORT'})
    return items

def export_search(report,query,out,*,distinct=False,filters=None,include_tests=False):
    out=safe_path(out)
    if out.exists() or out.is_relative_to(ROOT):raise Blocked('NEW_PACKET_DIRECTORY_REQUIRED')
    obj=search(report,query,limit=50,distinct=distinct,filters=filters,include_tests=include_tests);out.mkdir(parents=True,mode=0o700)
    packet={'schema':1,**obj,'corpus_manifest_sha256':report['corpus_manifest_sha256'],'source_bodies_included':False,
            'next_step':'Review the exact referenced source and dependencies. Reacquire matching original container bytes; do not reconstruct implementation from metadata. No execution or adoption is approved.',
            'observed_vs_claimed':'Structure, documented terms and lexical call leads. Not runtime guarantees.',
            'dependency_context_scope':'Displayed primary occurrences only; other origins remain separately reviewable.',
            'dependency_contexts':{row['definition_id']:dependency_context(report,row['definition_id']) for row in obj['results'] if 'definition_id' in row}}
    write(out/'reuse_candidates.json',canonical(packet),new=True)
    write(out/'receipt.json',canonical({'packet_sha256':sha(canonical(packet)),'release_approved':False,'records':len(obj['results'])}),new=True)
    return {'status':'STATIC_REUSE_PACKET_EXPORTED','records':len(obj['results']),'path':str(out),'release_approved':False}
