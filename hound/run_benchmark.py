"""Reproduce the narrow before/after detector comparison, not a novelty test."""
from pathlib import Path
import argparse,json
import baseline_sniffers as old
import hound
from atlas_hunt import strict_json,bounded_read
from value_gate import assess

def run(out:Path):
 if out.exists():raise ValueError('NEW_OUTPUT_DIRECTORY_REQUIRED')
 here=Path(__file__).parent
 raw=bounded_read(here/'tests/benchmark_cases.json',1_000_000)
 contract=strict_json(bounded_read(here/'evidence/benchmark_contract.json',100_000))
 if hound.sha(raw)!=contract['suite_sha256']:raise ValueError('BENCHMARK_CONTRACT_CHANGED')
 data=strict_json(raw);profiles=old.read_profiles(here/'profiles/baseline.json');rows=[]
 for case in data['rows']:
  code=case['source'].encode()
  a=[x['status'] for x in old.scan_bytes(code,source_id='fixture',profiles=profiles)['findings'] if x['profile_id']==profiles[0]['id']]
  b=[x['status'] for x in hound.scan_bytes(code,source_id='fixture')['findings'] if x['profile_id']==hound.RIGID]
  before=a[0] if len(a)==1 else 'NO_RESOLVED_CALL' if not a else 'MULTIPLE_CALLS'
  after=b[0] if len(b)==1 else 'NO_RESOLVED_CALL' if not b else 'MULTIPLE_CALLS'
  rows.append({'case_id':case['id'],'expected':case['expected'],'baseline':before,'candidate':after,
   'baseline_correct':before==case['expected'],'candidate_correct':after==case['expected']})
 baseline=sum(x['baseline_correct'] for x in rows);candidate=sum(x['candidate_correct'] for x in rows)
 regressions=sum(x['baseline_correct'] and not x['candidate_correct'] for x in rows)
 evidence={'baseline_id':hound.sha(Path(old.__file__).read_bytes()),'candidate_id':hound.engine_digest(),
  'suite_sha256':hound.sha(raw),'input_set_sha256':hound.sha(raw),'case_count':len(rows),
  'required_checks_passed':all(x['candidate_correct'] for x in rows), 'required_regressions':regressions,
  'metrics':{'correct_decisions':{'baseline':baseline,'candidate':candidate}},
  'tradeoffs':['Local-flow interpretation adds implementation and parsing complexity; throughput advantage not established.'],
  'limitations':['Small developer-authored diagnostics, not independent holdout data or real-library precision/recall.',
   'Only selected local syntax; no geometry runtime executed. No competitor comparison or global novelty proof.'],
  'cases':rows}
 out.mkdir(parents=True)
 body=json.dumps(evidence,indent=2).encode();(out/'comparison.json').write_bytes(body)
 proposal={k:evidence[k] for k in ('baseline_id','candidate_id','suite_sha256','input_set_sha256')}
 proposal.update(mode='better',problem='Resolve explicit/local ICP controls rather than classifying every named boolean as unknown',
  metric=contract['metric'],direction=contract['direction'],minimum_change=contract['minimum_change'],
  artifact={'path':'comparison.json','sha256':hound.sha(body)})
 (out/'proposal.json').write_text(json.dumps(proposal,indent=2))
 verdict=assess(proposal,out);(out/'value_gate.json').write_text(json.dumps(verdict,indent=2))
 return {'before':baseline,'after':candidate,'cases':len(rows),'regressions':regressions,'gate':verdict}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();result=run(a.out);print(json.dumps(result,indent=2))
 raise SystemExit(0 if result['gate']['status'] in {'MEASURED_ADVANTAGE_REVIEW_REQUIRED','USEFUL_DIFFERENCE_REVIEW_REQUIRED'} else 2)
