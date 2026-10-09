"""Second review: evidence authority and generated workflow invariants."""
import copy, json, random, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from forge_core import opportunities as op
from forge_core.common import Blocked, canonical, sha
from forge_core.opportunity_examples import example_bundle


class PlanReview(unittest.TestCase):
    def report(self):return op.evaluate(example_bundle())
    def test_changed_plan_cannot_authorize_agent_execution(self):
        r=self.report();r['hypotheses'][0]['next_investigation']['execution_authorized']=True
        self.assertRaises(Blocked,op.agent_packet,r)
    def test_changed_task_cannot_authorize_source_acquisition(self):
        r=self.report();r['hypotheses'][0]['next_investigation']['tasks'][0]['source_acquisition_authorized']=True
        self.assertRaises(Blocked,op.agent_packet,r)
    def test_extra_plan_command_is_rejected(self):
        r=self.report();r['hypotheses'][0]['next_investigation']['command']='execute arbitrary source'
        self.assertRaises(Blocked,op.agent_packet,r)
    def test_removed_falsifier_is_rejected(self):
        r=self.report();r['hypotheses'][0]['next_investigation']['tasks'][0]['falsifier']=''
        self.assertRaises(Blocked,op.agent_packet,r)
    def test_current_report_requires_its_plan(self):
        r=self.report();r['hypotheses'][0].pop('next_investigation')
        self.assertRaises(Blocked,op.agent_packet,r)
    def test_malformed_plan_fails_cleanly(self):
        r=self.report();r['hypotheses'][0]['next_investigation']=[]
        self.assertRaises(Blocked,op.agent_packet,r)
    def test_valid_plan_is_exported_without_execution_permission(self):
        r=self.report();p=op.agent_packet(r)
        self.assertEqual(p['tasks'][0]['next_investigation'],r['hypotheses'][0]['next_investigation'])
        self.assertIs(p['authority']['execute_source'],False)
    def test_rehashed_store_cannot_promote_new_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            w=SimpleNamespace(home=Path(tmp),guard=lambda:None);book=op.OpportunityStore(w)
            r=book.run(example_bundle());p=book.path(r['id'])/'record.json';v=json.loads(p.read_bytes())
            v['payload']['report']['hypotheses'][0]['next_investigation']['execution_authorized']=True
            v['sha256']=sha(canonical(v['payload']));p.write_bytes(canonical(v))
            self.assertRaises(Blocked,book.get,r['id'])
    def test_valid_packet_readback_remains_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            w=SimpleNamespace(home=Path(tmp)/'home',guard=lambda:None);book=op.OpportunityStore(w)
            r=book.run(example_bundle());dest=Path(tmp)/'export';book.export(r['id'],dest)
            self.assertEqual(op.verify_export(dest)['run'],r['id'])
    def test_plan_is_visible_in_rendered_report(self):
        self.assertIn('Next evidence to collect',op.render(self.report()))


class WorkflowReferenceReview(unittest.TestCase):
    def test_generated_forward_chains_match_reference(self):
        rng=random.Random(20260921)
        for trial in range(128):
            fields=['f'+str(i) for i in range(8)];initial=rng.sample(fields,rng.randrange(0,5));steps=[]
            for i in range(8):
                steps.append({'id':'s'+str(i),'requires':rng.sample(fields,rng.randrange(0,4)),
                              'produces':rng.sample(fields,rng.randrange(0,3))})
            # Independent tiny dataflow oracle: Boolean reachability only.
            known={f: f in initial for f in fields}; expected={}
            for step in steps:
                absent={f for f in step['requires'] if not known[f]};expected[step['id']]=absent
                if not absent:
                    for f in step['produces']:known[f]=True
            with self.subTest(trial=trial):
                self.assertEqual(op.missing_fields({'initial_fields':initial,'steps':steps}),expected)
    def test_supplying_more_inputs_cannot_increase_unmet_fields(self):
        rng=random.Random(83)
        for trial in range(64):
            fields=['f'+str(i) for i in range(6)];initial=rng.sample(fields,2)
            steps=[{'id':str(i),'requires':rng.sample(fields,2),'produces':[rng.choice(fields)]} for i in range(5)]
            a=op.missing_fields({'initial_fields':initial,'steps':steps})
            b=op.missing_fields({'initial_fields':initial+[rng.choice(fields)],'steps':steps})
            self.assertTrue(all(b[k]<=a[k] for k in a))


if __name__=='__main__':unittest.main()
