"""Second review: crash windows, cache binding, conservative evidence."""
import json,shutil,unittest
from unittest.mock import patch
from pathlib import Path
import test_analysis_recovery as fixture
from forge_core import corpus
from forge_core.common import Blocked,canonical,sha

class RecoveryReviewTests(unittest.TestCase):
    setUp=fixture.RecoveryTests.setUp
    tearDown=fixture.RecoveryTests.tearDown
    prepare=fixture.RecoveryTests.prepare
    runit=fixture.RecoveryTests.runit
    def test_crash_between_segment_success_and_container_publication(self):
        self.runit();real=corpus.write
        def fail(path,data,new=False):
            if Path(path).parent.name=='checkpoints':raise OSError('simulated aggregate publication loss')
            return real(path,data,new=new)
        with patch.object(corpus,'write',side_effect=fail):middle=self.runit()
        self.assertEqual(middle['status'],'INCOMPLETE');n=len(self.p.calls)
        final=self.runit();self.assertEqual(final['status'],'COMPLETED_FOR_SELECTED_CONTAINERS');self.assertEqual(len(self.p.calls),n)
    def test_finish_missing_after_cached_success_is_reconciled(self):
        self.p=fixture.Probe(0);self.runit();p=next((self.out/'analysis_attempts').glob('*/001.finish.json'));p.unlink()
        final=self.runit();self.assertTrue(p.exists());self.assertTrue(all(s['attempts'][-1]['state']=='SUCCEEDED' for s in final['recovery']['segments']))
    def test_success_cache_cannot_ignore_swapped_journals(self):
        self.p=fixture.Probe(0);self.runit();a,b=sorted((self.out/'analysis_attempts').iterdir());temp=a.parent/'swap';a.rename(temp);b.rename(a);temp.rename(b)
        try:final=self.runit()
        except Blocked:return
        self.assertEqual(final['status'],'INCOMPLETE')
    def test_inconsistent_cached_gap_list_rejected(self):
        self.runit();p=next((self.out/'checkpoints').glob('*.json'));obj=json.loads(p.read_bytes());obj['result']['hound_errors']=[];obj['result_sha256']=sha(canonical(obj['result']));p.write_bytes(canonical(obj))
        final=self.runit();self.assertEqual(final['status'],'INCOMPLETE')
    def test_attempt_budget_survives_aggregate_checkpoint_removal(self):
        self.p=fixture.Probe(100)
        for _ in range(6):
            self.runit()
            for p in (self.out/'checkpoints').glob('*.json'):p.unlink()
        self.assertEqual(sum(h==sha(fixture.A) for _,h in self.p.calls),3)
    def test_foreign_attempt_member_rejected(self):
        self.runit();p=next((self.out/'analysis_attempts').iterdir());(p/'extra.json').write_bytes(b'{}')
        with self.assertRaises(Blocked):self.runit()
    def test_malformed_finish_rejected(self):
        self.runit();p=next((self.out/'analysis_attempts').glob('*/001.finish.json'));p.write_bytes(b'[]')
        with self.assertRaises(Blocked):self.runit()
    def test_changed_source_and_no_source_rewrite(self):
        before={p.name:(sha(p.read_bytes()),p.stat().st_mtime_ns) for p in self.src.iterdir()};self.runit();self.runit()
        after={p.name:(sha(p.read_bytes()),p.stat().st_mtime_ns) for p in self.src.iterdir()};self.assertEqual(before,after)
    def test_exhaustion_survives_new_analyzer_instance(self):
        self.p=fixture.Probe(100)
        for _ in range(3):self.runit()
        self.p=fixture.Probe(0);r=self.runit();self.assertEqual(self.p.calls,[]);self.assertEqual(r['status'],'COMPLETED_WITH_GAPS')
    def test_original_gap_reason_retained_at_exhaustion(self):
        self.p=fixture.Probe(100)
        for _ in range(5):r=self.runit()
        self.assertTrue(any(x['reason']=='TOOL_TIMEOUT' for x in r['gaps']));self.assertEqual(r['recovery']['exhausted_segments'],1)
    def test_recovery_report_visible_in_rendered_report(self):
        self.runit();r=self.runit();html=corpus.render(r);self.assertIn('Analysis recovery',html);self.assertIn('original failures remain',html)
    def test_exhausted_starts_cannot_be_charged_a_fourth_time(self):
        self.p=fixture.Probe(100,'INTERRUPT')
        for _ in range(4):
            try:r=self.runit()
            except KeyboardInterrupt:pass
        starts=list((self.out/'analysis_attempts').glob('*/004.start.json'));self.assertEqual(starts,[])
        self.assertEqual(sum(h==sha(fixture.A) for _,h in self.p.calls),3)
if __name__=='__main__':unittest.main()
