"""Build a local Python zipapp from reviewed components in a retained corpus."""
from collections import defaultdict
from io import BytesIO
from pathlib import Path, PurePosixPath
import keyword
import re
import tempfile
from zipfile import ZipFile, ZipInfo

from . import corpus, opportunities
from .common import Blocked, canonical, loads, path_in_repo, read, safe_path, sha, text, write
from .mixed_intake import DEFAULTS
from .reviewed_evaluator import preflight


LEAD_LIMIT = 40
COMPONENT_LIMIT = 32
APP_BYTES = 8_000_000


def _plain_module(path):
    file = PurePosixPath(path)
    names = [*file.parts[:-1], file.stem]
    return (file.suffix == '.py' and file.parts[0] not in ('__main__.py', '__main__')
            and all(part.isidentifier() and not keyword.iskeyword(part) for part in names))


def _eligible(row):
    return (not row['test_path'] and row['format'] == 'python_file'
            and PurePosixPath(row['path']).suffix == '.py')


def _source_lead(row):
    return {key: row[key] for key in ('source_id', 'file_id', 'container', 'member_chain')}


def _quest(report, digest, run_id, idea):
    if idea is None:
        # Round-robin across modules, so one large file cannot take every slot.
        groups = defaultdict(list)
        for row in corpus.rows(report):
            if _eligible(row):
                groups[(row['container'], row['path'], row['source_id'])].append(row)
        paths = sorted(groups)
        selected = []
        for i in range(max(map(len, groups.values()), default=0)):
            for path in paths:
                if i < len(groups[path]):
                    selected.append(groups[path][i])
                    if len(selected) == LEAD_LIMIT:
                        break
            if len(selected) == LEAD_LIMIT:
                break
        basis = 'Round-robin sample from whole Python modules in path order.'
    else:
        search = corpus.search(report, idea, limit=100, filters={'record_kind': 'function'})
        selected = [row for row in search['results'] if _eligible(row)][:LEAD_LIMIT]
        basis = 'Ranked corpus search; up to the first 100 hits are considered for whole-module reuse.'
    leads = []
    for row in selected:
        leads.append({'name': row['name'], 'definition_id': row['definition_id'], 'path': row['path'],
                      **_source_lead(row),
                      'start_line': row['start_line'], 'end_line': row['end_line'],
                      'parameters': row['parameters'][:16], 'documented_terms': row['documented_terms'][:24],
                      'matched_terms': row.get('matched_query_terms', [])})
    return {'schema': 1, 'run_id': run_id, 'corpus_sha256': digest,
            'coverage_status': report['status'], 'mode': 'scout' if idea is None else 'idea',
            'idea': idea, 'lead_basis': basis, 'leads': leads,
            'next_step': 'Inspect promising definitions and their complete modules. Choose a user, purpose '
                         'and observable acceptance checks. Fill the recipe and write a Python entry point '
                         'that calls the selected components. Use quest-build to package it, then run an '
                         'independent acceptance check on the resulting archive.'}


def _opportunity_quest(workbench, report, digest, run_id, opportunity_run, workflow_id):
    opportunities.key(workflow_id)
    record = opportunities.OpportunityStore(workbench)._record(opportunity_run)
    saved = record['report']
    corpora = []
    for binding in saved['corpus_bindings']:
        current = corpus.get(workbench, binding['name'])
        if sha(canonical(current)) != binding['sha256']:
            raise Blocked('QUEST_OPPORTUNITY_CORPUS')
        corpora.append((binding['name'], current))
    if run_id not in {name for name, _ in corpora}:
        raise Blocked('QUEST_OPPORTUNITY_CORPUS')
    if opportunities.evaluate(record['bundle'], corpora) != saved:
        raise Blocked('QUEST_OPPORTUNITY_BINDING')
    workflows = [w for w in record['bundle']['workflows'] if w['id'] == workflow_id]
    if len(workflows) != 1:
        raise Blocked('QUEST_OPPORTUNITY_WORKFLOW')
    workflow = workflows[0]
    hypotheses = [h for h in saved['hypotheses'] if h['workflow'] == workflow_id]
    eligible = {row['definition_id']: row for row in corpus.rows(report) if _eligible(row)}
    leads = [
        {**candidate, **_source_lead(eligible[candidate['definition_id']]),
         'need': hypothesis['need']['id'], 'mechanism': group['mechanism']}
        for hypothesis in hypotheses for group in hypothesis['code_leads']
        for candidate in group['candidates']
        if candidate['corpus'] == run_id and candidate.get('definition_id') in eligible
    ]
    return {
        'schema': 1, 'run_id': run_id, 'corpus_sha256': digest, 'idea': None,
        'mode': 'opportunity', 'coverage_status': report['status'],
        'lead_basis': 'Source leads for the selected whole workflow in the saved opportunity run.',
        'leads': leads,
        'opportunity': {
            'run_id': opportunity_run, 'workflow_id': workflow_id,
            'bundle_sha256': saved['bundle_sha256'], 'report_sha256': sha(canonical(saved)),
            'workflow': workflow, 'hypotheses': hypotheses,
            'sources': record['bundle']['sources'], 'source_register': saved['source_register'],
        },
        'next_step': 'Review the complete workflow and existing alternatives. Choose the needs to '
                     'address, map each need to selected source components and a useful outcome, '
                     'and record the remaining gaps. Write the adapter and independent checks, '
                     'then package and evaluate the resulting prototype.',
    }


