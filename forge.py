#!/usr/bin/env python3
"""FORGE Workbench: one local home for bounded discovery and tested improvement."""
import argparse,json,os,sys,webbrowser,subprocess
from pathlib import Path
from forge_core.common import *
from forge_core.workspace import initialize,Workbench
from forge_core.policy import template
from forge_core.github import GitHub,Response
from forge_core import demo,__version__,DEFAULT_HOME_NAME

def outcome_exit(status):
    return 0 if status in {'COMPLETED_FOR_BOUNDED_MISSION','COMPLETED_NO_MATCH'} else 2

def export_packet(store,mission,out):
    out=safe_path(out)
    if out.exists():raise Blocked('OUTPUT_CONFLICT')
    packet=store.claim(mission)
    if packet is None:
        # The refusal itself is a safety rule (one bounded request at a time,
        # cooldowns and budgets). Explain which rule applies instead of
        # returning a bare code; nothing is relaxed here.
        raise PacketUnavailable(packet_refusal(store,mission))
    write(out,canonical(packet),new=True)
    return {'packet_path':str(out),'kind':packet['kind'],'url':packet['url']}

class PacketUnavailable(Blocked):
    def __init__(self,detail):
        super().__init__('NO_AVAILABLE_REQUEST_CHECK_STATUS');self.detail=detail

def packet_refusal(store,mission):
    snap=store.snapshot(mission);counts=snap.get('counts',{})
    detail={'mission':mission,'task_counts':counts,'mission_status':snap.get('status')}
    if snap.get('status')=='CANCELLED' or snap.get('cancelled'):why='The mission was cancelled. Create a new mission with `forge.py new`.'
    elif counts.get('LEASED'):why='A request packet for this mission is already out (only one may be outstanding). Record its answer with `forge.py accept --packet FILE --response FILE`, or wait about 5 minutes for the lease to expire and run `packet` again.'
    elif counts.get('READY'):why='Requests are waiting, but the GitHub provider is cooling down, on hold, or the request budget is used up. Check `forge.py status --mission ID` (next_allowed_at, gaps) and try again later.'
    else:why='This mission has no request left to hand out: every task is DONE or BLOCKED (for example after `forge.py run`). Use `forge.py export MISSION --out DIR` to save the results, or create a new mission.'
    detail['explanation']=why;detail['help']='docs/ERROR_CODES.md#no_available_request_check_status'
    return detail

def draft_source(raw,name):
    """Plain-text notes become one private first-hand source; JSON keeps working."""
    import datetime as dt,re
    textv=raw.decode('utf-8') if isinstance(raw,bytes) else raw
    if textv.lstrip().startswith('{'):return loads(raw)
    key=re.sub('[^A-Za-z0-9_-]+','-',Path(name).stem).strip('-_')[:60] or 'notes'
    if not key[0].isalnum():key='n'+key
    return {'id':'notes-'+key,'text':textv,'kind':'firsthand','public':False,'origin_group':'local-notes',
            'uri':'file-ref://local/'+key,'observed_on':dt.datetime.now(dt.timezone.utc).date().isoformat(),
            'derived_from':[],'capture_method':'attributed_summary'}

def local_manifest(root):
    """Describe every supported file under a plain folder as provider 'local'."""
    from forge_core.corpus import local_rows
    return {'schema':1,'files':local_rows(root)}

def human(value,indent=0,out=None):
    """Readable, indented rendering of a JSON result (lists capped at 20 rows)."""
    top=out is None;out=[] if top else out;pad='  '*indent
    if isinstance(value,dict):
        for k in sorted(value):
            v=value[k]
            if isinstance(v,(dict,list)) and v:out.append(pad+str(k)+':');human(v,indent+1,out)
            else:out.append(pad+str(k)+': '+_scalar(v))
    elif isinstance(value,list):
        for i,v in enumerate(value[:20]):
            if isinstance(v,(dict,list)) and v:out.append(pad+'- ['+str(i+1)+']');human(v,indent+1,out)
            else:out.append(pad+'- '+_scalar(v))
        if len(value)>20:out.append(pad+'... '+str(len(value)-20)+' more (use JSON output for everything)')
    else:out.append(pad+_scalar(value))
    return '\n'.join(out) if top else out

