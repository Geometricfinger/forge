"""Look up a literal function name in the definitions and calls already indexed."""
import keyword

from .common import Blocked, integer, text


def lookup(report, name, *, include_tests=False, limit=20):
    """A dotted name is qualified within its source. A bare name matches a leaf."""
    text(name, 200)
    if any(not part.isidentifier() or keyword.iskeyword(part) for part in name.split('.')):
        raise Blocked('INVALID_SYMBOL_NAME')
    if type(include_tests) is not bool:
        raise Blocked('SEARCH_BOOL')
    integer(limit, 1, 100)

    def matches(spelling):
        return spelling == name or ('.' not in name and spelling.rsplit('.', 1)[-1] == name)

    definitions, references = [], []
    definition_count = reference_count = 0
    uncertain_call_scopes = 0
    for source_record in report['sources']:
        source = source_record['source']
        for segment in source_record['segments']:
            if segment['test_path'] and not include_tests:
                continue
            origin = {'file_id': source['file_id'], 'container_sha256': source['sha256'],
                      'source_id': segment['source_id'], 'source_sha256': segment['source_sha256'],
                      'path': segment['path'], 'member_chain': segment['member_chain'],
                      'cell_index': segment['cell_index']}
            # Module calls exist even when the ordinary search emits only function rows.
            scopes = [('<module>', None, segment.get('module_structure', {}))]
            for definition in segment['definitions']:
                symbol = definition['name']
                identity = definition['definition_id']
                metadata = definition.get('structure', {})
                scopes.append((symbol, identity, metadata))
                if matches(symbol):
                    definition_count += 1
                    if len(definitions) < limit:
                        definitions.append({**origin, 'name': symbol, 'definition_id': identity,
                                            'start_line': definition['member_start_line'],
                                            'end_line': definition['member_end_line'],
                                            'implementation_kind': metadata.get('implementation_kind', 'NOT_CAPTURED'),
                                            'source_window': definition.get('source_window')})

            for owner, identity, metadata in scopes:
                if 'calls' not in metadata or metadata.get('truncated', True):
                    uncertain_call_scopes += 1
                for call in metadata.get('calls', []):
                    if not matches(call['name']):
                        continue
                    reference_count += 1
                    if len(references) < limit:
                        references.append({**origin, 'caller': owner, 'caller_definition_id': identity,
                                           'spelling': call['name'],
                                           'line': segment['start_line'] + call['line'] - 1})

    coverage = report.get('coverage', {})
    complete = coverage.get('definitions', {}).get('discovery_complete')
    if definition_count > 1:
        status = 'MULTIPLE_INDEXED_DEFINITIONS'
    elif definition_count:
        status = 'ONE_INDEXED_DEFINITION'
    elif complete is not True:
        status = 'DEFINITION_COVERAGE_UNKNOWN'
    else:
        status = 'NO_INDEXED_DEFINITION'
    return {'name': name, 'status': status, 'include_tests': include_tests,
            'definition_count': definition_count, 'definitions': definitions,
            'call_reference_count': reference_count, 'call_references': references,
            'call_reference_scope': 'CAPTURED_NAMED_CALLS_ONLY',
            'missing_or_truncated_call_scopes': uncertain_call_scopes,
            'limit_per_list': limit, 'definition_coverage': complete,
            'corpus_manifest_sha256': report.get('corpus_manifest_sha256'),
            'coverage_status': report['status'],
            'selected_containers': coverage.get('selected_containers'),
            'inspected_containers': coverage.get('inspected_containers'),
            'suggested_leaf_lookup': name.rsplit('.', 1)[1] if not definition_count and '.' in name else None,
            'scope': 'Indexed Python functions and methods. Qualified names use source-local nesting, without a module prefix. Call sites use their captured spelling; aliases and dynamic calls may differ.',
            'runtime_validation': 'NOT_RUN'}
