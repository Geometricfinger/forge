"""Synthetic integration cases for the public opportunity-review command."""
import copy,hashlib,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from forge_core import opportunities as op
from forge_core.common import canonical,sha
from forge_core.transfer_example import example,slot
from forge_core.transfer_review import ContractError,evaluate_with_contracts
ROOT=Path(__file__).resolve().parents[1]

def pair(value,mid):
    return next(p for h in value['transfer_reviews'] for p in h['pairs'] if p['mechanism']==mid)

def refresh(b,r):
    r['bundle_sha256']=sha(canonical(b))
    for m in b['mechanisms']:
        if m['id'] in r['mechanisms']:r['mechanisms'][m['id']]['mechanism_sha256']=sha(canonical(m))
    for w in b['workflows']:
        if w['id'] in r['workflows']:r['workflows'][w['id']]['workflow_sha256']=sha(canonical(w))

class TransferIntegration(unittest.TestCase):
    def test_original_report_retained(self):
        b,r=example();self.assertEqual(evaluate_with_contracts(b,[],r)['original_report'],op.evaluate(b,[]))
    def test_support_not_complete_product(self):
        b,r=example();out=evaluate_with_contracts(b,[],r)
        self.assertEqual(pair(out,'record')['status'],'DECLARED_SUPPORT_FIT')
        self.assertFalse(pair(out,'record')['annotation_truth_verified'])
    def test_missing_information_withheld(self):
        b,r=example();out=pair(evaluate_with_contracts(b,[],r),'matching')
        self.assertEqual(out['status'],'MISSING_INPUTS');self.assertEqual(out['missing_inputs'],['distinguishing_information'])
    def test_changed_bundle_refused(self):
        b,r=example();b['title']='changed'
        with self.assertRaisesRegex(ContractError,'BUNDLE_BINDING'):evaluate_with_contracts(b,[],r)
    def test_changed_source_binding_refused(self):
        b,r=example();r['mechanisms']['record']['source_bindings']={}
        with self.assertRaisesRegex(ContractError,'SOURCE_BINDING'):evaluate_with_contracts(b,[],r)
    def test_output_must_bind_to_mechanism(self):
        b,r=example();r['mechanisms']['record']['contract']['outputs'][0]['kind']='invented'
        with self.assertRaisesRegex(ContractError,'OUTPUT_REQUIREMENT_BINDING'):evaluate_with_contracts(b,[],r)
    def test_input_must_bind_to_mechanism(self):
        b,r=example();r['mechanisms']['record']['contract']['inputs']={}
        with self.assertRaisesRegex(ContractError,'INPUT_REQUIREMENT_BINDING'):evaluate_with_contracts(b,[],r)
    def test_contract_cannot_claim_verification(self):
        b,r=example();r['annotation_basis']='INDEPENDENTLY_VERIFIED'
        with self.assertRaisesRegex(ContractError,'ANNOTATION_AUTHORITY'):evaluate_with_contracts(b,[],r)
    def test_existing_solution_is_not_new(self):
        b,r=example();b['observations'][0]['kind']='supported';refresh(b,r)
        out=evaluate_with_contracts(b,[],r)
        self.assertEqual(out['transfer_reviews'][0]['status'],'ORIGINAL_EVIDENCE_GATE_RETAINED')
        self.assertEqual(out['transfer_reviews'][0]['experiment_candidates'],[])
    def test_unused_need_authority_rejected(self):
        b,r=example();b['observations'][0]['kind']='supported';refresh(b,r)
        r['workflows']['handoff']['need_contracts']['identity']['execute']=True
        with self.assertRaisesRegex(ContractError,'CONTRACT_FIELDS'):evaluate_with_contracts(b,[],r)
    def test_missing_annotation_unknown(self):
        b,r=example();del r['mechanisms']['record']
        self.assertEqual(pair(evaluate_with_contracts(b,[],r),'record')['status'],'UNRESOLVED_CONTRACT')
    def test_supplied_input_without_type_unknown(self):
        b,r=example();del r['workflows']['handoff']['available_contracts']['artifact']
        self.assertEqual(pair(evaluate_with_contracts(b,[],r),'record')['status'],'UNRESOLVED_CONTRACT')
    def test_later_output_not_used_before_creation(self):
        b,r=example();b['workflows'][0]['steps'][1]['produces'].append('distinguishing_information')
        r['workflows']['handoff']['available_contracts']['distinguishing_information']=slot('distinguishing_information');refresh(b,r)
        self.assertEqual(pair(evaluate_with_contracts(b,[],r),'matching')['status'],'MISSING_INPUTS')
    def test_original_inputs_and_repeatability(self):
        b,r=example();before=copy.deepcopy((b,r));a=evaluate_with_contracts(b,[],r)
        self.assertEqual(a,evaluate_with_contracts(b,[],r));self.assertEqual((b,r),before)
    def test_conditional_option_before_cutoff(self):
        b,r=example();template=copy.deepcopy(b['mechanisms'][1]);row=copy.deepcopy(r['mechanisms']['record'])
        for i in range(7):
            m=copy.deepcopy(template);m['id']='helper-'+str(i);b['mechanisms'].append(m)
            c=copy.deepcopy(row);c['contract']['id']=m['id'];r['mechanisms'][m['id']]=c
        refresh(b,r);out=evaluate_with_contracts(b,[],r)
        self.assertNotIn('matching',[m['id'] for m in out['original_report']['hypotheses'][0]['mechanisms']])
        self.assertEqual(pair(out,'matching')['status'],'MISSING_INPUTS')
    def test_domain_does_not_imply_interoperability(self):
        b,r=example();r['mechanisms']['record']['contract']['domain']='unrelated domain'
        self.assertEqual(pair(evaluate_with_contracts(b,[],r),'record')['status'],'DECLARED_SUPPORT_FIT')
    def test_cli_with_public_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();b,r=example()
            (root/'bundle.json').write_bytes(canonical(b));(root/'registry.json').write_bytes(canonical(r))
            cmd=[sys.executable,str(ROOT/'forge.py'),'--home',str(root/'home'),'opportunity-review',
                 '--bundle',str(root/'bundle.json'),'--registry',str(root/'registry.json'),'--out',str(root/'review.json')]
            p=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            out=json.loads(p.stdout);self.assertFalse(out['authority']['execute_source'])
            self.assertEqual(json.loads((root/'review.json').read_text()),out)
            self.assertEqual(pair(out,'matching')['status'],'MISSING_INPUTS')
            # Atomic export refuses replacing an earlier review.
            again=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
            self.assertEqual(again.returncode,2);self.assertEqual(json.loads(again.stdout)['reason'],'OUTPUT_CONFLICT')
