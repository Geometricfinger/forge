"""Third review: evidence truncation, malformed execution receipts and portable packets."""
import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from forge_core.common import Blocked,canonical,sha,loads
from forge_core.store import Store
from forge_core.reuse import Casebook
from test_reuse_contract import seed,contract

class FinalReview(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.s=Store.create(Path(self.t.name)/'home',{})
        self.mid,self.source=seed(self.s);self.b=Casebook(self.s);self.case=self.b.create(self.mid,contract());self.key=self.case['candidates'][0]['key']
    def tearDown(self):self.t.cleanup()
    def note(self):return {'candidate':self.key,'requirement':'context','verdict':'SUPPORTED','note':'reviewed','actor':'tester','supersedes':None}
    def test_deleted_evidence_tail_detected(self):
        self.b.record(self.case['id'],self.note())
        with self.s.tx() as c:c.execute('DELETE FROM reuse_evidence')
        with self.assertRaises(Blocked):self.b.compare(self.case['id'])
    def test_packet_tamper_fails(self):
        from forge_core.reuse import verify_packet
        p=Path(self.t.name)/'export';self.b.export(self.case['id'],p)
        (p/'Integration_Plan.md').write_text('modified')
        with self.assertRaises(Blocked):verify_packet(p)
    def test_packet_extra_file_fails(self):
        from forge_core.reuse import verify_packet
        p=Path(self.t.name)/'export';self.b.export(self.case['id'],p);(p/'extra.py').write_text('')
        with self.assertRaises(Blocked):verify_packet(p)
    def test_packet_roundtrip(self):
        from forge_core.reuse import verify_packet
        p=Path(self.t.name)/'export';self.b.export(self.case['id'],p)
        self.assertEqual(verify_packet(p)['status'],'PACKET_INTEGRITY_CONFIRMED_REVIEW_REQUIRED')
    def test_shared_event_kind_not_permitted(self):
        with self.s.tx() as c:
            body={'case_id':self.case['id'],'previous':None,'kind':'grant_deploy','payload':{}}
            data=canonical(body);h=sha(data)
            c.execute('INSERT INTO reuse_evidence(id,case_id,body,digest,previous) VALUES(?,?,?,?,?)',('ev_'+h,self.case['id'],data,h,None))
        with self.assertRaises(Blocked):self.b.compare(self.case['id'])
    def test_long_id_rejected_without_lookup_injection(self):
        with self.assertRaises(Blocked):self.b.get('x'*10000)
    def test_structural_criterion_cannot_request_semantic_assurance(self):
        c=contract();c['requirements'][0]['runtime_verified']=True
        with self.assertRaises(Blocked):self.b.create(self.mid,c)
    def test_all_public_review_statuses_never_release(self):
        for verdict in ['SUPPORTED','UNKNOWN','CONTRADICTED']:
            c=self.b.create(self.mid,dict(contract(),title=verdict))
            n=self.note();n['verdict']=verdict;self.b.record(c['id'],n)
            v=self.b.compare(c['id']);self.assertFalse(v['release_approved']);self.assertFalse(v['reuse_approved']);self.assertFalse(v['independent_evaluation'])
