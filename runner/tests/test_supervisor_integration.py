"""Real analyzer/CLI replay, plus source-binding checks. Synthetic code only."""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from research_runner import core,runner,bridge
from research_runner.supervise import Supervisor
ROOT=Path(__file__).resolve().parents[2]

class SupervisorIntegration(unittest.TestCase):
    def test_real_mission_followup_inspection_replay_and_cancel(self):
        with tempfile.TemporaryDirectory() as t:
            d=Path(t).resolve();src=d/'src';src.mkdir();work=d/'work';canary=d/'never-created'
            data=("from pathlib import Path\nPath("+repr(str(canary))+").write_text('not allowed')\n"
                  "import hashlib\ndef digest(data):\n    return hashlib.sha256(data).hexdigest()\n").encode()
            (src/'sample.py').write_bytes(data)
            source={'id':'sample','path':'sample.py','kind':'implementation','origin':'fixture://sample',
                    'capture':'synthetic_fixture','family':'synthetic','size':len(data),'sha256':core.sha(data)}
            m={'schema':1,'objective':'Inspect a synthetic hash helper without executing it','sources':[source],
               'questions':[{'id':'initial','query':'digest','intent':'code','sources':['sample']}],
               'budgets':{'max_sources':1,'max_source_bytes':8000000,'max_total_bytes':8000000,'max_questions':3,'packet_bytes':16384}}
            mp=d/'mission.json';mp.write_bytes(core.canonical(m))
            receipt=runner.run(m,src,work,ROOT)
            self.assertEqual(receipt['status'],'READY_FOR_SUPERVISOR_REVIEW')
            self.assertFalse(canary.exists())
            old={str(p):core.sha(p.read_bytes()) for p in work.rglob('*') if p.is_file() and p.name!='.runner.lock'}
            sup=Supervisor(mp,src,work,ROOT)
            req={'action':'ask','request_id':'followup','question':{'id':'followup','query':'sha256','intent':'code','sources':['sample']}}
            # A completed mission must use existing intake; additional question
            # and indexed evidence reads are not another parser invocation.
            with patch.object(bridge,'inspect_source',side_effect=AssertionError('unexpected intake')):
                out=sup.execute(req);self.assertEqual(out['state'],'COMPLETED',out)
                self.assertTrue(out['result']['results']);item=out['result']['results'][0]
                inspected=sup.execute({'action':'inspect','request_id':'view','source_id':'sample','evidence_id':item['id'],'max_chars':1024})
                self.assertEqual(inspected['state'],'COMPLETED');self.assertIn('sha256',inspected['result']['text'])
                with patch.object(bridge,'investigate',side_effect=AssertionError('replay searched again')):
                    replay=sup.execute(req);self.assertTrue(replay['replayed']);self.assertEqual(replay['result'],out['result'])
            request=d/'request.json';request.write_bytes(core.canonical(req))
            cli=[sys.executable,str(ROOT/'runner/forge_supervise.py'),'--manifest',str(mp),'--source-root',str(src),
                 '--work',str(work),'--forge-root',str(ROOT),'--request',str(request)]
            p=subprocess.run(cli,capture_output=True,text=True,timeout=60)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr);self.assertTrue(json.loads(p.stdout)['replayed'])
            self.assertFalse(sup.execute({'action':'status'})['authority']['execute'])
            sup.execute({'action':'cancel'})
            with self.assertRaisesRegex(core.Blocked,'SUPERVISION_CANCELED'):
                sup.execute({'action':'ask','request_id':'again','question':dict(req['question'],id='again')})
            self.assertFalse(canary.exists());self.assertEqual((src/'sample.py').read_bytes(),data)
            self.assertTrue(all(core.sha(Path(p).read_bytes())==h for p,h in old.items()))
    def test_code_binding_covers_workbench_not_just_analyzer(self):
        # The real verifier still hashes the unchanged analyzer. Intercept only
        # this one source read, to model a different retrieval implementation.
        old=bridge.verify_base(ROOT);real=Path.read_bytes
        def changed(p):
            data=real(p)
            return data+b'\n# changed retrieval\n' if p==ROOT/'forge_core/retrieval.py' else data
        with patch.object(Path,'read_bytes',changed):new=bridge.verify_base(ROOT)
        self.assertEqual(old['analyzer_lock_sha256'],new['analyzer_lock_sha256'])
        self.assertNotEqual(old['workbench_code_sha256'],new['workbench_code_sha256'])
    def test_changed_binding_blocks_completed_mission_resume(self):
        with tempfile.TemporaryDirectory() as t:
            d=Path(t).resolve();s=d/'s';s.mkdir();(s/'note.txt').write_text('Example reference')
            data=(s/'note.txt').read_bytes()
            m={'schema':1,'objective':'Reference test','sources':[{'id':'s','path':'note.txt','kind':'documentation',
               'origin':'fixture://note','capture':'synthetic_fixture','family':'fixture','size':len(data),'sha256':core.sha(data)}],
               'questions':[{'id':'q','query':'reference','intent':'reference','sources':['s']}],
               'budgets':{'max_sources':1,'max_source_bytes':8000000,'max_total_bytes':8000000,'max_questions':2,'packet_bytes':4096}}
            with patch.object(runner,'verify_base',return_value={'mock_binding':'A'}):runner.run(m,s,d/'work',ROOT)
            with patch.object(runner,'verify_base',return_value={'mock_binding':'B'}):
                with self.assertRaises(core.Blocked):runner.run(m,s,d/'work',ROOT)
    def test_changed_workbench_during_run_cannot_publish(self):
        with tempfile.TemporaryDirectory() as t:
            d=Path(t).resolve();s=d/'s';s.mkdir();(s/'note.txt').write_text('Reference');data=(s/'note.txt').read_bytes()
            m={'schema':1,'objective':'Reference test','sources':[{'id':'s','path':'note.txt','kind':'documentation','origin':'fixture://note',
               'capture':'synthetic_fixture','family':'fixture','size':len(data),'sha256':core.sha(data)}],
               'questions':[{'id':'q','query':'reference','intent':'reference','sources':['s']}],
               'budgets':{'max_sources':1,'max_source_bytes':8000000,'max_total_bytes':8000000,'max_questions':2,'packet_bytes':4096}}
            with patch.object(runner,'verify_base',side_effect=[{'mock_binding':'A'},{'mock_binding':'B'}]):
                with self.assertRaisesRegex(core.Blocked,'FORGE_CHANGED_DURING_RUN'):runner.run(m,s,d/'work',ROOT)
            self.assertFalse((d/'work/report.json').exists())
