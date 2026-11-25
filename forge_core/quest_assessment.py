"""Compare recorded product outputs with the exact build's data-only checks.

Artifacts and their bindings are verified; execution and the truth of externally
supplied observations are not authenticated. No supplied program is executed.
"""
import re

from .common import Blocked, canonical, loads, path_in_repo, read, safe_path, sha, text
from .reviewed_evaluator import preflight


MAX_CASES = 128
MAX_INPUTS_PER_CASE = 16
MAX_ARTIFACT_BYTES = 1_000_000
MAX_TOTAL_ARTIFACT_BYTES = 8_000_000
PASS = 'REPORTED_PASS'
FAIL = 'REPORTED_FAIL'
INCOMPLETE = 'REPORTED_INCOMPLETE'


def _fields(value, fields, code):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise Blocked(code)


def _digest(value, code):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise Blocked(code)


def _identifier(value):
    return isinstance(value, str) and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,79}', value)


def _same(left, right):
    # Python equality equates True with 1. The contract requires JSON types too.
    return canonical(left) == canonical(right)


def _artifact_reference(record, code):
    _fields(record, {'path', 'bytes', 'sha256'}, code)
    path = path_in_repo(record['path'])
    _digest(record['sha256'], code)
    if type(record['bytes']) is not int or not 0 <= record['bytes'] <= MAX_ARTIFACT_BYTES:
        raise Blocked(code)
    return path


def _responses(recipe):
    responses = recipe.get('need_responses', [])
    if not isinstance(responses, list) or len(responses) > 80:
        raise Blocked('QUEST_NEED_RESPONSES')
    needs = []
    for response in responses:
        _fields(response, {'need', 'outcome', 'components'}, 'QUEST_NEED_RESPONSES')
        if not _identifier(response['need']) or response['need'] in needs:
            raise Blocked('QUEST_NEED_RESPONSES')
        needs.append(response['need'])
    return needs


def validate_plan(recipe):
    """A plan may omit mappings, but assessment must keep those gaps explicit."""
    if 'evaluation_plan' not in recipe:
        return None
    plan = recipe['evaluation_plan']
    _fields(plan, {'schema', 'cases'}, 'QUEST_EVALUATION_PLAN')
    if (type(plan['schema']) is not int or plan['schema'] != 1
            or not isinstance(plan['cases'], list) or not 1 <= len(plan['cases']) <= MAX_CASES):
        raise Blocked('QUEST_EVALUATION_PLAN')
    addressed = set(_responses(recipe))
    seen = set()
    input_files = {}
    for case in plan['cases']:
        required = {'id', 'acceptance', 'needs', 'expected'}
        if (not isinstance(case, dict) or required - case.keys()
                or case.keys() - (required | {'inputs'})):
            raise Blocked('QUEST_EVALUATION_PLAN')
        if (not _identifier(case['id']) or case['id'] in seen
                or type(case['acceptance']) is not int
                or not 0 <= case['acceptance'] < len(recipe['acceptance'])
                or not isinstance(case['needs'], list) or len(case['needs']) > 80
                or not all(isinstance(need, str) for need in case['needs'])
                or len(set(case['needs'])) != len(case['needs'])
                or not set(case['needs']) <= addressed):
            raise Blocked('QUEST_EVALUATION_PLAN')
        if len(canonical(case['expected'])) > 64_000:
            raise Blocked('QUEST_EVALUATION_PLAN')
        if 'inputs' in case:
            inputs = case['inputs']
            if not isinstance(inputs, list) or not 1 <= len(inputs) <= MAX_INPUTS_PER_CASE:
                raise Blocked('QUEST_EVALUATION_INPUTS')
            paths = set()
            for record in inputs:
                path = _artifact_reference(record, 'QUEST_EVALUATION_INPUTS')
                if path in paths or (path in input_files and input_files[path] != record):
                    raise Blocked('QUEST_EVALUATION_INPUTS')
                paths.add(path)
                input_files[path] = record
            if sum(record['bytes'] for record in input_files.values()) > MAX_TOTAL_ARTIFACT_BYTES:
                raise Blocked('QUEST_EVALUATION_INPUTS')
        seen.add(case['id'])
    return plan


