"""Compose bounded, reviewable routes through declared mechanism contracts.

This is monotone field planning over supplied annotations. It does not invent
missing bridges, infer product capabilities, execute a mechanism, or establish
market novelty. All ordering and selection policies are explicit below.
"""
from collections import deque
import copy

from .common import canonical, sha


MAX_STEPS = 4
MAX_STATES = 4096
MAX_OPTIONS = 8
ROLES = {'existing_option', 'source_component', 'new_work', 'manual_step', 'unclassified'}
CONDITION_STATES = {'supported', 'incompatible', 'unknown'}
GATED_STATES = {'EXISTING_OPTION_TO_VERIFY', 'CONFLICT_REQUIRES_REVIEW',
                'COUNTEREVIDENCE_REVIEW', 'STALE_EVIDENCE_RECHECK'}
AUTHORITY = {'execution_authorized': False, 'implementation_tested': False,
             'reuse_approved': False, 'market_validated': False,
             'novelty_established': False, 'release_approved': False}


def _conditions(mechanism, required):
    declared = {row['condition']: row for row in mechanism.get('conditions', [])}
    return [copy.deepcopy(declared.get(condition, {
        'condition': condition, 'status': 'unknown', 'evidence': []
    })) for condition in required]


def _evidence_refs(citations):
    """Bind retained catalog citations without repeating their quote bodies."""
    return [{**{key: row[key] for key in ('source', 'start', 'end')},
             'quote_sha256': sha(row['quote'].encode('utf-8'))} for row in citations]


def _relevant(mechanisms, initial, target):
    """All possible declared predecessors, including other pattern families."""
    wanted = {target} - initial
    selected = {}
    while True:
        added = [m for m in mechanisms if m['id'] not in selected
                 and wanted.intersection(m['provides'])]
        if not added:
            return sorted(selected.values(), key=lambda m: m['id'])
        for mechanism in added:
            selected[mechanism['id']] = mechanism
            wanted.update(set(mechanism['requires']) - initial)


def _closure(mechanisms, initial):
    available = set(initial)
    while True:
        before = len(available)
        for mechanism in mechanisms:
            if set(mechanism['requires']) <= available:
                available.update(mechanism['provides'])
        if len(available) == before:
            return available


def _cycles(mechanisms, reachable):
    """Unseeded dependency groups, not unique or sufficient repair diagnoses."""
    blocked = {m['id']: m for m in mechanisms
               if not set(m['requires']) <= reachable}
    graph = {mid: sorted(other for other, producer in blocked.items()
                         if (set(mechanism['requires']) - reachable)
                         .intersection(producer['provides']))
             for mid, mechanism in blocked.items()}
    index = 0
    indexes, low, stack, active, groups = {}, {}, [], set(), []

    def visit(node):
        nonlocal index
        indexes[node] = low[node] = index
        index += 1
        stack.append(node)
        active.add(node)
        for other in graph[node]:
            if other not in indexes:
                visit(other)
                low[node] = min(low[node], low[other])
            elif other in active:
                low[node] = min(low[node], indexes[other])
        if low[node] == indexes[node]:
            group = []
            while True:
                other = stack.pop()
                active.remove(other)
                group.append(other)
                if other == node:
                    break
            if len(group) > 1 or node in graph[node]:
                groups.append(sorted(group))

    for node in sorted(graph):
        if node not in indexes:
            visit(node)
    return sorted(groups)


def _classification(roles):
    if 'new_work' in roles:
        return 'NEW_WORK_REQUIRED'
    if 'unclassified' in roles:
        return 'UNCLASSIFIED_REVIEW_REQUIRED'
    if all(role == 'manual_step' for role in roles):
        return 'MANUAL_PROCESS'
    if len(roles) == 1 and roles[0] == 'existing_option':
        return 'REUSE_EXISTING_OPTION'
    if len(roles) == 1 and roles[0] == 'source_component':
        return 'REUSE_SOURCE_COMPONENT'
    return 'COMBINE_DECLARED_MECHANISMS'