def _opportunity_responses(quest, recipe):
    origin = quest['opportunity']
    expected = {key: origin[key] for key in ('run_id', 'workflow_id')}
    if recipe.get('opportunity') != expected:
        raise Blocked('QUEST_OPPORTUNITY_RECIPE')
    responses = recipe.get('need_responses')
    if not isinstance(responses, list) or not 1 <= len(responses) <= 80:
        raise Blocked('QUEST_NEED_RESPONSES')
    needs = {h['need']['id'] for h in origin['hypotheses']}
    seen = set()
    for response in responses:
        if (not isinstance(response, dict) or set(response) != {'need', 'outcome', 'components'}
                or not isinstance(response['need'], str) or response['need'] not in needs
                or response['need'] in seen):
            raise Blocked('QUEST_NEED_RESPONSES')
        text(response['outcome'])
        selected = response['components']
        available = {lead['definition_id'] for lead in quest['leads'] if lead['need'] == response['need']}
        if (not isinstance(selected, list) or not selected
                or not all(isinstance(key, str) for key in selected)
                or len(set(selected)) != len(selected)
                or not set(selected) <= set(recipe['components'])
                or not set(selected) <= available):
            raise Blocked('QUEST_NEED_COMPONENTS')
        seen.add(response['need'])
    return {
        'opportunity': origin, 'need_responses': responses,
        'unaddressed_needs': sorted(needs - seen),
        'outcome_status': 'BUILDER_PROPOSED_NOT_EVALUATED',
        'market_validation': 'NOT_ESTABLISHED',
    }


