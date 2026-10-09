#!/usr/bin/env python3
"""Regenerate the analyzer manifests after an intentional analyzer change.

Writes <component>/SHA256SUMS.txt for loop/, addon/ and hound/, then pins the
digest of each manifest in contracts/analyzer_lock.json. Review the diff before
committing: the engine refuses to prepare a runtime when these do not match.
"""
from pathlib import Path
import hashlib, json
ROOT=Path(__file__).resolve().parents[1]
COMPONENTS=('loop','addon','hound')

def files(base):
    for p in sorted(base.rglob('*')):
        rel=p.relative_to(base)
        if not p.is_file() or p.name=='SHA256SUMS.txt' or p.name.startswith('._') or '__pycache__' in rel.parts or p.suffix=='.pyc':continue
        yield rel.as_posix(),p

def main():
    lock={'schema':1,'manifests':{}}
    for name in COMPONENTS:
        base=ROOT/name
        lines=[f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {rel}' for rel,p in files(base)]
        body=('\n'.join(lines)+'\n').encode()
        (base/'SHA256SUMS.txt').write_bytes(body)
        lock['manifests'][name]=hashlib.sha256(body).hexdigest()
    (ROOT/'contracts/analyzer_lock.json').write_text(json.dumps(lock,indent=2,sort_keys=True)+'\n')
    print(json.dumps(lock,indent=2,sort_keys=True))

if __name__=='__main__':main()
