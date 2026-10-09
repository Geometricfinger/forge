#!/usr/bin/env python3
"""Fixed fault sensitivity checks on disposable add-on copies only."""
import argparse, hashlib, json, os, shutil, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FAULTS=[
 ('question_coverage','research_runner/core.py',"questions=report.get('queries',[])","questions=report.get('queries',[])[:6]",'test_packet_coverage.PacketCoverage.test_all_short_query_summaries_before_details'),
 ('source_hash','research_runner/core.py',"len(b)!=s['size'] or sha(b)!=s['sha256']","len(b)!=s['size']",'test_contract.Contract.test_source_hash'),
 ('lexical_rescue','research_runner/core.py',"[('lexical',lexical),('forge',forge)]","[('lexical',[]),('forge',forge)]",'test_contract.Contract.test_full_source_rescue'),
 ('reference_gate','research_runner/core.py',"if r.get('kind')!='implementation' or r['id'] in seen:continue","if r['id'] in seen:continue",'test_contract.Contract.test_reference_cannot_be_code'),
 ('checkpoint_binding','research_runner/core.py',"v['binding']!=self.binding or ","",'test_contract.Contract.test_checkpoint_other_binding'),
 ('api_gate','research_runner/core.py',"if required_api and required_api not in observed.get(r['id'],[]):continue","if False:continue",'test_contract.Contract.test_required_api_not_inferred'),
 ('round_robin','research_runner/bridge.py',"forgerows=[row for layer in zip_longest(*lists) for row in layer if row is not None][:10]","forgerows=[row for group in lists for row in group][:10]",'test_second_review.SecondReview.test_both_sources_visible_within_budget'),
]
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    rows=[]
    for name,path,before,after,test in FAULTS:
        target=out/name;target.mkdir()
        for folder in ('research_runner','tests'):shutil.copytree(ROOT/folder,target/folder,ignore=shutil.ignore_patterns('__pycache__'))
        p=target/path;s=p.read_text();assert s.count(before)==1,(name,s.count(before));p.write_text(s.replace(before,after))
        env=dict(os.environ,PYTHONPATH=str(target)+os.pathsep+str(target/'tests'),PYTHONDONTWRITEBYTECODE='1')
        cp=subprocess.run([sys.executable,'-m','unittest',test,'-v'],cwd=target,env=env,capture_output=True,text=True,timeout=20)
        log=cp.stdout+cp.stderr;(out/(name+'.log')).write_text(log)
        detected=cp.returncode!=0 and 'FAILED (failures=1)' in log and 'ERROR:' not in log
        rows.append({'fault':name,'test':test,'returncode':cp.returncode,'assertion_detected':detected,'log_sha256':hashlib.sha256(log.encode()).hexdigest()})
    summary={'results':rows,'all_detected':all(r['assertion_detected'] for r in rows),'independent_evaluation':False,'mutants_are_not_product_defects':True}
    (out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2));return 0 if summary['all_detected'] else 2
if __name__=='__main__':raise SystemExit(main())
