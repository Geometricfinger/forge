"""Shared data-only verification of retained planner and handoff executions.

The caller must supply the independently retained pre-execution binding hash.
This verifies consistency of recorded bytes, not authenticated execution.
"""
from __future__ import annotations

import argparse
import csv
from io import BytesIO
import io
import hashlib
import itertools
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
from zipfile import ZipFile

try:
    from . import boundary_harness
    from .build import CORE_SHA256, SPEC_SHA256
except ImportError:
    import boundary_harness
    from build import CORE_SHA256, SPEC_SHA256


MAX_RECORD_BYTES = 1_000_000
MAX_PRODUCT_BYTES = 8_000_000
HARNESS_SHA256 = 'c8ac79a864e950c8a0444a2f0a1043f1baaef8a4bfb45054ce5be07d965f6047'
BASELINE_FREEZE = '99a3cb6fe718ee06d9fe4f9b187d07a2a78f495cb8351fbb0ab5a89537112187'
PLAN_FREEZE = 'bcd9050048370171b092b519d12ccccaef5d97915104582db7153e6fed2de1ce'
HANDOFF_FREEZE = '79e5965dcfe58dbc88cbbce689cc3360d13525901e61047a27d4e1a04b697a46'
FIELDS = ('case_id', 'status', 'minimum_cost', 'minimum_question_count',
          'optimal_question_sets', 'optimal_plan_count', 'omitted_optimal_plan_count',
          'indistinguishable_pairs')
ROUTES = ('manual_model', 'export_reconciliation', 'export_plus_planner')
STAGES = ('acquisition', 'interpretation', 'model_preparation', 'follow_up')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result

    def constant(_):
        raise ValueError('Nonfinite JSON number')

    require(len(raw) <= MAX_RECORD_BYTES, 'JSON size bound')
    try:
        value = json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=unique,
                           parse_constant=constant)
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            require(depth <= 32, 'JSON depth bound')
            if type(item) is str:
                item.encode('utf-8', 'strict')
            elif type(item) is float:
                require(math.isfinite(item), 'Nonfinite JSON float')
            elif type(item) is dict:
                pending.extend((x, depth + 1) for pair in item.items() for x in pair)
            elif type(item) is list:
                pending.extend((x, depth + 1) for x in item)
    except (UnicodeError, RecursionError, OverflowError) as exc:
        raise ValueError('Invalid bounded JSON') from exc
    return value


def regular(path, maximum=MAX_RECORD_BYTES):
    path = Path(path)
    require(not any(p.is_symlink() for p in [path, *path.parents]), 'Symlink artifact')
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and 0 <= info.st_size <= maximum,
            'Non-regular or oversized artifact')
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    require(len(raw) == info.st_size and len(raw) <= maximum, 'Artifact changed or oversized')
    return raw


def relative(value):
    require(type(value) is str and value and len(value) <= 500 and '\\' not in value
            and ':' not in value, 'Invalid relative artifact path')
    path = PurePosixPath(value)
    require(not path.is_absolute() and '..' not in path.parts and '.' not in path.parts
            and str(path) == value, 'Escaping or noncanonical artifact path')
    return Path(*path.parts)


def pinned(root, reference, maximum=MAX_RECORD_BYTES):
    require(type(reference) is dict and set(reference) == {'path', 'bytes', 'sha256'},
            'Artifact reference shape')
    require(type(reference['bytes']) is int and 0 <= reference['bytes'] <= maximum,
            'Artifact reference size')
    require(type(reference['sha256']) is str and re.fullmatch('[0-9a-f]{64}', reference['sha256']),
            'Artifact reference hash')
    raw = regular(Path(root) / relative(reference['path']), maximum)
    require(len(raw) == reference['bytes'] and digest(raw) == reference['sha256'],
            'Artifact identity mismatch: ' + reference['path'])
    return raw


def file_state(raw):
    try:
        raw.decode('utf-8', 'strict')
        utf8 = True
    except UnicodeError:
        utf8 = False
    return {'kind': 'REGULAR', 'bytes': len(raw), 'sha256': digest(raw), 'utf8': utf8}


def equal(actual, expected, message):
    if type(actual) is bytes or type(expected) is bytes:
        require(type(actual) is bytes and type(expected) is bytes and actual == expected, message)
    else:
        require(canonical(actual) == canonical(expected), message)


def subset(actual, expected, path='$'):
    require(type(actual) is type(expected), 'Semantic type mismatch at ' + path)
    if type(expected) is dict:
        for key, value in expected.items():
            require(key in actual, 'Missing semantic field at ' + path + '.' + key)
            subset(actual[key], value, path + '.' + key)
    elif type(expected) is list:
        require(len(actual) == len(expected), 'Semantic list length at ' + path)
        for index, (got, want) in enumerate(zip(actual, expected)):
            subset(got, want, path + '[' + str(index) + ']')
    else:
        equal(actual, expected, 'Semantic value mismatch at ' + path)


