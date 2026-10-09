"""One reviewed requirement-to-integration task; no arbitrary candidate executor."""
from __future__ import annotations
import io,json,random,shutil,struct,subprocess,sys,tempfile,zipfile,html,platform,time
from pathlib import Path
from .common import Blocked,canonical,loads,read,write,sha,git_sha,safe_path
from . import interop
from .engine import ROOT

COMMIT='010be76943fb879ee3eeed7227e47b6cfb8907ee'
REPO='trailofbits/rfc8785.py'

def external_vectors():
    interop.verify_pins()
    from ._vendor import rfc8785
    nums=loads(read(ROOT/'contracts/interop/rfc8785_numbers.json'))
    objs=loads(read(ROOT/'contracts/interop/rfc8785_objects.json'))
    rows=[]
    for case in nums['cases']:
        value=struct.unpack('>d',bytes.fromhex(case['ieee754_hex']))[0]
        r={'id':case['id'],'origin':'RFC 8785 Appendix B','expected':case['expected']}
        for name,func in [('existing_forge_canonical',canonical),('selected_component',rfc8785.dumps)]:
            try:actual=func(value).decode('utf-8');error=None
            except (ValueError,UnicodeError,OverflowError) as exc:actual=None;error=type(exc).__name__
            r[name]={'actual':actual,'error':error,'passed':actual==case['expected'] and ((error is not None)==(case['expected'] is None))}
        rows.append(r)
    for case in objs['cases']:
        # Ordinary parsing for the *component* conformance sample: the strict
        # application adapter deliberately excludes the sample's huge number.
        obj=json.loads(case['input']);r={'id':case['id'],'origin':'RFC 8785 section 3.2','expected_hex':case['expected_hex']}
        for name,func in [('existing_forge_canonical',canonical),('selected_component',rfc8785.dumps)]:
            result=func(obj)
            r[name]={'actual_hex':result.hex(),'passed':result.hex()==case['expected_hex']}
        rows.append(r)
    return {'case_count':len(rows),'cases':rows,'summary':{n:sum(r[n]['passed'] for r in rows) for n in ('existing_forge_canonical','selected_component')},'expectation_origin':'Published external examples; local transcription and evaluator by FORGE developer','independent_evaluator':False}

def accepted_records():
    # Cross-client numeric spelling and escaped text variations; entirely synthetic.
    base=[b'{ "run":"demo", "measurement":1.0,"label":"\\u00e9" }',
          b'{"label":"\xc3\xa9","measurement":1,"run":"demo"}',
          b'{"x":-0.0}',b'{"x":0}',b'{"x":1e-6}',
          b'{"\\ufb33":1,"\\ud83d\\ude00":2}',
          b'{"nested":[{"b":2,"a":1},null,false]}',b'{"__proto__":{"x":1},"2":0,"10":0}']
    rng=random.Random(8785)
    for i in range(96):
        keys=['unit','name','value','tags'];rng.shuffle(keys)
        values={'unit':'mm','name':'measurement-'+str(i),'value':rng.uniform(-2000,2000),'tags':['é','😀',i]}
        base.append(json.dumps({k:values[k] for k in keys},ensure_ascii=(i%2==0),indent=1 if i%3 else None).encode())
    return base

def node_check():
    interop.verify_pins()
    binary=shutil.which('node')
    if not binary:return {'status':'NOT_RUN','reason':'NODE_NOT_INSTALLED','passed':False}
    from ._vendor import rfc8785
    published=loads(read(ROOT/'contracts/interop/rfc8785_numbers.json'))['cases']
    rng=random.Random(8785);numbers=[r['ieee754_hex'] for r in published]+[rng.getrandbits(64).to_bytes(8,'big').hex() for _ in range(512)]
    records=accepted_records();payload=canonical({'numbers':numbers,'records':[b.decode('utf-8') for b in records]})
    p=subprocess.run([binary,str(ROOT/'tools/interop_reference.cjs')],input=payload,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20,env={'PATH':str(Path(binary).parent),'LANG':'C.UTF-8'})
    if p.returncode or len(p.stdout)>2000000:raise Blocked('NODE_REFERENCE_FAILED')
    ref=loads(p.stdout);number_bad=[];record_bad=[];baseline_bad=0
    if len(ref['numbers'])!=len(numbers) or len(ref['records'])!=len(records):raise Blocked('REFERENCE_CASE_COUNT')
    for h,expected in zip(numbers,ref['numbers']):
        f=struct.unpack('>d',bytes.fromhex(h))[0]
        try:actual=rfc8785.dumps(f).decode()
        except ValueError:actual=None
        if actual!=expected:number_bad.append(h)
    for i,(raw,expected) in enumerate(zip(records,ref['records'])):
        actual=interop.normalize_record(raw);r=interop.fingerprint(raw)
        if actual.hex()!=expected['canonical_hex'] or r['canonical_sha256']!=expected['sha256']:record_bad.append(i)
        if canonical(loads(raw)).hex()!=expected['canonical_hex']:baseline_bad+=1
    return {'status':'PASSED' if not number_bad and not record_bad else 'FAILED',
            'passed':not number_bad and not record_bad,'node':ref['node'],'v8':ref['v8'],
            'numeric_cases':len(numbers),'record_cases':len(records),
            'numeric_mismatches':number_bad,'record_mismatches':record_bad,
            'baseline_record_mismatches':baseline_bad,'glue_authored_by_same_developer':True,
            'reference_uses_independent_runtime':True,'independent_evaluation':False}

