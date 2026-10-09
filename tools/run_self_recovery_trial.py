#!/usr/bin/env python3
"""Supervised self-inspection with one explicitly injected timeout.

Real Hound analyses run on source text; the source being inspected is not imported.
No model, network call or production write. Compare a selected trusted build only.
"""
import argparse,hashlib,json,sys,threading,time
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--engine-root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--source-root',type=Path)
    p.add_argument('--manifest',type=Path)
    p.add_argument('--require-recovery',action='store_true')
    args=p.parse_args();root=args.engine_root.resolve();sys.path.insert(0,str(root))
    from forge_core.common import canonical,loads,read,sha,Blocked,write,safe_path
    from forge_core.workspace import initialize
    from forge_core.engine import Analyzer
    from forge_core import corpus
    out=safe_path(args.out)
    if out.exists() or out.is_relative_to(root):raise Blocked('NEW_EXTERNAL_OUTPUT_REQUIRED')
    out.mkdir(parents=True,mode=0o700)
    if args.source_root and args.manifest:source,manifest=args.source_root,args.manifest
    else:
        # Default: a deterministic zip of this engine's own forge_core sources, built in the output folder.
        import io,zipfile
        source=out/'self-source';source.mkdir(mode=0o700);buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
            for f in sorted((root/'forge_core').glob('*.py')):
                info=zipfile.ZipInfo('forge_core/'+f.name,date_time=(2020,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
                z.writestr(info,f.read_bytes())
        blob=buf.getvalue();write(source/'forge_core_self.zip',blob,new=True)
        manifest=out/'self-source-manifest.json'
        write(manifest,canonical({'schema':1,'files':[{'provider':'synthetic','file_id':'self_0','path':'forge_core_self.zip','size':len(blob),'sha256':sha(blob),'source_url':'fixture://self_0'}]}),new=True)
    w=initialize(out/'home')
    selected=corpus.manifest(loads(read(manifest,2_000_000)))
    source_before={r['path']:sha(read(source/r['path'],30_000_000)) for r in selected['files']}
    if any(source_before[r['path']]!=r['sha256'] for r in selected['files']):raise Blocked('SOURCE_VERSION_MISMATCH')
    target=b'def inspect_one(data,source,home,profile,analyzer,segment_dir=None,job_binding=None):'
    class ObservedAnalyzer:
        def __init__(self,fail_once=False):
            self.inner=Analyzer(w.home);self.fail_once=fail_once;self.events=[];self.lock=threading.Lock()
        def scan(self,data,sid,profile):
            with self.lock:
                failed=self.fail_once and target in data
                if failed:self.fail_once=False
                event={'source_id':sid,'source_sha256':sha(data),'action':'INJECTED_TOOL_TIMEOUT' if failed else 'REAL_HOUND_SCAN'}
                self.events.append(event)
            if failed:raise Blocked('TOOL_TIMEOUT')
            value=self.inner.scan(data,sid,profile)
            event['completed']=True
            return value
    analyzer=ObservedAnalyzer();steady=corpus.run(w,source,manifest,w.home/'corpora'/'steady',analyzer=analyzer)
    initial_actual=len(analyzer.events)
    fault=ObservedAnalyzer(True);destination=w.home/'corpora'/'recovery'
    first=corpus.run(w,source,manifest,destination,analyzer=fault)
    prior={p.name:sha(read(p,20_000_000)) for p in (destination/'segments').glob('*.json')}
    n1=len(fault.events);second=corpus.run(w,source,manifest,destination,analyzer=fault)
    n2=len(fault.events);third=corpus.run(w,source,manifest,destination,analyzer=fault)
    n3=len(fault.events)
    queries=['corpus checkpoint errors','hound errors checkpoint','cache retry analysis','source hash checkpoint','inspect_one','run_fixed']
    before_queries={q:corpus.search(steady,q,distinct=True) for q in queries}
    after_queries={q:corpus.search(second,q,distinct=True) for q in queries}
    def semantic(x):
        if isinstance(x,dict):return {k:semantic(v) for k,v in x.items() if k not in {'execution_receipt_binding','receipt_binding'}}
        if isinstance(x,list):return [semantic(v) for v in x]
        return x
    semantically_equal=semantic(before_queries)==semantic(after_queries)
    recovered=second['summary']['hound_segments']==steady['summary']['hound_segments']
    summary={'status':'RECOVERY_DEMONSTRATED' if recovered else 'RECOVERY_NOT_DEMONSTRATED',
      'source_origin':'This engine\'s own forge_core sources, packed into a local zip',
      'source_files':steady['summary']['segments'],'steady':steady['summary'],
      'first_faulted':{'status':first['status'],'summary':first['summary'],'attempts':n1},
      'second':{'status':second['status'],'summary':second['summary'],'new_attempts':n2-n1},
      'third':{'status':third['status'],'summary':third['summary'],'new_attempts':n3-n2},
      'actual_hound_successes':sum(e.get('completed',False) for e in analyzer.events+fault.events),
      'injected_timeouts':sum(e['action']=='INJECTED_TOOL_TIMEOUT' for e in fault.events),
      'source_bytes_unchanged':source_before=={r['path']:sha(read(source/r['path'],30_000_000)) for r in selected['files']},
      'successful_segment_cache_bytes_unchanged':all(sha(read(destination/'segments'/n,20_000_000))==h for n,h in prior.items()),
      'recovered_rows_equal_steady':list(corpus.all_rows(second))==list(corpus.all_rows(steady)),
      'query_semantics_equal_steady':semantically_equal,
      'cart_identical_to_steady':read(destination/'capabilities.ndjson.gz',3_000_000)==read(w.home/'corpora/steady/capabilities.ndjson.gz',3_000_000),
      'recovery_history':second.get('recovery'),'independent_model_calls':0,
      'source_code_executed':False,'release_approved':False,'independent_evaluation':False}
    for name,obj in [('steady',steady),('faulted-first',first),('recovered-second',second),('cached-third',third),('queries-before',before_queries),('queries-after',after_queries),('tool-events',{'steady':analyzer.events,'faulted':fault.events}),('SUMMARY',summary)]:
        write(out/(name+'.json'),canonical(obj),new=True)
    print(json.dumps({k:v for k,v in summary.items() if k!='recovery_history'}))
    checks=recovered and n2-n1==1 and n3-n2==0 and semantically_equal and summary['source_bytes_unchanged'] and summary['successful_segment_cache_bytes_unchanged'] and summary['cart_identical_to_steady']
    return 0 if checks or not args.require_recovery else 2
if __name__=='__main__':raise SystemExit(main())