def _publish(out, files):
    """Stage everything before exclusively reserving and filling a new directory."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.forge-quest-', dir=out.parent) as temporary:
        stage = Path(temporary)
        for name, data in files.items():
            write(stage / name, data, new=True)
        try:
            out.mkdir(mode=0o700)
        except FileExistsError as ex:
            raise Blocked('OUTPUT_CONFLICT') from ex
        try:
            # Same filesystem. Two concurrent publishers cannot both reserve out.
            stage.replace(out)
        except OSError:
            out.rmdir()
            raise


def start(workbench, run_id, idea, out, *, opportunity_run=None, workflow=None):
    out = preflight(workbench.home, out)['out']
    report, digest = corpus.get_with_digest(workbench, run_id)
    if (opportunity_run is None) != (workflow is None) or (opportunity_run is not None and idea is not None):
        raise Blocked('QUEST_OPPORTUNITY_ARGUMENTS')
    recipe = {'schema': 1, 'audience': '', 'purpose': '', 'acceptance': [], 'components': []}
    if opportunity_run is None:
        quest = _quest(report, digest, run_id, idea)
    else:
        quest = _opportunity_quest(workbench, report, digest, run_id, opportunity_run, workflow)
        origin = quest['opportunity']
        recipe.update({
            'audience': origin['workflow']['actor'], 'purpose': origin['workflow']['purpose'],
            'opportunity': {key: origin[key] for key in ('run_id', 'workflow_id')},
            'need_responses': [],
        })
    if len(canonical(quest)) > 4_000_000:
        raise Blocked('QUEST_INPUT_SIZE')
    _publish(out, {'quest.json': canonical(quest), 'recipe.template.json': canonical(recipe)})
    return {'status': 'QUEST_STARTED', 'quest': str(out / 'quest.json'), 'mode': quest['mode'],
            'leads': len(quest['leads']), 'coverage_status': report['status']}


def _recipe(raw):
    recipe = loads(raw)
    required = {'schema', 'audience', 'purpose', 'acceptance', 'components'}
    if (not isinstance(recipe, dict) or required - recipe.keys()
            or recipe.keys() - (required | {'support_modules', 'module_files', 'assets',
                                           'opportunity', 'need_responses', 'evaluation_plan'})
            or type(recipe['schema']) is not int or recipe['schema'] != 1):
        raise Blocked('QUEST_RECIPE')
    text(recipe['audience'])
    text(recipe['purpose'])
    checks = recipe['acceptance']
    if not isinstance(checks, list) or not 1 <= len(checks) <= 32:
        raise Blocked('QUEST_ACCEPTANCE')
    for check in checks:
        text(check)
    components = recipe['components']
    if (not isinstance(components, list) or not 1 <= len(components) <= COMPONENT_LIMIT
            or not all(isinstance(key, str) and re.fullmatch(r'definition_[0-9a-f]{64}', key)
                       for key in components) or len(set(components)) != len(components)):
        raise Blocked('QUEST_COMPONENT_SELECTION')
    support = recipe.get('support_modules', [])
    if (not isinstance(support, list) or len(support) + len(components) > COMPONENT_LIMIT
            or not all(isinstance(path, str) and _plain_module(path_in_repo(path)) for path in support)
            or len(support) != len(set(support))):
        raise Blocked('QUEST_SUPPORT_MODULES')
    from .quest_sources import validate
    validate(recipe, _plain_module, COMPONENT_LIMIT)
    return recipe


def _modules(report, root, recipe):
    from .quest_sources import Sources, check_paths
    sources = Sources(report, root)
    selected = set(recipe['components'])
    mappings = {row['source_id']: row['path'] for row in recipe.get('module_files', [])}
    plain = {segment['path']: source_id for source_id, (_, segment) in sources.segments.items()
             if not segment['member_chain'] and segment['format'] == 'python_file'}
    for path in recipe.get('support_modules', []):
        if path not in plain:
            raise Blocked('QUEST_UNINDEXED_MODULE')
        source_id = plain[path]
        if source_id in mappings and mappings[source_id] != path:
            raise Blocked('QUEST_MODULE_FILES')
        mappings[source_id] = path
    definitions = []
    for row in corpus.rows(report):
        if row['definition_id'] in selected:
            if not _eligible(row):
                raise Blocked('QUEST_WHOLE_MODULE_REQUIRED')
            inspection = corpus.inspect_definition(report, row['definition_id'], root)
            source_id = row['source_id']
            if source_id not in mappings:
                if row['member_chain'] or not _plain_module(row['path']):
                    raise Blocked('QUEST_MODULE_MAPPING_REQUIRED')
                mappings[source_id] = row['path']
            definitions.append({'definition_id': row['definition_id'], 'name': row['name'],
                                'path': row['path'], 'source_id': source_id,
                                'product_path': mappings[source_id],
                                'definition_sha256': inspection['source_window']['sha256']})
    if len(definitions) != len(selected):
        raise Blocked('QUEST_UNKNOWN_DEFINITION')
    if len(mappings) > COMPONENT_LIMIT:
        raise Blocked('QUEST_COMPONENT_SELECTION')
    paths = list(mappings.values()) + [row['path'] for row in recipe.get('assets', [])] + ['__main__.py']
    check_paths(paths, [*mappings.values(), '__main__.py'])
    modules, assets, source_files = {}, {}, []
    size = 0
    for source_id, path in sorted(mappings.items(), key=lambda item: item[1]):
        module, origin = sources.module(source_id)
        size += len(module)
        if size > APP_BYTES:
            raise Blocked('QUEST_PRODUCT_SIZE')
        modules[path] = module
        source_files.append({'path': path, 'sha256': sha(module), 'bytes': len(module),
                             'role': 'module', 'origin': origin})
        # A mapped package root still needs its original initializer. Merely
        # selecting an unrelated initializer at that destination is insufficient.
        original = PurePosixPath(sources.segments[source_id][1]['path'])
        destination_parents = list(PurePosixPath(path).parents)
        for level, original_parent in enumerate(original.parents):
            if original_parent == PurePosixPath('.'):
                break
            initializer = sources.initializer(source_id, (original_parent / '__init__.py').as_posix())
            if initializer is not None:
                parent = destination_parents[level] if level < len(destination_parents) else PurePosixPath('.')
                if parent == PurePosixPath('.') or mappings.get(initializer) != (parent / '__init__.py').as_posix():
                    raise Blocked('QUEST_SELECT_PACKAGE_INITIALIZER')
    for reference in recipe.get('assets', []):
        data, origin = sources.asset(reference)
        size += len(data)
        if size > APP_BYTES:
            raise Blocked('QUEST_PRODUCT_SIZE')
        assets[reference['path']] = data
        source_files.append({'path': reference['path'], 'bytes': len(data), 'sha256': sha(data),
                             'role': reference['role'], 'origin': origin})
    sources.verify_unchanged()
    return modules, definitions, assets, sorted(source_files, key=lambda row: row['path'])


def _archive(modules, entry):
    files = {**modules, '__main__.py': entry}
    directories = sorted({str(parent) + '/' for path in modules for parent in PurePosixPath(path).parents
                          if str(parent) != '.'})
    buffer = BytesIO()
    with ZipFile(buffer, 'w') as zip_file:
        # Explicit directories support namespace packages in zipimport. Fixed
        # timestamps make equivalent inputs produce the same archive bytes.
        for name in directories + sorted(files):
            zip_file.writestr(ZipInfo(name), files.get(name, b''))
    return buffer.getvalue()


def build(workbench, quest_file, recipe_file, entry_file, source_root, out, check_file=None):
    out = preflight(workbench.home, out)['out']
    root = safe_path(source_root)
    if (not root.is_dir() or out.is_relative_to(root) or root.is_relative_to(out)
            or root.is_relative_to(workbench.home) or workbench.home.is_relative_to(root)):
        raise Blocked('QUEST_SEPARATE_SOURCE_ROOT')
    quest_bytes = read(quest_file, 4_000_000)
    quest = loads(quest_bytes)
    if not isinstance(quest, dict) or not {'run_id', 'idea'} <= quest.keys():
        raise Blocked('QUEST_BINDING')
    if (not isinstance(quest['run_id'], str)
            or not re.fullmatch('[A-Za-z0-9_-]{1,80}', quest['run_id'])
            or (quest['idea'] is not None and not isinstance(quest['idea'], str))):
        raise Blocked('QUEST_BINDING')
    report, digest = corpus.get_with_digest(workbench, quest['run_id'])
    if quest.get('mode') == 'opportunity':
        origin = quest.get('opportunity')
        if (not isinstance(origin, dict) or not isinstance(origin.get('run_id'), str)
                or not isinstance(origin.get('workflow_id'), str)):
            raise Blocked('QUEST_BINDING')
        expected = _opportunity_quest(workbench, report, digest, quest['run_id'],
                                      origin['run_id'], origin['workflow_id'])
    else:
        expected = _quest(report, digest, quest['run_id'], quest['idea'])
    if canonical(quest) != canonical(expected):
        raise Blocked('QUEST_BINDING')
    raw_recipe = read(recipe_file, 256_000)
    recipe = _recipe(raw_recipe)
    purpose = None
    if quest['mode'] == 'opportunity':
        purpose = _opportunity_responses(quest, recipe)
    elif 'opportunity' in recipe or 'need_responses' in recipe:
        raise Blocked('QUEST_OPPORTUNITY_RECIPE')
    from .quest_assessment import validate_plan
    evaluation_plan = validate_plan(recipe)
    entry = read(entry_file, DEFAULTS['max_source_bytes'])
    try:
        compile(entry, '__main__.py', 'exec', dont_inherit=True)
    except (SyntaxError, ValueError, UnicodeError, LookupError, RecursionError) as ex:
        raise Blocked('QUEST_ENTRY_SYNTAX') from ex
    modules, definitions, assets, source_files = _modules(report, root, recipe)
    if len(entry) + sum(map(len, modules.values())) + sum(map(len, assets.values())) > APP_BYTES:
        raise Blocked('QUEST_PRODUCT_SIZE')
    artifact = _archive({**modules, **assets}, entry)
    receipt = {'schema': 1, 'status': 'PROTOTYPE_PACKAGED', 'mode': quest['mode'],
               'run_id': quest['run_id'], 'corpus_sha256': digest, 'quest_sha256': sha(quest_bytes),
               'recipe_sha256': sha(raw_recipe), 'audience': recipe['audience'], 'purpose': recipe['purpose'],
               'acceptance': recipe['acceptance'], 'entry_sha256': sha(entry),
               'modules': [{'path': path, 'sha256': sha(data), 'bytes': len(data)} for path, data in modules.items()],
               'definitions': definitions, 'product_sha256': sha(artifact), 'product_bytes': len(artifact),
               'source_code_executed': False, 'runtime_validation': 'NOT_RUN', 'acceptance_file': None}
    if 'module_files' in recipe or 'assets' in recipe:
        receipt['source_files'] = source_files
    if purpose is not None:
        receipt['purpose_evidence'] = purpose
    if evaluation_plan is not None:
        receipt['evaluation_plan'] = evaluation_plan
    if check_file is not None:
        check = read(check_file, 4_000_000)
        receipt['acceptance_file'] = {'sha256': sha(check), 'bytes': len(check)}
    _publish(out, {'product.pyz': artifact, 'receipt.json': canonical(receipt)})
    return {'status': receipt['status'], 'product': str(out / 'product.pyz'),
            'receipt': str(out / 'receipt.json'), 'product_sha256': receipt['product_sha256'],
            'runtime_validation': receipt['runtime_validation']}
