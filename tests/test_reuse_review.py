"""Second review: constrain claims, preserve history and protect output paths."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from forge_core.common import Blocked,canonical,sha,safe_path
from forge_core.reuse import Casebook
from forge_core.store import Store
from test_reuse_contract import seed,contract

class SecondReview(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.home=Path(self.t.name)/'home'
        self.s=Store.create(self.home,{});self.mid,self.source=seed(self.s);self.b=Casebook(self.s)
    def tearDown(self):self.t.cleanup()
    def setsource(self,v):
        with self.s.tx() as c:c.execute('UPDATE candidate SET result=?,result_hash=?',(canonical(v),sha(canonical(v))))
    def test_status_not_silently_promoted(self):
        v=copy.deepcopy(self.source);v['findings'][0]['status']='UNSUPPORTED'
        self.setsource(v)
        with self.assertRaises(Blocked):self.b.create(self.mid,contract())
    def test_source_runtime_claim_rejected(self):
        v=copy.deepcopy(self.source);v['runtime_verified']=True;self.setsource(v)
        with self.assertRaises(Blocked):self.b.create(self.mid,contract())
    def test_overlapping_scope_ids_rejected(self):
        v=copy.deepcopy(self.source);f=copy.deepcopy(v['findings'][0]);f['resolved_api']='json.dumps'
        v['findings'].append(f);self.setsource(v)
        with self.assertRaises(Blocked):self.b.create(self.mid,contract())
    def test_nonfinite_schema_rejected(self):
        v=contract();v['schema']=True
        with self.assertRaises(Blocked):self.b.create(self.mid,v)
    def test_contract_change_detected(self):
        case=self.b.create(self.mid,contract())
        with self.s.tx() as c:c.execute('UPDATE reuse_case SET body=?',(b'{}',))
        with self.assertRaises(Blocked):self.b.compare(case['id'])
    def test_history_mutation_detected(self):
        case=self.b.create(self.mid,contract());key=case['candidates'][0]['key']
        self.b.record(case['id'],{'candidate':key,'requirement':'context','verdict':'SUPPORTED','note':'review','actor':'op','supersedes':None})
        with self.s.tx() as c:c.execute('UPDATE reuse_evidence SET body=?',(b'{}',))
        with self.assertRaises(Blocked):self.b.compare(case['id'])
    def test_export_must_not_land_inside_engine(self):
        case=self.b.create(self.mid,contract());(self.home/'engine').mkdir()
        with self.assertRaises(Blocked):self.b.export(case['id'],self.home/'temporary'/'..'/'engine'/'new')
    def test_legacy_export_normalizes_escape(self):
        from forge_core.workspace import Workbench
        w=Workbench(self.home);w.guard=lambda:None;(self.home/'engine').mkdir()
        with self.assertRaises(Blocked):w.export(self.mid,self.home/'temporary'/'..'/'engine'/'new')
    def test_evidence_cannot_point_to_other_requirement(self):
        case=self.b.create(self.mid,contract());key=case['candidates'][0]['key']
        with self.assertRaises(Blocked):self.b.record(case['id'],{'candidate':key,'requirement':'none','verdict':'SUPPORTED','note':'review','actor':'op','supersedes':None})
    def test_arbitrary_case_cannot_use_builtin_runner(self):
        c=self.b.create(self.mid,contract())
        with self.assertRaises(Blocked):self.b.attach_fixed_trial(c['id'])
    def test_renderer_explicitly_nonrelease(self):
        c=self.b.create(self.mid,contract());from forge_core.reuse import render
        self.assertIn('No release',render(self.b.compare(c['id'])))
    def test_old_replay_does_not_roll_back_latest_evidence(self):
        c=self.b.create(self.mid,contract());n={'candidate':c['candidates'][0]['key'],'requirement':'context','verdict':'SUPPORTED','note':'first','actor':'op','supersedes':None}
        a=self.b.record(c['id'],n);self.b.record(c['id'],dict(n,verdict='CONTRADICTED',note='second',supersedes=a['id']))
        self.b.record(c['id'],n)
        self.assertEqual(self.b.compare(c['id'])['rows'][0]['requirements'][-1]['state'],'CONTRADICTED')
    def test_path_is_normalized_without_following_links(self):
        self.assertEqual(safe_path(self.home/'unused'/'..'/'x'),self.home/'x')
        (self.home/'link').symlink_to(self.home/'other',target_is_directory=True)
        with self.assertRaises(Blocked):safe_path(self.home/'link'/'..'/'x')
