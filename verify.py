#!/usr/bin/env python3
"""Qualify the packaged workbench, inherited loop, and actual scanner. No live HTTP."""
import argparse,json,os,signal,subprocess,sys,time
from pathlib import Path
from datetime import datetime,timezone
from forge_core.common import *
from forge_core.workspace import initialize,code_binding
from forge_core import demo
ROOT=Path(__file__).resolve().parent

def command(cmd,cwd,log,timeout=360):
    env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','HOME','TMPDIR')};env['PYTHONDONTWRITEBYTECODE']='1'
    with log.open('xb') as f:
        p=subprocess.Popen(cmd,cwd=cwd,env=env,stdout=f,stderr=subprocess.STDOUT,start_new_session=os.name=='posix')
        try:code=p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name=='posix':os.killpg(p.pid,signal.SIGKILL)
            else:p.kill()
            p.wait();raise Blocked('QUALIFICATION_TIMEOUT')
    if code:raise Blocked('QUALIFICATION_COMMAND_FAILED')

def main():
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('--out',type=Path,required=True);args=a.parse_args();out=safe_path(args.out)
    if out.exists() or out.is_relative_to(ROOT):raise Blocked('NEW_EXTERNAL_OUTPUT_REQUIRED')
    out.mkdir(parents=True);report={'started_utc':datetime.now(timezone.utc).isoformat(),'status':'RUNNING','model_calls':0,'live_network_requests':0,'release_approved':False,'independent_evaluation':False};before=code_binding()
    try:
        command([sys.executable,str(ROOT/'run_tests.py'),'--out',str(out/'workbench-tests')],ROOT,out/'workbench-tests.log',150)
        t=loads(read(out/'workbench-tests/tests.json'))
        if t['tests_run']!=785 or any(t[k] for k in ('failures','errors','skips')):raise Blocked('WORKBENCH_TESTS_FAILED')
        report['workbench_tests']=t
        w=initialize(out/'home')
        from forge_core import corpus,corpus_demo
        mixed=corpus_demo.run(w);mixed_report=corpus.get(w,mixed['run_id']);matched=corpus.search(mixed_report,'digest')
        if mixed_report['summary']['hound_observations']!=3 or matched['full_match_count']!=2 or mixed_report['upstream_code_executed'] is not False:raise Blocked('MIXED_CORPUS_QUALIFICATION')
        grouped=corpus.search(mixed_report,'digest',distinct=True,filters={'required_apis':['hashlib.sha256']})
        if len(grouped['results'])!=1 or grouped['total_matching_occurrences']!=2 or len(grouped['results'][0]['occurrences'])!=2:raise Blocked('RESEARCH_GROUP_QUALIFICATION')
        context=corpus.dependency_context(mixed_report,grouped['results'][0]['definition_id'])
        if context['dependency_closure_complete'] is not False or context['source_code_executed'] is not False:raise Blocked('RESEARCH_CONTEXT_AUTHORITY')
        report['research_retrieval']={'ranker':grouped['ranker_version'],'matching_occurrences':2,'displayed_groups':1,'context_status':context['status'],'no_runtime_promotion':True}
        packet=corpus.export_search(mixed_report,'digest',out/'corpus-packet')
        from forge_core.opportunity_demo import run as opportunity_run
        from forge_core.opportunities import OpportunityStore,verify_export
        opportunity=opportunity_run(w);book=OpportunityStore(w);opp=book.get(opportunity['run'])
        states={h['workflow']:h['state'] for h in opp['hypotheses']}
        if states!={'handoff':'SINGLE_SOURCE_PROBLEM','attestation':'INFERRED_HANDOFF_GAP','recovery':'INFERRED_HANDOFF_GAP','tracking':'EXISTING_OPTION_TO_VERIFY'}:raise Blocked('OPPORTUNITY_CASE_STATES')
        if opp['authority']['market_validated'] or opp['summary']['code_lead_count']==0:raise Blocked('OPPORTUNITY_AUTHORITY')
        book.export(opp['id'],out/'opportunity-packet');report['opportunity_packet']=verify_export(out/'opportunity-packet')
        repeated=opportunity_run(w)
        if repeated!=opportunity or book.get(opp['id'])!=opp:raise Blocked('OPPORTUNITY_REPEATABILITY')
        report['opportunity_case_study']=opportunity
        report['mixed_corpus']={'demo':mixed,'matching_definitions':matched['full_match_count'],'packet':packet,'actual_hound':True,'provider':'synthetic'}
        r=demo.run(w)
        report['discovery_demo']={'status':r['status'],'source_count':len(r['candidates']),'requests':r['counts']['DONE'],'findings':sum(len(c['findings']) for c in r['candidates']),'transport':'synthetic_fixture','actual_hound':True}
        w.export(r['id'],out/'demo-report')
        from forge_core.reuse_trial import run as run_reuse
        from forge_core.reuse import verify_packet
        reuse=run_reuse(w);write(out/'reuse-trial.json',canonical(reuse),new=True)
        w.casebook().export(reuse['case_id'],out/'reuse-packet')
        report['reuse_packet']=verify_packet(out/'reuse-packet')
        report['reuse_trial']={'status':reuse['status'],'acquisition_stages':reuse['acquisition_stages'],'verdicts':reuse['verdicts'],'test_executions':len(reuse['test_results']['results']),'function_invocations':reuse['test_results']['function_invocations'],'model_calls':0,'external_code_executed':False}
        command(w.cycle_command(out/'controlled-cycle'),ROOT,out/'controlled-cycle.log')
        c=loads(read(out/'controlled-cycle/demo.json',8_000_000));expected={'hound-tests':483,'addon-tests':69,'controller-tests':72};suites=[]
        for stage,n in expected.items():
            p=out/'controlled-cycle'/stage/'tests.json';v=loads(read(p));tests=v.get('tests_run',v.get('tests'))
            if tests!=n or any(v[k] for k in ('failures','errors','skips')):raise Blocked('INHERITED_TEST_FAILURE')
            suites.append({'stage':stage,'tests':n,'failures':0,'errors':0,'skips':0,'sha256':sha(read(p))})
        verdicts=[x['verdict']['status'] for x in c['result']['candidates']]
        if c['status']!='SUPERVISED_PROFILE_CYCLE_REPRODUCED' or verdicts!=['BASELINE_MEASURED','REJECTED','IMPROVED_BUT_INCOMPLETE','READY_FOR_ADOPTION_REVIEW'] or c['result']['completed_tasks']!=128:raise Blocked('CYCLE_VERDICTS_FAILED')
        if c['result']['adopted'] is not False or c['result']['release_approved'] is not False:raise Blocked('UNAUTHORIZED_PROMOTION')
        report['inherited_suites']=suites;report['cycle']={'tasks_completed':128,'verdicts':verdicts,'case_scores':[x['verdict']['correct_cases'] for x in c['result']['candidates']]}
        from forge_core.contract_trial import run as contract_run,verify_packet as verify_contract_packet
        integration=contract_run(w,out/'contract-integration')
        report['contract_integration']={'status':integration['status'],'external_vectors':integration['external_vector_report']['summary'],'external_case_count':integration['external_vector_report']['case_count'],'reference':integration['reference_report'],'admission_cases':integration['admission_report']['case_count'],'discovery':integration['discovery']['summary'],'handoff':integration['handoff'],'reviewed_public_component_executed':True}
        report['contract_packet']=verify_contract_packet(out/'contract-integration')
        if integration['status']!='READY_FOR_INTEGRATION_REVIEW':raise Blocked('CONTRACT_INTEGRATION_INCOMPLETE')
        command([sys.executable,str(ROOT/'tools/run_contract_fault_checks.py'),'--out',str(out/'contract-faults')],ROOT,out/'contract-faults.log',60)
        faults=loads(read(out/'contract-faults/summary.json'))
        if faults.get('status')!='DETECTED_ALL_FIXED_FAULTS':raise Blocked('CONTRACT_FAULT_CHECKS_FAILED')
        report['contract_faults']=faults
        command([sys.executable,str(ROOT/'tools/run_self_recovery_trial.py'),'--out',str(out/'self-recovery'),'--require-recovery'],ROOT,out/'self-recovery.log',150)
        report['self_recovery']=loads(read(out/'self-recovery/SUMMARY.json',2_000_000))
        if before!=code_binding():raise Blocked('PACKAGE_CHANGED')
        report['package_unchanged']=True;report['status']='PASSED_FOR_LOCAL_DECLARED_SCOPE'
    except Exception as e:
        report['status']='BLOCKED';report['reason']=str(e) if isinstance(e,Blocked) else type(e).__name__
    report['finished_utc']=datetime.now(timezone.utc).isoformat();write(out/'verification.json',canonical(report),new=True);print(json.dumps(report))
    return 0 if report['status']=='PASSED_FOR_LOCAL_DECLARED_SCOPE' else 2
if __name__=='__main__':
    try:raise SystemExit(main())
    except (Blocked,OSError) as e:print(str(e));raise SystemExit(2)
