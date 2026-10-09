"""Controller checks. Fixed synthetic source is parsed, never executed."""
from pathlib import Path
import copy,json,os,random,sqlite3,sys,tempfile,threading,unittest
from concurrent.futures import ThreadPoolExecutor
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import forge_cycle as f
from safeio import *
RUNTIME=strict_json(read(os.environ['FORGE_TEST_RUNTIME']))
P={'schema':1,'id':'test-profile','title':'Compiler and source evidence','targets':[{'api':'ast.parse','capability':'syntax'}]}
M={'schema':1,'id':'test-mission','requirement':'Find syntax and digest calls, not serializer calls.','allowed_api_roots':['hashlib','json'],'max_candidates':3,'max_added_targets':4,'minimum_gain':1}
MATCH=lambda name,api,cap:{'scope':'function','name':name,'api':api,'capability':cap}
C={'schema':1,'cases':[
 {'id':'syntax','code':'import ast\ndef f(x):\n return ast.parse(x)\n','expected':[MATCH('f','ast.parse','syntax')]},
 {'id':'digest','code':'import hashlib\ndef f(x):\n return hashlib.sha256(x)\n','expected':[MATCH('f','hashlib.sha256','digest')]},
 {'id':'serialize','code':'import json\ndef f(x):\n return json.dumps(x)\n','expected':[]},
 {'id':'shadow','code':'import hashlib\ndef f(hashlib,x):\n return hashlib.sha256(x)\n','expected':[]} ]}
SOURCE=b'import ast,hashlib\ndef a(x):\n return ast.parse(x)\ndef b(x):\n return hashlib.sha256(x)\n'
class Clock:
 def __init__(self):self.value=1000.
 def __call__(self):return self.value

def proposal(targets=None):
 return {'schema':1,'baseline_profile_sha256':digest(canonical(P)),'reason':'Inspect the declared digest call.','add_targets':targets or [{'api':'hashlib.sha256','capability':'digest'}],'source_references':['self:example']}

def drain(store):
 probe=f.probe_module(RUNTIME)
 while (t:=store.claim()) is not None:
  if t['kind']=='case':
   row=next(x for x in store.spec['contract']['cases'] if x['id']==t['input_id']);data=row['code'].encode();sid='fixture:'+row['id']
  else:row=store.spec['sources']['sources'][0];data=f.source_bytes(store.spec,row);sid=row['source_id']
  r=probe.scan(data,sid,t['profile'],Path(RUNTIME['hound']));store.finish(t,r)
 store.finalize()

