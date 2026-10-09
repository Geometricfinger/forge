"""Read-only adapter to the FORGE engine in this repository.

The adapter never edits its engine. Full source stays in the local workspace;
compact handoff records are bounded excerpts with source identities.
"""
from __future__ import annotations
import io, json, sqlite3, stat, sys, zipfile
from itertools import zip_longest
from pathlib import Path
from .core import *

def verify_base(root):
    """Verify the FORGE engine this runner drives (by default, the enclosing repository).

    The engine pins its analyzer through contracts/analyzer_lock.json and the
    component SHA256SUMS.txt manifests; every listed file is re-hashed here.
    """
    root=no_links(Path(root).resolve())
    if not (root/'forge.py').is_file() or not (root/'forge_core').is_dir():raise Blocked('FORGE_ENGINE_MISSING')
    lock=json.loads((root/'contracts/analyzer_lock.json').read_text())
    count=0
    for name,expected in sorted(lock['manifests'].items()):
        m=root/relpath(name)/'SHA256SUMS.txt'
        if not m.is_file() or sha(m.read_bytes())!=expected:raise Blocked('FORGE_ANALYZER_MANIFEST')
        for line in m.read_text().splitlines():
            h,rel=line.split('  ',1);p=no_links(root/relpath(name)/relpath(rel))
            if not p.is_file() or sha(p.read_bytes())!=h:raise Blocked('FORGE_ANALYZER_CHANGED')
            count+=1
    return {'analyzer_lock_sha256':sha((root/'contracts/analyzer_lock.json').read_bytes()),'files_verified':count}


def load_base(root):
    root=Path(root).resolve();verify_base(root)
    if 'forge_core' in sys.modules:
        module=Path(sys.modules['forge_core'].__file__).resolve()
        if not module.is_relative_to(root):raise Blocked('WRONG_FORGE_ALREADY_IMPORTED')
    else:sys.path.insert(0,str(root))
    from forge_core import corpus
    from forge_core.workspace import initialize,Workbench
    return corpus,initialize,Workbench


def text_chunks(text,source,kind=None):
    lines=text.splitlines();rows=[]
    for start in range(0,len(lines),32):
        piece='\n'.join(lines[start:start+40])
        rows.append({'id':digest([source['id'],source['sha256'],start+1,piece]),'source_id':source['id'],
          'source_sha256':source['sha256'],'origin':source['origin'],'family':source['family'],
          'capture':source['capture'],'title':source.get('title',source['path']),
          'path':source['path'],'start_line':start+1,'end_line':min(start+40,len(lines)),
          'kind':kind or source['kind'],'text':piece})
    return rows


