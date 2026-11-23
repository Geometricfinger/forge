"""Build a local Python zipapp from reviewed components in a retained corpus."""
from collections import defaultdict
from io import BytesIO
from pathlib import Path, PurePosixPath
import keyword
import re
import tempfile
from zipfile import ZipFile, ZipInfo

from . import corpus
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
    return (not row['test_path'] and row['format'] == 'python_file' and not row['member_chain']
            and row['container'] == row['path'] and _plain_module(row['path']))


def _quest(report, digest, run_id, idea):
    if idea is None:
        # Round-robin across modules, so one large file cannot take every slot.
        groups = defaultdict(list)
        for row in corpus.rows(report):
            if _eligible(row):
                groups[row['path']].append(row)
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


def start(workbench, run_id, idea, out):
    out = preflight(workbench.home, out)['out']
    report, digest = corpus.get_with_digest(workbench, run_id)
    quest = _quest(report, digest, run_id, idea)
    recipe = {'schema': 1, 'audience': '', 'purpose': '', 'acceptance': [], 'components': []}
    _publish(out, {'quest.json': canonical(quest), 'recipe.template.json': canonical(recipe)})
    return {'status': 'QUEST_STARTED', 'quest': str(out / 'quest.json'), 'mode': quest['mode'],
            'leads': len(quest['leads']), 'coverage_status': report['status']}


def _recipe(raw):
    recipe = loads(raw)
    required = {'schema', 'audience', 'purpose', 'acceptance', 'components'}
    if (not isinstance(recipe, dict) or required - recipe.keys()
            or recipe.keys() - (required | {'support_modules'})
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
    return recipe


def _module_bytes(record, root):
    source = record['source']
    corpus.manifest({'schema': 1, 'files': [source]})
    path = source['path']
    segments = record['segments']
    if (not _plain_module(path) or len(segments) != 1 or segments[0]['format'] != 'python_file'
            or segments[0]['member_chain'] or segments[0]['path'] != path):
        raise Blocked('QUEST_WHOLE_MODULE_REQUIRED')
    file = safe_path(root / path)
    if not file.is_relative_to(root):
        raise Blocked('SOURCE_ESCAPE')
    data = read(file, DEFAULTS['max_container_bytes'])
    if len(data) != source['size'] or sha(data) != source['sha256']:
        raise Blocked('SOURCE_VERSION_MISMATCH')
    return data


def _modules(report, root, recipe):
    selected = set(recipe['components'])
    records = {item['source']['path']: item for item in report['sources']}
    paths = set(recipe.get('support_modules', []))
    definitions = []
    for row in corpus.rows(report):
        if row['definition_id'] in selected:
            if not _eligible(row):
                raise Blocked('QUEST_WHOLE_MODULE_REQUIRED')
            inspection = corpus.inspect_definition(report, row['definition_id'], root)
            paths.add(row['path'])
            definitions.append({'definition_id': row['definition_id'], 'name': row['name'],
                                'path': row['path'], 'definition_sha256': inspection['source_window']['sha256']})
    if len(definitions) != len(selected):
        raise Blocked('QUEST_UNKNOWN_DEFINITION')
    if paths - records.keys():
        raise Blocked('QUEST_UNINDEXED_MODULE')
    modules = {}
    size = 0
    for path in sorted(paths):
        module = _module_bytes(records[path], root)
        size += len(module)
        if size > APP_BYTES:
            raise Blocked('QUEST_PRODUCT_SIZE')
        modules[path] = module
        # Keep package semantics. Extra executable package initializers must be
        # explicitly selected, including ones that have no function definitions.
        for parent in PurePosixPath(path).parents:
            if parent == PurePosixPath('.'):
                break
            init = (parent / '__init__.py').as_posix()
            if init not in paths and (init in records or safe_path(root / init).exists()):
                raise Blocked('QUEST_SELECT_PACKAGE_INITIALIZER')
            if parent.as_posix() + '.py' in paths:
                raise Blocked('QUEST_IMPORT_PATH_COLLISION')
    return modules, definitions


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
    expected = _quest(report, digest, quest['run_id'], quest['idea'])
    if canonical(quest) != canonical(expected):
        raise Blocked('QUEST_BINDING')
    raw_recipe = read(recipe_file, 256_000)
    recipe = _recipe(raw_recipe)
    entry = read(entry_file, DEFAULTS['max_source_bytes'])
    try:
        compile(entry, '__main__.py', 'exec', dont_inherit=True)
    except (SyntaxError, ValueError, UnicodeError, LookupError, RecursionError) as ex:
        raise Blocked('QUEST_ENTRY_SYNTAX') from ex
    modules, definitions = _modules(report, root, recipe)
    artifact = _archive(modules, entry)
    receipt = {'schema': 1, 'status': 'PROTOTYPE_PACKAGED', 'mode': quest['mode'],
               'run_id': quest['run_id'], 'corpus_sha256': digest, 'quest_sha256': sha(quest_bytes),
               'recipe_sha256': sha(raw_recipe), 'audience': recipe['audience'], 'purpose': recipe['purpose'],
               'acceptance': recipe['acceptance'], 'entry_sha256': sha(entry),
               'modules': [{'path': path, 'sha256': sha(data), 'bytes': len(data)} for path, data in modules.items()],
               'definitions': definitions, 'product_sha256': sha(artifact), 'product_bytes': len(artifact),
               'source_code_executed': False, 'runtime_validation': 'NOT_RUN', 'acceptance_file': None}
    if check_file is not None:
        check = read(check_file, 4_000_000)
        receipt['acceptance_file'] = {'sha256': sha(check), 'bytes': len(check)}
    _publish(out, {'product.pyz': artifact, 'receipt.json': canonical(receipt)})
    return {'status': receipt['status'], 'product': str(out / 'product.pyz'),
            'receipt': str(out / 'receipt.json'), 'product_sha256': receipt['product_sha256'],
            'runtime_validation': receipt['runtime_validation']}
