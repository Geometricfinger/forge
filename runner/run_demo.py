#!/usr/bin/env python3
"""Run the bounded, offline research example against this repository's FORGE engine.

No network, installation, source execution or inference. Requires a supported
POSIX Python host; the complete baseline's qualification is not implied.
"""
from pathlib import Path
import argparse, json, sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from research_runner.core import Blocked,canonical,strict_loads,no_links
from research_runner.bridge import verify_base
from research_runner.runner import run
ENGINE_ROOT=ROOT.parent

def prepare(parent):
    """Use the FORGE engine of the enclosing repository after verifying its analyzer manifests."""
    verify_base(ENGINE_ROOT);return ENGINE_ROOT

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--steps',type=int,default=1000);args=ap.parse_args()
    out=no_links(args.out)
    if out==ROOT or out.is_relative_to(ROOT) or ROOT.is_relative_to(out):raise Blocked('SEPARATE_DEMO_WORKSPACE')
    out.mkdir(parents=True,exist_ok=True)
    baseline=prepare(out)
    results=run(strict_loads((ROOT/'contracts/MISSION.json').read_bytes()),ROOT/'examples/sources',out/'mission',baseline,args.steps)
    report={'demo':results,'release_approved':False,'model_connected':False}
    (out/'DEMO_RESULT.json').write_bytes(canonical(report));print(canonical(report).decode())
    return 0 if results['status']=='READY_FOR_SUPERVISOR_REVIEW' else 2
if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as ex:
        print(canonical({'status':'BLOCKED','reason':str(ex) if isinstance(ex,Blocked) else type(ex).__name__,'release_approved':False}).decode());raise SystemExit(2)
