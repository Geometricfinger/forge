"""Require recorded utility before recommending an implementation for review.

This evaluates submitted evidence structure and hashed local artifact bytes,
not the truth of measurements, their independence, legal novelty or release safety.
"""
from __future__ import annotations
import argparse
import json
import math
import re
from pathlib import Path
from atlas_hunt import bounded_read,source_path,strict_json
from hound import sha


def assess(proposal:dict,evidence_root:Path)->dict:
    reasons=[]
    base={'release_approved':False,'global_novelty':'NOT_ASSESSED',
          'evidence_authenticity':'NOT_AUTHENTICATED','evidence_scope':'submitted benchmark only'}
    try:
        if not isinstance(proposal,dict):raise ValueError('PROPOSAL_MAPPING_REQUIRED')
        for key in ('problem','baseline_id','candidate_id'):
            if not isinstance(proposal.get(key),str) or not proposal[key].strip():raise ValueError('IDENTIFIED_PROBLEM_AND_BASELINE_REQUIRED')
        for key in ('suite_sha256','input_set_sha256'):
            if not isinstance(proposal.get(key),str) or re.fullmatch('[0-9a-f]{64}',proposal[key]) is None:raise ValueError('INVALID_EVIDENCE_DIGEST')
        if proposal.get('mode') not in {'better','different'}:raise ValueError('MODE_REQUIRED')
        if not proposal.get('problem') or not proposal.get('baseline_id') or not proposal.get('candidate_id'):raise ValueError('IDENTIFIED_PROBLEM_AND_BASELINE_REQUIRED')
        if proposal['baseline_id']==proposal['candidate_id']:raise ValueError('SAME_IMPLEMENTATION_ID')
        artifact=proposal['artifact'];raw=bounded_read(source_path(evidence_root,artifact['path']),8_000_000)
        if sha(raw)!=artifact['sha256']:raise ValueError('ARTIFACT_DIGEST_MISMATCH')
        evidence=strict_json(raw)
        if not isinstance(evidence,dict):raise ValueError('EVIDENCE_MAPPING_REQUIRED')
        for key in ('baseline_id','candidate_id','suite_sha256','input_set_sha256'):
            if not proposal.get(key) or evidence.get(key)!=proposal[key]:raise ValueError('EVIDENCE_BINDING_MISMATCH:'+key)
        if evidence.get('required_checks_passed') is not True or type(evidence.get('case_count')) is not int or evidence['case_count']<1:raise ValueError('REQUIRED_CHECKS_OR_CASES_MISSING')
        for key in ('tradeoffs','limitations'):
            rows=evidence.get(key)
            if not isinstance(rows,list) or not rows or not all(isinstance(x,str) and x.strip() for x in rows):
                raise ValueError('TRADEOFFS_AND_LIMITATIONS_REQUIRED')
        if type(evidence.get('required_regressions')) is not int or evidence['required_regressions']!=0:
            raise ValueError('REQUIRED_REGRESSION_OR_MISSING_EVIDENCE')
        if proposal['mode']=='better':
            metric=proposal['metric'];m=evidence['metrics'][metric]
            a,b=m['baseline'],m['candidate']
            if type(a) not in (int,float) or type(b) not in (int,float) or not math.isfinite(a) or not math.isfinite(b):raise ValueError('INVALID_METRICS')
            direction=proposal['direction']
            if direction not in {'higher','lower'}:raise ValueError('INVALID_DIRECTION')
            threshold=proposal['minimum_change']
            if type(threshold) not in (int,float) or not math.isfinite(threshold) or threshold<=0:raise ValueError('POSITIVE_PREDECLARED_THRESHOLD_REQUIRED')
            change=(b-a) if direction=='higher' else (a-b)
            if not math.isfinite(change):raise ValueError('NONFINITE_METRIC_CHANGE')
            if change<threshold:raise ValueError('NO_MEASURED_ADVANTAGE')
            # Required paired regressions are supplied by evaluator, not inferred from an average.
            if evidence.get('required_regressions',1)!=0:raise ValueError('REQUIRED_REGRESSION')
            return {**base,'status':'MEASURED_ADVANTAGE_REVIEW_REQUIRED','metric':metric,'observed_change':change,'minimum_change':threshold}
        req=proposal['requirement_id'];r=evidence['requirements'][req]
        if not isinstance(r,dict):raise ValueError('REQUIREMENT_MAPPING_REQUIRED')
        ids=r.get('acceptance_test_ids')
        if not isinstance(ids,list) or not ids or not all(isinstance(x,str) and x.strip() for x in ids) or len(set(ids))!=len(ids):
            raise ValueError('INVALID_ACCEPTANCE_TEST_IDS')
        if r.get('baseline_met') is not False or r.get('candidate_met') is not True or not r.get('acceptance_test_ids'):raise ValueError('NO_DEMONSTRATED_FUNCTIONAL_DIFFERENCE')
        return {**base,'status':'USEFUL_DIFFERENCE_REVIEW_REQUIRED','requirement_id':req}
    except (ValueError,KeyError,TypeError,AttributeError,OverflowError,OSError) as exc:
        reasons.append(str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
        return {**base,'status':'NOT_ELIGIBLE_FOR_ADOPTION_REVIEW','reasons':reasons}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('proposal',type=Path);p.add_argument('--evidence-root',type=Path,required=True)
    a=p.parse_args();r=assess(strict_json(bounded_read(a.proposal,1_000_000)),a.evidence_root)
    print(json.dumps(r,indent=2));return 0 if r['status'].endswith('_REVIEW_REQUIRED') else 2

if __name__=='__main__':raise SystemExit(main())
