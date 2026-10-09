"""Frozen defect-detection checks; only trusted synthetic data is executed."""
import copy,unittest
from unittest.mock import patch
from forge_core import interop,contract_trial
from forge_core.common import Blocked
class FaultContract(unittest.TestCase):
    def test_duplicate_rejected(self):
        with self.assertRaises(Blocked):interop.normalize_record(b'{"x":1,"x":2}')
    def test_changed_component_rejected(self):
        with patch.object(interop,'PINS',{'forge_core/_vendor/rfc8785/__init__.py':'0'*64}):
            with self.assertRaises(Blocked):interop.normalize_record(b'{}')
    def test_missing_reference_not_ready(self):
        r={'external_vector_report':contract_trial.external_vectors(),'reference_report':{'status':'NOT_RUN','passed':False},'release_approved':False,'independent_evaluation':False,'historical_ids_migrated':False,'arbitrary_source_execution':False,'model_calls':0,'external_network_requests':0}
        self.assertFalse(contract_trial.evaluate_gate(r))
    def test_out_of_domain_float_rejected(self):
        with self.assertRaises(Blocked):interop.normalize_record(b'{"x":9007199254740993.0}')
if __name__=='__main__':unittest.main()
