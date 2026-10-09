"""Reproduce a bounded, supervised profile-improvement cycle with existing FORGE.

The three supplied proposals were authored by contributors. This command is a
replay using real scans and comparisons, not autonomous idea generation.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse,json,os,subprocess,sys,time
from safeio import *
import forge_cycle as cycle
from prepare_runtime import prepare
ROOT=Path(__file__).resolve().parent
EX=ROOT/'examples'


def command_stage(name,cmd,out,expected_tests):
    log=out/(name+'.log');target=out/name
    with log.open('xb') as stream:
        p=subprocess.run(cmd+['--out',str(target)],stdout=stream,stderr=subprocess.STDOUT,timeout=180,
                         env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    r=strict_json(read(target/'tests.json'))
    if p.returncode or r.get('tests_run')!=expected_tests or any(r.get(k)!=0 for k in ('failures','errors','skips')):
        raise Blocked('REGRESSION_STAGE_FAILED:'+name)
    return {'stage':name,'tests_run':r['tests_run'],'exit_code':p.returncode,'artifact_sha256':digest(read(target/'tests.json'))}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--resume',action='store_true');p.add_argument('--steps',type=int,default=4)
    p.add_argument('--runtime',type=Path);p.add_argument('--skip-regression',action='store_true',help='Use only for a separately recorded integration run; no test pass will be claimed.')
    a=p.parse_args();integer(a.steps,'STEP_BUDGET',1,4);out=no_links(a.out).resolve()
    if out.is_relative_to(ROOT) or ROOT.is_relative_to(out):raise Blocked('SEPARATE_OUTPUT_REQUIRED')
    if not a.resume:
        if out.exists():raise Blocked('NEW_DEMO_DIRECTORY_REQUIRED')
        out.mkdir(parents=True,mode=0o700)
        if a.runtime:runtime=strict_json(read(a.runtime))
        else:runtime=prepare(out/'runtime')
        metadata={'schema':1,'runtime':runtime,'started_utc':datetime.now(timezone.utc).isoformat(),
                  'steps_complete':0,'regression_stages':[],'demo_binding':file_binding([*ROOT.glob('*.py'),*EX.glob('*.json'),*list((ROOT/'tests').glob('*.py'))])}
        write_new(out/'demo.json',canonical(metadata))
        if not a.skip_regression:
            for name,cmd,count in [
              ('hound-tests',[sys.executable,str(Path(runtime['hound'])/'run_tests.py')],483),
              ('addon-tests',[sys.executable,str(Path(runtime['addon'])/'tool/test_addon.py'),'--hound',runtime['hound']],69),
              ('controller-tests',[sys.executable,str(ROOT/'run_tests.py'),'--runtime',str(a.runtime or out/'runtime/runtime.json')],EXPECTED_CONTROLLER_TESTS)]:
                metadata['regression_stages'].append(command_stage(name,cmd,out,count));atomic_json(out/'demo.json',metadata)
        obj=lambda n:strict_json(read(EX/n))
        store=cycle.initialize(out/'cycle',runtime,Path(runtime['hound']),obj('mission.json'),obj('acceptance.json'),obj('self_sources.json'),obj('baseline_profile.json'))
    else:
        metadata=strict_json(read(out/'demo.json'));check_binding(metadata['demo_binding']);store=cycle.Store(out/'cycle')
    for _ in range(a.steps):
        status=store.status()
        if status['status']=='READY_FOR_ADOPTION_REVIEW':break
        if status['status']=='AWAITING_PROFILE_PROPOSAL':
            n=len(status['candidates'])-1
            names=['01-noisy','02-partial','03-complete']
            if n>=len(names):raise Blocked('NO_MORE_APPROVED_DEMO_PROPOSALS')
            write_new(out/f'agent-request-{n+1}.json',canonical(store.packet()))
            store.submit(strict_json(read(EX/f'proposal-{names[n]}.json')))
        result=cycle.run_jobs(store,100,2)
        step=len(result['candidates'])-1
        atomic_json(out/f'step-{step}.json',result)
        if result['status'] in {'WORK_REMAINS','BLOCKED_TASK_FAILURE','BUDGET_EXHAUSTED'}:raise Blocked('INCOMPLETE_DEMO_STEP')
        metadata['steps_complete']=step+1;atomic_json(out/'demo.json',metadata)
    status=store.status()
    if status['status']=='READY_FOR_ADOPTION_REVIEW':
        if not (out/'review').exists():cycle.export_review(store,out/'review')
        metadata['finished_utc']=datetime.now(timezone.utc).isoformat()
        metadata['status']='SUPERVISED_PROFILE_CYCLE_REPRODUCED';metadata['result']=status
    else:
        metadata['status']='PAUSED_AT_STEP_BOUNDARY';metadata['result']=status
    check_binding(metadata['demo_binding']);atomic_json(out/'demo.json',metadata)
    print(json.dumps({'status':metadata['status'],'completed_steps':metadata['steps_complete'],
                      'completed_tasks':status['completed_tasks'],
                      'candidate_verdicts':[r['verdict'] for r in status['candidates']],
                      'regression_stages':metadata['regression_stages'],'release_approved':False,
                      'independent_evaluation':False,'model_calls':0,'network_requests':0},sort_keys=True))
    return 0 if metadata['status']=='SUPERVISED_PROFILE_CYCLE_REPRODUCED' else 2

EXPECTED_CONTROLLER_TESTS=72
if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,OSError,KeyError,subprocess.TimeoutExpired) as e:
        print(json.dumps({'status':'BLOCKED','reason':str(e) if isinstance(e,Blocked) else type(e).__name__,'release_approved':False}));raise SystemExit(2)