def _source_files(receipt, recipe):
    """Check internal provenance consistency; original-source custody is not authenticated."""
    expected = 'module_files' in recipe or 'assets' in recipe
    if ('source_files' in receipt) != expected:
        raise Blocked('QUEST_ASSESS_SOURCE_FILES')
    if not expected:
        return
    from .quest_sources import ASSET_LIMIT, _path, chain, check_paths
    records = receipt['source_files']
    if not isinstance(records, list) or not 1 <= len(records) <= 32 + ASSET_LIMIT:
        raise Blocked('QUEST_ASSESS_SOURCE_FILES')
    modules, assets = {}, {}
    try:
        for row in records:
            _fields(row, {'path', 'sha256', 'bytes', 'role', 'origin'}, 'QUEST_ASSESS_SOURCE_FILES')
            _path(row['path']); _digest(row['sha256'], 'QUEST_ASSESS_SOURCE_FILES')
            if (type(row['bytes']) is not int or not 0 <= row['bytes'] <= 8_000_000
                    or row['role'] not in ('module', 'resource', 'notice')):
                raise Blocked('QUEST_ASSESS_SOURCE_FILES')
            origin = row['origin']
            fields = {'file_id', 'container_path', 'container_sha256', 'member_chain'}
            if row['role'] == 'module':
                fields |= {'source_id', 'analysis_source_sha256'}
            _fields(origin, fields, 'QUEST_ASSESS_SOURCE_FILES')
            text(origin['file_id'], 200); _path(origin['container_path'])
            _digest(origin['container_sha256'], 'QUEST_ASSESS_SOURCE_FILES'); chain(origin['member_chain'])
            raw_digest = (origin['member_chain'][-1]['sha256'] if origin['member_chain']
                          else origin['container_sha256'])
            if row['sha256'] != raw_digest:
                raise Blocked('QUEST_ASSESS_SOURCE_FILES')
            if row['role'] == 'module':
                source_id = origin['source_id']
                if (not isinstance(source_id, str) or not re.fullmatch('source_[0-9a-f]{64}', source_id)
                        or source_id in modules):
                    raise Blocked('QUEST_ASSESS_SOURCE_FILES')
                _digest(origin['analysis_source_sha256'], 'QUEST_ASSESS_SOURCE_FILES')
                modules[source_id] = row
            else:
                assets[row['path']] = row
        check_paths([row['path'] for row in records] + ['__main__.py'],
                    [row['path'] for row in modules.values()] + ['__main__.py'])
    except Blocked as exc:
        raise Blocked('QUEST_ASSESS_SOURCE_FILES') from exc
    if len(modules) > 32 or sum(row['bytes'] for row in records) > 8_000_000:
        raise Blocked('QUEST_ASSESS_SOURCE_FILES')
    for mapping in recipe.get('module_files', []):
        if modules.get(mapping['source_id'], {}).get('path') != mapping['path']:
            raise Blocked('QUEST_ASSESS_SOURCE_FILES')
    if not set(recipe.get('support_modules', [])) <= {row['path'] for row in modules.values()}:
        raise Blocked('QUEST_ASSESS_SOURCE_FILES')
    described = sorted([{key: row[key] for key in ('path', 'sha256', 'bytes')}
                        for row in modules.values()], key=lambda row: row['path'])
    if not _same(receipt['modules'], described) or len(assets) != len(recipe.get('assets', [])):
        raise Blocked('QUEST_ASSESS_SOURCE_FILES')
    for reference in recipe.get('assets', []):
        row = assets.get(reference['path'])
        if row is None or not _same(reference, {**{key: row[key] for key in ('path', 'sha256', 'bytes', 'role')},
                                               **{key: row['origin'][key] for key in ('file_id', 'member_chain')}}):
            raise Blocked('QUEST_ASSESS_SOURCE_FILES')
    for definition in receipt['definitions']:
        source_id = definition.get('source_id')
        if not isinstance(source_id, str):
            raise Blocked('QUEST_ASSESS_SOURCE_FILES')
        module = modules.get(source_id)
        if module is None or definition.get('product_path') != module['path']:
            raise Blocked('QUEST_ASSESS_SOURCE_FILES')
        origin = module['origin']
        original_path = (origin['member_chain'][-1]['path'] if origin['member_chain'] else origin['container_path'])
        if definition.get('path') != original_path:
            raise Blocked('QUEST_ASSESS_SOURCE_FILES')
    selected = {row['source_id'] for row in recipe.get('module_files', [])}
    selected.update(row['source_id'] for row in receipt['definitions'])
    for source_id, row in modules.items():
        if source_id not in selected and row['path'] not in recipe.get('support_modules', []):
            raise Blocked('QUEST_ASSESS_SOURCE_FILES')


