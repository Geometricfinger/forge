"""Pre-implementation declaration-contract checks, not semantic ground truth."""
import copy, unittest
from forge_core.transfer_review import assess, ContractError

def slot(kind='mesh',unit='mm',frame='object',clock='n/a'):
    return dict(kind=kind,unit=unit,frame=frame,clock=clock)
def fixture():
    c={'id':'surface-axis','inputs':{'mesh':slot()},'outputs':[slot('axes')],
       'effects':[],'preserves':['physical_scale'],'domain':'geometry'}
    t={'roles':[dict(id='orientation',mode='direct',contract=slot('axes'))],
       'allowed_effects':[],'required_preserves':['physical_scale'],'domain':'textiles'}
    return c,t,{'mesh':slot()}

class ContractChecks(unittest.TestCase):
    def test_cross_domain_compatible(self):
        c,t,a=fixture();self.assertEqual(assess(c,t,a)['status'],'DECLARED_DIRECT_FIT')
    def test_same_domain_wrong_output(self):
        c,t,a=fixture();t['domain']=c['domain'];c['outputs']=[slot('animal_visits')]
        self.assertEqual(assess(c,t,a)['status'],'NO_DECLARED_ROLE')
    def test_domain_rename_does_not_change_decision(self):
        c,t,a=fixture();r=assess(c,t,a);c['domain']='X';t['domain']='Y';self.assertEqual(r,assess(c,t,a))
    def test_misleading_id_not_used_as_semantics(self):
        c,t,a=fixture();c['id']='anything';self.assertEqual(assess(c,t,a)['status'],'DECLARED_DIRECT_FIT')
    def test_output_unit_mismatch(self):
        c,t,a=fixture();c['outputs'][0]['unit']='in';self.assertEqual(assess(c,t,a)['status'],'CONTRACT_MISMATCH')
    def test_input_unit_mismatch(self):
        c,t,a=fixture();a['mesh']['unit']='in';self.assertEqual(assess(c,t,a)['status'],'CONTRACT_MISMATCH')
    def test_output_frame_mismatch(self):
        c,t,a=fixture();c['outputs'][0]['frame']='patient';self.assertEqual(assess(c,t,a)['status'],'CONTRACT_MISMATCH')
    def test_input_frame_mismatch(self):
        c,t,a=fixture();a['mesh']['frame']='world';self.assertEqual(assess(c,t,a)['status'],'CONTRACT_MISMATCH')
    def test_clock_mismatch(self):
        c,t,a=fixture();c['outputs'][0]['clock']='device';t['roles'][0]['contract']['clock']='host';self.assertEqual(assess(c,t,a)['status'],'CONTRACT_MISMATCH')
    def test_unknown_output_unit(self):
        c,t,a=fixture();c['outputs'][0]['unit']='unknown';self.assertEqual(assess(c,t,a)['status'],'UNRESOLVED_CONTRACT')
    def test_unknown_available_input(self):
        c,t,a=fixture();a['mesh']['kind']='unknown';self.assertEqual(assess(c,t,a)['status'],'UNRESOLVED_CONTRACT')
    def test_unknown_output_kind_is_not_incompatible(self):
        c,t,a=fixture();c['outputs'][0]['kind']='unknown';self.assertEqual(assess(c,t,a)['status'],'UNRESOLVED_CONTRACT')
    def test_missing_input(self):
        c,t,a=fixture();self.assertEqual(assess(c,t,{})['status'],'MISSING_INPUTS')
    def test_unknown_and_missing_both_retained(self):
        c,t,a=fixture();c['outputs'][0]['unit']='unknown';r=assess(c,t,{})
        self.assertEqual(r['status'],'UNRESOLVED_CONTRACT');self.assertEqual(r['missing_inputs'],['mesh'])
    def test_forbidden_effect(self):
        c,t,a=fixture();c['effects']=['network'];self.assertEqual(assess(c,t,a)['status'],'CONTRACT_MISMATCH')
    def test_unknown_effect(self):
        c,t,a=fixture();c['effects']=['unknown'];self.assertEqual(assess(c,t,a)['status'],'UNRESOLVED_CONTRACT')
    def test_required_preservation_unknown(self):
        c,t,a=fixture();c['preserves']=[];self.assertEqual(assess(c,t,a)['status'],'UNRESOLVED_CONTRACT')
    def test_support_is_not_direct_fit(self):
        c,t,a=fixture();t['roles'][0]['mode']='support';self.assertEqual(assess(c,t,a)['status'],'DECLARED_SUPPORT_FIT')
    def test_required_input_type_mismatch(self):
        c,t,a=fixture();a['mesh']['kind']='point_cloud';self.assertEqual(assess(c,t,a)['status'],'CONTRACT_MISMATCH')
    def test_matching_later_output_not_hidden(self):
        c,t,a=fixture();c['outputs'].insert(0,slot('other'));self.assertEqual(assess(c,t,a)['status'],'DECLARED_DIRECT_FIT')
    def test_support_before_incompatible_direct(self):
        c,t,a=fixture();t['roles'].append(dict(id='support',mode='support',contract=slot('axes')));t['roles'][0]['contract']['unit']='in'
        self.assertEqual(assess(c,t,a)['status'],'DECLARED_SUPPORT_FIT')
    def test_no_automatic_unit_conversion(self):
        c,t,a=fixture();a['mesh']['unit']='cm';r=assess(c,t,a);self.assertFalse(r['execution_authorized']);self.assertEqual(r['status'],'CONTRACT_MISMATCH')
    def test_no_authority_on_compatible(self):
        c,t,a=fixture();r=assess(c,t,a)
        for key in ('runtime_verified','annotation_truth_verified','execution_authorized','reuse_approved','market_validated','novelty_established','release_approved'):self.assertIs(r[key],False)
    def test_input_objects_unchanged(self):
        c,t,a=fixture();old=copy.deepcopy((c,t,a));assess(c,t,a);self.assertEqual(old,(c,t,a))
    def test_no_shared_result(self):
        c,t,a=fixture();r=assess(c,t,a);r['missing_inputs'].append('fake');self.assertEqual(assess(c,t,a)['missing_inputs'],[])
    def test_unknown_authority_field_rejected(self):
        c,t,a=fixture();c['execute']='shell';self.assertRaises(ContractError,assess,c,t,a)
    def test_missing_field_rejected(self):
        c,t,a=fixture();del c['effects'];self.assertRaises(ContractError,assess,c,t,a)
    def test_duplicate_role_rejected(self):
        c,t,a=fixture();t['roles'].append(copy.deepcopy(t['roles'][0]));self.assertRaises(ContractError,assess,c,t,a)
    def test_invalid_role_rejected(self):
        c,t,a=fixture();t['roles'][0]['mode']='approved';self.assertRaises(ContractError,assess,c,t,a)
    def test_nonfinite_payload_rejected(self):
        c,t,a=fixture();c['outputs'][0]['unit']=float('nan');self.assertRaises(ContractError,assess,c,t,a)
    def test_empty_role_is_not_green(self):
        c,t,a=fixture();t['roles']=[];self.assertEqual(assess(c,t,a)['status'],'NO_DECLARED_ROLE')
    def test_empty_output_is_not_green(self):
        c,t,a=fixture();c['outputs']=[];self.assertEqual(assess(c,t,a)['status'],'NO_DECLARED_ROLE')
    def test_extra_observed_information_not_required(self):
        c,t,a=fixture();a['spare']=slot('image','n/a','n/a');self.assertEqual(assess(c,t,a)['status'],'DECLARED_DIRECT_FIT')
    def test_explicit_effect_allowed_not_executed(self):
        c,t,a=fixture();c['effects']=['write_scratch'];t['allowed_effects']=['write_scratch'];r=assess(c,t,a);self.assertEqual(r['status'],'DECLARED_DIRECT_FIT');self.assertFalse(r['execution_authorized'])
    def test_overlarge_contract_rejected(self):
        c,t,a=fixture();c['id']='x'*201;self.assertRaises(ContractError,assess,c,t,a)
    def test_evidence_free_contract_not_marked_verified(self):
        c,t,a=fixture();self.assertFalse(assess(c,t,a)['annotation_truth_verified'])
if __name__=='__main__':unittest.main()