def pinned_source_manifest(folder):
    """Recreate the reviewed selected-file envelope from byte-identical vendor files."""
    interop.verify_pins();folder=safe_path(folder);folder.mkdir(parents=True,exist_ok=True)
    buf=io.BytesIO();members=[]
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        for name in ('__init__.py','_impl.py'):
            body=read(ROOT/'forge_core/_vendor/rfc8785'/name);path='src/rfc8785/'+name
            item=zipfile.ZipInfo(path,(2026,9,21,0,0,0));item.compress_type=zipfile.ZIP_DEFLATED;item.external_attr=0o100600<<16
            z.writestr(item,body);members.append({'path':path,'size':len(body),'sha256':sha(body),'git_blob_sha1':git_sha(body)})
    blob=buf.getvalue();path=folder/'rfc8785-selected.zip'
    if path.exists():
        if read(path)!=blob:raise Blocked('SOURCE_ENVELOPE_CHANGED')
    else:write(path,blob,new=True)
    src={'file_id':f'github:{REPO}@{COMMIT}:selected-files','provider':'github','path':path.name,'size':len(blob),'sha256':sha(blob),'source_url':f'https://github.com/{REPO}/tree/{COMMIT}','repository':REPO,'commit':COMMIT,'container_kind':'LOCAL_SELECTED_FILE_ENVELOPE_NOT_FULL_REPOSITORY_ARCHIVE','selected_paths':[m['path'] for m in members],'members':members}
    m=folder/'manifest.json';data=canonical({'schema':1,'files':[src]})
    if m.exists():
        if read(m)!=data:raise Blocked('SOURCE_MANIFEST_CHANGED')
    else:write(m,data,new=True)
    return m

def admission_probe():
    """Execute authored application-domain checks, separately from RFC vectors."""
    invalid=[b'{"x":1,"x":2}',b'{"x":{"x":0,"x":1}}',b'{"x":1,"\\u0078":1}',
             b'{"x":NaN}',b'{"x":Infinity}',b'{"x":-Infinity}',b'{"x":1e999}',
             b'{"x":9007199254740992}',b'{"x":9007199254740992.0}',b'{"x":9.007199254740992e15}',
             b'{"x":1e-999}',b'{"\\ud800":1}',b'{"x":"\\udfff"}',b'\xff',b'\xef\xbb\xbf{}',
             b'[]',b'null',b'1',b'{"x":}',b'{"x":'+b'['*65+b'0'+b']'*65+b'}']
    rows=[]
    for i,raw in enumerate(invalid):
        try:interop.normalize_record(raw);rejected=False
        except Blocked:rejected=True
        rows.append({'id':f'reject-{i:02d}','expected':'rejection','passed':rejected})
    pairs=[(b'{"b":2,"a":1}',b'{ "a":1,"b":2 }',True),
           (b'{"x":1.0}',b'{"x":1e0}',True),
           (b'{"x":-0}',b'{"x":0.0}',True),
           (b'{"x":"\\u00e9"}','{"x":"é"}'.encode(),True),
           (b'{"x":[1,2]}',b'{"x":[2,1]}',False),
           ('{"x":"é"}'.encode(),'{"x":"e\\u0301"}'.encode(),False),
           (b'{"x":1}',b'{"x":true}',False),
           (b'{"x":"1"}',b'{"x":1}',False)]
    for i,(a,b,equal) in enumerate(pairs):
        actual=interop.normalize_record(a)==interop.normalize_record(b)
        rows.append({'id':f'equality-{i:02d}','expected_equal':equal,'passed':actual==equal})
    return {'case_count':len(rows),'passed':all(x['passed'] for x in rows),'cases':rows,'expectations_authored_by_same_developer':True}