def _receipt(raw, recipe_raw, recipe):
    receipt = loads(raw)
    required = {'schema', 'status', 'mode', 'run_id', 'corpus_sha256', 'quest_sha256',
                'recipe_sha256', 'audience', 'purpose', 'acceptance', 'entry_sha256',
                'modules', 'definitions', 'product_sha256', 'product_bytes',
                'source_code_executed', 'runtime_validation', 'acceptance_file'}
    if (not isinstance(receipt, dict) or required - receipt.keys()
            or receipt.keys() - (required | {'purpose_evidence', 'evaluation_plan', 'source_files', 'preflight'})
            or type(receipt['schema']) is not int or receipt['schema'] != 1
            or receipt['status'] != 'PROTOTYPE_PACKAGED'
            or receipt['mode'] not in ('idea', 'scout', 'opportunity')
            or not isinstance(receipt['run_id'], str)
            or not re.fullmatch('[A-Za-z0-9_-]{1,80}', receipt['run_id'])
            or receipt['source_code_executed'] is not False or receipt['runtime_validation'] != 'NOT_RUN'):
        raise Blocked('QUEST_ASSESS_RECEIPT')
    for field in ('corpus_sha256', 'quest_sha256', 'recipe_sha256', 'entry_sha256', 'product_sha256'):
        _digest(receipt[field], 'QUEST_ASSESS_RECEIPT')
    if receipt['recipe_sha256'] != sha(recipe_raw):
        raise Blocked('QUEST_ASSESS_RECIPE_BINDING')
    for field in ('audience', 'purpose', 'acceptance'):
        if not _same(receipt[field], recipe[field]):
            raise Blocked('QUEST_ASSESS_RECIPE_BINDING')
    plan = validate_plan(recipe)
    if (('evaluation_plan' in receipt) != ('evaluation_plan' in recipe)
            or not _same(receipt.get('evaluation_plan'), plan)):
        raise Blocked('QUEST_ASSESS_RECIPE_BINDING')
    if type(receipt['product_bytes']) is not int or not 0 <= receipt['product_bytes'] <= 20_000_000:
        raise Blocked('QUEST_ASSESS_RECEIPT')
    if not isinstance(receipt['definitions'], list):
        raise Blocked('QUEST_ASSESS_RECEIPT')
    ids = [row.get('definition_id') for row in receipt['definitions'] if isinstance(row, dict)]
    if (len(ids) != len(receipt['definitions']) or not all(isinstance(i, str) for i in ids)
            or sorted(ids) != sorted(recipe['components'])):
        raise Blocked('QUEST_ASSESS_RECIPE_BINDING')
    _source_files(receipt, recipe)
    if 'preflight' in receipt:
        preflight = receipt['preflight']
        _fields(preflight, {'status', 'report_sha256', 'files', 'issues', 'omitted_issues',
                            'source_code_executed', 'runtime_validation'}, 'QUEST_ASSESS_RECEIPT')
        _digest(preflight['report_sha256'], 'QUEST_ASSESS_RECEIPT')
        if (not isinstance(preflight['status'], str)
                or preflight['status'] not in {'NO_ISSUES_OBSERVED', 'REVIEW_REQUIRED', 'INCOMPLETE'}
                or preflight['source_code_executed'] is not False or preflight['runtime_validation'] != 'NOT_RUN'
                or type(preflight['files']) is not int or preflight['files'] != len(receipt['modules']) + 1
                or type(preflight['issues']) is not int or not 0 <= preflight['issues'] <= 512
                or type(preflight['omitted_issues']) is not int or preflight['omitted_issues'] < 0
                or (preflight['status'] == 'NO_ISSUES_OBSERVED') != (preflight['issues'] == 0)
                or (preflight['omitted_issues'] and preflight['status'] != 'INCOMPLETE')):
            raise Blocked('QUEST_ASSESS_RECEIPT')
    addressed = _responses(recipe)
    if receipt['mode'] == 'opportunity':
        purpose = receipt.get('purpose_evidence')
        _fields(purpose, {'opportunity', 'need_responses', 'unaddressed_needs',
                          'outcome_status', 'market_validation'}, 'QUEST_ASSESS_RECEIPT')
        origin = purpose['opportunity']
        if (not isinstance(origin, dict) or not isinstance(origin.get('hypotheses'), list)
                or not isinstance(origin.get('workflow_id'), str) or not isinstance(origin.get('run_id'), str)
                or not _same(purpose['need_responses'], recipe.get('need_responses'))
                or not _same(recipe.get('opportunity'), {k: origin[k] for k in ('run_id', 'workflow_id')})
                or purpose['outcome_status'] != 'BUILDER_PROPOSED_NOT_EVALUATED'
                or purpose['market_validation'] != 'NOT_ESTABLISHED'):
            raise Blocked('QUEST_ASSESS_RECIPE_BINDING')
        needs = []
        for hypothesis in origin['hypotheses']:
            if (not isinstance(hypothesis, dict) or not isinstance(hypothesis.get('need'), dict)
                    or not _identifier(hypothesis['need'].get('id'))):
                raise Blocked('QUEST_ASSESS_RECEIPT')
            needs.append(hypothesis['need']['id'])
        if (len(set(needs)) != len(needs) or not set(addressed) <= set(needs)
                or not _same(purpose['unaddressed_needs'], sorted(set(needs) - set(addressed)))):
            raise Blocked('QUEST_ASSESS_RECIPE_BINDING')
    elif 'purpose_evidence' in receipt or 'need_responses' in recipe or 'opportunity' in recipe:
        raise Blocked('QUEST_ASSESS_RECIPE_BINDING')
    return receipt, plan, addressed