def aggregate(events):
    unknown = sum(event['wall_time_ns'] is None for event in events)
    known = sum(event['wall_time_ns'] for event in events if event['wall_time_ns'] is not None)
    observed_complete = not unknown and all(event['measurement'] == 'observed' for event in events)
    return {'known_wall_time_ns': known, 'wall_time_ns': None if unknown else known,
            'complete': unknown == 0, 'step_count': sum(event['step_count'] for event in events),
            'unknown_event_count': unknown,
            'adapter_measured_wall_time_ns': sum(event['wall_time_ns'] or 0 for event in events
                if event['actor'] == 'adapter' and event['measurement'] == 'observed'),
            'observed_human_wall_time_ns': sum(event['wall_time_ns'] or 0 for event in events
                if event['actor'] == 'human' and event['measurement'] == 'observed'),
            'reported_wall_time_ns': sum(event['wall_time_ns'] or 0 for event in events
                if event['measurement'] == 'reported'),
            'synthetic_wall_time_ns': sum(event['wall_time_ns'] or 0 for event in events
                if event['measurement'] == 'synthetic'),
            'observed_complete': observed_complete,
            'measurement_class': 'incomplete' if unknown else ('measured' if observed_complete else 'mixed_declared')}


def verify_burden(ledger, plan, supplied, elapsed_seconds):
    equal(ledger['schema'], 1, 'Ledger schema changed')
    equal(ledger['timing_authenticated'], False, 'Unsupported timing authentication claim')
    equal(ledger['case_id'], plan['case_id'], 'Ledger case identity changed')
    events = ledger['events']
    require(type(events) is list and len(events) <= 100, 'Burden event bound')
    ids = set()
    original = {event['id']: event for event in supplied['burden_events']}
    export_routes = ('export_reconciliation', 'export_plus_planner')
    runtime_contract = {
        'runtime_validate': ('model_preparation', ROUTES),
        'runtime_read_artifacts': ('acquisition', export_routes),
        'runtime_extract': ('interpretation', export_routes),
        'runtime_manual_plan': ('model_preparation', ('manual_model',)),
        'runtime_export_model': ('model_preparation', export_routes),
    }
    if plan['model_input'] is not None:
        runtime_contract['runtime_export_plan'] = ('model_preparation', ('export_plus_planner',))
    if supplied['follow_up']:
        runtime_contract['runtime_apply_followup'] = ('follow_up', ('export_plus_planner',))
    runtime_seen = set()
    runtime_ns = 0
    for event in events:
        require(type(event) is dict and type(event.get('id')) is str
                and event['id'] not in ids, 'Burden event identity')
        ids.add(event['id'])
        require(event['stage'] in STAGES and event['actor'] in ('human', 'adapter')
                and event['measurement'] in ('observed', 'reported', 'synthetic', 'unknown'),
                'Burden event classification')
        duration = event['wall_time_ns']
        require((duration is None and event['measurement'] == 'unknown') or
                (type(duration) is int and 0 <= duration <= 10**18 and event['measurement'] != 'unknown'),
                'Burden duration')
        require(type(event['step_count']) is int and 0 <= event['step_count'] <= 1_000_000,
                'Burden step count')
        require(type(event['source']) is str and len(event['source']) <= 2000, 'Burden source')
        allocation = event.get('routes', list(ROUTES))
        require(type(allocation) is list and allocation and len(set(allocation)) == len(allocation)
                and set(allocation) <= set(ROUTES), 'Burden route allocation')
        if event['id'] in original:
            equal(event, original[event['id']], 'Supplied burden event changed')
        elif event['actor'] == 'adapter':
            require(event['measurement'] == 'observed' and duration is not None,
                    'Runtime adapter event origin')
            require(event['id'] in runtime_contract, 'Unexpected runtime adapter event')
            stage, routes = runtime_contract[event['id']]
            require(event['stage'] == stage and event['step_count'] == 1,
                    'Runtime adapter event stage or step count changed')
            equal(event.get('routes'), list(routes), 'Runtime adapter route allocation changed')
            runtime_seen.add(event['id'])
            runtime_ns += duration
        else:
            require(event['measurement'] == 'unknown' and duration is None
                    and event['step_count'] == 0, 'Invented human event')
    require(set(original) <= ids, 'Missing supplied burden event')
    require(runtime_seen == set(runtime_contract), 'Missing required runtime adapter event')
    require(runtime_ns <= int(elapsed_seconds * 1_000_000_000),
            'Adapter phase time exceeds supervised execution')
    require(all(any(event['actor'] == 'human' and event['stage'] == stage for event in events)
                for stage in STAGES[:3]), 'Missing necessary human measurement coverage')
    subset(ledger['totals'], aggregate(events), '$.burden.totals')
    for stage in STAGES:
        subset(ledger['phases'][stage], aggregate([event for event in events if event['stage'] == stage]),
               '$.burden.phases.' + stage)
    subset(ledger['phases']['unresolved'], aggregate([]), '$.burden.phases.unresolved')
    equal(ledger['phases']['unresolved']['cases_left_without_decision'], ledger['unresolved'],
          'Unresolved phase disagrees with route outcomes')
    for route in ROUTES:
        chosen = [event for event in events if route in event.get('routes', ROUTES)]
        require(all(any(event['actor'] == 'human' and event['stage'] == stage for event in chosen)
                    for stage in STAGES[:3]), 'Missing necessary human stage for a route')
        route_value = ledger['routes'][route]
        totals = route_value.get('totals', route_value)
        subset(totals, aggregate(chosen), '$.burden.routes.' + route)
        if 'phases' in route_value:
            for stage in STAGES:
                subset(route_value['phases'][stage], aggregate([e for e in chosen if e['stage'] == stage]),
                       '$.burden.routes.' + route + '.' + stage)
        require(ledger['unresolved'][route] == plan['comparison'][route]['unresolved_count']
                and type(ledger['unresolved'][route]) is int and ledger['unresolved'][route] in (0, 1),
                'Unresolved ledger disagreement')
    follow = ledger['follow_up']
    observed = plan['comparison']['export_plus_planner']
    for name in ('recorded_questions_asked', 'synthetic_questions_asked'):
        equal(follow[name], observed[name], 'Actual follow-up accounting disagrees')
    require(follow['recorded_questions_asked'] + follow['synthetic_questions_asked'] == len(supplied['follow_up']),
            'Planned or missing questions counted as asked')
    planner = plan.get('planner_result')
    plans = planner.get('plans_to_review', []) if planner else []
    planned = len(plans[0]) if plans else 0
    equal(follow['planned_question_count'], planned, 'Planned-question ledger disagreement')


