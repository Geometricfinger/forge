"""Repeat the frozen, developer-authored 28-case review comparison.

The included baseline scanner is unchanged. Each engine is loaded in a separate
Python process. Source snippets are parsed, never imported or executed.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import hound

SUITE_SHA256='aff027120ff1c451e7ba641836249b293e371c2c2bdf90c2d2c5b5c0ad8896e0'


def run(out):
    if out.exists():raise ValueError('NEW_OUTPUT_DIRECTORY_REQUIRED')
    root=Path(__file__).resolve().parent;raw=(root/'tests/flow_acceptance_cases.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=SUITE_SHA256:raise ValueError('FROZEN_SUITE_CHANGED')
    cases=json.loads(raw)['cases'];runs=[]
    script='''import sys,json
sys.path.insert(0,sys.argv[1])
import hound
cases=json.load(sys.stdin)
rows=[]
for c in cases:
 fs=hound.scan_bytes(c["source"].encode(),source_id="fixture")["findings"]
 rows.append([x["status"] for x in fs if x["profile_id"]==c["profile"]])
print(json.dumps({"engine_sha256":hound.engine_digest(),"rows":rows}))
'''
    for where in [root/'reference/hound_0_2',root]:
        p=subprocess.run([sys.executable,'-I','-B','-c',script,str(where)],input=json.dumps(cases),text=True,capture_output=True,
            timeout=15,env={'PATH':os.defpath,'LANG':'C.UTF-8'},check=True)
        runs.append(json.loads(p.stdout))
    rows=[{'id':c['id'],'expected':c['expected'],'baseline':a,'candidate':b,
           'baseline_correct':a==c['expected'],'candidate_correct':b==c['expected']}
          for c,a,b in zip(cases,runs[0]['rows'],runs[1]['rows'])]
    report={'suite_sha256':SUITE_SHA256,'case_count':len(rows),'baseline_engine_sha256':runs[0]['engine_sha256'],
            'candidate_engine_sha256':runs[1]['engine_sha256'],'baseline_correct':sum(x['baseline_correct'] for x in rows),
            'candidate_correct':sum(x['candidate_correct'] for x in rows),
            'regressions':sum(x['baseline_correct'] and not x['candidate_correct'] for x in rows),'rows':rows,
            'scope':'Developer-authored diagnostic set frozen before the flow changes; not independent holdout, market benchmark or scientific accuracy.',
            'minimum_improvement':1,'release_approved':False}
    report['accepted_for_review']=report['regressions']==0 and report['candidate_correct']==len(rows) and report['candidate_correct']-report['baseline_correct']>=1
    out.mkdir(parents=True);(out/'comparison.json').write_text(json.dumps(report,indent=2))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    r=run(a.out);print(json.dumps({k:v for k,v in r.items() if k!='rows'},indent=2));raise SystemExit(0 if r['accepted_for_review'] else 2)