def _artifact(record, root, files):
    path = _artifact_reference(record, 'QUEST_ASSESS_ARTIFACT')
    full = safe_path(root / path)
    if not full.is_relative_to(root) or not full.is_file():
        raise Blocked('QUEST_ASSESS_ARTIFACT')
    data = read(full, MAX_ARTIFACT_BYTES)
    if len(data) != record['bytes'] or sha(data) != record['sha256']:
        raise Blocked('QUEST_ASSESS_ARTIFACT')
    name = 'artifacts/' + path
    if name in files and files[name] != data:
        raise Blocked('QUEST_ASSESS_ARTIFACT')
    files[name] = data
    if sum(len(value) for key, value in files.items() if key.startswith('artifacts/')) > MAX_TOTAL_ARTIFACT_BYTES:
        raise Blocked('QUEST_ASSESS_ARTIFACT')
    return data


def _aggregate(statuses):
    if FAIL in statuses:
        return FAIL
    if not statuses or INCOMPLETE in statuses:
        return INCOMPLETE
    return PASS


def assess(workbench, receipt_file, recipe_file, product_file, results_file, artifact_root, out, check_file=None):
    """Publish a separate assessment, preserving all supplied result artifact bytes."""
    from .creation_quest import _publish, _recipe
    workbench.guard()
    out = preflight(workbench.home, out)['out']
    root = safe_path(artifact_root)
    inputs = [safe_path(path) for path in (receipt_file, recipe_file, product_file, results_file)]
    if check_file is not None:
        inputs.append(safe_path(check_file))
    if (not root.is_dir() or out.is_relative_to(root) or root.is_relative_to(out)
            or any(out == path or out.is_relative_to(path) or path.is_relative_to(out) for path in inputs)):
        raise Blocked('QUEST_ASSESS_SEPARATE_OUTPUT')
    receipt_raw = read(receipt_file, 8_000_000)
    recipe_raw = read(recipe_file, 256_000)
    recipe = _recipe(recipe_raw)
    receipt, plan, addressed = _receipt(receipt_raw, recipe_raw, recipe)
    product = read(product_file, 20_000_000)
    if len(product) != receipt['product_bytes'] or sha(product) != receipt['product_sha256']:
        raise Blocked('QUEST_ASSESS_PRODUCT_BINDING')
    files = {'build-receipt.json': receipt_raw, 'recipe.json': recipe_raw}
    checker = receipt['acceptance_file']
    checker_digest = None
    if checker is None:
        if check_file is not None:
            raise Blocked('QUEST_ASSESS_CHECKER_BINDING')
    else:
        _fields(checker, {'sha256', 'bytes'}, 'QUEST_ASSESS_CHECKER_BINDING')
        _digest(checker['sha256'], 'QUEST_ASSESS_CHECKER_BINDING')
        if (check_file is None or type(checker['bytes']) is not int
                or not 0 <= checker['bytes'] <= 4_000_000):
            raise Blocked('QUEST_ASSESS_CHECKER_BINDING')
        check = read(check_file, 4_000_000)
        if len(check) != checker['bytes'] or sha(check) != checker['sha256']:
            raise Blocked('QUEST_ASSESS_CHECKER_BINDING')
        checker_digest = checker['sha256']
        files['check-file'] = check
    results_raw = read(results_file, 1_000_000)
    results = loads(results_raw)
    _fields(results, {'schema', 'receipt_sha256', 'product_sha256', 'evaluation_plan_sha256',
                      'checker_sha256', 'cases'}, 'QUEST_ASSESS_RESULTS')
    bindings = {'receipt_sha256': sha(receipt_raw), 'product_sha256': receipt['product_sha256'],
                'evaluation_plan_sha256': sha(canonical(plan)) if plan is not None else None,
                'checker_sha256': checker_digest}
    if (type(results['schema']) is not int or results['schema'] != 1
            or any(not _same(results[key], value) for key, value in bindings.items())):
        raise Blocked('QUEST_ASSESS_RESULT_BINDING')
    if not isinstance(results['cases'], list) or len(results['cases']) > MAX_CASES:
        raise Blocked('QUEST_ASSESS_RESULTS')
    cases = plan['cases'] if plan is not None else []
    for case in cases:
        for record in case.get('inputs', []):
            _artifact(record, root, files)
    known = {case['id'] for case in cases}
    recorded = {}
    for row in results['cases']:
        _fields(row, {'id', 'status', 'artifact'}, 'QUEST_ASSESS_RESULTS')
        if (not isinstance(row['id'], str) or row['id'] not in known or row['id'] in recorded
                or row['status'] not in ('OBSERVED', 'NOT_RUN', 'ERROR')):
            raise Blocked('QUEST_ASSESS_RESULTS')
        if ((row['status'] == 'OBSERVED' and row['artifact'] is None)
                or (row['status'] == 'NOT_RUN' and row['artifact'] is not None)):
            raise Blocked('QUEST_ASSESS_RESULTS')
        artifact = _artifact(row['artifact'], root, files) if row['artifact'] is not None else None
        recorded[row['id']] = (row, loads(artifact) if row['status'] == 'OBSERVED' else None)
    case_results = []
    for case in cases:
        item = {**case, 'status': INCOMPLETE, 'reported_state': 'MISSING', 'artifact': None,
                'input_status': 'VERIFIED_DECLARED_INPUT_BYTES' if 'inputs' in case else 'NOT_DECLARED'}
        if case['id'] in recorded:
            row, observed = recorded[case['id']]
            item.update({'reported_state': row['status'], 'artifact': row['artifact']})
            if row['status'] == 'OBSERVED':
                item.update({'observed': observed, 'status': PASS if _same(observed, case['expected']) else FAIL})
        case_results.append(item)
    acceptance_results = []
    for index, description in enumerate(recipe['acceptance']):
        selected = [case for case in case_results if case['acceptance'] == index]
        acceptance_results.append({'acceptance': index, 'description': description,
                                   'cases': [case['id'] for case in selected],
                                   'status': _aggregate([case['status'] for case in selected])})
    need_results = []
    for need in addressed:
        selected = [case for case in case_results if need in case['needs']]
        need_results.append({'need': need, 'cases': [case['id'] for case in selected],
                             'status': _aggregate([case['status'] for case in selected])})
    status = _aggregate([row['status'] for row in case_results + acceptance_results + need_results])
    unaddressed = receipt.get('purpose_evidence', {}).get('unaddressed_needs', [])
    decision = ('REVISE' if status == FAIL else 'COMPLETE_EVALUATION' if status == INCOMPLETE else
                'REVIEW_UNADDRESSED_NEEDS' if unaddressed else 'READY_FOR_FIELD_TRIAL')
    assessment = {
        'schema': 1, 'status': 'QUEST_ASSESSED', 'outcome_status': status, 'next_step': decision,
        **bindings, 'recipe_sha256': sha(recipe_raw), 'results_sha256': sha(results_raw),
        'product_bytes': len(product), 'audience': receipt['audience'], 'purpose': receipt['purpose'],
        'evaluation_plan': plan, 'case_results': case_results, 'acceptance_results': acceptance_results,
        'need_results': need_results, 'unaddressed_needs': unaddressed,
        'source_code_executed': False, 'runtime_execution_authenticated': False,
        'execution_input_usage_authenticated': False,
        'market_validation': 'NOT_ESTABLISHED', 'release_approved': False,
        'scope': 'Exact supplied bytes and JSON comparisons verified. Declared input files are checked '
                 'when present; their use by the product is not authenticated. Observations, execution, '
                 'translation and chronological preregistration are externally supplied, not authenticated.',
    }
    if 'purpose_evidence' in receipt:
        assessment['purpose_evidence'] = receipt['purpose_evidence']
    files['recorded-results.json'] = results_raw
    files['assessment.json'] = canonical(assessment)
    _publish(out, files)
    return {'status': assessment['status'], 'outcome_status': status, 'next_step': decision,
            'assessment': str(out / 'assessment.json'), 'assessment_sha256': sha(files['assessment.json'])}