def verify_extraction(plan, supplied, work, source_fields):
    refs = {ref['id']: ref for ref in supplied['raw_artifacts']}
    raw = {key: regular(work / relative(ref['path'])) for key, ref in refs.items()}
    csv_fields = {row['label']: row['field'] for row in source_fields['medit_csv_items']}
    native_fields = set(source_fields['shape_native_categories'])
    expected = {}
    ignored, notes = [], []
    native_declared = False
    scan_only = bool(refs) and all(ref['kind'] in ('scan_stl', 'scan_ply', 'reviewer_note')
                                   for ref in refs.values())

    def evidence(ref, locator, value, **extra):
        return {'artifact_id': ref['id'], 'sha256': ref['sha256'], 'bytes': ref['bytes'],
                'locator': locator, 'value': value, 'basis': ref['provenance'], **extra}

    # Reconstruct the declared extraction evidence once per source. Inspecting
    # only the proofs present in an output misses omitted fields/observations.
    # This reads data only; no retained product or vendor code is executed.
    for ref in supplied['raw_artifacts']:
        if supplied['source'] == 'medit_export':
            if ref['kind'] in ('case_information_pdf', 'exocad_project_manifest'):
                notes.append({'artifact_id': ref['id'], 'reason': 'OPAQUE_LAYOUT_NOT_DOCUMENTED'})
            if ref['kind'] != 'medit_csv':
                continue
            rows = list(csv.reader(io.StringIO(raw[ref['id']].decode('utf-8-sig'), newline=''), strict=True))
            require(rows and len(rows) <= 1001 and rows[0] and len(rows[0]) <= 32
                    and len(set(rows[0])) == len(rows[0])
                    and all(len(row) == len(rows[0]) for row in rows[1:]), 'Invalid retained CSV profile')
            headers = rows[0]
            ignored.extend({'artifact_id': ref['id'], 'column': label, 'reason': 'UNDOCUMENTED_FIELD'}
                           for label in headers if label not in csv_fields)
            if 'Case Name' not in headers:
                notes.append({'artifact_id': ref['id'], 'reason': 'CASE_SELECTOR_UNAVAILABLE'})
                continue
            selected = [index for index in range(1, len(rows))
                        if rows[index][headers.index('Case Name')] == ref['selection']['case_name']]
            if not selected:
                notes.append({'artifact_id': ref['id'], 'reason': 'NO_MATCHING_CASE_ROW'})
            for index in selected:
                for label, value in zip(headers, rows[index]):
                    if label in csv_fields and value.strip():
                        expected.setdefault(csv_fields[label], []).append(
                            evidence(ref, 'row:' + str(index + 1) + '/column:' + label, value))
        elif supplied['source'] == '3shape_native' and ref['kind'] == 'native_package_manifest':
            manifest = strict_json(raw[ref['id']])
            require(manifest['case_id'] == supplied['case_id'], 'Native manifest case changed')
            package = refs[manifest['package_artifact']]
            if manifest['declared_format'] in ('stl', 'ply') or package['kind'] in ('scan_stl', 'scan_ply'):
                scan_only = True
                notes.append({'artifact_id': ref['id'], 'reason': 'SCAN_ONLY_NOT_NATIVE'})
                continue
            require(manifest['declared_format'] == '3oxz' and package['kind'] == 'opaque_native',
                    'Unsupported native package declaration')
            native_declared = True
            for observation in manifest['observations']:
                category = observation['category']
                note = refs[observation['artifact_id']]
                text = raw[note['id']].decode('utf-8', 'strict')
                start, end, quote = (observation[key] for key in ('start', 'end', 'quote'))
                require(note['kind'] == 'reviewer_note' and type(start) is int and type(end) is int
                        and 0 <= start < end <= len(text) and text[start:end] == quote,
                        'Native quotation is not bound to a reviewer note')
                if category not in native_fields:
                    ignored.append({'artifact_id': ref['id'], 'field': category, 'reason': 'UNDOCUMENTED_FIELD'})
                    continue
                synthetic = (manifest['provenance'] == package['provenance'] == note['provenance'] == 'synthetic'
                             and quote == 'Synthetic reviewer observation: ' + category + ' content is present.')
                if synthetic:
                    present, profile = True, 'synthetic_presence_statement_v1'
                else:
                    claim = strict_json(quote.encode('utf-8'))
                    require(type(claim) is dict and set(claim) == {'category', 'value'}
                            and claim['category'] == category and claim['value'] in ('present', 'absent', 'unknown'),
                            'Unsupported native category observation')
                    present, profile = claim['value'] == 'present', 'reviewed_category_json_v1'
                if present:
                    expected.setdefault(category, []).append(evidence(note, 'characters:' + str(start) + '-' + str(end),
                        'present', manifest_id=ref['id'], manifest_sha256=ref['sha256'], observation_profile=profile))
                else:
                    notes.append({'artifact_id': ref['id'], 'field': category, 'reason': 'CATEGORY_NOT_KNOWN_PRESENT'})
    if supplied['source'] == '3shape_native' and scan_only and not native_declared:
        notes.append({'reason': 'SCAN_ONLY_NOT_NATIVE'})
    needed = {field for history in supplied['reviewer_decisions'] for field in history['conditions']}
    needed.update(csv_fields.values() if supplied['source'] == 'medit_export'
                  else native_fields if supplied['source'] == '3shape_native' else [])
    require(type(plan['extraction']['fields']) is dict, 'Extraction field map')
    equal(sorted(plan['extraction']['fields']), sorted(needed | set(expected)), 'Extraction field inventory changed')
    for key in ('native_package_decoded', 'vendor_format_verified'):
        equal(plan['extraction'][key], False, 'Unsupported vendor interpretation claim')
    equal(plan['extraction']['native_package_declared'], native_declared, 'Native declaration changed')
    route_kind = ('medit_documented_csv_profile' if supplied['source'] == 'medit_export' else
                  'manual_supplied_model' if supplied['source'] == 'manual' else
                  'reviewed_native_manifest' if native_declared else 'scan_only' if scan_only else 'native_unverified')
    equal(plan['extraction']['route_kind'], route_kind, 'Extraction route changed')
    for key, wanted in (('ignored_fields', ignored), ('notes', notes)):
        require(type(plan['extraction'][key]) is list, 'Extraction ' + key + ' must be a list')
        equal(sorted(plan['extraction'][key], key=canonical), sorted(wanted, key=canonical),
              'Extraction ' + key + ' changed')
    for field_name, field in plan['extraction']['fields'].items():
        require(field['status'] in ('KNOWN', 'UNKNOWN', 'CONFLICT'), 'Extraction field status')
        proofs = field['provenance']
        require(type(proofs) is list and len(proofs) <= 32_000
                and all(type(proof) is dict for proof in proofs), 'Extraction provenance bound or shape')
        wanted = expected.get(field_name, [])
        values = sorted({proof['value'] for proof in wanted})
        equal(field['status'], 'UNKNOWN' if not values else 'KNOWN' if len(values) == 1 else 'CONFLICT',
              'Extraction state disagrees with complete source observations')
        # Proof order and JSON representation are not identities. Preserve
        # multiplicity and every semantic locator while allowing reordering.
        actual_proofs = [{key: value for key, value in proof.items() if key != 'interpretation'} for proof in proofs]
        equal(sorted(actual_proofs, key=canonical), sorted(wanted, key=canonical),
              'Extraction observations omitted, invented or changed')
        equal(field['value'], values[0] if len(values) == 1 else None, 'Extraction value contradicts sources')
        if len(values) > 1:
            equal(values, field['values'], 'Conflict field values contradict sources')


