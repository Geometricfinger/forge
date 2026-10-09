"""Fixed first-party reuse experiment. No arbitrary-code execution interface."""
from __future__ import annotations
import base64, platform, sys
from pathlib import Path
from .common import Blocked, canonical, sha, git_sha, loads, read
from . import reuse_examples, policy
from .github import Response
from .reuse import Casebook

ROOT=Path(__file__).resolve().parents[1]
FIXTURE_SHA256='83cd8cb92144a5fb8737ac5e147c88cb16889d72c2bf7ad790f44529fbb57d57'
REPO='forge-fixture/reuse-records'
COMMIT='7'*40
TREE='8'*40
PATH='src/reuse_examples.py'
FUNCTIONS={name:getattr(reuse_examples,name) for name in ('simple_json_fingerprint','strict_json_fingerprint')}

def fixtures():
    data=read(ROOT/'contracts/reuse_trial.json')
    if sha(data)!=FIXTURE_SHA256:raise Blocked('FROZEN_REUSE_TRIAL_CHANGED')
    return loads(data)

def requirement_contract():
    f=fixtures()
    return {'schema':1,'title':f['title'],'objective':f['objective'],'requirements':[
        {'id':'digest_call','label':'Declared SHA-256 call observed','kind':'observed_api','required':True,'apis':['hashlib.sha256']},
        *[{'id':x['id'],'label':x['label'],'kind':'behavior_test','required':True} for x in f['cases']],
        {'id':'context','label':'Dependency and scope limitations reviewed','kind':'review','required':True}]}


def run_for_case(case):
    if case['contract']!=requirement_contract():raise Blocked('FIXED_TRIAL_CONTRACT_REQUIRED')
    source_path=Path(reuse_examples.__file__);data=read(source_path);source_hash=sha(data)
    source_id=f'https://github.com/{REPO}/blob/{COMMIT}/{PATH}'
    rows=case['candidates']
    if len(rows)!=2 or {c['qualified_name'] for c in rows}!=set(FUNCTIONS):raise Blocked('FIXED_TRIAL_CANDIDATES_REQUIRED')
    for c in rows:
        if c['source_sha256']!=source_hash or c['source_id']!=source_id or c['provenance']!='synthetic_fixture':raise Blocked('FIXED_TRIAL_SOURCE_MISMATCH')
    before=sha(data);results=[];calls=0
    for c in rows:
        fn=FUNCTIONS[c['qualified_name']]
        for test in fixtures()['cases']:
            out=[];error=None;passed=False
            try:
                calls+=1;a=fn(test['a'].encode('utf-8'));out.append(a)
                if test['mode']!='reject':
                    calls+=1;b=fn(test['b'].encode('utf-8'));out.append(b)
                    passed=(a==b) if test['mode']=='equal' else (a!=b)
            except (ValueError,UnicodeError) as e:
                error=type(e).__name__;passed=test['mode']=='reject'
            except Exception as e:error='UNEXPECTED_'+type(e).__name__
            if any(not isinstance(v,str) or len(v)!=64 or any(ch not in '0123456789abcdef' for ch in v) for v in out):passed=False
            results.append({'candidate':c['key'],'test_id':test['id'],'passed':passed,'output_digests':out,'exception_type':error})
    if before!=sha(read(source_path)):raise Blocked('FIXED_TRIAL_SOURCE_CHANGED')
    return {'schema':1,'runner':'forge.fixed_json_record_trial.v1','fixture_sha256':FIXTURE_SHA256,
        'source_sha256':source_hash,'runner_sha256':sha(read(Path(__file__))),
        'helper_sha256':sha(read(ROOT/'forge_core/common.py')),'python':platform.python_version(),
        'platform':sys.platform,'function_invocations':calls,'results':results,
        'source_preserved':True,'external_code_executed':False,'independent_evaluation':False,
        'limitations':['Reviewed first-party functions and synthetic JSON fixtures only.','Python representation, not RFC 8785/JCS or a signature.','No universal collision, numeric-equivalence, Unicode-normalization or hostile-input claim.'],'release_approved':False}

class TrialTransport:
    """Synthetic provider envelopes carrying exact bundled first-party source."""
    def __init__(self):self.data=read(Path(reuse_examples.__file__));self.calls=[]
    def get(self,p):
        self.calls.append(p['kind']);kind=p['kind']
        if kind=='search':obj={'items':[{'full_name':REPO}],'total_count':1,'incomplete_results':False}
        elif kind=='repo':obj={'id':700,'full_name':REPO,'private':False,'license':{'spdx_id':'MIT'},'default_branch':'main'}
        elif kind=='commit':obj={'sha':COMMIT,'commit':{'tree':{'sha':TREE}}}
        elif kind=='tree':obj={'sha':TREE,'truncated':False,'tree':[{'path':PATH,'type':'blob','mode':'100644','sha':git_sha(self.data),'size':len(self.data)}]}
        elif kind=='file':obj={'type':'file','path':PATH,'sha':git_sha(self.data),'size':len(self.data),'encoding':'base64','content':base64.b64encode(self.data).decode()}
        else:raise Blocked('TRIAL_REQUEST_KIND')
        return Response(200,canonical(obj),{},'synthetic_fixture')


