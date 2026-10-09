#!/usr/bin/env python3
"""Repeat an approved mission; an optional fixed checkpoint interruption tests recovery."""
import argparse, hashlib, json, os, subprocess, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from research_runner.core import canonical, strict_loads, sha
from research_runner.runner import run

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--manifest',type=Path,required=True);ap.add_argument('--sources',type=Path,required=True)
    ap.add_argument('--forge-root',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--reference',type=Path)
    ap.add_argument('--child',action='store_true');ap.add_argument('--interrupt-after',type=int,default=3);args=ap.parse_args()
    if args.child:
        def stop(task,n):
            if n==args.interrupt_after:os._exit(75)
        run(strict_loads(args.manifest.read_bytes()),args.sources,args.out,args.forge_root,stop_after_checkpoint=stop)
        return 3
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False);work=out/'mission'
    cmd=[sys.executable,str(Path(__file__).resolve()),'--child','--manifest',str(args.manifest.resolve()),'--sources',str(args.sources.resolve()),'--forge-root',str(args.forge_root.resolve()),'--out',str(work),'--interrupt-after',str(args.interrupt_after)]
    cp=subprocess.run(cmd,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),capture_output=True,timeout=180)
    (out/'interrupt.log').write_bytes(cp.stdout+cp.stderr)
    if cp.returncode!=75:raise RuntimeError('INTERRUPTION_NOT_OBSERVED')
    before={p.name:sha(p.read_bytes()) for p in (work/'checkpoints').glob('*.json')}
    resumed=run(strict_loads(args.manifest.read_bytes()),args.sources,work,args.forge_root)
    preserved=all(sha((work/'checkpoints'/n).read_bytes())==h for n,h in before.items())
    payloads={n:sha((work/n).read_bytes()) for n in ('report.json','supervisor_packet.json','Review.html')}
    cached=run(strict_loads(args.manifest.read_bytes()),args.sources,work,args.forge_root)
    reference_match=None
    if args.reference:reference_match=all(sha((args.reference/n).read_bytes())==h for n,h in payloads.items())
    result={'interrupted_exit':75,'completed_checkpoints_retained':len(before),'retained_bytes_unchanged':preserved,
      'resumed':resumed,'cached':cached,'payload_hashes':payloads,'matches_uninterrupted_reference':reference_match,
      'all_checks':preserved and len(before)==args.interrupt_after and cached['new_tasks']==0 and reference_match is not False,
      'injected_interruption':True,'independent_evaluation':False}
    (out/'REPLAY.json').write_bytes(canonical(result));print(canonical(result).decode());return 0 if result['all_checks'] else 2
if __name__=='__main__':raise SystemExit(main())
