"""Bounded declared-contract review for FORGE. No learned semantic verification.

Cross-domain reuse is allowed when declared contracts fit. Contradictions and
unknowns remain visible. This module never imports or executes retrieved code,
fetches data, installs packages, edits the original report, or approves release.
"""
from __future__ import annotations
import copy, hashlib, json, re
from typing import Any

VERSION='forge-transfer-review-0.1'
MAX_BYTES=384_000
ATTRS=('kind','unit','frame','clock')
STATUS_ORDER={'DECLARED_DIRECT_FIT':0,'DECLARED_SUPPORT_FIT':1,'MISSING_INPUTS':2,
              'UNRESOLVED_CONTRACT':3,'NO_DECLARED_ROLE':4,'CONTRACT_MISMATCH':5}

class ContractError(ValueError):
    pass

def canonical(value: Any)->bytes:
    try:return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
    except (TypeError,ValueError,RecursionError) as e:raise ContractError('INVALID_JSON') from e

def digest(value: Any)->str:return hashlib.sha256(canonical(value)).hexdigest()

def exact(value: Any,fields:set[str])->None:
    if not isinstance(value,dict) or set(value)!=fields:raise ContractError('CONTRACT_FIELDS')

def word(value: Any)->None:
    if not isinstance(value,str) or not value.strip() or len(value)>200 or '\x00' in value:raise ContractError('CONTRACT_TEXT')

def words(value: Any)->None:
    if not isinstance(value,list) or len(value)>64:raise ContractError('CONTRACT_LIST')
    for item in value:word(item)
    if len(set(value))!=len(value):raise ContractError('DUPLICATE_VALUES')

def slot(value: Any)->None:
    exact(value,set(ATTRS))
    for v in value.values():word(v)

def slots(value: Any)->None:
    if not isinstance(value,dict) or len(value)>80:raise ContractError('INPUT_CONTRACTS')
    for k,v in value.items():word(k);slot(v)

def validate(capability:dict, task:dict, available:dict)->None:
    if len(canonical([capability,task,available]))>MAX_BYTES:raise ContractError('CONTRACT_TOO_LARGE')
    exact(capability,{'id','inputs','outputs','effects','preserves','domain'})
    word(capability['id']);word(capability['domain']);slots(capability['inputs'])
    if not isinstance(capability['outputs'],list) or len(capability['outputs'])>32:raise ContractError('OUTPUT_CONTRACTS')
    for v in capability['outputs']:slot(v)
    words(capability['effects']);words(capability['preserves'])
    exact(task,{'roles','allowed_effects','required_preserves','domain'})
    word(task['domain']);words(task['allowed_effects']);words(task['required_preserves'])
    if not isinstance(task['roles'],list) or len(task['roles'])>32:raise ContractError('ROLE_LIMIT')
    ids=set()
    for r in task['roles']:
        exact(r,{'id','mode','contract'});word(r['id']);slot(r['contract'])
        if r['id'] in ids:raise ContractError('DUPLICATE_ROLE')
        ids.add(r['id'])
        if r['mode'] not in ('direct','support'):raise ContractError('ROLE_MODE')
    slots(available)

def compare(actual:dict,required:dict,prefix:str)->tuple[list[str],list[str]]:
    conflicts=[];unknown=[]
    for k in ATTRS:
        av,rv=actual[k],required[k]
        if av=='unknown' or rv=='unknown':unknown.append(prefix+':'+k)
        elif av!=rv:conflicts.append(prefix+':'+k+':'+av+'!='+rv)
    return conflicts,unknown