def _scalar(v):
    if v=={} or v==[]:return 'none'
    if isinstance(v,bool):return 'yes' if v else 'no'
    if v is None:return '-'
    t=str(v);return t if len(t)<=600 else t[:597]+'...'

def emit(value):
    mode=OUTPUT_MODE
    if mode=='human':
        st=value.get('status') if isinstance(value,dict) else None
        if st:print('Status: '+str(st))
        print(human({k:v for k,v in value.items() if k!='status'} if st else value))
    elif mode=='pretty':print(json.dumps(value,sort_keys=True,indent=2))
    else:print(json.dumps(value,sort_keys=True))

OUTPUT_MODE='json'

def main():
    global OUTPUT_MODE
    p=argparse.ArgumentParser(description=__doc__,epilog='Output is one line of JSON by default. Add --human (readable summary) or --pretty (indented JSON) anywhere on the command line. Error codes: docs/ERROR_CODES.md');p.add_argument('--version',action='version',version='Forge '+__version__);p.add_argument('--home',type=Path,default=Path.home()/DEFAULT_HOME_NAME)
    p.add_argument('--human',action='store_true',help='print a readable, indented summary instead of JSON');p.add_argument('--pretty',action='store_true',help='print indented JSON')
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
    a=s.add_parser('opportunity-draft',help='turn plain-text notes (or a JSON source record) into draft cues');a.add_argument('--file',type=Path,required=True,help='plain-text notes, or a JSON source record')
    s.add_parser('corpus-demo')
    a=s.add_parser('corpus-run');a.add_argument('--source-root',type=Path,required=True,help='folder holding the sources');a.add_argument('--manifest',type=Path,help='manifest JSON; omit it to analyse every .py/.pyi/.pyw/.ipynb/.zip/.txt/.md/.rst file in --source-root as provider "local"');a.add_argument('--run-id',required=True);a.add_argument('--max-containers',type=int,default=20);a.add_argument('--profile',type=Path,help='data-only exact-API profile JSON (schema 1); default templates/corpus_profile.json')
    a=s.add_parser('corpus-search');a.add_argument('--run-id',required=True);a.add_argument('--query',required=True);a.add_argument('--include-tests',action='store_true');a.add_argument('--out',type=Path)
    a.add_argument('--distinct',action='store_true');a.add_argument('--filters',type=Path)
    a=s.add_parser('corpus-context');a.add_argument('--run-id',required=True);a.add_argument('--definition',required=True);a.add_argument('--out',type=Path)
    s.add_parser('corpus-list')
    a=s.add_parser('make-manifest',help='write a provider "local" manifest for a plain folder');a.add_argument('folder',type=Path);a.add_argument('--out',type=Path,required=True)
    s.add_parser('reuse-demo')
    a=s.add_parser('verify-packet');a.add_argument('folder',type=Path)
    a=s.add_parser('review-new');a.add_argument('mission');a.add_argument('--contract',type=Path,help='requirement contract JSON (default: templates/reuse_contract.json)')
    a=s.add_parser('review');a.add_argument('case')
    a=s.add_parser('review-note');a.add_argument('case');a.add_argument('--file',type=Path,required=True)
    a=s.add_parser('review-export');a.add_argument('case');a.add_argument('--out',type=Path,required=True)
    s.add_parser('demo');a=s.add_parser('cycle-demo');a.add_argument('--out',type=Path,required=True)
    argv=sys.argv[1:];flags={'--human','--pretty'}
    a=p.parse_args([x for x in argv if x not in flags])
    a.human='--human' in argv;a.pretty='--pretty' in argv
    OUTPUT_MODE='human' if a.human else 'pretty' if a.pretty else 'json'
    home=a.home.absolute()
    if a.command=='make-manifest':
        out=safe_path(a.out)
        if out.exists():raise Blocked('OUTPUT_CONFLICT')
        m=local_manifest(a.folder);write(out,canonical(m),new=True)
        emit({'status':'MANIFEST_WRITTEN','manifest':str(out),'files':len(m['files']),'provider':'local'});return 0
    if a.command=='contract-show':
        from forge_core.interop import contract
        emit(contract());return 0
    if a.command=='contract-verify':
        from forge_core.contract_trial import verify_packet
        emit(verify_packet(a.folder));return 0
    if a.command in {'interop-fingerprint','interop-verify'}:
        from forge_core import interop
        raw=read(a.input,interop.MAX_BYTES)
        if a.command=='interop-fingerprint':
            from forge_core.engine import ROOT
            out=safe_path(a.out)
            if out.is_relative_to(ROOT) or out==safe_path(a.input):raise Blocked('NEW_EXTERNAL_RECEIPT_REQUIRED')
            value=interop.fingerprint(raw);write(out,canonical(value),new=True)
        else:value=interop.verify_record(raw,loads(read(a.receipt,10000)))
        emit(value);return 0
    if a.command=='opportunity-verify':
        from forge_core.opportunities import verify_export
        emit(verify_export(a.folder));return 0
    if a.command=='verify-packet':
        from forge_core.reuse import verify_packet
        emit(verify_packet(a.folder));return 0
    if a.command in ('init','serve','reuse-demo','corpus-demo','corpus-run','opportunity-demo','opportunity-run','opportunity-draft','new','contract-trial') and not home.exists():w=initialize(home)
    else:w=Workbench(home)
    w.guard()
    if a.command=='init':value={'status':'INITIALIZED','home':str(home)}
    elif a.command=='contract-trial':
        from forge_core.contract_trial import run
        value=run(w,a.out)
        emit(value);return 0 if value['status']=='READY_FOR_INTEGRATION_REVIEW' else 2
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
        elif a.command=='opportunity-draft':value=draft(draft_source(read(a.file,30000),a.file.name))
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
        manifest=a.manifest
        if manifest is None:
            # Plain folder: write the generated manifest inside the Forge home
            # (never into the source folder) so the run stays reproducible.
            mdir=w.home/'manifests';mdir.mkdir(mode=0o700,exist_ok=True)
            manifest=mdir/(a.run_id+'.json')
            data=canonical(local_manifest(a.source_root))
            if manifest.exists() and read(manifest,2_000_000)!=data:raise Blocked('CORPUS_BINDING_CHANGED_NEW_RUN_REQUIRED')
            if not manifest.exists():write(manifest,data,new=True)
        obj=corpus.run(w,a.source_root,manifest,w.home/'corpora'/a.run_id,profile=profile,max_containers=a.max_containers)
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
    elif a.command=='review-new':
        from forge_core.engine import ROOT
        value=w.casebook().create(a.mission,loads(read(a.contract or ROOT/'templates/reuse_contract.json',16000)))
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
    emit(value);return (0 if value.get('status')=='COMPLETED_FOR_SELECTED_CONTAINERS' else 2) if a.command in {'corpus-run','corpus-demo'} else (outcome_exit(value.get('status')) if a.command in {'run','demo'} else 0)
if __name__=='__main__':
    try:raise SystemExit(main())
    except (Blocked,OSError,ValueError,KeyError) as e:
        out={'status':'BLOCKED','reason':str(e) if isinstance(e,Blocked) else type(e).__name__,'release_approved':False}
        if isinstance(e,PacketUnavailable):out['detail']=e.detail
        if isinstance(e,OSError) and getattr(e,'filename',None):out['file']=str(e.filename)
        if OUTPUT_MODE=='human':out['help']='See docs/ERROR_CODES.md for what '+out['reason']+' means and how to fix it.'
        emit(out);raise SystemExit(2)
