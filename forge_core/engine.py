"""Invoke only the hash-verified FORGE analyzer (shipped as source) in an isolated process."""
import os,subprocess,sys,signal
from pathlib import Path,PurePosixPath
from .common import *
ROOT=Path(__file__).resolve().parents[1]
# The analyzer ships as plain source in the repository (no bundled archives).
# Each component carries a SHA256SUMS.txt; contracts/analyzer_lock.json pins
# the digests of those manifests. Regenerate both with
# tools/update_analyzer_manifests.py after an intentional analyzer change.
COMPONENTS=('loop','addon','hound')
LOCK=ROOT/'contracts/analyzer_lock.json'

def _copy_component(src,dest):
    for p in sorted(src.rglob('*')):
        rel=p.relative_to(src)
        if p.is_symlink():raise Blocked('ANALYZER_LINK')
        if '__pycache__' in rel.parts or p.name.startswith('._') or p.suffix=='.pyc':continue
        if p.is_dir():(dest/rel).mkdir(parents=True,exist_ok=True)
        elif p.is_file():write(dest/rel,read(p,40_000_000),new=True)

def unpack_baseline(dest):
    """Copy the in-repository analyzer into a new private directory and verify it."""
    lock=loads(read(LOCK))
    if dest.exists():raise Blocked('NEW_ENGINE_DIRECTORY_REQUIRED')
    dest.mkdir(parents=True,mode=0o700)
    for name in COMPONENTS:
        src=ROOT/name
        if not (src/'SHA256SUMS.txt').is_file():raise Blocked('ANALYZER_COMPONENT_MISSING')
        _copy_component(src,dest/name)
        if sha(read(dest/name/'SHA256SUMS.txt',2_000_000))!=lock['manifests'].get(name):raise Blocked('ANALYZER_LOCK_CHANGED')
        verify_manifest(dest/name)
    return dest/'loop'

def verify_manifest(root):
    count=0
    for line in read(root/'SHA256SUMS.txt',2_000_000).decode().splitlines():
        digest,name=line.split('  ',1);path_in_repo(name)
        if sha(read(root/name,40_000_000))!=digest:raise Blocked('ENGINE_MANIFEST')
        count+=1
    if not count:raise Blocked('EMPTY_MANIFEST')
    return count

def clean_env(home):return {'PATH':os.environ.get('PATH','/usr/bin:/bin'),'HOME':str(home),'TMPDIR':str(home),'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}

def run_fixed(cmd,home,timeout=40,input=None,output_limit=None):
    """Run a fixed trusted tool with a shared streaming stdout/stderr byte cap."""
    from .bounded_io import run_bounded
    return run_bounded(cmd,home,clean_env(home),timeout,input,output_limit)

def prepare(home):
    root=home/'engine';baseline=unpack_baseline(root)
    out=run_fixed([sys.executable,str(baseline/'prepare_runtime.py'),'--out',str(root/'runtime')],home,40)
    r=loads(out);r['controller']=str(baseline)
    manifests={str(p):sha(read(p,40_000_000)) for base in [baseline,Path(r['addon']),Path(r['hound'])] for p in base.glob('*.py')}
    manifests.update({str(p):sha(read(p)) for p in (Path(r['addon'])/'tool').glob('*.py')})
    write(home/'engine.json',canonical({'runtime':r,'files':manifests}),new=True);return r

def runtime(home):
    r=loads(read(home/'engine.json'))
    for p,h in r['files'].items():
        if sha(read(p,40_000_000))!=h:raise Blocked('ENGINE_CHANGED')
    return r['runtime']

class Analyzer:
    def __init__(self,home):self.home=home
    def scan(self,data,source_id,profile):
        if len(data)>250000:raise Blocked('SOURCE_LIMIT')
        r=runtime(self.home)
        out=run_fixed([sys.executable,'-I',str(Path(r['controller'])/'probe_worker.py'),'--addon',r['addon'],'--hound',r['hound']],self.home,25,canonical({'source_hex':data.hex(),'source_id':source_id,'profile':profile}))
        result=loads(out)
        if result.get('source_sha256')!=sha(data) or result.get('source_id')!=source_id or result.get('registry_sha256')!=sha(canonical(profile)) or result.get('upstream_code_executed') is not False:raise Blocked('ANALYSIS_BINDING')
        return result
