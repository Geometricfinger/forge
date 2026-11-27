"""Prepare explicit source selections for a quest without building or executing."""
from copy import deepcopy
from pathlib import PurePosixPath

from . import creation_quest, quest_preflight, structure
from .common import Blocked, canonical, read, safe_path, sha
from .quest_assessment import validate_plan
from .reviewed_evaluator import preflight


SUPPORT_CANDIDATE_LIMIT = 64


def _complete_initializers(sources, mappings):
    """Add only indexed originals required by the existing package mapping rule."""
    completed = dict(mappings)
    added = {}
    for source_id, destination in sorted(mappings.items(), key=lambda item: item[1]):
        if source_id not in sources.segments:
            raise Blocked('QUEST_UNINDEXED_MODULE')
        original = PurePosixPath(sources.segments[source_id][1]['path'])
        destination_parents = list(PurePosixPath(destination).parents)
        for level, original_parent in enumerate(original.parents):
            if original_parent == PurePosixPath('.'):
                break
            original_path = (original_parent / '__init__.py').as_posix()
            initializer = sources.initializer(source_id, original_path)
            if initializer is None:
                continue
            parent = (destination_parents[level] if level < len(destination_parents)
                      else PurePosixPath('.'))
            if parent == PurePosixPath('.'):
                raise Blocked('QUEST_SELECT_PACKAGE_INITIALIZER')
            destination_path = (parent / '__init__.py').as_posix()
            if initializer in completed:
                if completed[initializer] != destination_path:
                    raise Blocked('QUEST_SELECT_PACKAGE_INITIALIZER')
            else:
                # Never replace a caller's different initializer or hide a path
                # collision by selecting an apparently equivalent source.
                if destination_path.casefold() in {path.casefold() for path in completed.values()}:
                    raise Blocked('QUEST_SELECT_PACKAGE_INITIALIZER')
                completed[initializer] = destination_path
                added[initializer] = {'source_id': initializer, 'path': destination_path,
                                      'original_path': original_path, 'required_by': []}
                if len(completed) > creation_quest.COMPONENT_LIMIT:
                    raise Blocked('QUEST_COMPONENT_SELECTION')
            if initializer in added:
                added[initializer]['required_by'].append(source_id)
    for row in added.values():
        row['required_by'] = sorted(set(row['required_by']))
    sources.verify_unchanged()
    return completed, sorted(added.values(), key=lambda row: row['path'])


def _support_candidates(report, sources, definitions, selected):
    """Retain unambiguous existing lexical call leads, never infer new imports."""
    candidates = {}
    incomplete = False
    for definition in definitions:
        context = structure.context(report, definition['definition_id'])
        incomplete |= (context['status'] != 'STATIC_CONTEXT_LEADS'
                       or bool(context.get('references_truncated')))
        for reference in context['references']:
            if reference['status'] != 'DECLARED_IMPORT_CANDIDATE' or len(reference['candidates']) != 1:
                continue
            candidate = reference['candidates'][0]
            source_id = candidate['source_id']
            if source_id in selected or source_id not in sources.segments:
                continue
            source, segment = sources.segments[source_id]
            if segment['format'] != 'python_file' or PurePosixPath(segment['path']).suffix != '.py':
                continue
            if source_id not in candidates:
                candidates[source_id] = {
                    'source_id': source_id, 'path': segment['path'],
                    'file_id': source['file_id'], 'container_sha256': source['sha256'],
                    'member_chain': segment['member_chain'],
                    'analysis_source_sha256': segment['source_sha256'],
                    'basis': 'EXISTING_UNAMBIGUOUS_DECLARED_IMPORT_CALL_LEAD',
                    'selection_required': True, 'runtime_resolution': 'NOT_ESTABLISHED',
                    'references': [],
                }
            candidates[source_id]['references'].append({
                'from_definition': definition['definition_id'], 'call': reference['call'],
                'line': reference['line'], 'candidate_definition': candidate['definition_id'],
            })
    ordered = sorted(candidates.values(), key=lambda row: (row['path'], row['source_id']))
    return {
        'scope': 'EXISTING_CALL_CONTEXT_FOR_SELECTED_DEFINITIONS_ONLY',
        'candidates': ordered[:SUPPORT_CANDIDATE_LIMIT],
        'omitted_candidates': max(0, len(ordered) - SUPPORT_CANDIDATE_LIMIT),
        'context_incomplete': incomplete,
        'dependency_closure_complete': False,
        'limitation': 'These are source-review leads. Module initialization, imported attributes '
                      'and dynamic dependencies may require other files. No support candidate was selected.',
    }