def prepare_manifest(data,s,work):
    staged=work/'staged';staged.mkdir(exist_ok=True,mode=0o700)
    if s.get('repository') and Path(s['path']).suffix in ('.py','.pyi','.pyw'):
        from forge_core.github_corpus import KIND
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
            info=zipfile.ZipInfo(s['repo_path'],date_time=(2020,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=(stat.S_IFREG|0o600)<<16;z.writestr(info,data)
        payload=stream.getvalue();name=s['id']+'.zip'
        row={'provider':'github','file_id':f"github:{s['repository']}@{s['commit']}:selected-files",'path':name,'size':len(payload),'sha256':sha(payload),
           'source_url':f"https://github.com/{s['repository']}/tree/{s['commit']}",'repository':s['repository'],'commit':s['commit'],
           'container_kind':KIND,'selected_paths':[s['repo_path']],
           'members':[{'path':s['repo_path'],'size':len(data),'sha256':sha(data),'git_blob_sha1':s['git_blob_sha1']}]}
    else:
        payload=data;name=s['id']+Path(s['path']).suffix
        row={'provider':'synthetic','file_id':'runner-local-'+s['id'],'path':name,'size':len(data),'sha256':sha(data),'source_url':'fixture://runner-local-'+s['id']}
    atomic_new(staged/name,payload)
    mf=staged/(s['id']+'.manifest.json');atomic_new(mf,canonical({'schema':1,'files':[row]}))
    return staged,mf,payload,row


def inspect_source(s,root,work,forge_root):
    data=read_source(root,s)
    if s['kind']!='implementation':
        if len(data)>250000:raise Blocked('REFERENCE_TEXT_LIMIT')
        try:text=data.decode('utf-8')
        except UnicodeError as e:raise Blocked('REFERENCE_ENCODING') from e
        return {'status':'REFERENCE_INDEXED','source':s,'records':[],'references':text_chunks(text,s),'gaps':[], 'source_code_executed':False}
    ext=Path(s['path']).suffix.lower()
    if ext not in ('.py','.pyi','.pyw','.txt','.md','.zip','.ipynb'):
        # Text fallback is deliberately not a new language parser.
        if len(data)>250000:raise Blocked('TEXT_FALLBACK_LIMIT')
        try:text=data.decode('utf-8')
        except UnicodeError:return {'status':'UNSUPPORTED_BINARY','source':s,'records':[],'references':[],'gaps':['UNSUPPORTED_FORMAT'],'source_code_executed':False}
        return {'status':'TEXT_ONLY_UNSUPPORTED_LANGUAGE','source':s,'records':[],
          'references':text_chunks(text,s,'code_text_only'),'gaps':['LANGUAGE_NOT_ANALYZED'],'source_code_executed':False}
    corpus,initialize,Workbench=load_base(forge_root)
    home=work/'forge-home'
    w=Workbench(home) if (home/'engine.json').exists() else initialize(home)
    staged,mf,payload,row=prepare_manifest(data,s,work)
    report_dir=work/'corpora'/s['id'];report=corpus.run(w,staged,mf,report_dir)
    parsed=corpus._parser(payload,row['path'],w.home)
    indexed=list(corpus.rows(report)); lookup={(r['path'],r['name'],r['start_line'],r['end_line'],r.get('cell_index')):r for r in indexed}
    records=[];retained_chars=0
    for seg in parsed['segments']:
        raw=bytes.fromhex(seg['source_hex']);lines=raw.decode('utf-8').splitlines()
        for d in seg['definitions']:
            start=seg['start_line']+d['start_line']-1;end=seg['start_line']+d['end_line']-1
            key=(seg['path'],d['name'],start,end,seg.get('cell_index'));mr=lookup.get(key)
            if mr is None:continue
            body='\n'.join(lines[d['start_line']-1:d['end_line']]);retained_chars+=len(body)
            if retained_chars>25000000:raise Blocked('LEXICAL_EXPANSION_LIMIT')
            owner=d.get('structure',{}).get('class_context') or {};classdoc=owner.get('documentation','')
            records.append({'id':mr['definition_id'],'source_id':s['id'],'source_sha256':mr['source_sha256'],
             'container_sha256':s['sha256'],'origin':s['origin'],'capture':s['capture'],'family':s['family'],
             'name':d['name'],'path':seg['path'],'start_line':start,'end_line':end,'kind':'implementation',
             'text':body,'class_documentation':classdoc,'test_path':seg['test_path'],'cell_index':seg.get('cell_index'),
             'runtime_validation':'NOT_RUN','observed_apis':mr.get('observed_apis',[])})
    if data!=read_source(root,s):raise Blocked('SOURCE_CHANGED_DURING_INSPECTION')
    return {'status':'CODE_INDEXED' if report['status']=='COMPLETED_FOR_SELECTED_CONTAINERS' else 'CODE_INDEXED_WITH_GAPS',
      'source':s,'records':records,'references':[],'gaps':report['gaps']+report['coverage']['blocked'],
      'forge_report_path':str(report_dir/'corpus.json'),'forge_summary':report['summary'], 'source_code_executed':False}


class Lexical:
    def __init__(self,records):
        self.rows=records;self.db=sqlite3.connect(':memory:')
        self.db.execute('CREATE VIRTUAL TABLE docs USING fts5(symbol,classdoc,source)')
        self.wordsets=[]
        for i,r in enumerate(records,1):
            fs=[' '.join(terms(r['name'])),' '.join(terms(r.get('class_documentation',''))),' '.join(terms(r['text']))]
            self.wordsets.append(set(' '.join(fs).split()));self.db.execute('INSERT INTO docs(rowid,symbol,classdoc,source) VALUES(?,?,?,?)',(i,*fs))
    def close(self):self.db.close()
    def search(self,query,limit=10):
        qt=set(terms(query))
        if not qt:return []
        expression=' OR '.join('"'+t+'"' for t in sorted(qt))
        rows=self.db.execute('SELECT rowid,bm25(docs,4,1,1) FROM docs WHERE docs MATCH ?',(expression,)).fetchall()
        results=[]
        for i,score in rows:
            r=self.rows[i-1];matched=sorted(qt & self.wordsets[i-1])
            results.append(project(r,query,matched)|{'lexical_score':score,'exact_symbol':query.strip() in (r['name'],r['name'].split('.')[-1])})
        results.sort(key=lambda r:(-int(r['exact_symbol']),-len(r['matched_terms']),r['lexical_score'],r['path'],r['start_line']))
        return results[:limit]


def project(r,query,matched=None):
    qt=set(terms(query));matched=matched if matched is not None else sorted(qt & set(terms(r['name']+' '+r.get('class_documentation','')+' '+r['text'])))
    return {k:r[k] for k in ('id','source_id','source_sha256','origin','capture','family','name','path','start_line','end_line','kind','observed_apis','runtime_validation')}|{'matched_terms':matched,'term_count':len(qt),'snippet':r['text'][:1600]}


def investigate(q,source_results,forge_root):
    selected=[r for r in source_results if r['source']['id'] in q['sources']]
    records=[r for source in selected for r in source.get('records',[]) if not r.get('test_path')]
    refs=[r for source in selected for r in source.get('references',[])]
    gaps=[{'source':r['source']['id'],'state':r['status'],'gaps':r.get('gaps',[])} for r in selected if r['status'] in ('BLOCKED','UNSUPPORTED_BINARY','CODE_INDEXED_WITH_GAPS','TEXT_ONLY_UNSUPPORTED_LANGUAGE')]
    lexical=[];forgerows=[];reference=[]
    if q['intent'] in ('code','mixed'):
        index=Lexical(records)
        try:lexical=index.search(q['query'],10)
        finally:index.close()
        by_id={r['id']:r for r in records}
        corpus,_,_=load_base(forge_root)
        lists=[]
        for source in sorted(selected,key=lambda item:item['source']['id']):
            source_rows=[]
            path=source.get('forge_report_path')
            if path:
                report=corpus.read_report(Path(path).parent)
                found=corpus.search(report,q['query'],limit=10,distinct=False,filters={'record_kind':'function'})
                for r in found['results']:
                    original=by_id.get(r['definition_id'])
                    if original is not None:source_rows.append(project(original,q['query']))
            if source_rows:lists.append(source_rows)
        forgerows=[row for layer in zip_longest(*lists) for row in layer if row is not None][:10]
        # Per-source list order is retained with deterministic round-robin policy;
        # no raw score equivalence across different source indexes is assumed.
    if q['intent'] in ('reference','mixed') or not records:
        reference=reference_hits(refs,q['query'],5)
    fused=rank_fusion(lexical,forgerows,5,required_api=q.get('required_api'))
    if q['intent']=='reference':fused=[]
    full=any(len(r['matched_terms'])==r['term_count'] for r in fused)
    status=('STATIC_LEADS_REVIEW_REQUIRED' if full else 'PARTIAL_QUERY_ONLY' if fused else
            'REFERENCE_OR_TEXT_ONLY' if reference else 'EVALUATION_BLOCKED' if gaps else 'NO_EVIDENCE_IN_SELECTED_SOURCES')
    warnings=['Token coverage is not proof of a requirement. Literature is not tested implementation or market demand.']
    if set(terms(q['query'])) & {'no','not','without','guarantee','secure','prove','never'}:warnings.append('NATURAL_LANGUAGE_CONSTRAINT_NOT_ENFORCED')
    return {'id':q['id'],'query':q['query'],'intent':q['intent'],'status':status,'results':fused,'references':reference,
      'lexical_results':lexical[:5],'forge_results':forgerows[:5],'source_gaps':gaps,'warnings':warnings,
      'next_action':('REVIEW_EVIDENCE_AND_DECLARE_BEHAVIOR_TEST' if fused else 'ACQUIRE_ADDITIONAL_PERMITTED_EVIDENCE'),
      'runtime_validation':'NOT_RUN','code_generated':False,'market_validated':False,'novelty_established':False}