def evaluate_gate(report):
    """Check required records, never promote absent execution or an edited total.

    A consistent local record is not authenticated external proof.
    """
    try:
        interop.verify_pins()
        if not isinstance(report,dict):return False
        for k in ('release_approved','independent_evaluation','historical_ids_migrated','arbitrary_source_execution'):
            if report.get(k) is not False:return False
        for k in ('model_calls','external_network_requests'):
            if type(report.get(k)) is not int or report[k]!=0:return False
        exp=loads(read(ROOT/'contracts/interop/rfc8785_numbers.json'))['cases']+loads(read(ROOT/'contracts/interop/rfc8785_objects.json'))['cases']
        ext=report['external_vector_report'];rows=ext['cases']
        if type(ext['case_count']) is not int or ext['case_count']!=28 or len(rows)!=28:return False
        if [x['id'] for x in rows]!=[x['id'] for x in exp]:return False
        for row,expected in zip(rows,exp):
            target='expected' if 'expected' in expected else 'expected_hex'
            observed='actual' if target=='expected' else 'actual_hex'
            if row.get(target)!=expected[target]:return False
            outcome=row['selected_component']
            if outcome.get('passed') is not True or outcome.get(observed)!=expected[target]:return False
            if target=='expected' and ((outcome.get('error') is not None)!=(expected[target] is None)):return False
        if type(ext['summary']['selected_component']) is not int or ext['summary']['selected_component']!=28:return False
        n=report['reference_report']
        if n['status']!='PASSED' or n['passed'] is not True:return False
        for k,v in [('numeric_cases',538),('record_cases',104)]:
            if type(n.get(k)) is not int or n[k]!=v:return False
        if n['numeric_mismatches']!=[] or n['record_mismatches']!=[]:return False
        if not isinstance(n.get('node'),str) or not isinstance(n.get('v8'),str):return False
        a=report['admission_report']
        if type(a['case_count']) is not int or a['case_count']!=28 or a['passed'] is not True:return False
        ids=[f'reject-{i:02d}' for i in range(20)]+[f'equality-{i:02d}' for i in range(8)]
        if [r['id'] for r in a['cases']]!=ids or any(x.get('passed') is not True for x in a['cases']):return False
        d=report['discovery']
        if d['status']!='COMPLETED_FOR_SELECTED_CONTAINERS' or d.get('selected_public_api_found') is not True or d.get('source_code_executed_during_discovery') is not False:return False
        h=report['handoff']
        if h['matched'] is not True or h['raw_bytes_equal'] is not False or h['different_original_bytes'] is not True or h['baseline_digest_agrees'] is not False:return False
        return True
    except (Blocked,KeyError,TypeError,ValueError):return False


