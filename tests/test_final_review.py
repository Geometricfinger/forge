import importlib.util,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from email.utils import formatdate
from forge_core.common import *
from forge_core.github import Response,delay_from
from forge_core import policy
from test_workbench import Setup

class FinalReview(Setup):
    def test_retry_after_date_honored(self):
        now=self.clock();self.assertGreaterEqual(delay_from({'retry-after':formatdate(now+3600,usegmt=True)},now),3600)
    def test_retry_after_not_shortened(self):
        self.assertEqual(delay_from({'retry-after':'172800'},self.clock()),172800)
    def test_excessive_wait_holds_provider_across_missions(self):
        p=self.store.claim(self.mid);self.disc.accept(p,Response(429,b'',{'retry-after':'172800'},'synthetic_fixture'))
        self.clock.advance(86401);other=self.store.create_mission(self.spec)
        self.assertIsNone(self.store.claim(other));self.assertEqual(self.store.snapshot(other)['status'],'PROVIDER_REVIEW_REQUIRED')
    def test_cancel_during_transport_returns_cancelled(self):
        outer=self
        class T:
            def get(self,p):outer.store.cancel(outer.mid);return Response(200,b'{}',{},'synthetic_fixture')
        r=self.disc.run(self.mid,T(),10);self.assertEqual(r['status'],'CANCELLED')
    def test_empty_discovery_is_no_match_not_synthetic_success(self):
        p=self.store.claim(self.mid);self.disc.accept(p,Response(200,canonical({'items':[],'total_count':0,'incomplete_results':False}),{},'synthetic_fixture'))
        self.assertEqual(self.store.snapshot(self.mid)['status'],'COMPLETED_NO_MATCH')
    def test_outcome_exit_incomplete(self):
        import forge
        for status in ('WORK_REMAINS','RATE_WAIT','BUDGET_EXHAUSTED','COMPLETED_WITH_GAPS','CANCELLED','PROVIDER_REVIEW_REQUIRED'):
            with self.subTest(status=status):self.assertEqual(forge.outcome_exit(status),2)
    def test_outcome_exit_complete_is_not_release(self):
        import forge
        for status in ('COMPLETED_FOR_BOUNDED_MISSION','COMPLETED_NO_MATCH'):
            self.assertEqual(forge.outcome_exit(status),0)
    def test_outcome_exit_unknown_fails_closed(self):
        import forge
        self.assertEqual(forge.outcome_exit('GREEN_TRUST_ME'),2)
    def test_existing_packet_output_does_not_claim(self):
        import forge
        f=self.root/'exists.json';write(f,b'{}')
        with self.assertRaises(Blocked):forge.export_packet(self.store,self.mid,f)
        self.assertEqual(self.store.snapshot(self.mid)['requests_used'],0)
    def test_new_packet_is_token_bound(self):
        import forge
        f=self.root/'new.json';forge.export_packet(self.store,self.mid,f)
        self.assertEqual(loads(read(f))['mission'],self.mid);self.assertEqual(self.store.snapshot(self.mid)['requests_used'],1)
    def test_corrupt_cancel_packet_does_not_invalidate_valid_claim(self):
        p=self.store.claim(self.mid);p['token']='wrong'
        with self.assertRaises(Blocked):self.disc.accept(p,Response(429,b'',{'retry-after':'172800'},'synthetic_fixture'))
        self.assertEqual(self.store.snapshot(self.mid)['counts']['LEASED'],1)
