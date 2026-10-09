"""Finite resumable mission controller using a dependency graph and immutable outputs.

This is ordinary software automation. A human reviewer must separately interpret
its evidence. No source can supply commands or expand its own permissions.
"""
from __future__ import annotations
import argparse, html, sys, time
from pathlib import Path
from .core import *
from .bridge import verify_base,inspect_source,investigate
ROOT=Path(__file__).resolve().parents[1]

def tool_binding():
    return {str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in sorted((ROOT/'research_runner').glob('*.py'))}

def render(r):
    e=html.escape;parts=[]
    for q in r['queries']:
        entries=''.join('<li><strong>'+e(x['name'])+'</strong><br>'+e(x['path'])+' · '+e(', '.join(x['channels']))+'<pre>'+e(x['snippet'])+'</pre></li>' for x in q['results'])
        refs=''.join('<li>'+e(x['kind'])+': '+e(x['title'])+'<p>'+e(x['text'])+'</p></li>' for x in q['references'])
        parts.append('<article><h2>'+e(q['query'])+'</h2><p class="state">'+e(q['status'])+'</p><ul>'+entries+refs+'</ul><p>'+e(q['next_action'])+'</p></article>')
    return '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>FORGE Research Runner — recorded results</title><style>body{font:16px system-ui;background:#101a25;color:#eef3f8;max-width:1060px;margin:40px auto;padding:20px}h1{font-size:38px}article{background:#1b2b3b;padding:24px;border-radius:16px;margin:18px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;background:#10202d;padding:14px}.state{color:#97d8dd}p{line-height:1.55}li{margin:16px 0}</style></head><body><h1>FORGE · Research Runner</h1><p>Recorded execution results. No live agent, crawler or deployment is running.</p><p>'+e(r['objective'])+'</p><p>'+e(r['status'])+' · '+str(len(r['queries']))+' questions · runtime and novelty unverified</p>'+''.join(parts)+'</body></html>'

