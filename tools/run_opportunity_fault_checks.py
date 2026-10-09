import pathlib,json,subprocess,sys,shutil,hashlib,argparse
P=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(P))
from forge_core.common import safe_path,Blocked
ap=argparse.ArgumentParser(description='Run six fixed fault-removal checks in disposable copies; never edit the active build.')
ap.add_argument('--out',type=pathlib.Path,required=True);args=ap.parse_args();OUT=safe_path(args.out)
if OUT.exists() or OUT.is_relative_to(P):raise Blocked('NEW_EXTERNAL_OUTPUT_REQUIRED')
OUT.mkdir(parents=True)
source=(P/'forge_core/opportunities.py').read_text()
contract=[
 'test_opportunities.OpportunityTests.test_copy_same_words_not_second_user',
 'test_opportunities.OpportunityTests.test_advertising_not_user_pain',
 'test_opportunities.OpportunityTests.test_quote_changed_rejected',
 'test_opportunities.OpportunityTests.test_shape_matching_missing_information_blocks_prerequisite',
 'test_opportunity_review.ResultReview.test_partial_code_match_not_main_candidate',
 'test_opportunity_final_review.FinalEvidenceReview.test_rehashed_agent_authority_still_rejected',
]
mutations={
 'duplicate_votes':("groups=families(b['sources'])","groups={s['id']:s['id'] for s in b['sources']}"),
 'marketing_as_pain':("{'firsthand','issue'}","{'firsthand','issue','product_listing'}"),
 'invented_quote':("s[r['start']:r['end']]!=r['quote']","False"),
 'missing_prerequisites':("absent=sorted(set(m['requires'])-available)","absent=[]"),
 'partial_leads':("if h.get('all_query_terms_matched') is True","if True"),
 'agent_authority':("if request!=agent_packet(r):raise Blocked('OPPORTUNITY_PACKET_REQUEST_CHANGED')","if False:raise Blocked('OPPORTUNITY_PACKET_REQUEST_CHANGED')"),
}
# Same fixed selection for healthy control and every separately damaged implementation.
stored=json.loads((P/'contracts/opportunity_negative_controls.json').read_text())
if stored['tests']!=contract or stored['mutations']!=list(mutations):raise Blocked('NEGATIVE_CONTROL_CONTRACT_CHANGED')
runner="""import unittest,json,sys
suite=unittest.defaultTestLoader.loadTestsFromNames(json.loads(sys.argv[1]))
r=unittest.TextTestRunner(verbosity=2).run(suite)
print('RESULT_JSON '+json.dumps({'tests':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skips':len(r.skipped),'failed_ids':[x.id() for x,_ in r.failures]}))
raise SystemExit(0 if r.wasSuccessful() else 1)
"""
rows=[]
for name,change in [('healthy',None),*mutations.items()]:
 d=OUT/name;d.mkdir();shutil.copytree(P/'forge_core',d/'forge_core',ignore=shutil.ignore_patterns('__pycache__'))
 for t in ['test_opportunities.py','test_opportunity_review.py','test_opportunity_final_review.py']:shutil.copy2(P/'tests'/t,d/t)
 if change:
  a,b=change
  if a not in source:raise AssertionError(name)
  (d/'forge_core/opportunities.py').write_text(source.replace(a,b,1))
 p=subprocess.run([sys.executable,'-B','-c',runner,json.dumps(contract)],cwd=d,capture_output=True,text=True,timeout=25)
 (OUT/(name+'.log')).write_text(p.stdout+p.stderr)
 v=json.loads(next(line.removeprefix('RESULT_JSON ') for line in p.stdout.splitlines() if line.startswith('RESULT_JSON ')))
 rows.append({'variant':name,'exit_code':p.returncode,**v})
 if v['tests']!=len(contract) or v['errors'] or v['skips'] or (bool(v['failures'])!=(name!='healthy')):raise AssertionError(rows[-1])
result={'status':'ALL_SIX_REMOVALS_DETECTED','contract':contract,'test_file_hashes':{n:hashlib.sha256((P/'tests'/n).read_bytes()).hexdigest() for n in ['test_opportunities.py','test_opportunity_review.py','test_opportunity_final_review.py']},'results':rows,'independent_evaluation':False}
(OUT/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
