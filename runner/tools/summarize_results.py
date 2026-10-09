#!/usr/bin/env python3
"""Recompute diagnostic ranks; no feedback is passed to retrieval."""
import argparse, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from research_runner.core import canonical, sha

def summarize(report,manifest,targets):
    qs={q['id']:q for q in manifest['questions']};sources={s['id']:s for s in manifest['sources']};rows=[]
    for q in report['queries']:
        expected=targets.get(q['id']);ranks={}
        for channel in ('lexical_results','forge_results','results'):
            ranks[channel]=next((i for i,r in enumerate(q[channel],1) if expected and r['name']==expected and r['source_id'] in qs[q['id']]['sources'] and r['kind']=='implementation' and r['start_line']<=r['end_line'] and r['origin']==sources[r['source_id']]['origin']),None)
        rows.append({'id':q['id'],'query':q['query'],'target':expected,'status':q['status'],**ranks})
    selected=[r for r in rows if r['target']]
    scores={c:{'target_first':sum(r[c]==1 for r in selected),'target_top3':sum(r[c] is not None and r[c]<=3 for r in selected),'target_top5':sum(r[c] is not None and r[c]<=5 for r in selected),'cases':len(selected)} for c in ('lexical_results','forge_results','results')}
    return {'rows':rows,'scores':scores,'target_matching':'fixed qualified names in their declared single-source scope, implementation rows only; not an independently labeled relevance benchmark','positive_tasks':len(selected),'other_scenarios':len(rows)-len(selected)}

def main():
    p=argparse.ArgumentParser();p.add_argument('--report',type=Path,required=True);p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    result=summarize(json.loads(args.report.read_text()),json.loads((ROOT/'contracts/MISSION.json').read_text()),json.loads((ROOT/'contracts/TARGETS.json').read_text())['targets'])
    args.out.write_bytes(canonical(result));print(json.dumps(result['scores'],indent=2))
if __name__=='__main__':main()