def validate_result_records(report):
    """Validate evidence structure independently of a successful qualification.

    A failed or unavailable reference is legitimate evidence. Missing required
    cases, altered expectations and inconsistent pass summaries are corruption,
    not merely a low score. No claimed source truth or external attestation.
    """
    try:
        expected=loads(read(ROOT/'contracts/interop/rfc8785_numbers.json'))['cases']+loads(read(ROOT/'contracts/interop/rfc8785_objects.json'))['cases']
        ext=report['external_vector_report'];rows=ext['cases']
        if type(ext['case_count']) is not int or ext['case_count']!=len(expected) or not isinstance(rows,list) or len(rows)!=len(expected):raise ValueError()
        totals={'existing_forge_canonical':0,'selected_component':0}
        for row,case in zip(rows,expected):
            if not isinstance(row,dict) or row.get('id')!=case['id']:raise ValueError()
            expkey='expected' if 'expected' in case else 'expected_hex'
            actualkey='actual' if expkey=='expected' else 'actual_hex'
            if row.get(expkey)!=case[expkey]:raise ValueError()
            for name in totals:
                outcome=row[name]
                if not isinstance(outcome,dict) or type(outcome.get('passed')) is not bool or actualkey not in outcome:raise ValueError()
                actual=outcome[actualkey]
                if actual is not None and not isinstance(actual,str):raise ValueError()
                agrees=actual==case[expkey]
                if expkey=='expected':
                    if 'error' not in outcome or (outcome['error'] is not None and not isinstance(outcome['error'],str)):raise ValueError()
                    agrees=agrees and ((outcome['error'] is not None)==(case[expkey] is None))
                if outcome['passed'] is not agrees:raise ValueError()
                totals[name]+=int(agrees)
        if not isinstance(ext['summary'],dict) or set(ext['summary'])!=set(totals):raise ValueError()
        if any(type(ext['summary'][k]) is not int or ext['summary'][k]!=v for k,v in totals.items()):raise ValueError()
        admission=report['admission_report'];cases=admission['cases']
        ids=[f'reject-{i:02d}' for i in range(20)]+[f'equality-{i:02d}' for i in range(8)]
        if type(admission['case_count']) is not int or admission['case_count']!=28 or not isinstance(cases,list) or len(cases)!=28:raise ValueError()
        equals=[True,True,True,True,False,False,False,False]
        for i,(row,identity) in enumerate(zip(cases,ids)):
            if not isinstance(row,dict) or row.get('id')!=identity or type(row.get('passed')) is not bool:raise ValueError()
            if i<20:
                if row.get('expected')!='rejection':raise ValueError()
            elif row.get('expected_equal') is not equals[i-20]:raise ValueError()
        if type(admission['passed']) is not bool or admission['passed'] is not all(r['passed'] for r in cases):raise ValueError()
    except (KeyError,TypeError,ValueError,IndexError) as exc:
        raise Blocked('PACKET_EVIDENCE_STRUCTURE') from exc


def verify_packet(folder):
    """Verify a local result packet, not provenance, truth, or third-party execution."""
    folder=safe_path(folder)
    packet=loads(read(folder/'packet.json',100000))
    wanted={'handoff-receipt.json','canonical-record.json','contract.json','results.json','Review.html'}
    if not isinstance(packet,dict) or set(packet)!={'schema_version','files','release_approved'} or type(packet['schema_version']) is not int or packet['schema_version']!=1 or packet['release_approved'] is not False:raise Blocked('PACKET_SHAPE')
    if not isinstance(packet['files'],dict) or set(packet['files'])!=wanted or {p.name for p in folder.iterdir()}!=wanted|{'packet.json'}:raise Blocked('PACKET_MEMBERS')
    for name,digest in packet['files'].items():
        if not isinstance(digest,str) or sha(read(folder/name,10000000))!=digest:raise Blocked('PACKET_HASH')
    result=loads(read(folder/'results.json',10000000))
    if not isinstance(result,dict) or any(result.get(k) is not False for k in ('release_approved','independent_evaluation','historical_ids_migrated','arbitrary_source_execution')):raise Blocked('PACKET_AUTHORITY')
    validate_result_records(result)
    expected_status='READY_FOR_INTEGRATION_REVIEW' if evaluate_gate(result) else 'INCOMPLETE_OR_FAILED'
    if result.get('status')!=expected_status:raise Blocked('PACKET_STATUS')
    if loads(read(folder/'contract.json'))!=interop.contract() or result.get('contract')!=interop.contract():raise Blocked('PACKET_CONTRACT')
    if result.get('source_lock')!=loads(read(ROOT/'third_party/rfc8785/PROVENANCE.json')):raise Blocked('PACKET_SOURCE_LOCK')
    receipt=loads(read(folder/'handoff-receipt.json'));raw=read(folder/'canonical-record.json')
    interop.verify_record(raw,receipt)
    if read(folder/'Review.html',10000000)!=render(result).encode('utf-8'):raise Blocked('PACKET_RENDER')
    return {'status':'VERIFIED_LOCAL_PACKET','qualification':expected_status,'files_checked':5,'authenticated':False,'release_approved':False}

