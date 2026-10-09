"""Evidence can be legitimately incomplete, but absent/corrupt test rows are not valid."""
import copy,shutil,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from forge_core import contract_trial as trial
from forge_core.common import Blocked,canonical,loads,sha
from forge_core.workspace import initialize

class IncompletePacketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name);cls.w=initialize(cls.root/'home');cls.out=cls.root/'original'
        # Explicitly exercise the supported missing-reference path, not PATH luck.
        with patch.object(trial,'node_check',return_value={'status':'NOT_RUN','reason':'NODE_NOT_INSTALLED','passed':False}):
            cls.result=trial.run(cls.w,cls.out,with_discovery=False)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def packet(self,mutate):
        dst=self.root/self._testMethodName;shutil.copytree(self.out,dst)
        r=loads((dst/'results.json').read_bytes());mutate(r)
        (dst/'results.json').write_bytes(canonical(r));(dst/'Review.html').write_text(trial.render(r))
        p=loads((dst/'packet.json').read_bytes());p['files']={n:sha((dst/n).read_bytes()) for n in p['files']};(dst/'packet.json').write_bytes(canonical(p))
        return dst
    def test_honest_incomplete_packet_remains_inspectable(self):
        r=trial.verify_packet(self.out);self.assertEqual(r['qualification'],'INCOMPLETE_OR_FAILED');self.assertFalse(r['release_approved'])
    def test_incomplete_cannot_hide_empty_external_cases(self):
        def mutate(r):r['external_vector_report']['cases']=[];r['external_vector_report']['case_count']=0
        with self.assertRaises(Blocked):trial.verify_packet(self.packet(mutate))
    def test_incomplete_cannot_hide_duplicate_external_case(self):
        def mutate(r):r['external_vector_report']['cases'][1]=copy.deepcopy(r['external_vector_report']['cases'][0])
        with self.assertRaises(Blocked):trial.verify_packet(self.packet(mutate))
    def test_incomplete_cannot_hide_false_summary(self):
        def mutate(r):r['external_vector_report']['summary']['selected_component']=999
        with self.assertRaises(Blocked):trial.verify_packet(self.packet(mutate))
    def test_incomplete_cannot_hide_empty_admission(self):
        def mutate(r):r['admission_report']['cases']=[];r['admission_report']['case_count']=0
        with self.assertRaises(Blocked):trial.verify_packet(self.packet(mutate))
    def test_incomplete_cannot_hide_false_pass_boolean(self):
        def mutate(r):r['external_vector_report']['cases'][0]['selected_component']['passed']=1
        with self.assertRaises(Blocked):trial.verify_packet(self.packet(mutate))
    def test_incomplete_cannot_hide_changed_external_expectation(self):
        def mutate(r):r['external_vector_report']['cases'][0]['expected']='altered'
        with self.assertRaises(Blocked):trial.verify_packet(self.packet(mutate))
    def test_incomplete_cannot_hide_admission_aggregate(self):
        def mutate(r):r['admission_report']['passed']=False
        with self.assertRaises(Blocked):trial.verify_packet(self.packet(mutate))
    def test_incomplete_cannot_hide_forged_actual_pass(self):
        def mutate(r):r['external_vector_report']['cases'][0]['selected_component']['actual']='wrong'
        with self.assertRaises(Blocked):trial.verify_packet(self.packet(mutate))
    def test_recorded_real_failure_can_remain_incomplete(self):
        def mutate(r):
            row=r['external_vector_report']['cases'][0]['selected_component'];row['actual']='wrong';row['passed']=False
            r['external_vector_report']['summary']['selected_component']-=1
        r=trial.verify_packet(self.packet(mutate));self.assertEqual(r['qualification'],'INCOMPLETE_OR_FAILED')
if __name__=='__main__':unittest.main()
