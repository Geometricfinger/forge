"""Goal-directed retrieval of verified static findings and source-context leads.

No model or source code execution. Constraints select observed patterns, not
whole-program guarantees. Context links are lexical candidates, never asserted
runtime call edges. Every result preserves existing Atlas identities.
"""
from __future__ import annotations
from collections import defaultdict
import argparse
import json
from pathlib import Path,PurePosixPath
import hound
import atlas_hunt as atlas


STATUS_VOCABULARY={
 'geometry.object_factor_context':{'OBJECT_FACTOR_CONTEXT_REQUIRES_REVIEW'},
 hound.RIGID:{'STATIC_CANDIDATE','NEEDS_CONTEXT','CONTRADICTED_CALL_CONTRACT'},
 hound.SCALE:{'INPUT_ALIAS_RECEIVER','COPY_CALL_RESULT_RECEIVER','UNRESOLVED_RECEIVER'},
 hound.ABSTAIN:{'EXPLICIT_NONDECISION_LITERAL','NONDECISION_REQUIRES_CONTROL_FLOW_REVIEW','GENERATOR_RETURN_REQUIRES_REVIEW'},
 'storage.resolved_path_guard':{'STATIC_CANDIDATE'},
 'validation.explicit_units_finite_values':{'STATIC_CANDIDATE'},
 hound.PRIMITIVES:{'COMPUTATIONAL_PRIMITIVE_OBSERVED'},
 hound.DIMENSION:{'DIMENSION_REJECTION_PATTERN'},
 hound.SCALED_MATRIX:{'SCALED_MATRIX_EXPRESSION_OBSERVED'},
}

def validate_finding_span(f,card):
    span=f.get('function_lines');cr=card.get('source_range',{})
    if not isinstance(span,list) or len(span)!=2 or any(type(x) is not int for x in span):
        raise ValueError('MISSION_FUNCTION_RANGE')
    start,end=cr.get('start'),cr.get('end')
    if type(start) is not int or type(end) is not int or not 1<=start<=span[0]<=span[1]==end:
        raise ValueError('MISSION_FUNCTION_RANGE')
    if card.get('qualified_name') is not None and f.get('qualified_name')!=card['qualified_name']:
        raise ValueError('MISSION_FUNCTION_NAME')
    ev=f.get('evidence')
    if not isinstance(ev,list) or not ev:raise ValueError('MISSION_EVIDENCE_RANGE')
    for row in ev:
        if not isinstance(row,dict) or set(row)-{'line_start','line_end','observation'}:
            raise ValueError('MISSION_EVIDENCE_SCHEMA')
        a,b=row.get('line_start'),row.get('line_end')
        if type(a) is not int or type(b) is not int or not span[0]<=a<=b<=span[1]:
            raise ValueError('MISSION_EVIDENCE_RANGE')
    if f.get('status') not in STATUS_VOCABULARY.get(f.get('profile_id'),set()):
        raise ValueError('MISSION_FINDING_STATUS')


def validate_mission(m):
    if not isinstance(m,dict) or set(m)-{'id','title','requirements','disqualifiers','exclude_tests','max_candidates'}:raise ValueError('MISSION_SCHEMA')
    if not isinstance(m.get('id'),str) or not m['id'].strip() or not isinstance(m.get('title'),str) or not m['title'].strip():raise ValueError('MISSION_ID')
    if not isinstance(m.get('requirements'),list) or not m['requirements']:raise ValueError('EMPTY_MISSION')
    if type(m.get('exclude_tests',True)) is not bool:raise ValueError('MISSION_TEST_POLICY')
    if type(m.get('max_candidates',25)) is not int or not 1<=m.get('max_candidates',25)<=100:raise ValueError('MISSION_LIMIT')
    if not isinstance(m.get('disqualifiers',[]),list):raise ValueError('MISSION_DISQUALIFIERS')
    seen=set()
    for clause in m['requirements']+m.get('disqualifiers',[]):
        if not isinstance(clause,dict) or set(clause)-{'id','profile_id','statuses','operations','dimensions'}:raise ValueError('MISSION_CLAUSE')
        if clause.get('profile_id') not in hound.PROFILE_IDS:raise ValueError('UNKNOWN_PROFILE')
        if not isinstance(clause.get('statuses'),list) or not clause['statuses'] or not all(isinstance(x,str) for x in clause['statuses']):raise ValueError('MISSION_STATUSES')
        if not isinstance(clause.get('id'),str) or not clause['id'].strip() or clause['id'] in seen:raise ValueError('MISSION_DUPLICATE_CLAUSE')
        seen.add(clause['id'])
        if set(clause['statuses'])-STATUS_VOCABULARY[clause['profile_id']]:raise ValueError('MISSION_UNKNOWN_STATUS')
        if len(set(clause['statuses']))!=len(clause['statuses']):raise ValueError('MISSION_DUPLICATE_STATUS')
        if 'dimensions' in clause and (not isinstance(clause['dimensions'],list) or not clause['dimensions'] or not all(type(x) is int and x in (2,3) for x in clause['dimensions'])):raise ValueError('MISSION_DIMENSIONS')
        if 'operations' in clause and (not isinstance(clause['operations'],list) or not clause['operations'] or not all(isinstance(x,str) for x in clause['operations'])):raise ValueError('MISSION_OPERATIONS')


