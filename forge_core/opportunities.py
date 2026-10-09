"""Evidence-bounded opportunity discovery, not automatic business validation.

Inputs are attributed source excerpts and reviewed workflow annotations. Missing
marketing text is not product absence. Matching code is not user demand. The
engine is deterministic and model-free; it never fetches or executes source.
"""
from __future__ import annotations
import copy, datetime as dt, gzip, html, re
from pathlib import Path
from urllib.parse import urlsplit
from .common import Blocked, canonical, integer, read, safe_path, sha, text, write
from . import __version__

VERSION='opportunity-1.1-inventor-audit'
MAX_INPUT=384_000
PATTERNS={'identity_loss','manual_bridge','verification_gap','information_gap','format_gap','recovery_gap','coordination_gap'}
KINDS={'firsthand','product_listing','product_docs','issue','research','source_review','synthetic'}
OBS={'pain','manual','missing','supported','unknown','not_described','refutes'}
AUTHORITY={'market_validated':False,'novelty_established':False,'reuse_approved':False,
           'release_approved':False,'execute_source':False,'model_calls':0,'network_requests':0,
           'independent_evaluation':False}


def obj(v,required,optional=()):
    if not isinstance(v,dict) or not set(required)<=set(v) or set(v)-set(required)-set(optional):
        raise Blocked('OPPORTUNITY_FIELDS')
    return v


def arr(v,limit=200,minimum=0):
    if not isinstance(v,list) or not minimum<=len(v)<=limit:raise Blocked('OPPORTUNITY_LIST_BOUNDS')
    return v


def key(v):
    if not isinstance(v,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,79}',v):raise Blocked('OPPORTUNITY_ID')
    return v


def strings(v,limit=50):
    arr(v,limit)
    for s in v:text(s,160)
    if len(v)!=len(set(v)):raise Blocked('DUPLICATE_VALUE')
    return v