def verify_planner_diagnostics(result, case):
    """Check derivable diagnostics; frozen independent labels check optima."""
    worlds = sorted(case['worlds'], key=lambda world: world['id'])
    questions = sorted((question for question in case['questions'] if question['available']),
                       key=lambda question: question['id'])
    pairs = [(a, b) for a, b in itertools.combinations(worlds, 2) if a['decision'] != b['decision']]
    groups = {decision: [world['id'] for world in worlds if world['decision'] == decision]
              for decision in sorted({world['decision'] for world in worlds})}
    coverage = {question['id']: [index for index, (a, b) in enumerate(pairs)
                if question['id'] in a['answers'] and question['id'] in b['answers']
                and a['answers'][question['id']] != b['answers'][question['id']]] for question in questions}
    covered = {index for values in coverage.values() for index in values}
    all_cost = sum(question['cost'] for question in questions)
    equal(result['decision_groups'], groups, 'Planner decision groups changed')
    equal(result['cross_decision_pairs'], [[a['id'], b['id']] for a, b in pairs], 'Planner decision pairs changed')
    equal(result['question_pair_coverage'], coverage, 'Planner coverage changed')
    equal(result['ask_all_baseline'], {'questions': [question['id'] for question in questions],
        'cost': all_cost, 'question_count': len(questions), 'distinguishes_decisions': len(covered) == len(pairs),
        'within_budget': all_cost <= case['max_cost']}, 'Planner ask-all comparison changed')
    equal(result['subsets_examined'], 0 if len(covered) != len(pairs) else 1 << len(questions),
          'Planner subset count changed')
    equal(result['plans_to_review'], [] if result['status'] == 'BUDGET_BLOCKED' else result['optimal_question_sets'],
          'Planner recommendation differs from retained optima')
    for key, value in (('model_complete_in_reality', False), ('evidence_authenticated', False),
                       ('questions_sent', 0), ('search_complete_for_declared_model', True)):
        equal(result[key], value, 'Unsupported planner claim: ' + key)