def matching(f,clause):
    return f.get('profile_id')==clause['profile_id'] and f.get('status') in clause['statuses'] and (
        'operations' not in clause or f.get('operation') in clause['operations']) and ('dimensions' not in clause or f.get('expected_columns') in clause['dimensions'])


def module_name(path):
    p=PurePosixPath(path)
    if p.suffix!='.py':return None
    parts=list(p.with_suffix('').parts)
    if parts[-1]=='__init__':parts.pop()
    return '.'.join(parts)


def context_trails(card,cards):
    """At most one unambiguous static module/symbol match, limited to this project."""
    module=module_name(card.get('relative_path',''))
    if module is None:return []
    candidates=[c for c in cards if c.get('project_id')==card.get('project_id')]
    package=module.split('.')[:-1]
    if PurePosixPath(card.get('relative_path','')).name=='__init__.py':package=module.split('.')
    imports={i.get('alias'):i for i in card.get('module_imports',[]) if i.get('alias')}
    local=set(card.get('local_bindings',[]));trails=[];seen=set()
    for call in card.get('lexical_calls',[]):
        name=call.get('name','');head,_,tail=name.partition('.')
        if not head or head in local:continue
        target_module,target_symbol=module,name
        if head in imports:
            i=imports[head];im=i.get('module','');level=i.get('level',0)
            if level:
                if level-1>len(package):continue
                parent=package[:len(package)-(level-1)] if level>1 else package
                im='.'.join([*parent,*im.split('.')]) if im else '.'.join(parent)
            if i.get('name'):
                target_module=im;target_symbol=i['name']+('.'+tail if tail else '')
            elif tail:
                # Conventional `import package as alias; alias.function` only.
                target_module=im;target_symbol=tail
            else:continue
        elif '.' in name:continue
        matches=[c for c in candidates if module_name(c.get('relative_path',''))==target_module and c.get('qualified_name')==target_symbol]
        if not matches:continue
        key=(name,call.get('line'))
        if key in seen:continue
        seen.add(key)
        trails.append({'lexical_call':name,'call_line':call.get('line'),
            'status':'LEXICAL_CONTEXT_CANDIDATE_NOT_RUNTIME_EDGE' if len(matches)==1 else 'AMBIGUOUS_CONTEXT_TARGET',
            'target_card_ids':[c['card_id'] for c in matches],
            'next_action':'Read the exact target and its tests; resolve dynamic dispatch, rebinding and dependencies before reuse.'})
    return trails[:30]