def assess(capability:dict,task:dict,available:dict)->dict:
    """Compare declarations, without asserting their semantic or runtime truth."""
    validate(capability,task,available)
    missing=sorted(set(capability['inputs'])-set(available));base_conflicts=[];base_unknown=[]
    for name,required in capability['inputs'].items():
        if name in available:
            c,u=compare(available[name],required,'input:'+name);base_conflicts+=c;base_unknown+=u
    for effect in capability['effects']:
        if effect=='unknown':base_unknown.append('side_effects')
        elif effect not in task['allowed_effects']:base_conflicts.append('forbidden_effect:'+effect)
    base_unknown+=['unestablished_preservation:'+p for p in task['required_preserves'] if p not in capability['preserves']]
    choices=[]
    for role in task['roles']:
        for output in capability['outputs']:
            if output['kind']!=role['contract']['kind'] and 'unknown' not in (output['kind'],role['contract']['kind']):continue
            c,u=compare(output,role['contract'],'output');c+=base_conflicts;u+=base_unknown
            if c:status='CONTRACT_MISMATCH'
            elif u:status='UNRESOLVED_CONTRACT'
            elif missing:status='MISSING_INPUTS'
            else:status='DECLARED_DIRECT_FIT' if role['mode']=='direct' else 'DECLARED_SUPPORT_FIT'
            choices.append(dict(status=status,role_id=role['id'],role_mode=role['mode'],conflicts=sorted(set(c)),unknowns=sorted(set(u))))
    if choices:
        chosen=min(choices,key=lambda r:(STATUS_ORDER[r['status']],r['role_mode']!='direct',r['role_id'],len(r['conflicts']),len(r['unknowns'])))
    else:
        chosen=dict(status='NO_DECLARED_ROLE',role_id=None,role_mode=None,conflicts=sorted(set(base_conflicts)),unknowns=sorted(set(base_unknown)))
    return {**chosen,'missing_inputs':missing,'runtime_verified':False,'annotation_truth_verified':False,
        'execution_authorized':False,'reuse_approved':False,'market_validated':False,'novelty_established':False,'release_approved':False}

