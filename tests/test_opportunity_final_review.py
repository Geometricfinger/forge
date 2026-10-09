"""Final review: packet authority/type validation and cooperative feedback writes."""
import json,tempfile,unittest
from pathlib import Path
from forge_core.common import Blocked,canonical,sha
from forge_core.opportunities import OpportunityStore,verify_export
from forge_core.opportunity_examples import example_bundle
from forge_core.corpus import _lock

class FinalEvidenceReview(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        class W:
            def __init__(self,home):self.home=home
            def guard(self):pass
        self.book=OpportunityStore(W(self.root/'home'));self.r=self.book.run(example_bundle())
        self.out=self.root/'packet';self.book.export(self.r['id'],self.out)
    def tearDown(self):self.temp.cleanup()
    def modify_receipt(self,fn):
        p=self.out/'receipt.json';r=json.loads(p.read_bytes());fn(r);p.write_bytes(canonical(r))
    def rehash_payload(self,name,fn):
        p=self.out/name;r=json.loads(p.read_bytes());fn(r);p.write_bytes(canonical(r))
        self.modify_receipt(lambda rec:rec['files'].__setitem__(name,sha(p.read_bytes())))
    def note(self):return {'hypothesis':self.r['hypotheses'][0]['id'],'decision':'REVISE','reason':'Check the handoff, not the popularity.','actor':'Owner'}
    def test_boolean_receipt_schema_rejected(self):
        self.modify_receipt(lambda r:r.__setitem__('schema',True));self.assertRaises(Blocked,verify_export,self.out)
    def test_malformed_receipt_files_rejected_cleanly(self):
        self.modify_receipt(lambda r:r.__setitem__('files',list(r['files'])));self.assertRaises(Blocked,verify_export,self.out)
    def test_rehashed_agent_authority_still_rejected(self):
        self.rehash_payload('agent_request.json',lambda r:r['authority'].__setitem__('execute_source',True));self.assertRaises(Blocked,verify_export,self.out)
    def test_rehashed_agent_run_mismatch_rejected(self):
        self.rehash_payload('agent_request.json',lambda r:r.__setitem__('run','opp_'+'a'*64));self.assertRaises(Blocked,verify_export,self.out)
    def test_rehashed_feedback_validation_claim_rejected(self):
        def alter(r):r.append({'run':self.r['id'],'note':self.note(),'kind':'VALIDATED_DEMAND','authority':{}})
        self.rehash_payload('feedback.json',alter);self.assertRaises(Blocked,verify_export,self.out)
    def test_feedback_respects_publication_lock(self):
        with _lock(self.book.path(self.r['id'])/'feedback.lock'):
            self.assertRaises(Blocked,self.book.feedback,self.r['id'],self.note())
        self.assertEqual(self.book.feedbacks(self.r['id']),[])
    def test_feedback_retry_after_lock_released(self):
        with _lock(self.book.path(self.r['id'])/'feedback.lock'):
            try:self.book.feedback(self.r['id'],self.note())
            except Blocked:pass
        a=self.book.feedback(self.r['id'],self.note());b=self.book.feedback(self.r['id'],self.note())
        self.assertEqual(a,b);self.assertEqual(len(self.book.feedbacks(self.r['id'])),1)
    def test_valid_packet_still_passes(self):self.assertEqual(verify_export(self.out)['status'],'OPPORTUNITY_PACKET_CHECKSUMS_VERIFIED')

if __name__=='__main__':unittest.main()