def verify_plan(plan, supplied):
    for key, value in (('schema', 1), ('version', 'handoff-bench-1'), ('evidence_authenticated', False),
                       ('model_complete_in_reality', False), ('questions_sent', 0)):
        equal(plan[key], value, 'Unsupported handoff claim: ' + key)
    equal(plan['question_sources'], {question['id']: question['source'] for question in supplied['questions']},
          'Question source declarations changed')
    def model(histories):
        return {'schema': 1, 'cases': [{'schema': 1, 'case_id': supplied['case_id'],
            'worlds': [{key: history[key] for key in ('id', 'decision', 'answers')} for history in histories],
            'questions': [{key: question[key] for key in ('id', 'label', 'cost', 'available')}
                          for question in supplied['questions']], 'max_cost': supplied['budget']}]}
    manual = model(supplied['reviewer_decisions'])
    equal(plan['manual_model_input'], manual, 'Original supplied model changed')
    verify_planner_diagnostics(plan['manual_planner_result'], manual['cases'][0])
    fields = plan['extraction']['fields']
    conflict = any(field['status'] == 'CONFLICT' for field in fields.values())
    retained, eliminated = [], []
    if not conflict:
        for history in supplied['reviewer_decisions']:
            mismatches = sorted(field for field, value in history['conditions'].items()
                if fields[field]['status'] == 'KNOWN' and fields[field]['value'] != value)
            if mismatches:
                eliminated.append({'history_id': history['id'], 'fields': mismatches})
            else:
                retained.append(history)
    status = 'EXPORT_EVIDENCE_CONFLICT' if conflict else 'CONSISTENT' if retained else 'EXPORT_MODEL_CONFLICT'
    equal(plan['model_status'], status, 'Export model status changed')
    equal(plan['eliminated_histories'], eliminated, 'History elimination evidence changed')
    if status == 'CONSISTENT':
        expected = model(retained)
        equal(plan['model_input'], expected, 'Export-derived model changed')
        verify_planner_diagnostics(plan['planner_result'], expected['cases'][0])
    else:
        equal(plan['model_input'], None, 'Conflicting evidence fabricated a model')
        equal(plan['planner_result'], None, 'Conflicting evidence fabricated a plan')


def legacy_expected(root):
    baseline = root / 'frozen/inputs/manual-baseline'
    raw = regular(baseline / 'freeze.json')
    require(digest(raw) == BASELINE_FREEZE, 'Original independent baseline changed')
    freeze = strict_json(raw)
    for row in freeze['files']:
        pinned(baseline, row)
    raw = regular(baseline / 'evaluation-plan-freeze.json')
    require(digest(raw) == PLAN_FREEZE, 'Original evaluation-plan freeze changed')
    plan_freeze = strict_json(raw)
    require(plan_freeze['original_freeze_sha256'] == BASELINE_FREEZE, 'Original plan binding changed')
    for row in plan_freeze['files']:
        pinned(baseline, row)
    refs = {row['path']: row for row in plan_freeze['input_refs']}
    require(set(refs) == {'inputs/input-batch.json', 'inputs/SPEC.md'}, 'Original spec inventory changed')
    for original, relative_path in [('inputs/input-batch.json', 'frozen/inputs/manual-baseline/input-batch.json'),
                                    ('inputs/SPEC.md', 'frozen/SPEC.md')]:
        ref = dict(refs[original], path=relative_path)
        pinned(root, ref)
    require(digest(regular(root / 'frozen/SPEC.md')) == SPEC_SHA256, 'Original specification changed')
    manual = strict_json(regular(baseline / 'expected.json'))
    rows = []
    for item in manual['cases']:
        row = {name: item[name] for name in FIELDS}
        if row['status'] == 'PLAN_FOUND':
            row['status'] = 'QUESTIONS_FOR_REVIEW'
        rows.append(row)
    plan = strict_json(regular(baseline / 'evaluation-plan.json'))
    equal([case['expected'] for case in plan['cases']], rows, 'Original labels and plan disagree')
    return rows, manual