def run(workbench):
    """The fixed trial is explicitly separate from public-source discovery."""
    spec=policy.template('reliability');spec['title']='JSON record reuse comparison';spec['public_brief']='Synthetic provider; exact bundled first-party implementations. No public repository claim.'
    spec['profile']={'schema':1,'id':'forge-json-digest-trial','title':'Digest candidates','targets':[{'api':'hashlib.sha256','capability':'digest_call'}]}
    transport=TrialTransport();mid=workbench.store.create_mission(spec)
    snapshot=workbench.discovery().run(mid,transport,steps=10,seconds=60)
    if snapshot['status']!='COMPLETED_FOR_BOUNDED_MISSION':raise Blocked('REUSE_DISCOVERY_INCOMPLETE')
    book=Casebook(workbench.store);case=book.create(mid,requirement_contract());before=book.compare(case['id'])
    evidence=book.attach_fixed_trial(case['id'])
    for c in case['candidates']:
        book.record(case['id'],{'candidate':c['key'],'requirement':'context','verdict':'SUPPORTED',
        'note':'Bundled first-party review: Python standard library; strict candidate uses forge_core.common.loads/canonical. This is Python-specific and not a signature or universal JSON canonicalization. External integration and host qualification remain pending.',
        'actor':'BUNDLED_DEMO_REVIEW_NOT_INDEPENDENT','supersedes':None})
    comparison=book.compare(case['id'])
    verdicts={r['candidate']['qualified_name']:r['verdict'] for r in comparison['rows']}
    if verdicts!={'simple_json_fingerprint':'CONTRADICTED','strict_json_fingerprint':'READY_FOR_INTEGRATION_REVIEW'}:raise Blocked('REUSE_TRIAL_OUTCOME')
    return {'status':'FIRST_PARTY_REUSE_TRIAL_COMPLETED','mission':mid,'case_id':case['id'],
      'acquisition_stages':len(transport.calls),'before_verdicts':[r['verdict'] for r in before['rows']],
      'verdicts':verdicts,'test_results':evidence['payload'],'comparison':comparison,
      'live_network_requests':0,'model_calls':0,'release_approved':False,'independent_evaluation':False}


def validate_receipt(case,receipt):
    """Stored execution evidence must remain complete, source-bound and typed."""
    if case['contract']!=requirement_contract():raise Blocked('FIXED_TRIAL_CONTRACT_REQUIRED')
    if not isinstance(receipt,dict) or receipt.get('runner')!='forge.fixed_json_record_trial.v1' or receipt.get('fixture_sha256')!=FIXTURE_SHA256:raise Blocked('TRIAL_RECEIPT_IDENTITY')
    if any(receipt.get(k) is not False for k in ['external_code_executed','independent_evaluation','release_approved']) or receipt.get('source_preserved') is not True:raise Blocked('TRIAL_RECEIPT_AUTHORITY')
    actual_hash=sha(read(Path(reuse_examples.__file__)))
    if receipt.get('source_sha256')!=actual_hash or any(c['source_sha256']!=actual_hash for c in case['candidates']):raise Blocked('TRIAL_RECEIPT_SOURCE')
    from .reuse import digest
    for k in ('runner_sha256','helper_sha256'):digest(receipt.get(k))
    expected={(c['key'],t['id']) for c in case['candidates'] for t in fixtures()['cases']};observed=set()
    rows=receipt.get('results')
    if not isinstance(rows,list) or len(rows)!=len(expected):raise Blocked('TRIAL_CASE_COUNT')
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'candidate','test_id','passed','output_digests','exception_type'}:raise Blocked('TRIAL_CASE_SHAPE')
        key=(row['candidate'],row['test_id'])
        if key not in expected or key in observed or type(row['passed']) is not bool:raise Blocked('TRIAL_CASE_IDENTITY')
        observed.add(key)
        if not isinstance(row['output_digests'],list) or len(row['output_digests'])>2:raise Blocked('TRIAL_OUTPUTS')
        for h in row['output_digests']:digest(h)
    if type(receipt.get('function_invocations')) is not int or not 1<=receipt['function_invocations']<=60:raise Blocked('TRIAL_CALL_COUNT')