def prepare(workbench, quest_file, recipe_file, source_root, out):
    """Write a build-compatible recipe and bounded source review in a new folder."""
    out = preflight(workbench.home, out)['out']
    root = safe_path(source_root)
    if (not root.is_dir() or out.is_relative_to(root) or root.is_relative_to(out)
            or root.is_relative_to(workbench.home) or workbench.home.is_relative_to(root)):
        raise Blocked('QUEST_SEPARATE_SOURCE_ROOT')
    quest, quest_bytes, report, digest = creation_quest.load_quest_binding(workbench, quest_file)
    raw_recipe = read(recipe_file, 256_000)
    recipe = creation_quest._recipe(raw_recipe)
    if quest['mode'] == 'opportunity':
        creation_quest._opportunity_responses(quest, recipe)
    elif 'opportunity' in recipe or 'need_responses' in recipe:
        raise Blocked('QUEST_OPPORTUNITY_RECIPE')
    validate_plan(recipe)

    sources, selected_mappings, definitions = creation_quest._selected_modules(report, root, recipe)
    mappings, added = _complete_initializers(sources, selected_mappings)
    prepared = deepcopy(recipe)
    prepared['module_files'] = [{'source_id': source_id, 'path': path}
                                for source_id, path in sorted(mappings.items(), key=lambda item: item[1])]
    prepared_bytes = canonical(prepared)
    # The prepared file remains an ordinary build input with the same size and
    # schema limits. Existing source/path/asset checks still decide its validity.
    if len(prepared_bytes) > 256_000:
        raise Blocked('INPUT_FILE_BOUNDS')
    creation_quest._recipe(prepared_bytes)
    modules, definitions, assets, source_files = creation_quest._modules(report, root, prepared)
    packaging = quest_preflight.run(workbench.home, modules, assets, None)
    packaging_bytes = canonical(packaging)
    support = _support_candidates(report, sources, definitions, set(mappings))
    preparation = {
        'schema': 1, 'status': 'RECIPE_PREPARED_REVIEW_REQUIRED',
        'run_id': quest['run_id'], 'corpus_sha256': digest, 'quest_sha256': sha(quest_bytes),
        'input_recipe_sha256': sha(raw_recipe), 'prepared_recipe_sha256': sha(prepared_bytes),
        'definitions': definitions, 'source_files': source_files,
        'added_initializers': added,
        'source_preflight': quest_preflight.summary(packaging, packaging_bytes),
        'support_review': support,
        'reuse_review': {
            'status': 'BUILDER_REVIEW_REQUIRED',
            'selected_notices': [row['path'] for row in source_files if row['role'] == 'notice'],
            'notice_presence_is_rights_clearance': False,
            'guidance': 'Review reuse terms and select the required original notices and resources. '
                        'This preparation does not determine which notices are legally required.',
        },
        'entry_point_reviewed': False, 'product_built': False,
        'dependency_closure_complete': False, 'source_code_executed': False,
        'runtime_validation': 'NOT_RUN', 'release_approved': False,
        'next_step': 'Review source-preflight.json and the retained support leads. Select any needed '
                     'support modules, resources and notices explicitly. Review an entry point, then '
                     'use quest-build with recipe.prepared.json and test the exact resulting archive '
                     'under separate execution authorization.',
        'limitations': [
            'Archive and renamed-module destination paths were supplied by the builder.',
            'Only required original indexed package initializers were added automatically.',
            'Business intent, acceptance checks and evaluation expectations remain builder supplied and unchanged.',
            'Source preflight inspected selected modules only; no entry point or complete product was reviewed.',
            'Raw bytes were checked against the indexed source; hashes are local consistency records, not provider authentication.',
        ],
    }
    creation_quest._publish(out, {'recipe.prepared.json': prepared_bytes,
                                  'preparation.json': canonical(preparation),
                                  'source-preflight.json': packaging_bytes})
    return {
        'status': preparation['status'], 'recipe': str(out / 'recipe.prepared.json'),
        'preparation': str(out / 'preparation.json'), 'preflight': str(out / 'source-preflight.json'),
        'preflight_status': packaging['status'], 'preflight_issues': len(packaging['issues']),
        'added_initializers': len(added), 'modules': len(modules), 'assets': len(assets),
        'source_code_executed': False, 'runtime_validation': 'NOT_RUN', 'release_approved': False,
    }
