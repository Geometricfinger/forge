#!/usr/bin/env python3
"""FORGE Workbench: one local home for bounded discovery and tested improvement."""
import argparse,json,os,sys,webbrowser,subprocess
from pathlib import Path
from forge_core.common import *
from forge_core.workspace import initialize,Workbench
from forge_core.policy import template
from forge_core.github import GitHub,Response
from forge_core import demo

def outcome_exit(status):
    return 0 if status in {'COMPLETED_FOR_BOUNDED_MISSION','COMPLETED_NO_MATCH'} else 2

def export_packet(store,mission,out):
    out=safe_path(out)
    if out.exists():raise Blocked('OUTPUT_CONFLICT')
    packet=store.claim(mission)
    if packet is None:raise Blocked('NO_AVAILABLE_REQUEST_CHECK_STATUS')
    write(out,canonical(packet),new=True)
    return {'packet_path':str(out),'kind':packet['kind'],'url':packet['url']}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--home',type=Path,default=Path.home()/'.forge-workbench-0.8.4')
    s=p.add_subparsers(dest='command',required=True)
    s.add_parser('init');a=s.add_parser('serve');a.add_argument('--no-browser',action='store_true')
    a=s.add_parser('new');a.add_argument('--template',choices=['understanding','reliability','evaluation'],default='understanding');a.add_argument('--query');a.add_argument('--brief')
    a=s.add_parser('run');a.add_argument('mission');a.add_argument('--steps',type=int,default=10)
    a=s.add_parser('status');a.add_argument('--mission')
    a=s.add_parser('packet');a.add_argument('mission');a.add_argument('--out',type=Path,required=True)
    a=s.add_parser('accept');a.add_argument('--packet',type=Path,required=True);a.add_argument('--response',type=Path,required=True)
    a=s.add_parser('export');a.add_argument('mission');a.add_argument('--out',type=Path,required=True)
    a=s.add_parser('cancel');a.add_argument('mission')
    s.add_parser('contract-show')
    a=s.add_parser('contract-trial');a.add_argument('--out',type=Path,required=True)
    a=s.add_parser('contract-verify');a.add_argument('folder',type=Path)
    a=s.add_parser('interop-fingerprint');a.add_argument('--input',type=Path,required=True);a.add_argument('--out',type=Path,required=True)
    a=s.add_parser('interop-verify');a.add_argument('--input',type=Path,required=True);a.add_argument('--receipt',type=Path,required=True)
    s.add_parser('opportunity-demo')
    s.add_parser('opportunity-list')
    a=s.add_parser('opportunity-verify');a.add_argument('folder',type=Path)
    a=s.add_parser('opportunity-run');a.add_argument('--bundle',type=Path,required=True);a.add_argument('--corpus-run',action='append',default=[])
    a=s.add_parser('opportunity-show');a.add_argument('run_id')
    a=s.add_parser('opportunity-export');a.add_argument('run_id');a.add_argument('--out',type=Path,required=True)
    a=s.add_parser('opportunity-feedback');a.add_argument('run_id');a.add_argument('--file',type=Path,required=True)
    a=s.add_parser('opportunity-draft');a.add_argument('--file',type=Path,required=True)
    s.add_parser('corpus-demo')
    a=s.add_parser('corpus-run');a.add_argument('--source-root',type=Path,required=True);a.add_argument('--manifest',type=Path,required=True);a.add_argument('--run-id',required=True);a.add_argument('--max-containers',type=int,default=20);a.add_argument('--profile',type=Path,help='data-only exact-API profile JSON (schema 1); default templates/corpus_profile.json')
    a=s.add_parser('corpus-search');a.add_argument('--run-id',required=True);a.add_argument('--query',required=True);a.add_argument('--include-tests',action='store_true');a.add_argument('--out',type=Path)
    a.add_argument('--distinct',action='store_true');a.add_argument('--filters',type=Path)
    a=s.add_parser('corpus-context');a.add_argument('--run-id',required=True);a.add_argument('--definition',required=True);a.add_argument('--out',type=Path)
    s.add_parser('corpus-list')
    s.add_parser('reuse-demo')
    a=s.add_parser('verify-packet');a.add_argument('folder',type=Path)
    a=s.add_parser('review-new');a.add_argument('mission');a.add_argument('--contract',type=Path,required=True)
    a=s.add_parser('review');a.add_argument('case')
    a=s.add_parser('review-note');a.add_argument('case');a.add_argument('--file',type=Path,required=True)
    a=s.add_parser('review-export');a.add_argument('case');a.add_argument('--out',type=Path,required=True)
    s.add_parser('demo');a=s.add_parser('cycle-demo');a.add_argument('--out',type=Path,required=True)
    a=p.parse_args();home=a.home.absolute()
    if a.command=='contract-show':
        from forge_core.interop import contract
        print(json.dumps(contract(),sort_keys=True));return 0
    if a.command=='contract-verify':
        from forge_core.contract_trial import verify_packet
        print(json.dumps(verify_packet(a.folder),sort_keys=True));return 0
    if a.command in {'interop-fingerprint','interop-verify'}:
        from forge_core import interop
        raw=read(a.input,interop.MAX_BYTES)
        if a.command=='interop-fingerprint':
            from forge_core.engine import ROOT
            out=safe_path(a.out)
            if out.is_relative_to(ROOT) or out==safe_path(a.input):raise Blocked('NEW_EXTERNAL_RECEIPT_REQUIRED')
            value=interop.fingerprint(raw);write(out,canonical(value),new=True)
        else:value=interop.verify_record(raw,loads(read(a.receipt,10000)))
        print(json.dumps(value,sort_keys=True));return 0
    if a.command=='opportunity-verify':
        from forge_core.opportunities import verify_export
        print(json.dumps(verify_export(a.folder),sort_keys=True));return 0
    if a.command=='verify-packet':
        from forge_core.reuse import verify_packet
        print(json.dumps(verify_packet(a.folder),sort_keys=True));return 0
    if a.command in ('init','serve','reuse-demo','corpus-demo','corpus-run','opportunity-demo','opportunity-run','contract-trial') and not home.exists():w=initialize(home)
    else:w=Workbench(home)
    w.guard()
    if a.command=='init':value={'status':'INITIALIZED','home':str(home)}
    elif a.command=='contract-trial':
        from forge_core.contract_trial import run
        value=run(w,a.out)
        print(json.dumps(value,sort_keys=True));return 0 if value['status']=='READY_FOR_INTEGRATION_REVIEW' else 2
    elif a.command.startswith('opportunity-'):
        from forge_core.opportunities import OpportunityStore,draft,MAX_INPUT
        from forge_core import corpus
        book=OpportunityStore(w)
        if a.command=='opportunity-demo':
            from forge_core.opportunity_demo import run
            value=run(w)
        elif a.command=='opportunity-list':value={'runs':book.list()}
        elif a.command=='opportunity-show':value=book.get(a.run_id)
        elif a.command=='opportunity-run':
            reports=[(name,corpus.get(w,name)) for name in a.corpus_run]
            value=book.run(loads(read(a.bundle,MAX_INPUT)),reports)
        elif a.command=='opportunity-export':value=book.export(a.run_id,a.out)
        elif a.command=='opportunity-feedback':value=book.feedback(a.run_id,loads(read(a.file,5000)))
        elif a.command=='opportunity-draft':value=draft(loads(read(a.file,30000)))
    elif a.command=='new':
        m=template(a.template)
        if a.query:m['queries']=[a.query]
        if a.brief:m['public_brief']=a.brief
        value={'mission':w.store.create_mission(m)}
    elif a.command=='run':value=w.discovery().run(a.mission,GitHub(os.environ.get('FORGE_GITHUB_TOKEN')),steps=a.steps,seconds=120)
    elif a.command=='status':value=w.store.snapshot(a.mission)
    elif a.command=='packet':
        value=export_packet(w.store,a.mission,a.out)
    elif a.command=='accept':
        packet=loads(read(a.packet));r=loads(read(a.response,3_000_000))
        if set(r)!={'status','body','headers','provenance'}:raise Blocked('RESPONSE_ENVELOPE')
        value=w.discovery().accept(packet,Response(r['status'],canonical(r['body']),r['headers'],r['provenance']))
    elif a.command=='export':value=w.export(a.mission,a.out)
    elif a.command=='cancel':w.store.cancel(a.mission);value={'status':'CANCELLED'}
    elif a.command=='demo':value=demo.run(w)
    elif a.command=='corpus-demo':
        from forge_core.corpus_demo import run
        value=run(w)
    elif a.command=='corpus-run':
        from forge_core import corpus
        import re
        if not re.fullmatch('[A-Za-z0-9_-]{1,80}',a.run_id):raise Blocked('CORPUS_RUN_ID')
        profile=loads(read(a.profile,200_000)) if a.profile else None
        obj=corpus.run(w,a.source_root,a.manifest,w.home/'corpora'/a.run_id,profile=profile,max_containers=a.max_containers)
        value={k:obj[k] for k in ['status','summary','coverage','cache','upstream_code_executed']};value['run_id']=a.run_id
    elif a.command=='corpus-list':
        from forge_core.corpus import list_runs
        value={'corpora':list_runs(w)}
    elif a.command=='corpus-search':
        from forge_core import corpus
        r=corpus.get(w,a.run_id)
        options={'distinct':a.distinct,'include_tests':a.include_tests,'filters':loads(read(a.filters,8000)) if a.filters else None}
        value=corpus.export_search(r,a.query,a.out,**options) if a.out else corpus.search(r,a.query,**options)
    elif a.command=='corpus-context':
        from forge_core import corpus
        value=corpus.dependency_context(corpus.get(w,a.run_id),a.definition)
        if a.out:write(a.out,canonical(value),new=True)
    elif a.command=='review-new':value=w.casebook().create(a.mission,loads(read(a.contract,16000)))
    elif a.command=='review':value=w.casebook().compare(a.case)
    elif a.command=='review-note':value=w.casebook().record(a.case,loads(read(a.file,6000)))
    elif a.command=='review-export':value=w.casebook().export(a.case,a.out)
    elif a.command=='reuse-demo':
        from forge_core.reuse_trial import run
        value=run(w)
    elif a.command=='cycle-demo':
        rc=subprocess.call(w.cycle_command(a.out),env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','HOME','TMPDIR')}|{'PYTHONDONTWRITEBYTECODE':'1'});return rc
    elif a.command=='serve':
        from forge_core.server import Console
        srv=Console(w);url=srv.origin+'/#'+srv.token
        # Fragment is not sent to the HTTP server or provider. Treat the console link as a local secret.
        print('Local operator console: '+url,flush=True)
        if not a.no_browser:webbrowser.open(url)
        try:srv.serve_forever(poll_interval=.2)
        except KeyboardInterrupt:pass
        finally:srv.server_close()
        return 0
    print(json.dumps(value,sort_keys=True));return (0 if value.get('status')=='COMPLETED_FOR_SELECTED_CONTAINERS' else 2) if a.command in {'corpus-run','corpus-demo'} else (outcome_exit(value.get('status')) if a.command in {'run','demo'} else 0)
if __name__=='__main__':
    try:raise SystemExit(main())
    except (Blocked,OSError,ValueError,KeyError) as e:
        print(json.dumps({'status':'BLOCKED','reason':str(e) if isinstance(e,Blocked) else type(e).__name__,'release_approved':False}));raise SystemExit(2)
