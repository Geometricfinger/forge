"""Unified home for missions, runtime, reports, and controlled-profile evaluations."""
import gzip,zipfile,sys,subprocess
from pathlib import Path
from .common import *
from .engine import prepare,Analyzer,runtime,ROOT
from .store import Store
from .discovery import Discovery

def code_binding():
    paths=[ROOT/'forge.py',ROOT/'verify.py',ROOT/'run_tests.py',*sorted((ROOT/'tests').glob('*.py')),*sorted((ROOT/'forge_core').glob('*.py')),*sorted((ROOT/'templates').glob('*.json')),*sorted((ROOT/'contracts').glob('*.json')),*sorted((ROOT/'assets').glob('*'))]
    for base in ('forge_core/_vendor','contracts/interop','third_party/rfc8785'):
        paths += sorted(p for p in (ROOT/base).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
    paths += [ROOT/'tools/interop_reference.cjs',ROOT/'tools/contract_fault_cases.py',ROOT/'tools/run_contract_fault_checks.py']
    paths += [ROOT/'tools/run_self_recovery_trial.py',*sorted((ROOT/'examples/demo').rglob('*.py'))]
    paths += [ROOT/name/'SHA256SUMS.txt' for name in ('loop','addon','hound')]
    return {p.relative_to(ROOT).as_posix():sha(read(p)) for p in paths}

def initialize(home):
    home=safe_path(home)
    if home==ROOT or home.is_relative_to(ROOT) or ROOT.is_relative_to(home):raise Blocked('SEPARATE_WORKSPACE_REQUIRED')
    store=Store.create(home,code_binding());prepare(home);return Workbench(home)

class Workbench:
    def __init__(self,home):self.home=safe_path(home);self.store=Store(home)
    def guard(self):
        if self.store.binding()!=code_binding():raise Blocked('WORKBENCH_CODE_CHANGED_NEW_HOME_REQUIRED')
        runtime(self.home)
    def casebook(self):
        self.guard()
        from .reuse import Casebook
        return Casebook(self.store)
    def discovery(self):self.guard();return Discovery(self.store,Analyzer(self.home),self.guard)
    def export(self,mid,out):
        self.guard();obj=self.store.snapshot(mid);out=safe_path(out)
        if out.exists() or out.is_relative_to(ROOT) or out.is_relative_to(self.home/'engine'):raise Blocked('NEW_REPORT_DIRECTORY_REQUIRED')
        out.mkdir(parents=True,mode=0o700)
        write(out/'mission.json',canonical(obj),new=True)
        findings=[f for c in obj['candidates'] for f in c['findings']]
        cart=gzip.compress(b''.join(canonical(f)+b'\n' for f in findings),mtime=0)
        write(out/'findings.ndjson.gz',cart,new=True)
        packet={'schema':1,'mission':mid,'objective':obj['public_brief'],'finding_count':len(findings),'sources':[{k:c[k] for k in ['source_id','source_sha256','commit','path','declared_license','rights_status']} for c in obj['candidates']], 'findings':findings,'required_next_step':'Review contracts and rights. Retrieve exact authorized source context before proposing code. Do not execute source or weaken acceptance criteria.','authority':{'execute_source':False,'deploy':False,'change_tests':False},'model_calls':0}
        write(out/'agent_packet.json',canonical(packet),new=True)
        receipt={'mission_sha256':sha(canonical(obj)),'cart_sha256':sha(cart),'cart_bytes':len(cart),'records':len(findings),'source_bodies_included':False,'release_approved':False}
        write(out/'receipt.json',canonical(receipt),new=True)
        from .report import render
        write(out/'Review.html',render(obj).encode(),new=True);return receipt
    def cycle_command(self,out):
        self.guard();r=runtime(self.home);out=safe_path(out)
        if out.exists() or out.is_relative_to(ROOT) or out.is_relative_to(self.home/'engine'):raise Blocked('NEW_CYCLE_DIRECTORY_REQUIRED')
        return [sys.executable,str(Path(r['controller'])/'run_demo.py'),'--out',str(out),'--runtime',str(self.home/'engine/runtime/runtime.json')]
