"""Entirely synthetic declarations illustrating the transfer-review format.

These declarations are hand-written examples, not inferred behavior or proof that
an identity mechanism works. No private source or user data is needed.
"""
from copy import deepcopy
from .common import canonical,sha
from .opportunity_examples import example_bundle


def slot(kind):
    return {'kind':kind,'unit':'n/a','frame':'n/a','clock':'n/a'}


def example():
    bundle=example_bundle()
    sources={s['id']:s for s in bundle['sources']}
    mechanisms={}
    for m in bundle['mechanisms']:
        mechanisms[m['id']]={
            'mechanism_sha256':sha(canonical(m)),
            'source_bindings':{r['source']:sha(sources[r['source']]['text'].encode()) for r in m['evidence']},
            'contract':{'id':m['id'],'domain':m['domain'],
                        'inputs':{name:slot(name) for name in m['requires']},
                        'outputs':[slot(name) for name in m['provides']],
                        'effects':[],'preserves':[]}}
    w=bundle['workflows'][0]
    registry={'schema':1,'bundle_sha256':sha(canonical(bundle)),
              'annotation_basis':'SUPERVISOR_DECLARED_NOT_INDEPENDENTLY_VERIFIED',
              'mechanisms':mechanisms,'workflows':{
                  w['id']:{'workflow_sha256':sha(canonical(w)),
                    'available_contracts':{name:slot(name) for name in ('artifact','attributes','file')},
                    'need_contracts':{'identity':{
                        'roles':[{'id':'preserved-record','mode':'support','contract':slot('identity_evidence')},
                                 {'id':'candidate-list','mode':'support','contract':slot('identity_candidates')}],
                        'allowed_effects':[],'required_preserves':[],'domain':w['domain']}}}}}
    return deepcopy(bundle),deepcopy(registry)
