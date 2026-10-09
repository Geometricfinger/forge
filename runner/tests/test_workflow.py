"""Controller boundary tests; mocked acquisition is labeled and separate from live-Hound trials."""
import copy, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from research_runner import runner
from research_runner.core import Blocked, check_manifest, canonical, digest
from test_contract import manifest

class Workflow(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.src=self.base/'sources';self.src.mkdir();(self.src/'x.py').write_bytes(b'pass')
        self.work=self.base/'work';self.forge=self.base/'forge';self.forge.mkdir()
        self.calls=[];self.m=manifest()
        self.p1=patch.object(runner,'verify_base',return_value={'mocked_for_controller_test':True})
        self.p2=patch.object(runner,'inspect_source',side_effect=self.source)
        self.p3=patch.object(runner,'investigate',side_effect=self.query)
        self.p1.start();self.p2.start();self.p3.start()
    def tearDown(self):
        self.p3.stop();self.p2.stop();self.p1.stop();self.tmp.cleanup()
    def source(self,s,*args):
        self.calls.append('source');return {'status':'CODE_INDEXED','source':s,'records':[],'references':[],'gaps':[]}
    def query(self,q,s,*args):
        self.calls.append('query');return {'id':q['id'],'query':q['query'],'status':'NO_EVIDENCE_IN_SELECTED_SOURCES','results':[], 'references':[], 'next_action':'REVIEW'}
    def runit(self,steps=1000,callback=None):return runner.run(self.m,self.src,self.work,self.forge,steps,callback)
    def test_pause_resume(self):
        self.assertEqual(self.runit(1)['status'],'PAUSED_BUDGET');self.assertEqual(self.calls,['source'])
        self.assertEqual(self.runit()['new_tasks'],2);self.assertEqual(self.calls,['source','query'])
        self.assertEqual(self.runit()['new_tasks'],0)
    def test_changed_source_blocks_cached(self):
        self.runit();(self.src/'x.py').write_bytes(b'fail')
        with self.assertRaises(Blocked):self.runit()
    def test_cancel_stops_new_tasks(self):
        self.runit(1);(self.work/'CANCEL.json').write_bytes(canonical({'canceled':True}))
        self.assertEqual(self.runit()['status'],'CANCELED');self.assertEqual(self.calls,['source'])
    def test_zero_budget_starts_nothing(self):
        self.assertEqual(self.runit(0)['new_tasks'],0);self.assertEqual(self.calls,[])
    def test_report_repeat_bytes(self):
        self.runit();before=(self.work/'report.json').read_bytes();self.runit()
        self.assertEqual(before,(self.work/'report.json').read_bytes())
    def test_checkpoint_damage_stops_reuse(self):
        self.runit(1);p=next((self.work/'checkpoints').glob('*.json'));p.write_bytes(b'{}')
        with self.assertRaises(Blocked):self.runit()
    def test_interruption_after_checkpoint_does_not_repeat(self):
        def stop(*args):raise RuntimeError('injected interrupt')
        with self.assertRaises(RuntimeError):self.runit(callback=stop)
        self.assertEqual(self.calls,['source']);self.assertEqual(self.runit()['new_tasks'],2)
        self.assertEqual(self.calls,['source','query'])
    def test_failed_acquisition_is_visible(self):
        with patch.object(runner,'inspect_source',side_effect=Blocked('SOURCE_MISSING')):
            result=self.runit()
        self.assertEqual(result['status'],'INCOMPLETE_SOURCE_EVIDENCE')
        report=json.loads((self.work/'report.json').read_text());self.assertEqual(report['sources'][0]['status'],'BLOCKED')
    def test_changed_manifest_requires_new_workspace(self):
        self.runit(1);self.m['objective']='Different goal'
        with self.assertRaises(Blocked):self.runit()
    def test_output_cannot_replace_source(self):
        with self.assertRaises(Blocked):runner.run(self.m,self.src,self.src,self.forge)
    def test_packet_does_not_authorize_source_execution(self):
        self.runit();p=json.loads((self.work/'supervisor_packet.json').read_text())
        self.assertFalse(p['authority']['execute']);self.assertFalse(p['authority']['deploy'])

class DerivationContract(unittest.TestCase):
    def test_structured_derived_selection_preserved(self):
        m=manifest();m['sources'][0]['derived_from']={'archive_sha256':'a'*64,'members':['x.py'],'fresh_drive_read':False}
        self.assertEqual(check_manifest(m),m)
    def test_derived_record_rejects_authority(self):
        m=manifest();m['sources'][0]['derived_from']={'archive_sha256':'a'*64,'members':['x.py'],'fresh_drive_read':False,'execute':True}
        with self.assertRaises(Blocked):check_manifest(m)
    def test_derived_member_traversal(self):
        m=manifest();m['sources'][0]['derived_from']={'archive_sha256':'a'*64,'members':['../x.py'],'fresh_drive_read':False}
        with self.assertRaises(Blocked):check_manifest(m)
if __name__=='__main__':unittest.main()