def _option(hypothesis, path, mechanisms, initial, required):
    bindings = {field: {'origin': 'workflow', 'mechanism': None} for field in initial}
    steps, unconfirmed, unimplemented, roles = [], [], [], []
    for mid in path:
        mechanism = mechanisms[mid]
        role = mechanism.get('role', 'unclassified')
        roles.append(role)
        conditions = _conditions(mechanism, required)
        unconfirmed.extend({'mechanism': mid, 'condition': row['condition']}
                           for row in conditions if row['status'] == 'unknown')
        if role == 'new_work':
            unimplemented.append(mid)
        condition_checks = [{**row, 'evidence': _evidence_refs(row['evidence'])}
                            for row in conditions]
        steps.append({
            'mechanism': mid, 'title': mechanism['title'], 'role': role,
            'description': mechanism['mechanism'],
            'requires': sorted(mechanism['requires']),
            'provides': sorted(mechanism['provides']),
            'input_bindings': [{'field': field, **bindings[field]}
                               for field in sorted(mechanism['requires'])],
            'condition_checks': condition_checks,
            'evidence': _evidence_refs(mechanism['evidence']),
            'limits': copy.deepcopy(mechanism['limits']),
            'code_queries': copy.deepcopy(mechanism['code_queries']),
        })
        for field in mechanism['provides']:
            bindings.setdefault(field, {'origin': 'mechanism', 'mechanism': mid})
    option = {
        'classification': _classification(roles),
        'mechanism_ids': sorted(path), 'steps': steps,
        'condition_status': 'UNCONFIRMED' if unconfirmed else 'DECLARED_SUPPORTED',
        'unconfirmed_conditions': unconfirmed,
        'unimplemented_mechanisms': unimplemented,
        'implementation_tested': False,
    }
    return {'id': 'option_' + sha(canonical([hypothesis['id'], option]))[:32], **option}


def _order(option):
    """Review order, never a usefulness, novelty or success score."""
    return (bool(option['unconfirmed_conditions']),
            len(option['unimplemented_mechanisms']),
            option['classification'] == 'UNCLASSIFIED_REVIEW_REQUIRED',
            len(option['steps']), tuple(option['mechanism_ids']))


def _shortlist(options):
    """Keep distinct mechanism sets and one representative of each role class."""
    ordered = sorted(options, key=_order)
    chosen, classes = [], set()
    for option in ordered:
        if option['classification'] not in classes:
            chosen.append(option)
            classes.add(option['classification'])
            if len(chosen) == MAX_OPTIONS:
                break
    identities = {option['id'] for option in chosen}
    for option in ordered:
        if len(chosen) == MAX_OPTIONS:
            break
        if option['id'] not in identities:
            chosen.append(option)
            identities.add(option['id'])
    return sorted(chosen, key=_order)


