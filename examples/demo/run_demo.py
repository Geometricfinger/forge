#!/usr/bin/env python3
"""End-to-end public demo: scan the bundled fake source, search it, and run the opportunity case study.

Everything here is synthetic. No network access, model calls or execution of the scanned code.
Usage: python3 examples/demo/run_demo.py --out /tmp/forge-demo   (the folder must not exist yet)
"""
import argparse, json, subprocess, sys
from pathlib import Path

DEMO = Path(__file__).resolve().parent
ROOT = DEMO.parents[1]


def forge(home, *args):
    p = subprocess.run([sys.executable, str(ROOT / 'forge.py'), '--home', str(home), *args],
                       capture_output=True, text=True, timeout=300)
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise SystemExit('forge.py %s failed:\n%s%s' % (args[0], p.stdout, p.stderr))
    if p.returncode != 0 or out.get('status') == 'BLOCKED':
        raise SystemExit('forge.py %s failed: %s' % (args[0], json.dumps(out)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True, help='new folder outside the repository')
    out = ap.parse_args().out.resolve()
    if out.exists() or out.is_relative_to(ROOT):
        raise SystemExit('Choose a new output folder outside the repository')
    home = out / 'home'
    forge(home, 'init')
    scan = forge(home, 'corpus-run', '--source-root', str(DEMO / 'src'), '--manifest', str(DEMO / 'manifest.json'),
                 '--run-id', 'demo', '--profile', str(DEMO / 'profile.json'))
    searches = {q: forge(home, 'corpus-search', '--run-id', 'demo', '--query', q)['full_match_count']
                for q in ('pickle load', 'subprocess run', 'xml parse')}
    opportunity = forge(home, 'opportunity-demo')
    summary = {'scan_status': scan['status'], 'definitions': scan['summary']['definitions'],
               'hound_observations': scan['summary']['hound_observations'], 'searches_full_matches': searches,
               'opportunity_hypotheses': opportunity['summary']['hypotheses'],
               'opportunity_code_leads': opportunity['summary']['code_lead_count'],
               'upstream_code_executed': scan['upstream_code_executed'], 'home': str(home)}
    print(json.dumps(summary, indent=2))
    ok = (scan['status'] == 'COMPLETED_FOR_SELECTED_CONTAINERS' and summary['hound_observations'] > 0
          and all(searches.values()) and summary['opportunity_code_leads'] > 0 and not scan['upstream_code_executed'])
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
