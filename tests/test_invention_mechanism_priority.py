"""Third-review contract: alphabetic IDs must not hide an equally feasible output match."""
import copy,unittest
from forge_core import opportunities as op
from forge_core.opportunity_examples import example_bundle

class MechanismPriorityTests(unittest.TestCase):
    def fixture(self):
        b=example_bundle();w=b['workflows'][0];n=w['needs'][0]
        base=b['mechanisms'][1]
        ms=[dict(copy.deepcopy(base),id='a'+str(i)) for i in range(7)]
        direct=dict(copy.deepcopy(base),id='z_direct',provides=['identity'])
        return n,w,ms+[direct]
    def test_full_declared_output_survives_six_option_cap(self):
        n,w,ms=self.fixture();out=op.mechanism_options(n,w,ms)
        self.assertEqual(out[0]['id'],'z_direct')
        self.assertEqual(len(out),6)
    def test_equal_input_readiness_prefers_output_fit(self):
        n,w,ms=self.fixture();out=op.mechanism_options(n,w,[ms[0],ms[-1]])
        self.assertEqual(out[0]['output_fit'],'DECLARED_OUTPUT_MATCH')
    def test_unavailable_direct_output_is_not_ready(self):
        n,w,ms=self.fixture();ms[-1]['requires'].append('unavailable_input')
        out=op.mechanism_options(n,w,[ms[0],ms[-1]])
        self.assertEqual(out[0]['id'],'a0')
        self.assertEqual(out[1]['missing_prerequisites'],['unavailable_input'])
    def test_direct_output_stays_a_declaration_not_test_proof(self):
        n,w,ms=self.fixture();out=op.mechanism_options(n,w,[ms[-1]])
        self.assertEqual(out[0]['output_fit'],'DECLARED_OUTPUT_MATCH')
        self.assertIs(out[0]['implementation_tested'],False)
    def test_input_order_does_not_change_priorities(self):
        n,w,ms=self.fixture()
        self.assertEqual(op.mechanism_options(n,w,ms),op.mechanism_options(n,w,list(reversed(ms))))

if __name__=='__main__':unittest.main()
