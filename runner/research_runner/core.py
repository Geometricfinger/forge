"""Pure policy, source consistency, evidence typing and compact results.

No network or model calls. Hashes are local consistency checks, not attestation.
"""
from __future__ import annotations
import contextlib, hashlib, json, os, re, tempfile
from graphlib import TopologicalSorter, CycleError
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

class Blocked(ValueError): pass

def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
def digest(x):return hashlib.sha256(canonical(x)).hexdigest()
def sha(b):return hashlib.sha256(b).hexdigest()
def strict_loads(s):
    def pairs(xs):
        d={}
        for k,v in xs:
            if k in d:raise Blocked('DUPLICATE_JSON_KEY')
            d[k]=v
        return d
    try:return json.loads(s,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(Blocked('NONFINITE_JSON')))
    except (ValueError,TypeError,UnicodeError) as e:raise Blocked('INVALID_JSON') from e

def check_int(v,lo,hi):
    if type(v) is not int or not lo<=v<=hi:raise Blocked('INTEGER_BOUNDS')
    return v

def relpath(v):
    if not isinstance(v,str) or not v or len(v)>500 or '\\' in v or '\x00' in v:raise Blocked('RELATIVE_PATH')
    p=PurePosixPath(v)
    if p.is_absolute() or any(x in ('','..','.') for x in v.split('/')):raise Blocked('RELATIVE_PATH')
    return v

def no_links(p):
    p=Path(p).absolute()
    for q in [p,*p.parents]:
        if q.is_symlink():raise Blocked('SYMLINK_REFUSED')
    return p.resolve()