def run(manifest,source_root,work,forge_root,max_steps=1000,stop_after_checkpoint=None):
    check_int(max_steps,0,1000);m=check_manifest(manifest);work=no_links(work);source_root=no_links(source_root);forge_root=no_links(forge_root)
    for p in (source_root,forge_root,ROOT):
        if work==p or work.is_relative_to(p) or p.is_relative_to(work):raise Blocked('SEPARATE_WORKSPACE_REQUIRED')
    work.mkdir(parents=True,exist_ok=True,mode=0o700)
    binding={'tool':tool_binding(),'forge':verify_base(forge_root),'manifest':digest(m),'source_root':str(source_root)}
    key=digest(binding);atomic_new(work/'binding.json',canonical(binding))
    graph={**{'source:'+s['id']:[] for s in m['sources']}, **{'query:'+q['id']:['source:'+s for s in q['sources']] for q in m['questions']}}
    graph['assemble']=['query:'+q['id'] for q in m['questions']];order=make_order(graph)
    source_map={s['id']:s for s in m['sources']};question_map={q['id']:q for q in m['questions']};completed={};new=0;start=time.monotonic()
    with locked(work):
        cache=Checkpoints(work/'checkpoints',key)
        # Verify the actual original sources even when all tasks have checkpoints.
        source_errors={}
        for s in m['sources']:
            try:read_source(source_root,s)
            except Blocked as ex:source_errors[s['id']]=str(ex)
        if source_errors and any((work/'checkpoints').iterdir()):raise Blocked('SOURCE_CHANGED_OR_MISSING_ON_RESUME')
        for task in order:
            if (work/'CANCEL.json').exists():
                atomic_state(work/'progress.json',canonical({'state':'CANCELED','completed':list(completed),'new_tasks':new}));return {'status':'CANCELED','new_tasks':new,'completed_tasks':len(completed)}
            saved=cache.get(task)
            if saved is not None:completed[task]=saved;continue
            if new>=max_steps:
                result={'status':'PAUSED_BUDGET','new_tasks':new,'completed_tasks':len(completed),'remaining_tasks':len(order)-len(completed)}
                atomic_state(work/'progress.json',canonical(result));return result
            if not all(d in completed for d in graph[task]):raise Blocked('DEPENDENCY_NOT_COMPLETE')
            if task.startswith('source:'):
                s=source_map[task.split(':',1)[1]]
                try:output=inspect_source(s,source_root,work,forge_root)
                except Exception as ex:
                    output={'status':'BLOCKED','source':s,'records':[],'references':[],'gaps':[str(ex) if isinstance(ex,ValueError) else type(ex).__name__],'source_code_executed':False}
            elif task.startswith('query:'):
                q=question_map[task.split(':',1)[1]]
                output=investigate(q,[completed['source:'+s] for s in q['sources']],forge_root)
            else:
                output={'schema':1,'objective':m['objective'],'status':'READY_FOR_SUPERVISOR_REVIEW',
                  'sources':[{'id':s['id'],'status':completed['source:'+s['id']]['status'],'sha256':s['sha256'],'origin':s['origin'],
                     'capture':s['capture'],'definition_count':len(completed['source:'+s['id']]['records']),'gaps':completed['source:'+s['id']]['gaps']} for s in m['sources']],
                  'queries':[completed['query:'+q['id']] for q in m['questions']],
                  'authority':{'execute_source':False,'modify_baseline':False,'deploy':False,'market_validated':False,'novelty_established':False},
                  'model_calls':0,'independent_evaluation':False,'tool_binding':key}
                if any(s['status']=='BLOCKED' for s in output['sources']):output['status']='INCOMPLETE_SOURCE_EVIDENCE'
            cache.put(task,output);completed[task]=output;new+=1
            atomic_state(work/'progress.json',canonical({'state':'RUNNING','completed':list(completed),'new_tasks':new,'active_task':None}))
            if stop_after_checkpoint is not None:stop_after_checkpoint(task,new)
        # Do not publish after a source/tool mutation.
        for s in m['sources']:
            if s['id'] not in source_errors:read_source(source_root,s)
        if binding['tool']!=tool_binding():raise Blocked('RUNNER_CHANGED_DURING_RUN')
        verify_base(forge_root)
        result=completed['assemble'];packet=compact_packet(result,m['budgets']['packet_bytes'])
        atomic_new(work/'report.json',canonical(result));atomic_new(work/'supervisor_packet.json',canonical(packet));atomic_new(work/'Review.html',render(result).encode())
        receipt={'report_sha256':digest(result),'packet_sha256':digest(packet),'packet_bytes':len(canonical(packet)),
          'completed_tasks':len(completed),'new_tasks':new,'elapsed_seconds':time.monotonic()-start,'source_code_executed':False,'model_calls':0,'status':result['status']}
        atomic_state(work/'progress.json',canonical(receipt));return receipt

def main():
    ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest='cmd',required=True)
    a=sub.add_parser('run');a.add_argument('--manifest',type=Path,required=True);a.add_argument('--source-root',type=Path,required=True);a.add_argument('--work',type=Path,required=True);a.add_argument('--forge-root',type=Path,required=True);a.add_argument('--steps',type=int,default=1000)
    for cmd in ('status','cancel'):
        x=sub.add_parser(cmd);x.add_argument('--work',type=Path,required=True)
    args=ap.parse_args()
    if args.cmd=='run':result=run(strict_loads(args.manifest.read_bytes()),args.source_root,args.work,args.forge_root,args.steps)
    elif args.cmd=='status':result=strict_loads(no_links(args.work/'progress.json').read_bytes())
    else:
        if not (args.work/'binding.json').exists():raise Blocked('UNKNOWN_MISSION')
        atomic_new(args.work/'CANCEL.json',canonical({'canceled':True}));result={'status':'CANCEL_REQUESTED'}
    print(canonical(result).decode());return 0 if result.get('status') in ('READY_FOR_SUPERVISOR_REVIEW','CANCEL_REQUESTED','CANCELED') else 2
if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as ex:print(canonical({'status':'BLOCKED','reason':str(ex) if isinstance(ex,ValueError) else type(ex).__name__}).decode());raise SystemExit(2)
