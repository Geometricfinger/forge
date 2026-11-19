"""Compare the citations of two saved Opportunity Lab runs, without reranking either."""
from functools import lru_cache

from .common import Blocked, sha


def _index(rows):
    by_id = {row['id']: row for row in rows}
    if len(by_id) != len(rows):
        raise Blocked('OPPORTUNITY_COMPARE_AMBIGUOUS_ID')
    return by_id


def _steps_for_need(workflow, target_id):
    """Include prior declared producers of prerequisites, up to the initial inputs."""
    steps = workflow['steps']
    target = next(i for i, step in enumerate(steps) if step['id'] == target_id)
    initial = set(workflow['initial_fields'])
    required = set(steps[target]['requires']) - initial
    selected = [steps[target]]
    for step in reversed(steps[:target]):
        if required.intersection(step['produces']):
            selected.append(step)
            required.update(set(step['requires']) - initial)
    return selected


def _scope(hypothesis, workflow):
    need = hypothesis['need']
    citations = {('need', need['id']): need['evidence']}
    annotations = {('need', need['id']): (need['step'], need['pattern'], need['why'], need['basis'])}
    inferred = all(n['id'] != need['id'] for n in workflow['needs'])
    for step in _steps_for_need(workflow, need['step']):
        slot = ('step', step['id'])
        annotations[slot] = (step['action'], sorted(step['requires']), sorted(step['produces']))
        # An inferred need copies its step's citations. Report those references once.
        if step['id'] != need['step'] or not inferred:
            citations[slot] = step['evidence']
    for observation in hypothesis['evidence']['observations']:
        slot = ('observation', observation['id'])
        citations[slot] = observation['evidence']
        annotations[slot] = (observation['kind'], observation['scope'], observation['statement'])
    for option in hypothesis['mechanisms']:
        slot = ('mechanism', option['id'])
        citations[slot] = option['evidence']
        annotations[slot] = {k: v for k, v in option.items() if k != 'evidence'}
    return citations, annotations


def _at(citation):
    if citation is None:
        return None
    return {'start': citation['start'], 'end': citation['end'],
            'quote_sha256': sha(citation['quote'].encode('utf-8'))}


def _location(citation):
    return citation['source'], citation['start'], citation['end'], citation['quote']


def _citation_changes(previous, current, old_sources, new_sources, registers, occurrences):
    old_register, new_register = registers
    changes = []
    provenance = ('kind', 'origin_group', 'derived_from', 'capture_method', 'observed_on', 'public', 'fresh')

    for role, owner in sorted(previous.keys() | current.keys()):
        prior_refs = previous.get((role, owner), [])
        new_refs = current.get((role, owner), [])
        old_locations = {_location(c) for c in prior_refs}
        unmatched = {_location(c): c for c in new_refs}
        # Reserve exact locations so an earlier repeated quote cannot take another's match.
        moved_candidates = [c for c in new_refs if _location(c) not in old_locations]

        def record(old, new, status):
            sid = (old or new)['source']
            a, b = old_register.get(sid), new_register.get(sid)
            same_source = a is not None and b is not None and a['uri'] == b['uri']
            changes.append({'role': role, 'owner': owner, 'source': sid, 'status': status,
                            'before': _at(old), 'after': _at(new),
                            'source_text_changed': same_source and a['content_sha256'] != b['content_sha256'],
                            'source_metadata_changed': same_source and any(a.get(k) != b.get(k) for k in provenance)})

        for old in prior_refs:
            sid, quote = old['source'], old['quote']
            a, b = old_sources[sid], new_sources.get(sid)
            new = None
            if b is None:
                status = 'SOURCE_REMOVED'
            elif a['uri'] != b['uri']:
                status = 'SOURCE_REPLACED'
            elif _location(old) in unmatched:
                new = unmatched.pop(_location(old))
                status = 'RETAINED'
            elif occurrences('after', sid, quote) == 0:
                status = 'PASSAGE_MISSING'
            else:
                matches = [c for c in moved_candidates if c['source'] == sid and c['quote'] == quote
                           and _location(c) in unmatched]
                if not matches:
                    status = 'NO_LONGER_CITED'
                elif occurrences('before', sid, quote) > 1 or occurrences('after', sid, quote) > 1:
                    status = 'OCCURRENCE_UNCERTAIN'
                else:
                    new = unmatched.pop(_location(matches[0]))
                    status = 'MOVED'
            record(old, new, status)
        for new in unmatched.values():
            record(None, new, 'NEW_CITATION')
    return changes


def compare(before, after):
    old, new = before['report'], after['report']
    old_sources, new_sources = (_index(record['bundle']['sources']) for record in (before, after))
    registers = (_index(old['source_register']), _index(new['source_register']))
    old_rows, new_rows = _index(old['hypotheses']), _index(new['hypotheses'])
    old_workflows, new_workflows = (_index(record['bundle']['workflows']) for record in (before, after))

    @lru_cache(maxsize=2048)
    def occurrences(side, source, quote):
        text = (old_sources if side == 'before' else new_sources)[source]['text']
        first = text.find(quote)
        if first < 0:
            return 0
        return 2 if text.find(quote, first + 1) >= 0 else 1

    rows = []
    summary = dict(total=0, review_required=0, retained=0, added=0, removed=0)
    for hid in sorted(old_rows.keys() | new_rows.keys()):
        a, b = old_rows.get(hid), new_rows.get(hid)
        reasons, citations = set(), []
        if a is None:
            status, key = 'ADDED', 'added'
        elif b is None:
            status, key = 'REMOVED', 'removed'
        else:
            previous, old_annotations = _scope(a, old_workflows[a['workflow']])
            current, new_annotations = _scope(b, new_workflows[b['workflow']])
            citations = _citation_changes(previous, current, old_sources, new_sources, registers, occurrences)
            reasons.update(c['status'] for c in citations if c['status'] not in {'RETAINED', 'MOVED'})
            if any(c['source_metadata_changed'] for c in citations):
                reasons.add('SOURCE_METADATA_CHANGED')
            if a['state'] != b['state']:
                reasons.add('EVIDENCE_STATE_CHANGED')
            if (old_annotations != new_annotations or a['workflow_context'] != b['workflow_context']
                    or a['concept'] != b['concept']
                    or a.get('workflow_readiness') != b.get('workflow_readiness')):
                reasons.add('DECLARED_WORKFLOW_OR_INTERPRETATION_CHANGED')
            status, key = ('REVIEW_REQUIRED', 'review_required') if reasons else ('CITATIONS_RETAINED', 'retained')
        summary['total'] += 1
        summary[key] += 1
        rows.append({'id': hid, 'title': (b or a)['concept']['title'], 'status': status,
                     'reasons': sorted(reasons), 'citations': citations})
    return {'schema': 1, 'before': old['id'], 'after': new['id'], 'summary': summary, 'hypotheses': rows,
            'scope': 'Compared cited wording, source identity and reviewed declarations. '
                     'Surrounding text can change meaning even when a citation is retained.',
            'entailment_verified': False}