def evaluate_with_contracts(bundle:dict,corpora:list,registry:dict)->dict:
    """Invoke the unchanged FORGE module, then attach a source-bound review.

    The operator supplies the registry. The learner cannot use this to create
    authority. Original results are preserved. Review considers every declared
    pattern-matching mechanism before the old six-option display cutoff.
    """
    from forge_core import opportunities as op
    from forge_core.common import canonical as forge_canonical
    def fh(value):return hashlib.sha256(forge_canonical(value)).hexdigest()
    bundle=op.validate(bundle)
    if len(canonical(registry))>MAX_BYTES:raise ContractError('REGISTRY_TOO_LARGE')
    exact(registry,{'schema','bundle_sha256','mechanisms','workflows','annotation_basis'})
    if type(registry['schema']) is not int or registry['schema']!=1:raise ContractError('REGISTRY_SCHEMA')
    if registry['bundle_sha256']!=fh(bundle):raise ContractError('BUNDLE_BINDING')
    if registry['annotation_basis']!='SUPERVISOR_DECLARED_NOT_INDEPENDENTLY_VERIFIED':raise ContractError('ANNOTATION_AUTHORITY')
    if not isinstance(registry['mechanisms'],dict) or not isinstance(registry['workflows'],dict):raise ContractError('REGISTRY_MAPS')
    mechanisms={m['id']:m for m in bundle['mechanisms']};workflows={w['id']:w for w in bundle['workflows']};sources={s['id']:s for s in bundle['sources']}
    if set(registry['mechanisms'])-set(mechanisms) or set(registry['workflows'])-set(workflows):raise ContractError('UNKNOWN_REGISTRY_ID')
    for mid,row in registry['mechanisms'].items():
        exact(row,{'mechanism_sha256','source_bindings','contract'})
        if row['mechanism_sha256']!=fh(mechanisms[mid]):raise ContractError('MECHANISM_BINDING')
        expected={r['source']:hashlib.sha256(sources[r['source']]['text'].encode()).hexdigest() for r in mechanisms[mid]['evidence']}
        if row['source_bindings']!=expected:raise ContractError('SOURCE_BINDING')
        validate(row['contract'], {'roles':[], 'allowed_effects':[], 'required_preserves':[], 'domain':'validation'}, {})
        if {o['kind'] for o in row['contract']['outputs']}!=set(mechanisms[mid]['provides']):raise ContractError('OUTPUT_REQUIREMENT_BINDING')
        if row['contract'].get('id')!=mid or set(row['contract'].get('inputs',{}))!=set(mechanisms[mid]['requires']):raise ContractError('INPUT_REQUIREMENT_BINDING')
    for wid,row in registry['workflows'].items():
        exact(row,{'workflow_sha256','available_contracts','need_contracts'})
        if row['workflow_sha256']!=fh(workflows[wid]):raise ContractError('WORKFLOW_BINDING')
        slots(row['available_contracts'])
        declared=set(workflows[wid]['initial_fields'])|{v for s in workflows[wid]['steps'] for v in s['produces']}
        if set(row['available_contracts'])-declared:raise ContractError('UNDECLARED_AVAILABLE_INPUT')
        if not isinstance(row['need_contracts'],dict) or set(row['need_contracts'])-{n['id'] for n in op.needs_for(workflows[wid])}:raise ContractError('UNKNOWN_NEED')
        for task in row['need_contracts'].values():
            validate({'id':'validation','inputs':{},'outputs':[],'effects':[],'preserves':[],'domain':'validation'}, task, row['available_contracts'])
    original=op.evaluate(bundle,corpora);before=fh(original);results=[];cache={}
    withheld_states={'EXISTING_OPTION_TO_VERIFY','CONFLICT_REQUIRES_REVIEW','COUNTEREVIDENCE_REVIEW','STALE_EVIDENCE_RECHECK'}
    for h in original['hypotheses']:
        w=workflows[h['workflow']];n=h['need'];wr=registry['workflows'].get(w['id']);rows=[]
        available=set(op.workflow_trace(w)[n['step']]['available_before'])
        if h['state'] in withheld_states:
            results.append({'hypothesis':h['id'],'workflow':w['id'],'status':'ORIGINAL_EVIDENCE_GATE_RETAINED','pairs':[],'experiment_candidates':[],'code_leads':[]});continue
        for m in bundle['mechanisms']:
            # Reuse the original eligibility rule, but evaluate before its global top-six limit.
            eligible=op.mechanism_options(n,w,[m])
            if not eligible:continue
            mr=registry['mechanisms'].get(m['id'])
            if not wr or not mr or n['id'] not in wr['need_contracts']:
                r={'status':'UNRESOLVED_CONTRACT','missing_inputs':eligible[0]['missing_prerequisites'],'conflicts':[],'unknowns':['missing_source_bound_annotation'],'execution_authorized':False,'runtime_verified':False,'annotation_truth_verified':False,'reuse_approved':False,'release_approved':False,'market_validated':False,'novelty_established':False,'role_id':None,'role_mode':None}
            else:
                av={k:v for k,v in wr['available_contracts'].items() if k in available}
                # Present-but-untyped input must not be silently treated as a missing physical input.
                for k in available:
                    if k not in av:av[k]={a:'unknown' for a in ATTRS}
                r=assess(mr['contract'],wr['need_contracts'][n['id']],av)
            rows.append({'mechanism':m['id'],**r})
        rows.sort(key=lambda r:(STATUS_ORDER[r['status']],r['mechanism']))
        selected=[r for r in rows if r['status'] in ('DECLARED_DIRECT_FIT','DECLARED_SUPPORT_FIT')][:6]
        opts=[op.mechanism_options(n,w,[mechanisms[r['mechanism']]])[0] for r in selected]
        links=op.code_leads(opts,corpora,cache)
        results.append({'hypothesis':h['id'],'workflow':w['id'],'status':'DECLARATION_REVIEW_ONLY','pairs':rows,'experiment_candidates':[r['mechanism'] for r in selected],'code_leads':links})
    if fh(original)!=before:raise ContractError('ORIGINAL_REPORT_CHANGED')
    binding={'version':VERSION,'module_sha256':hashlib.sha256(__import__('pathlib').Path(__file__).read_bytes()).hexdigest(),'bundle_sha256':fh(bundle),'registry_sha256':digest(registry),'original_report_sha256':before}
    return {'id':'transfer_'+digest(binding),'binding':binding,'original_report':original,'transfer_reviews':results,
      'summary':{'pairs_reviewed':sum(len(r['pairs']) for r in results),'selected_experiment_components':sum(len(r['experiment_candidates']) for r in results),'additional_code_searches':len(cache)},
      'authority':copy.deepcopy(op.AUTHORITY),'scope':'Operator-declared type/role compatibility. Not semantic truth, tested interoperability, independent evidence, invention quality, market validation, or release approval.'}