def date(v):
    try:
        if not isinstance(v,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',v):raise ValueError()
        return dt.date.fromisoformat(v)
    except ValueError:raise Blocked('OPPORTUNITY_DATE')


def normalized(s):return ' '.join(s.casefold().split())


def source_record(s,as_of):
    obj(s,{'id','text','kind','public','origin_group','uri','observed_on','derived_from'}, {'capture_method'})
    if s.get('capture_method','attributed_summary') not in {'attributed_summary','verbatim_excerpt','synthetic_fixture'}:raise Blocked('SOURCE_CAPTURE_METHOD')
    key(s['id']);text(s['origin_group'],200)
    if s['kind'] not in KINDS or type(s['public']) is not bool:raise Blocked('OPPORTUNITY_SOURCE_KIND')
    if not isinstance(s['text'],str) or not s['text'].strip() or len(s['text'])>24_000 or '\0' in s['text']:raise Blocked('SOURCE_TEXT_BOUNDS')
    text(s['uri'],1200);u=urlsplit(s['uri'])
    if u.scheme not in {'https','fixture','conversation','file-ref'} or u.username or u.password or not u.netloc:raise Blocked('SOURCE_URI')
    if s['public'] and u.scheme!='https':raise Blocked('PUBLIC_SOURCE_URI')
    if date(s['observed_on'])>as_of:raise Blocked('FUTURE_SOURCE')
    strings(s['derived_from'],10)
    return s


def refs(values,sources):
    arr(values,8,1)
    seen=set()
    for r in values:
        obj(r,{'source','start','end','quote'})
        if r['source'] not in sources:raise Blocked('UNKNOWN_EVIDENCE_SOURCE')
        s=sources[r['source']]['text'];integer(r['start'],0,len(s));integer(r['end'],r['start']+1,len(s))
        if not isinstance(r['quote'],str) or s[r['start']:r['end']]!=r['quote']:raise Blocked('EVIDENCE_QUOTE_MISMATCH')
        h=sha(canonical(r))
        if h in seen:raise Blocked('DUPLICATE_CITATION')
        seen.add(h)
    return values


def unique(rows,field='id'):
    if any(not isinstance(x,dict) or field not in x for x in rows):raise Blocked('OPPORTUNITY_ROW')
    ids=[key(x[field]) for x in rows]
    if len(set(ids))!=len(ids):raise Blocked('DUPLICATE_ID')
    return {x[field]:x for x in rows}


def _validate(bundle):
    b=copy.deepcopy(bundle)
    try:encoded=canonical(b)
    except (ValueError,TypeError,RecursionError):raise Blocked('OPPORTUNITY_JSON')
    if len(encoded)>MAX_INPUT:raise Blocked('OPPORTUNITY_INPUT_BOUNDS')
    obj(b,{'schema','title','as_of','max_age_days','public_brief','sources','workflows','observations','mechanisms','preferences'})
    if type(b['schema']) is not int or b['schema']!=1:raise Blocked('OPPORTUNITY_SCHEMA')
    text(b['title'],200);text(b['public_brief'],500);as_of=date(b['as_of']);integer(b['max_age_days'],1,3650)
    arr(b['sources'],80,1);sources=unique(b['sources'])
    for s in b['sources']:source_record(s,as_of)
    for s in b['sources']:
        if s['id'] in s['derived_from'] or any(x not in sources for x in s['derived_from']):raise Blocked('SOURCE_LINEAGE')
    # Reject circular attribution rather than pretending it is independent.
    def visit(i,active,done):
        if i in active:raise Blocked('SOURCE_LINEAGE_CYCLE')
        if i in done:return
        for d in sources[i]['derived_from']:visit(d,active|{i},done)
        done.add(i)
    done=set()
    for i in sources:visit(i,set(),done)
    arr(b['workflows'],20,1);workflows=unique(b['workflows'])
    for w in b['workflows']:
        obj(w,{'id','title','actor','domain','purpose','initial_fields','steps','needs'})
        for f in ['title','actor','domain','purpose']:text(w[f],1000)
        strings(w['initial_fields'],80);arr(w['steps'],30,1);steps=unique(w['steps'])
        for s in w['steps']:
            obj(s,{'id','action','requires','produces','evidence'});text(s['action'],500)
            strings(s['requires'],30);strings(s['produces'],30);refs(s['evidence'],sources)
        arr(w['needs'],30);unique(w['needs'])
        if len({(n.get('step'),n.get('field')) for n in w['needs']}) != len(w['needs']):raise Blocked('DUPLICATE_NEED_SLOT')
        for n in w['needs']:
            obj(n,{'id','step','field','pattern','why','basis','evidence'})
            if n['step'] not in steps or n['field'] not in steps[n['step']]['requires']:raise Blocked('NEED_STEP_CONTRACT')
            if n['pattern'] not in PATTERNS or n['basis'] not in {'reported','inferred'}:raise Blocked('NEED_CLASSIFICATION')
            text(n['why'],1200);refs(n['evidence'],sources)
    arr(b['observations'],200);unique(b['observations'])
    for o in b['observations']:
        obj(o,{'id','workflow','need','kind','scope','statement','evidence'})
        if o['workflow'] not in workflows or o['need'] not in {n['id'] for n in workflows[o['workflow']]['needs']}:raise Blocked('OBSERVATION_TARGET')
        if o['kind'] not in OBS or o['scope'] not in {'target','analogy','unspecified'}:raise Blocked('OBSERVATION_KIND')
        text(o['statement'],1200);refs(o['evidence'],sources)
    arr(b['mechanisms'],40);unique(b['mechanisms'])
    for m in b['mechanisms']:
        obj(m,{'id','title','domain','purpose','mechanism','addresses','requires','provides','limits','evidence','code_queries'})
        for f in ['title','domain','purpose','mechanism']:text(m[f],1200)
        strings(m['addresses'],10);strings(m['requires'],30);strings(m['provides'],30);strings(m['limits'],10)
        if not m['addresses'] or set(m['addresses'])-PATTERNS or not m['limits']:raise Blocked('MECHANISM_SCOPE')
        refs(m['evidence'],sources);strings(m['code_queries'],4)
        for q in m['code_queries']:text(q,160)
    if not isinstance(b['preferences'],dict) or set(b['preferences'])-PATTERNS:raise Blocked('PREFERENCE_FIELDS')
    for v in b['preferences'].values():integer(v,1,5)
    return b


def validate(bundle):
    try:return _validate(bundle)
    except (TypeError,KeyError,IndexError,AttributeError,RecursionError) as exc:
        raise Blocked('MALFORMED_OPPORTUNITY_INPUT') from exc


def families(sources):
    """Treat declared common origins and byte/text duplicates as one evidence group.

    Grouping is conservative bookkeeping, not identity verification. Normalized
    content is used only to avoid repeated votes; exact source hashes are retained.
    """
    parent={s['id']:s['id'] for s in sources}
    def root(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    def union(a,b):
        a,b=root(a),root(b);parent[max(a,b)]=min(a,b)
    seen_f={};seen_t={};seen_uri={}
    for s in sources:
        # Same document excerpts are not separate independent observations.
        # Keep the original URI verbatim in provenance; only grouping ignores fragments.
        u=urlsplit(s['uri']); locator=u._replace(scheme=u.scheme.lower(),netloc=u.netloc.lower(),fragment='').geturl()
        for field,store in [(s['origin_group'],seen_f),(normalized(s['text']),seen_t),(locator,seen_uri)]:
            if field in store:union(s['id'],store[field])
            else:store[field]=s['id']
        for d in s['derived_from']:union(s['id'],d)
    return {i:root(i) for i in parent}


def source_evidence(o,sources,as_of,max_age):
    result=[]
    for r in o['evidence']:
        s=sources[r['source']];age=(as_of-date(s['observed_on'])).days
        result.append({'source':s['id'],'kind':s['kind'],'age_days':age,'fresh':age<=max_age,
                       'source_sha256':sha(s['text'].encode()),'origin_group':s['origin_group'],
                       'citation':copy.deepcopy(r),'source_uri':s['uri'],'public':s['public']})
    return result


def state_for(observations,sources,as_of,max_age,groups,handoff_missing):
    obs=[];support=[];refutations=[];problem=[];fresh_ids=set();stale=[];all_problem=set();information_missing=False
    for o in observations:
        ev=source_evidence(o,sources,as_of,max_age);fresh=[e for e in ev if e['fresh']]
        obs.append({**o,'checked_evidence':ev})
        stale.extend(e['source'] for e in ev if not e['fresh'])
        if o['scope']!='target' or not fresh:continue
        fresh_ids.update(e['source'] for e in fresh)
        if o['kind']=='not_described':information_missing=True
        if o['kind']=='supported':support.append(o['id'])
        if o['kind']=='refutes':refutations.append(o['id'])
        if o['kind'] in {'pain','manual','missing'}:
            # Published capabilities and source syntax cannot manufacture user pain.
            qualifying=[e for e in fresh if e['kind'] in {'firsthand','issue'}]
            if qualifying:
                problem.append(o['id']);all_problem.update(groups[e['source']] for e in qualifying)
    if (support or refutations) and problem:state='CONFLICT_REQUIRES_REVIEW'
    elif refutations:state='COUNTEREVIDENCE_REVIEW'
    elif support:state='EXISTING_OPTION_TO_VERIFY'
    elif len(all_problem)>=2:state='CORROBORATED_WORKFLOW_PROBLEM'
    elif problem:state='SINGLE_SOURCE_PROBLEM'
    elif stale and not fresh_ids:state='STALE_EVIDENCE_RECHECK'
    elif information_missing:state='INFORMATION_GAP_ONLY'
    elif handoff_missing:state='INFERRED_HANDOFF_GAP'
    else:state='UNTESTED_GAP_HYPOTHESIS'
    return state,{'problem_observations':problem,'support_observations':support,'counter_observations':refutations,
                  'independent_origin_groups':len(all_problem),'source_count':len(fresh_ids),
                  'stale_source_ids':sorted(set(stale)),'observations':obs,
                  'independence_verified':False,'annotation_truth_verified':False}


def infer_pattern(field):
    terms=set(re.findall(r'[a-z]+',field.lower()))
    if terms & {'identity','id','provenance'}:return 'identity_loss'
    if terms & {'verification','evidence','integrity','accuracy','verdict'}:return 'verification_gap'
    if terms & {'unit','units','format','schema'}:return 'format_gap'
    if terms & {'restore','recovery','startup'}:return 'recovery_gap'
    return 'information_gap'


def workflow_trace(w):
    """Forward propagation of declared prerequisites, NOT runtime execution.

    A step lists mandatory inputs. Its promised outputs are available to a later
    step only after those inputs are declared reachable. Root explanations are
    conservative unions, not a minimal/sufficient repair plan. A later producer
    cannot repair a prior consumer retroactively.
    """
    available=set(w['initial_fields']); pending={}; result={}
    for step in w['steps']:
        before=set(available); absent=set(step['requires'])-before
        roots={name:sorted(pending.get(name,{name})) for name in sorted(absent)}
        union=set().union(*(set(x) for x in roots.values())) if roots else set()
        result[step['id']]={
            'available_before':sorted(before),
            'missing_prerequisites':sorted(absent),
            'upstream_missing_inputs':roots,
            'unconfirmed_outputs':sorted(set(step['produces'])-before) if absent else [],
            'status':'BLOCKED_BY_DECLARED_INPUTS' if absent else 'INPUTS_DECLARED_NOT_EXECUTED',
            'runtime_verified':False,
            'explanation_is_minimal_repair':False}
        if absent:
            for output in step['produces']:
                if output not in available:pending.setdefault(output,set()).update(union)
        else:
            available.update(step['produces'])
            for output in step['produces']:pending.pop(output,None)
    return result


def missing_fields(w):
    return {sid:set(row['missing_prerequisites']) for sid,row in workflow_trace(w).items()}


def needs_for(w):
    needs=copy.deepcopy(w['needs']);known={(n['step'],n['field']) for n in needs};missing=missing_fields(w)
    for step in w['steps']:
        for field in sorted(missing[step['id']]):
            if (step['id'],field) not in known:
                nid='inferred-'+sha(canonical([step['id'],field]))[:24]
                needs.append({'id':nid,'step':step['id'],'field':field,'pattern':infer_pattern(field),
                              'why':'This declared step requires information not supplied by earlier declared steps.',
                              'basis':'inferred','evidence':copy.deepcopy(step['evidence'])})
    return needs


def mechanism_options(need,w,mechanisms):
    options=[]
    # Only information declared available before the target step can satisfy an input.
    available=set(workflow_trace(w)[need['step']]['available_before'])
    for m in mechanisms:
        if need['pattern'] not in m['addresses']:continue
        absent=sorted(set(m['requires'])-available)
        options.append({**copy.deepcopy(m),'missing_prerequisites':absent,
                        'status':'PREREQUISITES_UNCONFIRMED' if absent else 'INPUTS_DECLARED_REVIEW_REQUIRED',
                        'cross_domain':m['domain'].casefold()!=w['domain'].casefold(),
                        'analogy_basis':'Pattern-to-mechanism mapping from reviewed annotations; not trained analogy inference.',
                        'implementation_tested':False,
                        'output_fit':'DECLARED_OUTPUT_MATCH' if need['field'] in m['provides'] else 'SUPPORTING_COMPONENT_ONLY',
                        'unprovided_required_fields':[] if need['field'] in m['provides'] else [need['field']]})
    return sorted(options,key=lambda m:(len(m['missing_prerequisites']),m['output_fit']!='DECLARED_OUTPUT_MATCH',m['id']))[:6]


def counter_plan(w,n,state):
    q=f"{w['domain']} {n['field']} existing solution manual workflow"
    return [{'kind':'existing_solution','question':f"What already supplies {n['field']} for this exact handoff?",'query':q[:200]},
            {'kind':'disconfirm_need','question':'Observe users completing the task without this proposed intervention. Is the alleged missing step actually necessary?'},
            {'kind':'scope_check','question':'Check the same actor, artifact, version and operating conditions; a neighboring product is not automatically a substitute.'},
            {'kind':'mechanism_limit','question':'Which prerequisites or indistinguishable inputs make the proposed mechanism insufficient?'},
            {'kind':'demand','question':'Obtain independent task observations and effort measurements; listings and code do not establish willingness to pay.'}]


def concept(w,n,state,options):
    action=('EVALUATE_EXISTING_OPTION' if state=='EXISTING_OPTION_TO_VERIFY' else
            'RESOLVE_EVIDENCE_FIRST' if state in {'CONFLICT_REQUIRES_REVIEW','COUNTEREVIDENCE_REVIEW','STALE_EVIDENCE_RECHECK'} else 'INVESTIGATE_WORKFLOW_HYPOTHESIS')
    return {'title':f"{w['title']}: {n['field']} handoff review",'user':w['actor'],'recommended_action':action,
            'problem_hypothesis':n['why'],'input':sorted(set(w['initial_fields'])),
            'proposed_intervention':f"Preserve or recover {n['field']} before {next(s['action'] for s in w['steps'] if s['id']==n['step'])}.",
            'output':f"Evidence-linked {n['field']} or an explicit request for missing information.",
            'mechanism_options':[m['id'] for m in options],
            'existing_solution_state':state,'missing_work':['Confirm the workflow and alternatives','Qualify prerequisites and rights','Build a bounded adapter','Test with independent task cases'],
            'do_not_build_when':['Existing tools already meet the requirement at acceptable effort','No meaningful workflow burden is observed','Required distinguishing information is unavailable'],
            'status':'RULE_GENERATED_PROPOSAL_NOT_A_BUILT_APPLICATION'}


def experiment(w,n):
    return {'question':f"Does the proposed intervention reduce the effort of obtaining {n['field']} without increasing incorrect decisions?",
            'baseline':'Current observed workflow, version and task inputs must be frozen before evaluation.',
            'comparison':'Same task family and permitted information, with and without the candidate intervention.',
            'measures':['Correct outcomes','Incorrect acceptance','Appropriate abstention','Operator steps/time','Resource cost'],
            'required_cases':['Representative routine task','Missing input','Conflicting evidence','Indistinguishable alternatives','Existing solution that already works'],
            'stop_rules':['Budget exhausted','Required behavior regresses','No useful advantage over baseline'],
            'status':'PLAN_ONLY_NOT_EXECUTED','acceptance_owner':'Separate reviewer required before execution'}


def query_intent(value):
    """Explicit symbol mode plus backward-compatible qualified/underscored names.

    A plain word is a concept query unless an exact result is actually present.
    This is a transparent heuristic, not learned intent or a semantic proof.
    """
    text(value,160)
    symbol_re=r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*'
    if value.startswith('symbol:'):
        q=value[len('symbol:'):].strip()
        if not re.fullmatch(symbol_re,q):raise Blocked('INVALID_EXPLICIT_SYMBOL_QUERY')
        return q,True
    exact=bool(re.fullmatch(symbol_re,value) and ('_' in value or '.' in value))
    return value,exact


def code_leads(options,corpora,cache):
    from . import corpus
    leads=[]
    for m in options:
        found=[];partial=0
        for requested_query in m['code_queries']:
            q,exact_required=query_intent(requested_query)
            for name,report in corpora:
                k=(name,q)
                if k not in cache:cache[k]=corpus.search(report,q,limit=3,distinct=True)
                result=cache[k]
                hits=[h for h in result['results'] if h.get('all_query_terms_matched') is True]
                partial += len(result['results'])-len(hits)
                # An exact symbol query should not be padded with incidental mentions.
                exact_hits=[h for h in hits if h['name']==q or h['name'].endswith('.'+q)]
                if exact_required or exact_hits:hits=exact_hits
                elif any(h.get('exact_symbol_match') for h in hits):hits=[h for h in hits if h.get('exact_symbol_match')]
                for hit in hits:
                    ident=hit.get('definition_id') or hit.get('source_id')
                    if not ident:raise Blocked('CODE_RESULT_ID_MISSING')
                    found.append({'corpus':name,'query':requested_query,'search_mode':'EXACT_SYMBOL' if exact_required else 'CONCEPT_TERMS_WITH_EXACT_PREFERENCE','name':hit['name'],'definition_id':hit.get('definition_id'),
                                  'source_id':hit.get('source_id'),'source_sha256':hit.get('source_sha256'),
                                  'container_id':hit.get('file_id'),'path':hit.get('member_path',hit.get('path')),
                                  'start_line':hit.get('member_start_line',hit.get('start_line')),
                                  'end_line':hit.get('member_end_line',hit.get('end_line')),
                                  'all_query_terms_matched':hit.get('all_query_terms_matched',False),
                                  'index_binding':result.get('index_binding'),'evidence_level':'METADATA_RETRIEVAL_ONLY',
                                  'runtime_tested':False,'rights_reviewed':False})
        # Keep repeated search routes, but not duplicate candidate records per mechanism.
        unique_hits={canonical([h['corpus'],h['definition_id'],h['source_id']]):h for h in found}
        leads.append({'mechanism':m['id'],'candidates':list(unique_hits.values())[:12],'partial_matches_held_for_review':partial,'status':'CANDIDATES_FOR_REVIEW' if found else 'NO_METADATA_MATCH_ESTABLISHED'})
    return leads


def evaluate(bundle,corpora=()):
    b=validate(bundle);sources={s['id']:s for s in b['sources']};groups=families(b['sources']);as_of=date(b['as_of'])
    corpora=list(corpora)
    if len(corpora)>4 or len({name for name,_ in corpora})!=len(corpora):raise Blocked('CORPUS_SELECTION')
    bindings=[]
    for name,r in corpora:
        text(name,100);bindings.append({'name':name,'sha256':sha(canonical(r))})
    # Full corpus bytes bind output; no claim of provider authentication or live refresh.
    run_id='opp_'+sha(canonical({'version':VERSION,'bundle':b,'corpora':bindings}))
    if sum(len(needs_for(w)) for w in b['workflows'])>80:raise Blocked('OPPORTUNITY_HYPOTHESIS_BUDGET')
    out=[];cache={}
    for w in b['workflows']:
        trace=workflow_trace(w);missing=missing_fields(w)
        for n in needs_for(w):
            obs=[o for o in b['observations'] if o['workflow']==w['id'] and o['need']==n['id']]
            state,ev=state_for(obs,sources,as_of,b['max_age_days'],groups,n['field'] in missing[n['step']])
            options=mechanism_options(n,w,b['mechanisms'])
            # Preferences order investigation, never change evidence state.
            priority=[int(state in {'CORROBORATED_WORKFLOW_PROBLEM','SINGLE_SOURCE_PROBLEM'}),
                      min(ev['independent_origin_groups'],3),b['preferences'].get(n['pattern'],2),
                      sum(not m['missing_prerequisites'] for m in options)]
            hid='hyp_'+sha(canonical([w['id'],n['id'],n['field']]))[:32]
            links=[] if state in {'EXISTING_OPTION_TO_VERIFY','CONFLICT_REQUIRES_REVIEW','COUNTEREVIDENCE_REVIEW','STALE_EVIDENCE_RECHECK'} else code_leads(options,corpora,cache)
            out.append({'id':hid,'workflow':w['id'],'need':n,'state':state,'evidence':ev,
                        'workflow_context':{k:w[k] for k in ('title','actor','domain','purpose')},
                        'handoff_missing_in_declared_map':n['field'] in missing[n['step']],
                        'workflow_readiness':copy.deepcopy(trace[n['step']]),
                        'mechanisms':options,'code_leads':links,'priority_components':priority,
                        'priority_is_probability':False,'concept':concept(w,n,state,options),
                        'counter_searches':counter_plan(w,n,state),'experiment':experiment(w,n),
                        'human_judgment_needed':True})
            from .invention_plan import next_investigation
            out[-1]['next_investigation']=next_investigation(out[-1])
    if bindings != [{'name':name,'sha256':sha(canonical(r))} for name,r in corpora]:raise Blocked('OPPORTUNITY_CORPUS_CHANGED')
    out.sort(key=lambda h:(tuple(-x for x in h['priority_components']),h['id']))
    freshness={s['id']:(as_of-date(s['observed_on'])).days<=b['max_age_days'] for s in b['sources']}
    return {'schema':1,'version':VERSION,'id':run_id,'title':b['title'],'as_of':b['as_of'],
            'status':'OPPORTUNITIES_FOR_REVIEW','hypotheses':out,
            'source_register':[{**{k:s[k] for k in ('id','kind','uri','public','origin_group','observed_on','derived_from')},
                                'content_sha256':sha(s['text'].encode()),'fresh':freshness[s['id']],
                                'capture_method':s.get('capture_method','attributed_summary'),
                                'group':groups[s['id']]} for s in b['sources']],
            'bundle_sha256':sha(canonical(b)),'corpus_bindings':bindings,
            'summary':{'workflows':len(b['workflows']),'hypotheses':len(out),'sources':len(sources),
                       'source_groups':len(set(groups.values())),
                       'states':{s:sum(h['state']==s for h in out) for s in sorted({h['state'] for h in out})},
                       'code_searches':len(cache),'code_lead_count':sum(len(c['candidates']) for h in out for c in h['code_leads'])},
            'scope':'Bounded reviewed input collection, not market census or independent opportunity validation.',
            'coverage':{'input_sources_processed':len(sources),'internet_coverage':'NOT_MEASURED','exhaustive_market_review':False},
            'authority':copy.deepcopy(AUTHORITY),
            'limitations':['Workflow annotations and source interpretations are reviewer supplied.',
                           'Exact quotes verify text correspondence, not entailment or factual truth.',
                           'Missing description is not missing capability. Code is not demand.',
                           'Family metadata and freshness are supplied observations, not authenticated provenance.',
                           'Mechanism matches are rule-based hypotheses and prerequisites require verification.']}


class OpportunityStore:
    """Content-addressed immutable input/result pairs and separately retained feedback.

    Same-user local filesystem access remains trusted. No claim of signed logs.
    """
    def __init__(self,w):self.w=w;self.root=safe_path(w.home/'opportunities')
    def run(self,bundle,corpora=()):
        self.w.guard();report=evaluate(bundle,corpora);self.w.guard();rid=report['id'];dest=self.root/rid
        payload={'bundle':validate(bundle),'report':report}
        record={'payload':payload,'sha256':sha(canonical(payload))}
        self.root.mkdir(parents=True,exist_ok=True)
        from .corpus import _lock
        with _lock(self.root/'publication.lock'):
            if dest.exists():
                old=self.get(rid)
                if old!=report:raise Blocked('IMMUTABLE_RUN_CONFLICT')
            else:
                dest.mkdir(mode=0o700)
                try:write(dest/'record.json',canonical(record),new=True)
                except Exception:
                    # Incomplete state remains an explicit blocker, not an empty success.
                    raise
        return report
    def path(self,rid):
        if not isinstance(rid,str) or not re.fullmatch('opp_[0-9a-f]{64}',rid):raise Blocked('OPPORTUNITY_RUN_ID')
        return self.root/rid
    def get(self,rid):
        self.w.guard();p=self.path(rid);r=__import__('json')
        from .common import loads
        if not (p/'record.json').is_file():raise Blocked('OPPORTUNITY_RUN_MISSING')
        v=loads(read(p/'record.json',10_000_000));obj(v,{'payload','sha256'})
        if sha(canonical(v['payload']))!=v['sha256']:raise Blocked('OPPORTUNITY_RECEIPT_CHANGED')
        data=v['payload'];obj(data,{'bundle','report'});validate(data['bundle'])
        if data['report']['id']!=rid or data['report']['bundle_sha256']!=sha(canonical(data['bundle'])):raise Blocked('OPPORTUNITY_BINDING')
        expected='opp_'+sha(canonical({'version':data['report']['version'],'bundle':data['bundle'],'corpora':data['report']['corpus_bindings']}))
        if expected!=rid or data['report'].get('authority')!=AUTHORITY:raise Blocked('OPPORTUNITY_AUTHORITY')
        validate_investigation_report(data['report'])
        return data['report']
    def list(self):
        self.w.guard()
        if not self.root.exists():return []
        out=[]
        for p in sorted(self.root.glob('opp_*'))[:500]:
            r=self.get(p.name);out.append({k:r[k] for k in ('id','title','status','summary')})
        return out
    def feedback(self,rid,note):
        report=self.get(rid);obj(note,{'hypothesis','decision','reason','actor'})
        if note['hypothesis'] not in {h['id'] for h in report['hypotheses']}:raise Blocked('FEEDBACK_HYPOTHESIS')
        if note['decision'] not in {'INVESTIGATE','REVISE','REJECT'}:raise Blocked('FEEDBACK_DECISION')
        text(note['reason'],2000);text(note['actor'],160)
        result={'run':rid,'note':copy.deepcopy(note),'kind':'OWNER_JUDGMENT_NOT_VALIDATION','authority':copy.deepcopy(AUTHORITY)}
        fid='feedback_'+sha(canonical(result));dest=self.path(rid)/'feedback'/f'{fid}.json'
        from .corpus import _lock
        with _lock(self.path(rid)/'feedback.lock'):
            if dest.exists():
                if read(dest)!=canonical(result):raise Blocked('FEEDBACK_CHANGED')
            else:write(dest,canonical(result),new=True)
        return {'id':fid,**result}
    def feedbacks(self,rid):
        self.get(rid);from .common import loads
        result=[]
        for p in sorted((self.path(rid)/'feedback').glob('feedback_*.json'))[:2000]:
            r=loads(read(p,10000))
            if p.stem!='feedback_'+sha(canonical(r)) or r.get('run')!=rid:raise Blocked('FEEDBACK_CHANGED')
            result.append(r)
        return result
    def export(self,rid,out):
        report=self.get(rid);out=safe_path(out)
        from .engine import ROOT
        if out.exists() or out==self.w.home or out.is_relative_to(self.w.home/'engine') or out.is_relative_to(ROOT):raise Blocked('NEW_OPPORTUNITY_EXPORT_REQUIRED')
        out.mkdir(parents=True,mode=0o700)
        files={'opportunity.json':canonical(report),'feedback.json':canonical(self.feedbacks(rid)),
               'Review.html':render(report).encode(),
               'hypotheses.ndjson.gz':gzip.compress(b''.join(canonical(h)+b'\n' for h in report['hypotheses']),mtime=0),
               'agent_request.json':canonical(agent_packet(report))}
        for name,data in files.items():write(out/name,data,new=True)
        receipt={'schema':1,'run':rid,'files':{n:sha(d) for n,d in files.items()},'authority':copy.deepcopy(AUTHORITY)}
        write(out/'receipt.json',canonical(receipt),new=True);return receipt


def validate_investigation_report(report):
    """Validate deterministic plans at storage/export boundaries.

    This prevents an internally inconsistent packet from granting new authority;
    it is not an external signature or an equally-privileged-host security layer.
    """
    from .invention_plan import next_investigation
    try:
        for h in arr(report['hypotheses'],80):
            if 'next_investigation' not in h:
                if report.get('version')==VERSION:raise Blocked('INVESTIGATION_PLAN_MISSING')
                continue  # Historical version: no plan is invented.
            if h['next_investigation'] != next_investigation(h):
                raise Blocked('INVESTIGATION_PLAN_CHANGED')
    except (TypeError,KeyError,IndexError,AttributeError,RecursionError) as exc:
        raise Blocked('INVALID_INVESTIGATION_REPORT') from exc


def agent_packet(report):
    validate_investigation_report(report)
    # No source excerpts or private identifiers enter a public-search brief.
    return {'schema':1,'run':report['id'],'role':'Private opportunity reviewer',
            'tasks':[{'hypothesis':h['id'],'state':h['state'],'questions':[q['question'] for q in h['counter_searches']],
                      'experiment':h['experiment'],
                      **({'next_investigation':h['next_investigation']} if 'next_investigation' in h else {})} for h in report['hypotheses']],
            'instructions':'Use authorized sources. Cite actual evidence; retain existing solutions, contradictory information and unknowns. Do not execute source or claim validated demand.',
            'public_search_authorized':False,'authority':copy.deepcopy(AUTHORITY)}


def draft(source,as_of=None):
    """Conservative text cue extraction; not automatic workflow understanding."""
    as_of=as_of or dt.datetime.now(dt.timezone.utc).date().isoformat()
    s=source_record(copy.deepcopy(source),date(as_of));suggestions=[]
    terms={'manual':r'\b(manually|manual|re-enter|copy and paste)\b',
           'requirement':r'\b(must|requires?|need(?:s)? to)\b',
           'limitation':r'\b(cannot|not supported|does not|missing|without)\b'}
    for typ,pat in terms.items():
        for match in list(re.finditer(pat,s['text'],re.I))[:12]:
            start=max(s['text'].rfind('.',0,match.start())+1,s['text'].rfind('\n',0,match.start())+1)
            end=s['text'].find('.',match.end());end=len(s['text']) if end<0 else end+1
            if end-start>1500:continue
            suggestions.append({'cue':typ,'evidence':{'source':s['id'],'start':start,'end':end,'quote':s['text'][start:end]},
                                'status':'ANNOTATION_REQUIRED','supports_gap':False})
    return {'source':s['id'],'source_sha256':sha(s['text'].encode()),'suggestions':suggestions,
            'status':'DRAFT_CUES_NOT_CLAIMS','model_calls':0,'instructions_in_source_executed':False}


def render(r):
    validate_investigation_report(r)
    e=lambda x:html.escape(str(x),quote=True)
    cards=[]
    for h in r['hypotheses']:
        mechanisms=''.join('<li><b>'+e(m['title'])+'</b>: '+e(m['mechanism'])+'<br>Output fit: '+e(m['output_fit'].replace('_',' ').lower())+'<br>Prerequisites unresolved: '+e(', '.join(m['missing_prerequisites']) or 'None in the declared map; still unverified')+'</li>' for m in h['mechanisms'])
        agenda=''.join('<li><b>'+e(t['question'])+'</b><br>What could disprove it: '+e(t['falsifier'])+'</li>' for t in h.get('next_investigation',{}).get('tasks',[]))
        agenda=('<h3>Next evidence to collect</h3><ol>'+agenda+'</ol><p><small>Planned investigation only. No research, contact, acquisition or execution is authorized by this plan.</small></p>') if agenda else ''
        citations=''.join('<li>'+e(ev['source_uri'])+' — '+e(ev['citation']['quote'])+'</li>' for o in h['evidence']['observations'] for ev in o['checked_evidence'])
        code=''.join('<li>'+e(c['name'])+' · '+e(c['corpus'])+' · '+e(c['definition_id'])+'</li>' for m in h['code_leads'] for c in m['candidates'])
        cards.append('<article><span class="tag">'+e(h['state'].replace('_',' '))+'</span><h2>'+e(h['concept']['title'])+'</h2><p>'+e(h['concept']['problem_hypothesis'])+'</p><p><b>Next action:</b> '+e(h['concept']['recommended_action'].replace('_',' ').lower())+'</p><p><b>User:</b> '+e(h['concept']['user'])+'</p><p><b>Proposed intervention:</b> '+e(h['concept']['proposed_intervention'])+'</p>'+agenda+'<h3>Mechanisms, not promises</h3><ul>'+mechanisms+'</ul><h3>What could disprove it?</h3><p>'+e(h['counter_searches'][0]['question'])+'</p><p>'+e(h['experiment']['question'])+'</p><p><small>Experiment is a plan, not an executed validation. Source interpretations and mechanism mappings require review.</small></p><details><summary>Evidence and implementation leads</summary><ul>'+citations+'</ul><ul>'+code+'</ul></details></article>')
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FORGE Opportunity Review</title><style>body{font:16px/1.6 system-ui;background:#10141c;color:#e9eef7;max-width:1080px;margin:auto;padding:32px}h1{font-size:40px;line-height:1.15}article{background:#1b2431;border:1px solid #344151;border-radius:12px;padding:28px;margin:20px 0}.tag{color:#7ddac6;font-size:12px;font-weight:700}p,li{overflow-wrap:anywhere}summary{cursor:pointer}small{color:#aebdce}h3{margin-bottom:6px}@media(max-width:600px){body{padding:16px}h1{font-size:28px}article{padding:18px}}</style><small>FORGE '+e(__version__)+' / OPPORTUNITY DISCOVERY</small><h1>'+e(r['title'])+'</h1><p>Find the missing step. Challenge the idea. Trace a plausible mechanism.</p><p><b>'+e(r['summary']['hypotheses'])+' hypotheses · '+e(r['summary']['sources'])+' input sources · '+e(r['summary']['code_lead_count'])+' code leads</b></p><p>Deterministic rule-generated hypotheses from reviewed inputs. Not a market census, independent AI invention or validated business. No source code executed.</p>'+''.join(cards)+'<footer>Source and code identities remain private. All proposals require review, independent task evidence, rights checks and qualified experiments.</footer></html>'


def verify_export(folder):
    from .common import loads
    folder=safe_path(folder);rec=loads(read(folder/'receipt.json',10000))
    obj(rec,{'schema','run','files','authority'})
    expected={'opportunity.json','feedback.json','Review.html','hypotheses.ndjson.gz','agent_request.json'}
    if type(rec['schema']) is not int or rec['schema']!=1 or not isinstance(rec['files'],dict) or set(rec['files'])!=expected or rec['authority']!=AUTHORITY:raise Blocked('OPPORTUNITY_PACKET_SCHEMA')
    if {p.name for p in folder.iterdir()}!=expected|{'receipt.json'}:raise Blocked('OPPORTUNITY_PACKET_CONTENTS')
    for name,digest in rec['files'].items():
        if sha(read(folder/name,10_000_000))!=digest:raise Blocked('OPPORTUNITY_PACKET_HASH')
    r=loads(read(folder/'opportunity.json',10_000_000))
    if not isinstance(r,dict) or r.get('id')!=rec['run'] or r.get('authority')!=AUTHORITY:raise Blocked('OPPORTUNITY_PACKET_BINDING')
    request=loads(read(folder/'agent_request.json',10_000_000))
    if request!=agent_packet(r):raise Blocked('OPPORTUNITY_PACKET_REQUEST_CHANGED')
    judgments=loads(read(folder/'feedback.json',10_000_000));arr(judgments,2000)
    for judgment in judgments:
        obj(judgment,{'run','note','kind','authority'})
        if judgment['run']!=r['id'] or judgment['kind']!='OWNER_JUDGMENT_NOT_VALIDATION' or judgment['authority']!=AUTHORITY:raise Blocked('OPPORTUNITY_PACKET_FEEDBACK_AUTHORITY')
        n=judgment['note'];obj(n,{'hypothesis','decision','reason','actor'})
        if n['hypothesis'] not in {h['id'] for h in r['hypotheses']} or n['decision'] not in {'INVESTIGATE','REVISE','REJECT'}:raise Blocked('OPPORTUNITY_PACKET_FEEDBACK_SCOPE')
        text(n['reason'],2000);text(n['actor'],160)
    return {'status':'OPPORTUNITY_PACKET_CHECKSUMS_VERIFIED','run':rec['run'],'files_verified':len(expected),'authenticated_attestation':False,'market_validated':False}


def guided_bundle(value):
    """Turn explicit operator form fields into the same reviewed input schema.

    No language model invents the actor, needed field or claim state. The user
    supplies them directly. A default 'unknown' leaves gap evidence unestablished.
    """
    obj(value,{'note','actor','domain','received','needed','pattern','observation'})
    for f in ['actor','domain','received','needed']:text(value[f],160)
    if not isinstance(value['note'],str) or not value['note'].strip() or len(value['note'])>6000 or '\0' in value['note']:raise Blocked('GUIDED_NOTE')
    if value['pattern'] not in PATTERNS or value['observation'] not in {'manual','missing','pain','unknown','not_described','supported'}:raise Blocked('GUIDED_CLASSIFICATION')
    from .opportunity_examples import example_bundle,source,cite
    b=example_bundle();today=dt.datetime.now(dt.timezone.utc).date().isoformat()
    s=source('owner-note',value['note'],kind='firsthand',uri='conversation://local/owner-note');s['observed_on']=today;s['capture_method']='verbatim_excerpt'
    b.update({'title':value['needed']+' workflow investigation','as_of':today,'public_brief':'An owner-defined workflow investigation. Public search is not authorized.','sources':[s]})
    w=b['workflows'][0];w.update({'title':value['needed']+' handoff','actor':value['actor'],'domain':value['domain'],
                               'purpose':'Investigate the required '+value['needed'],'initial_fields':[value['received']]})
    w['steps']=[{'id':'receive','action':'Receive input','requires':[value['received']],'produces':[value['received']],'evidence':[cite(s)]},
                {'id':'review','action':'Complete the next task','requires':sorted({value['received'],value['needed']}),'produces':['task_result'],'evidence':[cite(s)]}]
    w['needs']=[{'id':'need','step':'review','field':value['needed'],'pattern':value['pattern'],
                 'why':'Determine whether '+value['needed']+' is available when the next task needs it.','basis':'reported' if value['observation'] in {'manual','missing','pain'} else 'inferred','evidence':[cite(s)]}]
    b['observations']=[{'id':'owner-claim','workflow':w['id'],'need':'need','kind':value['observation'],'scope':'target',
                       'statement':'The owner selected this observation state; it is not inferred from a keyword.','evidence':[cite(s)]}]
    # Do not silently substitute scanner-specific mechanisms for an unrelated task.
    b['mechanisms']=[]
    return validate(b)