class JobCase(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.src=self.root/'src';self.src.mkdir()
  self.source=self.src/'example.py';self.source.write_bytes(SOURCE)
  self.sources={'schema':1,'sources':[{'source_id':'self:example','path':'example.py','sha256':digest(SOURCE)}]}
  self.job=self.root/'job';self.clock=Clock()
  self.s=f.initialize(self.job,RUNTIME,self.src,copy.deepcopy(M),copy.deepcopy(C),copy.deepcopy(self.sources),copy.deepcopy(P))
  self.s.clock=self.clock
 def tearDown(self):self.temp.cleanup()
 def baseline(self):drain(self.s)
 def test_initial_budget_does_not_charge_queued_work(self):
  q=self.s.status();self.assertEqual(q['attempts'],0);self.assertEqual(q['tasks'],{'QUEUED':5})
 def test_waits_for_measured_baseline(self):
  with self.assertRaisesRegex(ValueError,'BASELINE_NOT'):self.s.submit(proposal())
 def test_baseline_measurement(self):
  self.baseline();q=self.s.status();self.assertEqual(q['status'],'AWAITING_PROFILE_PROPOSAL');self.assertEqual(q['candidates'][0]['verdict']['correct_cases'],3)
 def test_positive_improvement_reaches_review_not_adoption(self):
  self.baseline();self.s.submit(proposal());drain(self.s);q=self.s.status()
  self.assertEqual(q['status'],'READY_FOR_ADOPTION_REVIEW');self.assertFalse(q['adopted']);self.assertFalse(q['release_approved']);self.assertEqual(q['candidates'][-1]['verdict']['correct_cases'],4)
 def test_more_hits_with_regression_rejected(self):
  self.baseline();self.s.submit(proposal([{'api':'hashlib.sha256','capability':'digest'},{'api':'json.dumps','capability':'digest'}]));drain(self.s)
  v=self.s.status()['candidates'][-1]['verdict'];self.assertEqual(v['status'],'REJECTED');self.assertEqual(v['required_regressions'],['serialize'])
 def test_handoff_does_not_include_test_source(self):
  self.baseline();p=self.s.packet();self.assertNotIn('contract',p);self.assertTrue(all(case['code'] not in canonical(p).decode() for case in C['cases']));self.assertEqual(p['status'],'REQUEST_FOR_EXTERNAL_AGENT_NOT_DISPATCHED')
 def test_repeated_submission_idempotent(self):
  self.baseline();first=self.s.submit(proposal());second=self.s.submit(proposal());self.assertEqual(first['id'],second['id']);self.assertTrue(second['duplicate'])
 def test_evaluate_one_candidate_before_next(self):
  self.baseline();self.s.submit(proposal())
  with self.assertRaisesRegex(ValueError,'EVALUATE_CURRENT'):self.s.submit(proposal([{'api':'json.dumps','capability':'serializer'}]))
 def test_stale_proposal_rejected(self):
  self.baseline();p=proposal();p['baseline_profile_sha256']='0'*64
  with self.assertRaisesRegex(ValueError,'STALE_PROPOSAL'):self.s.submit(p)
 def test_source_references_required(self):
  self.baseline();p=proposal();p['source_references']=['invented']
  with self.assertRaisesRegex(ValueError,'UNBOUND'):self.s.submit(p)
 def test_commands_cannot_be_submitted(self):
  self.baseline();p=proposal();p['command']='touch SENTINEL'
  with self.assertRaisesRegex(ValueError,'PROPOSAL_SCHEMA'):self.s.submit(p)
 def test_cannot_remove_old_target(self):
  self.baseline();p=proposal();p['replace_targets']=[]
  with self.assertRaises(ValueError):self.s.submit(p)
 def test_cannot_relabel_existing_target(self):
  self.baseline()
  with self.assertRaises(ValueError):self.s.submit(proposal([{'api':'ast.parse','capability':'definitely_secure'}]))
 def test_scope_restriction(self):
  self.baseline()
  with self.assertRaisesRegex(ValueError,'TARGET_SCOPE'):self.s.submit(proposal([{'api':'os.system','capability':'execute'}]))
 def test_unknown_authority_rejected(self):
  self.baseline();p=proposal();p['release_approved']=True
  with self.assertRaises(ValueError):self.s.submit(p)
 def test_empty_addition_rejected(self):
  self.baseline();p=proposal();p['add_targets']=[]
  with self.assertRaises(ValueError):self.s.submit(p)
 def test_new_submission_stops_after_success(self):
  self.baseline();self.s.submit(proposal());drain(self.s)
  with self.assertRaisesRegex(ValueError,'ALREADY_SATISFIED'):self.s.submit(proposal([{'api':'json.dumps','capability':'serializer'}]))
 def test_claim_expiry_rejects_old_worker(self):
  old=self.s.claim(2);self.clock.value+=3;new=self.s.claim(2);self.assertEqual(old['id'],new['id'])
  with self.assertRaisesRegex(ValueError,'STALE_WORKER'):self.s.finish(old,{'claim':'old'})
  self.s.finish(new,{'claim':'new'})
 def test_heartbeat_extends_live_claim(self):
  t=self.s.claim(2);self.clock.value+=1;self.s.heartbeat(t,5);self.clock.value+=2;self.s.finish(t,{'ok':1})
 def test_heartbeat_cannot_revive_expired_claim(self):
  t=self.s.claim(2);self.clock.value+=3
  with self.assertRaisesRegex(ValueError,'STALE_WORKER'):self.s.heartbeat(t)
 def test_heartbeat_rejects_unbounded_lease(self):
  t=self.s.claim()
  with self.assertRaises(ValueError):self.s.heartbeat(t,1000000)
 def test_heartbeat_rejects_boolean_lease(self):
  t=self.s.claim()
  with self.assertRaises(ValueError):self.s.heartbeat(t,True)
 def test_unstarted_tasks_retain_retry_budget(self):
  t=self.s.claim(2);self.clock.value+=3;t=self.s.claim(2);self.clock.value+=3;self.s.claim(2)
  with self.s.transaction() as c:rows=c.execute('SELECT status,attempts FROM tasks').fetchall()
  self.assertEqual(sum(r['status']=='FAILED' for r in rows),1);self.assertEqual(sum(r['attempts']==0 for r in rows),3)
 def test_retry_cap_stops_failed_parser(self):
  t=self.s.claim();self.s.fail(t,'test');t=self.s.claim();self.s.fail(t,'test')
  self.assertEqual(self.s.status()['status'],'BLOCKED_TASK_FAILURE')
 def test_concurrent_claims_are_distinct(self):
  with ThreadPoolExecutor(3) as p:tasks=list(p.map(lambda _:f.Store(self.job,self.clock).claim(),range(5)))
  self.assertEqual(len({t['id'] for t in tasks}),5)
 def test_restart_preserves_claim_and_result(self):
  t=self.s.claim();self.s.finish(t,{'saved':1});other=f.Store(self.job,self.clock)
  with other.transaction() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM tasks WHERE status="DONE"').fetchone()[0],1)
 def test_event_corruption_rejected(self):
  with self.s.transaction() as c:c.execute('UPDATE events SET payload=? WHERE seq=1',(canonical({'forged':1}),))
  with self.assertRaisesRegex(ValueError,'EVENT_CHAIN'):self.s.guard()
 def test_result_corruption_rejected(self):
  t=self.s.claim();self.s.finish(t,{'saved':1})
  with self.s.transaction() as c:c.execute('UPDATE tasks SET result=? WHERE id=?',(b'{}',t['id']))
  with self.assertRaisesRegex(ValueError,'RESULT_DIGEST'):self.s.guard()
 def test_forged_valid_hash_cannot_become_evaluation(self):
  t=self.s.claim();self.s.finish(t,{'case_count':100,'passed':True,'release_approved':True})
  # Complete remaining work; the malformed result must be refused at evaluation.
  with self.assertRaises(ValueError):drain(self.s)
 def test_frozen_tests_changed(self):
  q=copy.deepcopy(self.s.spec);q['contract']['cases'][0]['expected']=[];(self.job/'spec.json').write_bytes(canonical(q))
  with self.assertRaises(ValueError):self.s.status()
 def test_source_change_is_not_a_cache_hit(self):
  self.baseline();self.source.write_bytes(SOURCE+b'\n# changed')
  with self.assertRaisesRegex(ValueError,'SOURCE_VERSION'):self.s.status()
 def test_source_symlink_rejected(self):
  self.source.unlink();self.source.symlink_to(self.root/'outside.py');(self.root/'outside.py').write_bytes(SOURCE)
  with self.assertRaises(ValueError):self.s.guard()
 def test_ledger_symlink_rejected(self):
  self.s.path.rename(self.root/'outside.sqlite');self.s.path.symlink_to(self.root/'outside.sqlite')
  with self.assertRaises(ValueError):f.Store(self.job)
 def test_partial_task_budget_is_not_complete(self):
  r=f.run_jobs(self.s,max_tasks=0,workers=1);self.assertEqual(r['status'],'WORK_REMAINS');self.assertEqual(r['this_run']['claims'],0)
 def test_worker_limit(self):
  with self.assertRaises(ValueError):f.run_jobs(self.s,0,4)
 def test_task_budget_boolean_rejected(self):
  with self.assertRaises(ValueError):f.run_jobs(self.s,True,1)
 def test_export_does_not_include_original_bodies(self):
  self.baseline();out=self.root/'export';f.export_review(self.s,out)
  for path in out.rglob('*'):
   if path.is_file() and path.name!='acceptance_contract.json':self.assertNotIn(SOURCE,read(path))
 def test_export_into_job_rejected(self):
  with self.assertRaises(ValueError):f.export_review(self.s,self.job/'bad')
 def test_review_export_duplicate_rejected(self):
  self.baseline();out=self.root/'export';f.export_review(self.s,out)
  with self.assertRaises(ValueError):f.export_review(self.s,out)
 def test_no_installed_source_execution(self):
  sentinel=self.root/'EXECUTED'
  code=f'from pathlib import Path\nPath({str(sentinel)!r}).write_text("bad")\nimport ast\ndef f(x):\n return ast.parse(x)\n'.encode()
  probe=f.probe_module(RUNTIME);probe.isolated_scan(code,'synthetic:sentinel',P,Path(RUNTIME['hound']))
  self.assertFalse(sentinel.exists())
 def test_real_worker_packet(self):
  t=self.s.claim();r=f.execute_task(self.s,t);self.s.finish(t,r)
  self.assertFalse(r['upstream_code_executed']);self.assertFalse(r['release_approved'])
 def test_baseline_already_meets_requirement_stops(self):
  m=copy.deepcopy(M);c=copy.deepcopy(C);p=copy.deepcopy(P);p['targets'].append({'api':'hashlib.sha256','capability':'digest'})
  s=f.initialize(self.root/'already',RUNTIME,self.src,m,c,self.sources,p);drain(s)
  self.assertEqual(s.status()['status'],'BASELINE_ALREADY_SATISFIES_MISSION')
 def test_budget_exhaustion(self):
  m=copy.deepcopy(M);m['max_candidates']=1
  s=f.initialize(self.root/'budget',RUNTIME,self.src,m,C,self.sources,P);drain(s)
  s.submit(proposal([{'api':'json.dumps','capability':'serializer'}]));drain(s)
  self.assertEqual(s.status()['status'],'BUDGET_EXHAUSTED')
  with self.assertRaisesRegex(ValueError,'BUDGET'):s.submit(proposal())

class SecondReview(unittest.TestCase):
 setUp=JobCase.setUp
 tearDown=JobCase.tearDown
 baseline=JobCase.baseline
 def test_positive_gain_cannot_hide_a_required_regression(self):
  obj=lambda name:strict_json(read(ROOT/'examples'/name))
  s=f.initialize(self.root/'regression',RUNTIME,self.src,obj('mission.json'),obj('acceptance.json'),self.sources,obj('baseline_profile.json'))
  drain(s);p=obj('proposal-01-noisy.json');p['source_references']=['self:example'];s.submit(p);drain(s)
  v=s.status()['candidates'][-1]['verdict']
  self.assertGreater(v['net_correct_case_gain'],0);self.assertEqual(v['status'],'REJECTED');self.assertEqual(v['required_regressions'],['unrelated_serializer'])
 def test_missing_task_does_not_qualify(self):
  with self.s.transaction() as c:c.execute('DELETE FROM tasks WHERE id=(SELECT id FROM tasks LIMIT 1)')
  with self.assertRaisesRegex(ValueError,'TASK_COVERAGE'):self.s.status()
 def test_unknown_task_status_rejected(self):
  with self.s.transaction() as c:c.execute('UPDATE tasks SET status="APPROVED" WHERE id=(SELECT id FROM tasks LIMIT 1)')
  with self.assertRaisesRegex(ValueError,'TASK_STATE'):self.s.guard()
 def test_negative_attempt_ledger_rejected(self):
  with self.s.transaction() as c:c.execute('UPDATE tasks SET attempts=-1 WHERE id=(SELECT id FROM tasks LIMIT 1)')
  with self.assertRaisesRegex(ValueError,'ATTEMPT_LEDGER'):self.s.guard()
 def test_hardlinked_database_rejected(self):
  os.link(self.s.path,self.root/'other.sqlite')
  with self.assertRaisesRegex(ValueError,'HARDLINK'):f.Store(self.job)
 def test_finished_result_cannot_be_overwritten(self):
  t=self.s.claim();self.s.finish(t,{'result':1})
  with self.assertRaisesRegex(ValueError,'STALE_WORKER'):self.s.finish(t,{'result':2})
 def test_late_failure_cannot_erase_new_completion(self):
  old=self.s.claim(1);self.clock.value+=2;new=self.s.claim();self.s.finish(new,{'done':1})
  with self.assertRaisesRegex(ValueError,'STALE_WORKER'):self.s.fail(old,'late')
 def test_repeated_complete_run_starts_no_new_parser(self):
  self.baseline();self.s.submit(proposal());drain(self.s)
  result=f.run_jobs(self.s,100,2);self.assertEqual(result['this_run']['claims'],0);self.assertEqual(result['status'],'READY_FOR_ADOPTION_REVIEW')
 def test_forged_verdict_rejected(self):
  self.baseline()
  with self.s.transaction() as c:c.execute('UPDATE candidates SET verdict=? WHERE id="baseline"',(canonical({'status':'READY_FOR_ADOPTION_REVIEW'}),))
  with self.assertRaisesRegex(ValueError,'VERDICT_CHANGED'):self.s.status()
 def test_no_case_source_in_worker_request(self):
  self.baseline();packet=self.s.packet()
  def walk(x):
   if isinstance(x,dict):
    self.assertNotIn('code',x)
    for v in x.values():walk(v)
   elif isinstance(x,list):
    for v in x:walk(v)
  walk(packet)
 def test_claim_nan_rejected(self):
  with self.assertRaises(ValueError):self.s.claim(float('nan'))
 def test_started_cancellation_retains_queued_work(self):
  cancel=threading.Event();cancel.set();r=f.run_jobs(self.s,40,2,cancel)
  self.assertEqual(r['attempts'],0);self.assertEqual(r['status'],'WORK_REMAINS')

class Validation(unittest.TestCase):pass

def add_case(name,fn):
 def test(self):
  with self.assertRaises((ValueError,TypeError,KeyError)):fn()
 setattr(Validation,'test_'+name,test)
add_case('duplicate_json',lambda:strict_json('{"a":1,"a":2}'))
add_case('nan_json',lambda:strict_json('{"a":NaN}'))
add_case('overflow_json',lambda:strict_json('{"a":[1e400]}'))
add_case('boolean_budget',lambda:f.validate_mission({**M,'max_candidates':True}))
add_case('unlimited_budget',lambda:f.validate_mission({**M,'max_candidates':999999}))
add_case('mission_command',lambda:f.validate_mission({**M,'command':'echo x'}))
add_case('empty_contract',lambda:f.validate_contract({'schema':1,'cases':[]}))
add_case('duplicate_cases',lambda:f.validate_contract({'schema':1,'cases':C['cases']+C['cases']}))
add_case('positives_required',lambda:f.validate_contract({'schema':1,'cases':C['cases'][2:]}))
add_case('negatives_required',lambda:f.validate_contract({'schema':1,'cases':C['cases'][:2]}))
add_case('absolute_path',lambda:relative('/etc/passwd'))
add_case('parent_path',lambda:relative('../foo.py'))
add_case('windows_path',lambda:relative('C:\\secret.py'))
add_case('normalized_path',lambda:relative('a//b.py'))
add_case('nonpython_manifest',lambda:f.validate_sources({'schema':1,'sources':[{'source_id':'x','path':'x.exe','sha256':'0'*64}]}))
add_case('empty_manifest',lambda:f.validate_sources({'schema':1,'sources':[]}))

class StateSequence(unittest.TestCase):
 setUp=JobCase.setUp
 tearDown=JobCase.tearDown
 # A deterministic reference-model exercise; no Hypothesis dependency is claimed.
 def test_randomized_claim_sequence(self):
  rng=random.Random(60427);now=self.clock;active=[];completed=set()
  for _ in range(40):
   if rng.random()<.35:now.value+=2
   t=self.s.claim(1)
   if t:active.append(t)
   if active and rng.random()<.8:
    pick=active.pop(rng.randrange(len(active)))
    with self.s.transaction() as c:row=c.execute('SELECT status,token,expires FROM tasks WHERE id=?',(pick['id'],)).fetchone()
    can=(row['status']=='LEASED' and row['token']==pick['token'] and row['expires']>now())
    if can:self.s.finish(pick,{'sequence':1});completed.add(pick['id'])
    else:
     with self.assertRaisesRegex(ValueError,'STALE_WORKER'):self.s.finish(pick,{'late':1})
   with self.s.transaction() as c:
    rows=c.execute('SELECT id,status,attempts FROM tasks').fetchall()
    self.assertEqual({r['id'] for r in rows if r['status']=='DONE'},completed)
    self.assertTrue(all(0<=r['attempts']<=2 for r in rows))