def query(mission,report,catalog):
    validate_mission(mission)
    sources,cards,cat_hash=atlas.load_catalog(catalog)
    if report.get('catalog_sha256')!=cat_hash:raise ValueError('HUNT_CATALOG_MISMATCH')
    byid={c['card_id']:c for c in cards};groups=defaultdict(list)
    seen_findings=set()
    scanned=report.get('selected_profiles')
    needed={x['profile_id'] for x in mission['requirements']+mission.get('disqualifiers',[])}
    if not isinstance(scanned,list) or not all(isinstance(x,str) for x in scanned) or needed-set(scanned):
        raise ValueError('MISSION_PROFILES_NOT_SCANNED')
    coverage={}
    for row in report.get('source_coverage',[]):
        if not isinstance(row,dict) or row.get('source_id') not in sources:raise ValueError('MISSION_COVERAGE_SCHEMA')
        sid=row['source_id'];source=sources[sid]
        if sid in coverage:raise ValueError('DUPLICATE_SOURCE_COVERAGE')
        if row.get('observation_id')!=source['observation_id'] or row.get('source_sha256')!=source['sha256']:
            raise ValueError('MISSION_COVERAGE_BINDING')
        coverage[sid]=row
    current_cards=[c for c in cards if c.get('source_id') in sources and sources[c['source_id']]['_active']
                   and c['observation_id']==sources[c['source_id']]['observation_id']]
    for f in report.get('findings',[]):
        if f.get('finding_id') in seen_findings:raise ValueError('DUPLICATE_FINDING')
        seen_findings.add(f.get('finding_id'))
        if coverage.get(f.get('source_id'),{}).get('status')!='STATIC_INSPECTED':raise ValueError('SOURCE_NOT_INSPECTED')
        c=byid.get(f.get('card_id'));s=sources.get(f.get('source_id'))
        if c is None or s is None or f.get('observation_id')!=s['observation_id'] or c['observation_id']!=s['observation_id'] or f.get('source_sha256')!=s['sha256'] or c['source_id']!=s['source_id']:
            raise ValueError('MISSION_FINDING_BINDING')
        if f.get('function_id')!=c['function_id']:raise ValueError('MISSION_FUNCTION_BINDING')
        if f.get('profile_id') not in scanned:raise ValueError('UNSCANNED_FINDING_PROFILE')
        validate_finding_span(f,c)
        if f.get('runtime_verified') is not False:raise ValueError('UNSUPPORTED_EVIDENCE_PROMOTION')
        core={k:v for k,v in f.items() if k!='finding_id'}
        canonical_id='finding_'+hound.sha(hound.canonical(core))
        # v0.2 Atlas adapter hashes the original scanner ID into the bound ID.
        # Verify that exact legacy recipe; do not accept an arbitrary mismatched digest.
        bound_fields={'card_id','function_id','observation_id','project_id','source_location','display_path','is_test'}
        scanner_core={k:v for k,v in core.items() if k not in bound_fields}
        scanner_id='finding_'+hound.sha(hound.canonical(scanner_core))
        legacy_id='finding_'+hound.sha(hound.canonical({**core,'finding_id':scanner_id}))
        if f.get('finding_id') not in (canonical_id,legacy_id):raise ValueError('MISSION_FINDING_DIGEST')
        groups[c['card_id']].append(f)
    accepted=[];excluded=[]
    for cid,findings in groups.items():
        card=byid[cid];s=sources[card['source_id']]
        if not s['_active']:continue
        is_test='/tests/' in '/'+s.get('relative_path','') or PurePosixPath(s.get('relative_path','')).name.startswith('test_')
        if mission.get('exclude_tests',True) and is_test:continue
        clauses=[{'requirement_id':r['id'],'finding_ids':[f['finding_id'] for f in findings if matching(f,r)]} for r in mission['requirements']]
        if not all(c['finding_ids'] for c in clauses):continue
        conflicts=[f['finding_id'] for d in mission.get('disqualifiers',[]) for f in findings if matching(f,d)]
        common={'card_id':cid,'function_id':card['function_id'],'observation_id':card['observation_id'],
            'source_id':card['source_id'],'source_sha256':s['sha256'],'project_id':s['project_id'],
            'qualified_name':card.get('qualified_name'), 'display_path':s.get('display_path'),'source_location':s.get('url'),
            'source_range':card.get('source_range'),'is_test':is_test}
        if conflicts:
            excluded.append({**common,'status':'EXCLUDED_BY_OBSERVED_CONTRADICTION','finding_ids':conflicts});continue
        traits=sorted({f.get('operation') for f in findings if f.get('operation')})
        accepted.append({**common,'status':'STATIC_CANDIDATE_REQUIRES_REVIEW','matched_requirements':clauses,
            'observed_operations':traits,'context_trails':context_trails(card,current_cards),
            'declared_parameters':card.get('parameters',[]),'contract':card.get('contract',{}),
            'readiness':'NOT_RUNTIME_VERIFIED','rights':'NOT_REVIEWED','release_approved':False,
            'gaps':['Confirm units, coordinate frames, runtime types and dependencies.',
                    'Test against a frozen baseline before claiming useful improvement.'],
            'evidence_rank':sum(len(c['finding_ids']) for c in clauses),
            'rank_meaning':'Count of matching observations, NOT confidence, quality or probability.'})
    accepted.sort(key=lambda x:(-x['evidence_rank'],x['display_path'] or '',x['qualified_name'] or '',x['card_id']))
    limit=mission.get('max_candidates',25)
    return {'mission':mission,'mission_sha256':hound.sha(hound.canonical(mission)),'catalog_sha256':cat_hash,
        'hunt_engine_sha256':report.get('engine_sha256'),'hunt_coverage':report.get('coverage'),
        'eligible_candidate_count':len(accepted),'candidates':accepted[:limit],'omitted_by_limit':max(0,len(accepted)-limit),
        'excluded':excluded,'source_code_executed':False,'model_called':False,'no_match_is_not_proof_of_absence':True,
        'limitations':['Recognized static patterns only; unrecognized implementations are not ruled out.',
                      'Context trails are lexical candidates, not established runtime dependencies.',
                      'No market, identity, numerical correctness or global novelty assessment.']}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mission',type=Path,required=True);p.add_argument('--hunt',type=Path,required=True)
    p.add_argument('--catalog',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    try:
        if a.out.exists():raise ValueError('NEW_OUTPUT_FILE_REQUIRED')
        m=atlas.strict_json(atlas.bounded_read(a.mission,100_000));r=atlas.strict_json(atlas.bounded_read(a.hunt,20_000_000))
        answer=query(m,r,a.catalog);a.out.parent.mkdir(parents=True,exist_ok=True)
        with a.out.open('xb') as stream:stream.write(hound.canonical(answer))
        print(json.dumps({'candidates':answer['eligible_candidate_count'],'excluded':len(answer['excluded'])}));return 0
    except (ValueError,OSError) as exc:
        print(json.dumps({'status':'BLOCKED','reason':str(exc)}));return 2

if __name__=='__main__':raise SystemExit(main())