def synthesize(hypothesis, mechanisms):
    """Return inclusion-minimal routes found within fixed, reported bounds.

    The caller validates the reviewed mechanism catalog. Mechanisms have only
    positive input/output declarations; no destructive effects, type conversion,
    synonym matching or undeclared adapters are inferred.
    """
    initial = set(hypothesis['workflow_readiness']['available_before'])
    target = hypothesis['need']['field']
    required = sorted(hypothesis['workflow_context'].get('required_conditions', []))
    result = {
        'schema': 1, 'status': 'NO_DECLARED_ROUTE', 'target_field': target,
        'next_decision': 'OBTAIN_MISSING_INFORMATION_OR_REVISE',
        'required_conditions': required, 'options': [],
        'unresolved': {'missing_inputs_without_provider': [],
                       'unreachable_mechanisms': [], 'cyclic_dependency_groups': [],
                       'incompatible_mechanisms': []},
        'coverage': {
            'mechanisms_supplied': len(mechanisms), 'relevant_mechanisms': 0,
            'relevant_mechanism_ids': [], 'eligible_mechanism_ids': [],
            'eligible_mechanisms': 0, 'max_steps': MAX_STEPS,
            'max_states': MAX_STATES, 'max_options': MAX_OPTIONS,
            'search_performed': False, 'states_retained': 0, 'states_expanded': 0,
            'transitions_omitted_by_state_budget': 0,
            'transitions_omitted_by_depth': 0,
            'equivalent_orders_collapsed': 0, 'redundant_complete_sets_omitted': 0,
            'minimal_plans_found': 0, 'options_retained': 0, 'options_omitted': 0,
            'state_budget_exhausted': False, 'depth_limited': False,
            'search_complete_within_depth': None,
            'declared_reachable_without_depth_limit': None,
            'selection_policy': 'DISTINCT_MECHANISM_SETS_WITH_ROLE_CLASS_COVERAGE',
        },
        'authority': copy.deepcopy(AUTHORITY),
        'limitations': [
            'Plans combine reviewed declarations; they do not execute mechanisms or infer capability truth.',
            'Exact field names are required. Missing adapters and distinguishing signals are not invented.',
            'Operating conditions and roles are attributed annotations, not verified properties or reuse permission.',
            'Step evidence references resolve to exact retained citations in the shared mechanism catalog.',
            'Inclusion minimality concerns mechanism sets for this one declared output, not effort, cost or market value.',
            'Search is bounded. Omitted work and options do not establish that other solutions are absent.',
        ],
    }
    if hypothesis['state'] in GATED_STATES:
        result['status'] = ('EXISTING_OPTION_REVIEW_REQUIRED'
                            if hypothesis['state'] == 'EXISTING_OPTION_TO_VERIFY'
                            else 'EVIDENCE_REVIEW_REQUIRED')
        result['next_decision'] = ('VERIFY_EXISTING_OPTION' if hypothesis['state'] == 'EXISTING_OPTION_TO_VERIFY'
                                   else 'RESOLVE_EVIDENCE_BEFORE_BUILDING')
        return result
    if target in initial:
        result['status'] = 'NO_NEW_WORK_DECLARED_OUTPUT_AVAILABLE'
        result['next_decision'] = 'NO_NEW_BUILD_FROM_DECLARED_MAP'
        result['coverage']['declared_reachable_without_depth_limit'] = True
        return result

    relevant = _relevant(mechanisms, initial, target)
    coverage = result['coverage']
    coverage['relevant_mechanisms'] = len(relevant)
    coverage['relevant_mechanism_ids'] = [mechanism['id'] for mechanism in relevant]
    incompatible = []
    for mechanism in relevant:
        conflicts = [row for row in _conditions(mechanism, required)
                     if row['status'] == 'incompatible']
        if conflicts:
            incompatible.append({'mechanism': mechanism['id'], 'conditions': [
                {**row, 'evidence': _evidence_refs(row['evidence'])} for row in conflicts]})
    result['unresolved']['incompatible_mechanisms'] = incompatible
    excluded = {row['mechanism'] for row in incompatible}
    eligible = _relevant([m for m in relevant if m['id'] not in excluded], initial, target)
    coverage['eligible_mechanisms'] = len(eligible)
    coverage['eligible_mechanism_ids'] = [mechanism['id'] for mechanism in eligible]
    reachable = _closure(eligible, initial)
    coverage['declared_reachable_without_depth_limit'] = target in reachable
    produced = set().union(*(set(m['provides']) for m in eligible)) if eligible else set()
    needed = {target}.union(*(set(m['requires']) for m in eligible))
    result['unresolved']['missing_inputs_without_provider'] = sorted(needed - initial - produced)
    result['unresolved']['unreachable_mechanisms'] = [
        {'mechanism': m['id'], 'missing_inputs': sorted(set(m['requires']) - reachable)}
        for m in eligible if not set(m['requires']) <= reachable
    ]
    result['unresolved']['cyclic_dependency_groups'] = _cycles(eligible, reachable)
    if target not in reachable:
        # The monotone closure is complete for these declarations. No sequence
        # search is needed; this is not a statement about external alternatives.
        return result

    by_id = {m['id']: m for m in eligible}
    queue = deque([((), frozenset())])
    seen = {frozenset()}
    complete = []
    coverage['search_performed'] = True
    while queue:
        path, extra = queue.popleft()
        coverage['states_expanded'] += 1
        selected = frozenset(path)
        available = initial | extra
        for mechanism in eligible:
            mid = mechanism['id']
            if mid in selected or not set(mechanism['requires']) <= available:
                continue
            additions = set(mechanism['provides']) - available
            if not additions:
                continue
            if len(path) == MAX_STEPS:
                coverage['transitions_omitted_by_depth'] += 1
                continue
            identity = selected | {mid}
            if identity in seen:
                coverage['equivalent_orders_collapsed'] += 1
                continue
            if len(seen) == MAX_STATES:
                coverage['transitions_omitted_by_state_budget'] += 1
                continue
            seen.add(identity)
            next_path = (*path, mid)
            if target in additions:
                if any(frozenset(prior) <= identity for prior in complete):
                    coverage['redundant_complete_sets_omitted'] += 1
                else:
                    complete.append(next_path)
            else:
                queue.append((next_path, extra | additions))

    options = [_option(hypothesis, path, by_id, initial, required) for path in complete]
    result['options'] = _shortlist(options)
    coverage.update({
        'states_retained': len(seen), 'minimal_plans_found': len(options),
        'options_retained': len(result['options']),
        'options_omitted': len(options) - len(result['options']),
        'state_budget_exhausted': bool(coverage['transitions_omitted_by_state_budget']),
        'depth_limited': bool(coverage['transitions_omitted_by_depth']),
        'search_complete_within_depth': not coverage['transitions_omitted_by_state_budget'],
    })
    result['status'] = ('BUILD_OPTIONS_FOR_REVIEW' if result['options']
                        else 'NO_PLAN_FOUND_WITHIN_BOUNDS')
    if result['options']:
        existing = any(option['condition_status'] == 'DECLARED_SUPPORTED'
                       and all(step['role'] == 'existing_option' for step in option['steps'])
                       for option in options)
        result['next_decision'] = ('VERIFY_EXISTING_ROUTE_BEFORE_NEW_WORK' if existing
                                   else 'COMPARE_CANDIDATE_ROUTES')
    return result