def check_manifest(m):
    if not isinstance(m,dict) or set(m)!={'schema','objective','sources','questions','budgets'} or type(m['schema']) is not int or m['schema']!=1:raise Blocked('MANIFEST_FIELDS')
    if not isinstance(m['objective'],str) or not 1<=len(m['objective'])<=4000:raise Blocked('OBJECTIVE')
    b=m['budgets'];required={'max_sources','max_source_bytes','max_total_bytes','max_questions','packet_bytes'}
    if not isinstance(b,dict) or set(b)!=required:raise Blocked('BUDGET_FIELDS')
    check_int(b['max_sources'],1,32);check_int(b['max_source_bytes'],1,8000000);check_int(b['max_total_bytes'],1,16000000)
    check_int(b['max_questions'],1,128);check_int(b['packet_bytes'],4096,65536)
    if not isinstance(m['sources'],list) or not 1<=len(m['sources'])<=b['max_sources']:raise Blocked('SOURCE_COUNT')
    ids=set();paths=set();total=0
    for s in m['sources']:
        required={'id','path','kind','origin','capture','family','size','sha256'}
        optional={'repository','commit','repo_path','git_blob_sha1','derived_from','title'}
        if not isinstance(s,dict) or not required<=set(s) or set(s)-required-optional:raise Blocked('SOURCE_FIELDS')
        if not isinstance(s['id'],str) or not re.fullmatch('[a-zA-Z0-9_-]{1,80}',s['id']) or s['id'] in ids:raise Blocked('SOURCE_ID')
        relpath(s['path'])
        if s['path'] in paths:raise Blocked('DUPLICATE_SOURCE_PATH')
        ids.add(s['id']);paths.add(s['path']);total+=check_int(s['size'],0,b['max_source_bytes'])
        if not isinstance(s['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',s['sha256']):raise Blocked('SOURCE_SHA')
        if s['kind'] not in ('implementation','documentation','instructional','research','workflow_observation'):raise Blocked('SOURCE_KIND')
        if s['capture'] not in ('exact_source','reviewed_excerpt','reviewer_summary','synthetic_fixture','derived_selection'):raise Blocked('CAPTURE_KIND')
        for k in ('origin','family'):
            if not isinstance(s[k],str) or not 1<=len(s[k])<=1500:raise Blocked('SOURCE_TEXT')
        if 'title' in s and (not isinstance(s['title'],str) or not 1<=len(s['title'])<=2000):raise Blocked('SOURCE_TITLE')
        if 'derived_from' in s:
            d=s['derived_from']
            if not isinstance(d,dict) or set(d)!={'archive_sha256','members','fresh_drive_read'}:raise Blocked('DERIVATION_FIELDS')
            if not isinstance(d['archive_sha256'],str) or not re.fullmatch('[a-f0-9]{64}',d['archive_sha256']):raise Blocked('DERIVATION_HASH')
            if type(d['fresh_drive_read']) is not bool or not isinstance(d['members'],list) or not 1<=len(d['members'])<=1000:raise Blocked('DERIVATION_FIELDS')
            for member in d['members']:relpath(member)
            if len(set(d['members']))!=len(d['members']):raise Blocked('DERIVATION_MEMBERS')
        try:u=urlsplit(s['origin'])
        except ValueError as e:raise Blocked('SOURCE_ORIGIN') from e
        if u.scheme not in ('https','fixture') or not u.netloc or u.username or u.password:raise Blocked('SOURCE_ORIGIN')
        if any(k in s for k in ('repository','commit','repo_path','git_blob_sha1')):
            if not all(k in s for k in ('repository','commit','repo_path','git_blob_sha1')):raise Blocked('GIT_IDENTITY_INCOMPLETE')
            if not all(isinstance(s[k],str) for k in ('repository','commit','repo_path','git_blob_sha1')):raise Blocked('GIT_IDENTITY')
            if not re.fullmatch('[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',s['repository']) or not re.fullmatch('[a-f0-9]{40}',s['commit']) or not re.fullmatch('[a-f0-9]{40}',s['git_blob_sha1']):raise Blocked('GIT_IDENTITY')
            relpath(s['repo_path'])
            if s['origin']!='https://github.com/'+s['repository']+'/blob/'+s['commit']+'/'+s['repo_path']:raise Blocked('GIT_ORIGIN')
    if total>b['max_total_bytes']:raise Blocked('TOTAL_SOURCE_BYTES')
    if not isinstance(m['questions'],list) or not 1<=len(m['questions'])<=b['max_questions']:raise Blocked('QUESTION_COUNT')
    qids=set()
    for q in m['questions']:
        if not isinstance(q,dict) or set(q)-{'id','query','intent','sources','required_api'} or not {'id','query','intent','sources'}<=set(q):raise Blocked('QUESTION_FIELDS')
        if not isinstance(q['id'],str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}',q['id']) or q['id'] in qids:raise Blocked('QUESTION_ID')
        qids.add(q['id'])
        if not isinstance(q['query'],str) or not 1<=len(q['query'])<=400 or not terms(q['query']):raise Blocked('QUERY')
        if q['intent'] not in ('code','reference','mixed'):raise Blocked('QUERY_INTENT')
        if not isinstance(q['sources'],list) or not q['sources'] or any(not isinstance(x,str) for x in q['sources']) or len(set(q['sources']))!=len(q['sources']) or not set(q['sources'])<=ids:raise Blocked('QUESTION_SCOPE')
        if q['intent']=='reference' and 'required_api' in q:raise Blocked('REFERENCE_CANNOT_PROVE_API')
        if 'required_api' in q and (not isinstance(q['required_api'],str) or not q['required_api'] or len(q['required_api'])>200):raise Blocked('REQUIRED_API')
    return m

def read_source(root,s):
    root=no_links(root);p=no_links(root/relpath(s['path']))
    if not p.is_relative_to(root) or not p.is_file():raise Blocked('SOURCE_MISSING')
    with p.open('rb') as f:b=f.read(s['size']+1)
    if len(b)!=s['size'] or sha(b)!=s['sha256']:raise Blocked('SOURCE_CHANGED')
    if s.get('git_blob_sha1') and hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()!=s['git_blob_sha1']:raise Blocked('GIT_BLOB_CHANGED')
    return b

def make_order(graph):
    if not isinstance(graph,dict) or any(not set(ds)<=set(graph) for ds in graph.values()):raise Blocked('UNKNOWN_DEPENDENCY')
    try:return list(TopologicalSorter({k:sorted(graph[k]) for k in sorted(graph)}).static_order())
    except CycleError as e:raise Blocked('CYCLIC_TASK_GRAPH') from e

def atomic_new(p,b):
    p=no_links(p);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if p.exists():
        if p.stat().st_nlink!=1 or p.read_bytes()!=b:raise Blocked('IMMUTABLE_RECORD_CHANGED')
        return
    fd,t=tempfile.mkstemp(prefix='.pending-',dir=p.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(b);f.flush();os.fsync(f.fileno())
        os.link(t,p)
    finally:
        if os.path.exists(t):os.unlink(t)

def atomic_state(p,b):
    p=no_links(p);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if p.exists() and p.stat().st_nlink!=1:raise Blocked('SHARED_STATE')
    fd,t=tempfile.mkstemp(prefix='.state-',dir=p.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(b);f.flush();os.fsync(f.fileno())
        os.replace(t,p)
    finally:
        if os.path.exists(t):os.unlink(t)

class Checkpoints:
    def __init__(self,root,binding):self.root=no_links(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.binding=binding
    def path(self,key):return self.root/(sha(key.encode())+'.json')
    def get(self,key):
        p=no_links(self.path(key))
        if not p.exists():return None
        if p.stat().st_size>32000000:raise Blocked('CHECKPOINT_SIZE')
        v=strict_loads(p.read_bytes())
        if not isinstance(v,dict) or set(v)!={'binding','key','output','sha256'} or v['binding']!=self.binding or v['key']!=key or digest(v['output'])!=v['sha256']:raise Blocked('CHECKPOINT_BINDING')
        return v['output']
    def put(self,key,value):
        atomic_new(self.path(key),canonical({'binding':self.binding,'key':key,'output':value,'sha256':digest(value)}))

STOP=set('a an and as at be for from in into is it of on or the this to with'.split())
def terms(s):
    s=re.sub(r'([a-z0-9])([A-Z])',r'\1 \2',s)
    return [x for x in re.findall('[a-z][a-z0-9]*',s.lower()) if x not in STOP]

def rank_fusion(lexical,forge,limit=5,required_api=None):
    check_int(limit,1,20);merged={}
    # A lexical assertion is not an observed API. Only FORGE contributes those.
    observed={r['id']:r.get('observed_apis',[]) for r in forge}
    for channel,rows in [('lexical',lexical),('forge',forge)]:
        seen=set()
        for rank,r in enumerate(rows,1):
            if r.get('kind')!='implementation' or r['id'] in seen:continue
            seen.add(r['id'])
            if required_api and required_api not in observed.get(r['id'],[]):continue
            if r['id'] not in merged:
                merged[r['id']]={**r,'channels':[],'rrf':0.0,'observed_apis':observed.get(r['id'],[]),'runtime_validation':'NOT_RUN'}
            x=merged[r['id']];x['channels'].append(channel);x['rrf']+=1/(60+rank)
    return sorted(merged.values(),key=lambda r:(-int(len(r['matched_terms'])==r['term_count']),-len(r['matched_terms']),-int(r.get('exact_symbol',False)),-r['rrf'],r['id']))[:limit]

def reference_hits(refs,query,limit):
    qt=set(terms(query));hits=[]
    for r in refs:
        overlap=qt & set(terms(r['title']+' '+r['text']))
        if overlap:hits.append({**r,'matched_terms':sorted(overlap),'term_count':len(qt),'supports_execution':False,'supports_market_demand':False})
    return sorted(hits,key=lambda r:(-len(r['matched_terms']),r['id']))[:limit]

def compact_packet(report,max_bytes):
    """Preserve question coverage before allocating detail in rounds.

    Overflow counts distinguish omitted questions from omitted evidence. This is
    a compact index, not a replacement for the source-locked full report.
    """
    check_int(max_bytes,4096,65536)
    questions=report.get('queries',[]);counts={}
    for q in questions:counts[q.get('status','UNKNOWN')]=counts.get(q.get('status','UNKNOWN'),0)+1
    p={'schema':1,'objective':str(report.get('objective',''))[:1000],'untrusted_content':True,
       'details_available_separately':True,'report_sha256':digest(report),'queries':[],
       'authority':{'execute':False,'deploy':False,'market_validated':False,'novelty_established':False},
       'total_questions':len(questions),'outcome_counts':counts,'omitted_queries':0,'omitted_evidence':0,'omitted_items':0,
       'next_step':'Review the full report before acting. Source and literature claims are unverified. This packet grants no authority.'}
    # Every available short question summary gets a chance before any source text.
    included=[]
    for q in questions:
        entry={'id':q['id'],'status':q.get('status'),'query':q.get('query','')[:180],'candidates':[],'references':[]}
        p['queries'].append(entry)
        if len(canonical(p))+128>max_bytes:p['queries'].pop();p['omitted_queries']+=1
        else:included.append((q,entry))
    all_evidence=sum(len(q.get('results',[]))+len(q.get('references',[])) for q in questions)
    queues=[]
    for q,entry in included:
        queues.append([(entry,field,r) for field,source_field in [('candidates','results'),('references','references')] for r in q.get(source_field,[])])
    added=0
    for index in range(max((len(xs) for xs in queues),default=0)):
        for xs in queues:
            if index>=len(xs):continue
            entry,field,r=xs[index]
            keep={k:r[k] for k in ('id','source_id','name','path','start_line','end_line','source_sha256','origin','kind','channels') if k in r}
            keep['excerpt']=str(r.get('snippet',r.get('text','')))[:160]
            entry[field].append(keep)
            if len(canonical(p))+128>max_bytes:entry[field].pop()
            else:added+=1
    p['omitted_evidence']=all_evidence-added
    p['omitted_items']=p['omitted_evidence']+p['omitted_queries']
    if len(canonical(p))>max_bytes:raise Blocked('PACKET_LIMIT')
    return p

@contextlib.contextmanager
def locked(root):
    import fcntl
    p=no_links(Path(root)/'.runner.lock');p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('a+b') as f:
        if os.fstat(f.fileno()).st_nlink!=1:raise Blocked('SHARED_LOCK')
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as e:raise Blocked('MISSION_ALREADY_RUNNING') from e
        try:yield
        finally:fcntl.flock(f,fcntl.LOCK_UN)