def run(workbench,out,*,with_discovery=True):
    """Fixed candidate only. Permission to run is not permission to deploy."""
    workbench.guard();interop.verify_pins();out=safe_path(out)
    if out.exists() or out.is_relative_to(ROOT) or ROOT.is_relative_to(out) or out.is_relative_to(workbench.home/'engine'):
        raise Blocked('NEW_CONTRACT_OUTPUT_REQUIRED')
    out.mkdir(parents=True,mode=0o700);started=time.monotonic();before=interop.verify_pins()
    result={'schema_version':1,'task':'cross-tool evidence fingerprint','contract':interop.contract(),
            'source_lock':loads(read(ROOT/'third_party/rfc8785/PROVENANCE.json')),
            'external_vector_report':external_vectors(),'reference_report':node_check(),'admission_report':admission_probe(),
            'model_calls':0,'external_network_requests':0,'component_modified':False,'adapter_and_evaluator_authored_by_same_developer':True,
            'reviewed_public_component_executed':True,'arbitrary_source_execution':False,
            'task_definition_basis':'Published external RFC + explicit developer-authored FORGE input-policy additions',
            'independent_evaluation':False,'release_approved':False,'historical_ids_migrated':False}
    if with_discovery:
        from . import corpus
        inputs=workbench.home/'contract_inputs';manifest=pinned_source_manifest(inputs)
        rr=corpus.run(workbench,inputs,manifest,workbench.home/'corpora'/'interop-source',max_containers=1)
        hits=corpus.search(rr,'canonical serialization',distinct=True)
        result['discovery']={'summary':rr['summary'],'status':rr['status'],'results':hits['results'],'selected_public_api_found':any(h.get('qualified_name',h.get('name'))=='dumps' for h in hits['results']),'source_code_executed_during_discovery':False}
    else:result['discovery']={'status':'NOT_RUN','reason':'explicit reuse of separately tested inspection'}
    # Actual write/read handoff. Canonicalized content is derived from synthetic data only.
    a,b=accepted_records()[:2];receipt=interop.fingerprint(a)
    write(out/'handoff-receipt.json',canonical(receipt),new=True)
    write(out/'canonical-record.json',interop.normalize_record(a),new=True)
    restored=loads(read(out/'handoff-receipt.json'))
    result['handoff']=interop.verify_record(b,restored)
    result['handoff']['different_original_bytes']=sha(a)!=sha(b)
    result['handoff']['baseline_digest_agrees']=sha(canonical(loads(a)))==sha(canonical(loads(b)))
    good=evaluate_gate(result)
    if before!=interop.verify_pins():raise Blocked('COMPONENT_CHANGED_DURING_TRIAL')
    workbench.guard()
    result['status']='READY_FOR_INTEGRATION_REVIEW' if good else 'INCOMPLETE_OR_FAILED'
    result['elapsed_seconds']=time.monotonic()-started
    write(out/'contract.json',canonical(result['contract']),new=True)
    write(out/'results.json',canonical(result),new=True)
    report=render(result);write(out/'Review.html',report.encode('utf-8'),new=True)
    files={p.name:sha(read(p,10000000)) for p in out.iterdir() if p.is_file()}
    write(out/'packet.json',canonical({'schema_version':1,'files':files,'release_approved':False}),new=True)
    return result

def render(r):
    a=r['external_vector_report'];n=r['reference_report'];h=r['handoff']
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>FORGE — Contract integration result</title><style>body{font:17px system-ui;max-width:1000px;margin:40px auto;padding:24px;line-height:1.55}table{border-collapse:collapse;width:100%}td,th{padding:12px;border-bottom:1px solid}pre{white-space:pre-wrap;overflow-wrap:anywhere}h1{font-size:38px}</style><main><p>FORGE · CONTRACT LAB</p><h1>From a requirement to a tested integration</h1><p>Cross-tool evidence fingerprints. '+html.escape(r['status'])+'. No automatic adoption.</p><table><tr><th>Check</th><th>Observed result</th></tr><tr><td>Published RFC examples</td><td>Existing serializer '+str(a['summary']['existing_forge_canonical'])+'/'+str(a['case_count'])+'; selected component '+str(a['summary']['selected_component'])+'/'+str(a['case_count'])+'</td></tr><tr><td>JavaScript reference</td><td>'+html.escape(n['status'])+' · '+str(n.get('numeric_cases',0))+' numbers; '+str(n.get('record_cases',0))+' records</td></tr><tr><td>Record handoff</td><td>Canonical match: '+str(h['matched'])+'; same original bytes: '+str(h['raw_bytes_equal'])+'</td></tr></table><h2>Boundaries</h2><p>The adapter rejects unsafe integral numbers and underflow. JCS canonicalization does not prove identity, origin, schema validity or truth. Existing FORGE evidence IDs are unchanged. The published samples predate this work; task selection, adapter and evaluation glue are supervised.</p><details><summary>Exact evidence and source lock</summary><pre>'+html.escape(json.dumps(r,indent=2,ensure_ascii=True,sort_keys=True))+'</pre></details></main></html>'
