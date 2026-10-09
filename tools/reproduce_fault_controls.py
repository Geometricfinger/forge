#!/usr/bin/env python3
"""Re-run four fixed removal checks on disposable tool copies, never the original."""
import argparse,hashlib,json,shutil,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from forge_core.common import Blocked,safe_path,write,canonical
MUTATIONS=[
 ('missing_evidence_head',"if head is None or head['head']!=previous:raise Blocked('EVIDENCE_TAIL_CHANGED')","if False:raise Blocked('EVIDENCE_TAIL_CHANGED')",'test_reuse_final.py'),
 ('stale_writer_allowed',"if note['supersedes']!=head:raise Blocked('STALE_EVIDENCE_HEAD')","if False:raise Blocked('STALE_EVIDENCE_HEAD')",'test_reuse_contract.py'),
 ('behavior_requirements_ignored',"needed=[r for r in checks if r['required']]","needed=[r for r in checks if r['required'] and r['kind']!='behavior_test']",'test_reuse_contract.py'),
 ('operator_can_claim_execution',"if req['kind']!='review':raise Blocked('OPERATOR_CANNOT_ASSERT_EXECUTION_OR_SYNTAX')","if False:raise Blocked('OPERATOR_CANNOT_ASSERT_EXECUTION_OR_SYNTAX')",'test_reuse_contract.py')]
def main():
    a=argparse.ArgumentParser();a.add_argument('--out',type=Path,required=True);opt=a.parse_args();out=safe_path(opt.out)
    if out.exists() or out.is_relative_to(ROOT):raise Blocked('NEW_EXTERNAL_OUTPUT_REQUIRED')
    out.mkdir(parents=True);results=[]
    with tempfile.TemporaryDirectory(prefix='forge-fixed-removals-') as temp:
        for name,old,new,pattern in MUTATIONS:
            d=Path(temp)/name
            shutil.copytree(ROOT,d,ignore=shutil.ignore_patterns('history','evidence','__pycache__','*.pyc'))
            p=d/'forge_core/reuse.py';s=p.read_text()
            if s.count(old)!=1:raise Blocked('REMOVAL_TARGET_CHANGED')
            p.write_text(s.replace(old,new))
            code="import unittest,io,json;st=io.StringIO();su=unittest.defaultTestLoader.discover('tests',pattern="+repr(pattern)+");r=unittest.TextTestRunner(stream=st,verbosity=2).run(su);open('run.log','w').write(st.getvalue());print(json.dumps({'tests':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skips':len(r.skipped),'failed_ids':[t.id() for t,_ in r.failures]}))"
            cp=subprocess.run([sys.executable,'-c',code],cwd=d,capture_output=True,timeout=30)
            if cp.returncode:raise Blocked('REMOVAL_HARNESS_ERROR')
            rec=json.loads(cp.stdout);rec['name']=name;rec['test_sha256']=hashlib.sha256((d/'tests'/pattern).read_bytes()).hexdigest()
            rec['detected']=rec['tests']>0 and rec['failures']>0 and not rec['errors'] and not rec['skips'];results.append(rec)
            shutil.copy(d/'run.log',out/(name+'.txt'))
    result={'status':'ALL_REMOVALS_DETECTED' if all(r['detected'] for r in results) else 'BLOCKED','results':results,'independent_evaluation':False,'release_approved':False}
    write(out/'results.json',canonical(result),new=True);print(json.dumps(result));return 0 if result['status']=='ALL_REMOVALS_DETECTED' else 2
if __name__=='__main__':raise SystemExit(main())