def quality(plan, expected):
    truth = expected.get('synthetic_truth')
    result = {}
    for route in ROUTES:
        observed = plan['comparison'][route]
        status = 'UNKNOWN'
        if truth and observed['status'] == 'RESOLVED':
            status = 'CORRECT' if (observed['decision'] == truth['decision']
                and truth['world_id'] in observed['candidate_ids']) else 'INCORRECT'
        result[route] = {'status': status, 'basis': 'frozen synthetic expectation',
                         'truth_available': bool(truth)}
    return result


def _verify(binding_path, run, trusted_freeze_sha256):
    require(type(trusted_freeze_sha256) is str and re.fullmatch('[0-9a-f]{64}', trusted_freeze_sha256),
            'Caller-trusted pre-execution freeze hash required')
    binding_path, run = Path(binding_path).absolute(), Path(run).absolute()
    root = binding_path.parent.parent
    raw = regular(binding_path)
    require(digest(raw) == trusted_freeze_sha256, 'Trusted execution binding changed')
    binding = strict_json(raw)
    require(binding['schema'] == 1, 'Binding schema')
    inventory = binding['files']
    require(len({row['path'] for row in inventory}) == len(inventory), 'Duplicate frozen inventory')
    for ref in inventory:
        pinned(root, ref, MAX_PRODUCT_BYTES if ref['path'].endswith('product.pyz') else MAX_RECORD_BYTES)
    product = pinned(root, binding['product'], MAX_PRODUCT_BYTES)
    with ZipFile(BytesIO(product)) as archive:
        require(len(archive.namelist()) == len(set(archive.namelist())), 'Duplicate product member')
        require(digest(archive.read('planner_core.py')) == CORE_SHA256, 'Original optimizer changed')
        for ref in binding['members']:
            member = archive.getinfo(ref['path'])
            require(member.file_size == ref['bytes'] and member.file_size <= MAX_PRODUCT_BYTES,
                    'Product member size changed')
            member_raw = archive.read(ref['path'])
            require(digest(member_raw) == ref['sha256'], 'Product module identity changed')
        require(set(archive.namelist()) == {ref['path'] for ref in binding['members']}, 'Product member inventory changed')
    harness = pinned(root, binding['harness'])
    require(digest(harness) == HARNESS_SHA256, 'Reviewed supervisor changed')
    profile_raw = pinned(root, binding['profile'])
    profile = strict_json(profile_raw)
    source_fields = strict_json(regular(root / 'frozen/SOURCE_FIELDS.json'))
    expected_legacy, manual = legacy_expected(root)
    handoff_expected = strict_json(pinned(root, binding['handoff_expected']))
    handoff_root = root / 'frozen/inputs/handoff'
    handoff_freeze_raw = regular(handoff_root / 'freeze.json')
    require(digest(handoff_freeze_raw) == HANDOFF_FREEZE, 'Independent handoff fixture freeze changed')
    for reference in strict_json(handoff_freeze_raw)['files']:
        pinned(handoff_root, reference)
    expectation_by_id = {case['case_id']: case for case in handoff_expected['cases']}
    require(len(expectation_by_id) == 5 and len(handoff_expected['cases']) == 5, 'Handoff fixture inventory')
    record = strict_json(regular(run / 'run.json'))
    require(record['product_sha256'] == digest(product) and record['product_bytes'] == len(product),
            'Recorded product identity changed')
    equal(pinned(run, record['profile']), profile_raw, 'Recorded profile differs from frozen profile')
    equal(pinned(run, record['harness']), harness, 'Recorded supervisor differs from frozen supervisor')
    cases = profile['cases']
    ids = [case['id'] for case in cases]
    require(ids == ['legacy-24', *expectation_by_id], 'Profile case inventory changed')
    equal([row['id'] for row in record['results']], ids, 'Incomplete or changed run inventory')
    results = []
    failures = []
    legacy_summary = None
    for case, row in zip(cases, record['results']):
        case_id = case['id']
        execution = strict_json(pinned(run, row['execution']))
        observation = strict_json(pinned(run, row['observation']))
        output_raw = pinned(run, execution['stdout'])
        stderr = pinned(run, execution['stderr'])
        require(len(output_raw) <= profile['limits']['combined_stream_bytes'], 'Output size exceeds frozen limit')
        require(execution['termination'] == 'EXITED' and execution['exit_code'] == 0
                and not execution['streams_truncated'] and not stderr, 'Unsuccessful or incomplete execution')
        require(execution['case_id'] == case_id and execution['profile_sha256'] == digest(profile_raw)
                and execution['harness_sha256'] == digest(harness), 'Execution identity mismatch')
        equal(execution['limits'], profile['limits'], 'Recorded resource limits changed')
        arguments = case['arguments']
        command, launcher = execution['product_command'], execution['launcher_command']
        require(len(command) == 5 + len(arguments) and command[1:4] == ['-I', '-S', '-B']
                and command[5:] == arguments and Path(command[4]).name == 'product.pyz'
                and len(launcher) == 8 + len(arguments) and launcher[1:4] == ['-I', '-S', '-B']
                and launcher[0] == command[0] and Path(launcher[4]).name == 'harness.py'
                and launcher[5] == '_launch' and launcher[8:] == arguments
                and Path(launcher[7]).name == 'product.pyz', 'Recorded command changed')
        equal(strict_json(launcher[6].encode()), profile['limits'], 'Launcher resource limits changed')
        elapsed = execution['elapsed_seconds']
        require(type(elapsed) in (float, int) and math.isfinite(elapsed)
                and 0 <= elapsed <= profile['limits']['wall_seconds'] + 1, 'Invalid execution elapsed time')
        folder = run / 'cases' / case_id
        retained_product = regular(folder / 'product.pyz', MAX_PRODUCT_BYTES)
        equal(retained_product, product, 'Executed archive differs from trusted archive')
        product_state = file_state(product)
        equal(execution['product_before'], product_state, 'Recorded pre-execution product contradicts bytes')
        equal(execution['product_after'], product_state, 'Recorded post-execution product contradicts bytes')
        input_states = {}
        for item in case['files']:
            source_raw = pinned(root / 'frozen', item['artifact'])
            copied_raw = regular(folder / 'work' / relative(item['path']))
            equal(copied_raw, source_raw, 'Raw execution input changed')
            input_states[item['path']] = file_state(copied_raw)
        equal(execution['inputs_before'], input_states, 'Recorded pre-execution inputs contradict bytes')
        after_states = dict(input_states)
        for name in case['exports']:
            after_states[name] = file_state(regular(folder / 'work' / relative(name)))
        equal(execution['files_after'], after_states, 'Recorded post-execution file state contradicts bytes')
        equal(execution['stream_bytes_seen_at_least'], {'stdout': len(output_raw), 'stderr': 0},
              'Recorded streams contradict retained bytes')
        observed = boundary_harness.observe(case, output_raw, execution, input_states, after_states, True)
        equal(observation, observed, 'Observation differs from raw execution')
        equal(observation, case['expected'], 'Frozen transport expectation not met')
        equal(row['expected'], case['expected'], 'Recorded transport expectation changed')
        equal(row['observed'], observed, 'Recorded transport observation changed')
        require(row['passed'] is True, 'Recorded transport failure')
        output = strict_json(output_raw)
        if case_id == 'legacy-24':
            input_raw = regular(folder / 'work/input-batch.json')
            legacy_input = strict_json(input_raw)
            require(output['input_sha256'] == digest(input_raw), 'Legacy input hash mismatch')
            require(output['version'] == 'evidence-recovery-0.2', 'Original planner version claim changed')
            actual_rows = [{name: value[name] for name in FIELDS} for value in output['cases']]
            require(len(actual_rows) == 24 and len({x['case_id'] for x in actual_rows}) == 24,
                    'Original semantic case inventory')
            mismatches = [want['case_id'] for got, want in zip(actual_rows, expected_legacy)
                          if canonical(got) != canonical(want)]
            for value, independent in zip(output['cases'], manual['cases']):
                naive = independent['ask_all_available_baseline']
                equal(value['ask_all_baseline'], {'questions': naive['question_ids'],
                    'cost': naive['declared_cost'], 'question_count': naive['question_count'],
                    'distinguishes_decisions': naive['distinguishes_all_cross_decision_pairs'],
                    'within_budget': naive['within_budget']}, 'Original ask-all baseline disagreement')
            for value, model in zip(output['cases'], legacy_input['cases']):
                verify_planner_diagnostics(value, model)
            legacy_summary = {'exact_cases': 24 - len(mismatches), 'total_cases': 24,
                              'fields_per_case': len(FIELDS), 'mismatches': mismatches}
            failures.extend({'case_id': name, 'reason': 'Legacy semantic disagreement'} for name in mismatches)
        else:
            require(set(output) == {'plan', 'burden'}, 'Handoff stdout shape')
            plan, ledger = output['plan'], output['burden']
            equal(plan, strict_json(regular(folder / 'work/result/plan.json')), 'Plan sidecar differs from stdout')
            equal(ledger, strict_json(regular(folder / 'work/result/burden.json')), 'Burden sidecar differs from stdout')
            supplied = strict_json(regular(folder / 'work' / arguments[1]))
            require(plan['input_sha256'] == digest(regular(folder / 'work' / arguments[1]))
                    and plan['planner_core_sha256'] == CORE_SHA256,
                    'Plan input or original-core claim changed')
            require(plan['case_id'] == supplied['case_id'] and plan['source'] == supplied['source'],
                    'Plan case identity changed')
            equal(plan['raw_artifacts'], supplied['raw_artifacts'], 'Plan raw-artifact identity changed')
            for claim in ('native_package_decoded', 'vendor_format_verified'):
                if claim in plan['extraction']:
                    require(plan['extraction'][claim] is False, 'Unsupported vendor interpretation claim')
            verify_extraction(plan, supplied, folder / 'work', source_fields)
            verify_plan(plan, supplied)
            receipt = strict_json(regular(folder / 'work/result/receipt.json'))
            require(set(receipt) == {'schema', 'input_sha256', 'planner_core_sha256', 'files', 'stdout'}
                    and type(receipt['schema']) is int and receipt['schema'] == 1,
                    'Handoff receipt shape')
            require(receipt['input_sha256'] == digest(regular(folder / 'work' / arguments[1]))
                    and receipt['planner_core_sha256'] == CORE_SHA256, 'Handoff receipt input or core mismatch')
            equal(receipt['stdout'], {'bytes': len(output_raw), 'sha256': digest(output_raw)},
                  'Handoff receipt stdout mismatch')
            require([ref['path'] for ref in receipt['files']] == ['plan.json', 'burden.json'],
                    'Handoff receipt sidecar inventory')
            for ref in receipt['files']:
                pinned(folder / 'work/result', ref)
            verify_burden(ledger, plan, supplied, elapsed)
            expected = expectation_by_id[case_id]
            passed = True
            try:
                subset(output, expected['expected'])
            except ValueError as exc:
                passed = False
                failures.append({'case_id': case_id, 'reason': str(exc)})
            results.append({'id': case_id, 'plan': plan, 'burden': ledger,
                            'checks': {'passed': passed, 'ledger_consistent': True,
                                       'raw_artifacts_consistent': True, 'sidecars_consistent': True},
                            'decision_quality': quality(plan, expected),
                            'execution': {'elapsed_seconds': elapsed, 'usage': execution['usage']}})
    require(legacy_summary is not None, 'Missing original benchmark')
    require(record['counts'] == {'passed': len(cases), 'total': len(cases)}, 'Run counts disagree')
    for flag in ('original_product_preserved', 'profile_source_preserved',
                 'harness_source_preserved', 'fixture_sources_preserved'):
        require(record[flag] is True, 'Recorded source preservation failure')
    source_states = {item['artifact']['path']: file_state(pinned(root / 'frozen', item['artifact']))
                     for case in cases for item in case['files']}
    equal(record['source_fixture_after'], source_states, 'Recorded fixture-source identities disagree')
    return {'schema': 1, 'status': 'PASS' if not failures else 'FAIL', 'legacy': legacy_summary,
            'cases': results, 'failures': failures, 'product_sha256': digest(product),
            'freeze_sha256': trusted_freeze_sha256, 'harness_sha256': HARNESS_SHA256,
            'profile_sha256': digest(profile_raw), 'core_sha256': CORE_SHA256,
            'limits': profile['limits'],
            'limitations': ['Synthetic interchange fixtures, not authenticated vendor exports.',
                'Unknown human time remains unknown; synthetic time is not measured human savings.',
                'Recorded decisions are compared only with independent frozen synthetic truth.',
                'Local hash consistency does not authenticate execution, chronology or input consumption.',
                'Resource supervision on trusted Linux only; filesystem/network isolation is not provided.']}


def verify_run(binding_path, execution_dir, *, trusted_freeze_sha256):
    try:
        return _verify(binding_path, execution_dir, trusted_freeze_sha256)
    except (KeyError, TypeError, IndexError, OSError, OverflowError) as exc:
        raise ValueError('Malformed or missing retained execution evidence') from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binding', required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--trusted-freeze-sha256', required=True)
    parser.add_argument('--out')
    args = parser.parse_args()
    try:
        result = verify_run(args.binding, args.run, trusted_freeze_sha256=args.trusted_freeze_sha256)
        raw = canonical(result) + b'\n'
        require(len(raw) <= MAX_RECORD_BYTES, 'Saved benchmark size limit')
        if args.out:
            with Path(args.out).open('xb') as stream:
                stream.write(raw)
        print(json.dumps({'status': result['status'], 'legacy': result['legacy'],
                          'handoff_cases': len(result['cases']), 'failures': result['failures']}, indent=2))
        return 0 if result['status'] == 'PASS' else 1
    except ValueError as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
