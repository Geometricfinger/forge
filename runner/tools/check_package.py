#!/usr/bin/env python3
"""Verify local manifest consistency, not authenticity or production readiness."""
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def check():
    m=json.loads((ROOT/'FILE_HASHES.json').read_text());bad=[]
    for name,expected in m.items():
        p=ROOT/name
        if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(ROOT) or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:bad.append(name)
    result={'manifest_files':len(m),'mismatches':bad,'consistent':not bad,'release_approved':False}
    print(json.dumps(result,indent=2));return 0 if not bad else 2
if __name__=='__main__':raise SystemExit(check())
