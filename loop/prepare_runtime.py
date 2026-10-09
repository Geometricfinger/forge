"""Verify and copy the sibling analyzer components (addon, hound). No downloads or installs.

The scanner add-on and the Hound analyzer ship as plain source next to this
controller (../addon and ../hound). Each carries a SHA256SUMS.txt manifest that
is verified after copying into a new, separate runtime directory.
"""
from pathlib import Path, PurePosixPath
import argparse, json
from safeio import Blocked,read,digest,write_new,canonical,no_links
ROOT=Path(__file__).resolve().parent
COMPONENTS=(('addon',ROOT.parent/'addon'),('hound',ROOT.parent/'hound'))

def verify(root):
    count=0
    for line in read(root/'SHA256SUMS.txt',1_000_000).decode().splitlines():
        h,n=line.split('  ',1);p=PurePosixPath(n)
        if p.is_absolute() or '..' in p.parts or '\\' in n: raise Blocked('MANIFEST_PATH')
        if digest(read(root/n,30_000_000))!=h:raise Blocked('MANIFEST_HASH')
        count+=1
    if not count:raise Blocked('EMPTY_MANIFEST')
    return count

def copy_component(src,dest):
    src=no_links(src)
    if not (src/'SHA256SUMS.txt').is_file():raise Blocked('COMPONENT_MISSING')
    for p in sorted(src.rglob('*')):
        rel=p.relative_to(src)
        if p.is_symlink():raise Blocked('LINK_PATH')
        if '__pycache__' in rel.parts or p.name.startswith('._') or p.suffix=='.pyc':continue
        if p.is_dir():(dest/rel).mkdir(parents=True,exist_ok=True)
        elif p.is_file():
            if p.stat().st_size>30_000_000:raise Blocked('COMPONENT_LIMIT')
            write_new(dest/rel,p.read_bytes())
    return dest,verify(dest)

def prepare(out):
    out=no_links(out)
    if out.exists() or out.is_relative_to(ROOT):raise Blocked('NEW_SEPARATE_RUNTIME_REQUIRED')
    out.mkdir(parents=True,mode=0o700)
    meta={'schema':1,'verified_manifest_entries':{}}
    for name,src in COMPONENTS:
        dest,count=copy_component(src,out/name)
        meta[name]=str(dest);meta['verified_manifest_entries'][name]=count
        meta[name+'_manifest_sha256']=digest(read(dest/'SHA256SUMS.txt',1_000_000))
    # No model or network capability is installed by copying.
    write_new(out/'runtime.json',canonical(meta));return meta

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    try:print(json.dumps(prepare(a.out)))
    except (ValueError,OSError,KeyError) as e:print(json.dumps({'status':'BLOCKED','reason':str(e)}));raise SystemExit(2)
