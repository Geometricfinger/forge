"""Typed consistency checks for locally retained corpus and segment results.

The outer digest detects byte changes; these checks reject contradictory saved
states even when that digest was recomputed. They do not authenticate a cache
against someone able to rewrite all of its contents on the trusted local host.
"""
import re

from .common import Blocked, canonical, sha
from .mixed_intake import DEFAULTS, GAP_STATES

INVENTORY_STATES = GAP_STATES | {
    'PYTHON_SEGMENT', 'CONTEXT_ONLY', 'NON_CODE_BLOCK',
    'TEXT_CONTAINER_INSPECTED', 'SENSITIVE_PATH_EXCLUDED',
    'DEPENDENCY_OR_GENERATED_EXCLUDED', 'NON_CODE_PAYLOAD_NOT_READ',
}


def _require(condition, reason):
    if not condition:
        raise Blocked(reason)


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _hash(value, prefix=''):
    return isinstance(value, str) and re.fullmatch(prefix + '[0-9a-f]{64}', value) is not None


def _strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _chain(value):
    return isinstance(value, list) and all(
        isinstance(item, dict) and set(item) == {'path', 'sha256'}
        and isinstance(item['path'], str) and _hash(item['sha256']) for item in value)


def _structure(value, reason):
    _require(isinstance(value, dict), reason)
    for name in ('calls', 'declared_api_leads'):
        rows = value.get(name, [])
        _require(isinstance(rows, list) and all(isinstance(row, dict)
                 and isinstance(row.get('name'), str) for row in rows), reason)
    comparisons = value.get('comparisons', [])
    _require(isinstance(comparisons, list) and all(isinstance(row, dict)
             and _strings(row.get('operators')) for row in comparisons), reason)
    terms = value.get('term_frequencies', {})
    _require(isinstance(terms, dict) and all(isinstance(name, str)
             and isinstance(counts, dict) and all(isinstance(key, str) and _integer(count, 1)
                 for key, count in counts.items()) for name, counts in terms.items()), reason)
    if value.get('class_context') is not None:
        owner = value['class_context']
        _require(isinstance(owner, dict), reason)
        for name in ('documentation_start_line', 'documentation_end_line', 'start_line', 'end_line'):
            _require(owner.get(name) is None or _integer(owner[name], 1), reason)


def validate_segment(segment, source=None, reason='CORPUS_CHECKPOINT_SEGMENTS'):
    """Validate fields consumed by corpus publication and replay before use."""
    _require(isinstance(segment, dict), reason)
    _require('cell_index' in segment and 'logical_path_hint' in segment, reason)
    _require(_hash(segment.get('source_id'), 'source_')
             and _hash(segment.get('segment_local_id'), 'segment_')
             and _hash(segment.get('source_sha256')), reason)
    _require(segment.get('format') in ('python_file', 'python_text', 'python_fence', 'notebook_cell')
             and isinstance(segment.get('path'), str)
             and _chain(segment.get('member_chain')), reason)
    _require(_integer(segment.get('start_line'), 1)
             and _integer(segment.get('end_line'))
             and segment['end_line'] >= segment['start_line'] - 1, reason)
    _require(segment.get('cell_index') is None or _integer(segment['cell_index']), reason)
    _require(segment.get('logical_path_hint') is None or isinstance(segment['logical_path_hint'], str), reason)
    _require(isinstance(segment.get('encoding'), str)
             and _integer(segment.get('source_bytes'))
             and segment['source_bytes'] <= DEFAULTS['max_module_bytes'], reason)
    _require(all(type(segment.get(name)) is bool for name in ('test_path', 'script_present', 'large_module'))
             and _strings(segment.get('imports')), reason)
    locator = {name: segment[name] for name in (
        'member_chain', 'format', 'start_line', 'end_line', 'cell_index', 'source_sha256')}
    _require(segment['segment_local_id'] == 'segment_' + sha(canonical(locator)), reason)
    if source is not None:
        expected = {'file_id': source['file_id'], 'container_sha256': source['sha256'],
                    'segment': segment['segment_local_id']}
        _require(segment['source_id'] == 'source_' + sha(canonical(expected)), reason)
    _structure(segment.get('module_structure'), reason)
    if 'analysis_view' in segment:
        view = segment['analysis_view']
        _require(segment['format'] == 'python_fence' and isinstance(view, dict)
                 and _hash(view.get('analysis_sha256'))
                 and view.get('source_sha256') == segment['source_sha256']
                 and view.get('source_bytes') == segment['source_bytes']
                 and _integer(view.get('analysis_bytes'))
                 and view['analysis_bytes'] <= DEFAULTS['max_module_bytes'], reason)
    definitions = segment.get('definitions')
    _require(isinstance(definitions, list) and type(segment.get('definitions_count')) is int
             and segment['definitions_count'] == len(definitions), reason)
    seen = set()
    for definition in definitions:
        _require(isinstance(definition, dict) and isinstance(definition.get('name'), str), reason)
        _require(all(_integer(definition.get(name), 1) for name in (
            'start_line', 'definition_line', 'end_line', 'member_start_line', 'member_end_line')), reason)
        _require(definition['start_line'] <= definition['definition_line'] <= definition['end_line']
                 and definition['member_start_line'] == segment['start_line'] + definition['start_line'] - 1
                 and definition['member_end_line'] == segment['start_line'] + definition['end_line'] - 1
                 and definition['member_end_line'] <= segment['end_line'], reason)
        did = 'definition_' + sha(canonical([segment['source_id'], definition['name'],
                                            definition['start_line'], definition['end_line']]))
        _require(definition.get('definition_id') == did and did not in seen, reason)
        seen.add(did)
        _require(_strings(definition.get('parameters')) and _strings(definition.get('declared_terms')), reason)
        _structure(definition.get('structure'), reason)
        window = definition.get('source_window')
        _require(isinstance(window, dict) and window.get('status') in ('AVAILABLE', 'DEFINITION_SIZE_LIMIT')
                 and window.get('complete') is True and _hash(window.get('sha256'))
                 and window.get('module_sha256') == segment['source_sha256']
                 and all(_integer(window.get(name)) for name in ('byte_start', 'byte_end', 'bytes')), reason)
        _require(window['byte_start'] <= window['byte_end'] <= segment['source_bytes']
                 and window['bytes'] == window['byte_end'] - window['byte_start']
                 and window['status'] == ('AVAILABLE' if window['bytes'] <= DEFAULTS['max_source_bytes']
                                          else 'DEFINITION_SIZE_LIMIT'), reason)
    hound = segment.get('hound')
    _require(isinstance(hound, dict) and segment.get('hound_status') in ('BLOCKED', 'INSPECTED'), reason)
    if segment['hound_status'] == 'BLOCKED':
        _require(hound == {} and isinstance(segment.get('hound_reason'), str)
                 and bool(segment['hound_reason']), reason)
    else:
        analysis_hash = segment['source_sha256']
        if 'analysis_view' in segment:
            analysis_hash = segment['analysis_view']['analysis_sha256']
        _require(hound.get('source_id') == segment['source_id']
                 and hound.get('source_sha256') == analysis_hash
                 and hound.get('upstream_code_executed') is False
                 and isinstance(hound.get('findings'), list), reason)
        for finding in hound['findings']:
            _require(isinstance(finding, dict), reason)
            for name in ('qualified_name', 'function', 'function_name', 'resolved_api', 'api', 'scope_kind'):
                _require(finding.get(name) is None or isinstance(finding[name], str), reason)
    if 'structural_partition' in segment:
        partition = segment['structural_partition']
        _require(isinstance(partition, dict)
                 and all(_integer(partition.get(name)) for name in ('units_total', 'units_analyzed', 'units_omitted'))
                 and partition['units_total'] == partition['units_analyzed'] + partition['units_omitted']
                 and all(type(partition.get(name)) is bool for name in ('module_context_complete', 'syntax_validation_complete')),
                 reason)
    return segment


def validate_result(result, source):
    """Reconcile intake state, parser inventory, segments and analyzer gaps."""
    _require(isinstance(result, dict), 'CORPUS_CHECKPOINT_INVALID')
    if result.get('source') != source:
        raise Blocked('CORPUS_CHECKPOINT_SOURCE')
    _require(isinstance(result.get('source_reconstruction'), dict), 'CORPUS_CHECKPOINT_SOURCE')
    inventory = result.get('inventory')
    _require(isinstance(inventory, list), 'CORPUS_CHECKPOINT_INVENTORY')
    for row in inventory:
        _require(isinstance(row, dict) and isinstance(row.get('path'), str)
                 and isinstance(row.get('status'), str) and row['status'] in INVENTORY_STATES
                 and _chain(row.get('member_chain')), 'CORPUS_CHECKPOINT_INVENTORY')
    gaps = result.get('gaps')
    _require(isinstance(gaps, list) and all(isinstance(row, dict) for row in gaps),
             'CORPUS_CHECKPOINT_GAPS')
    expected_gaps = [row for row in inventory if row['status'] in GAP_STATES]
    _require(gaps == expected_gaps, 'CORPUS_CHECKPOINT_GAP_MISMATCH')
    _require(result.get('intake_status') == ('PARSED_WITH_GAPS' if expected_gaps else 'PARSED'),
             'CORPUS_CHECKPOINT_INTAKE_MISMATCH')
    segments = result.get('segments')
    _require(isinstance(segments, list), 'CORPUS_CHECKPOINT_SEGMENTS')
    seen = set()
    expected_inventory = []
    errors = []
    for segment in segments:
        validate_segment(segment, source)
        sid = segment['source_id']
        _require(sid not in seen, 'CORPUS_CHECKPOINT_SEGMENTS')
        seen.add(sid)
        expected_inventory.append({name: segment[name] for name in (
            'path', 'member_chain', 'segment_local_id', 'start_line', 'end_line', 'cell_index')}
            | {'status': 'PYTHON_SEGMENT'})
        if segment['hound_status'] == 'BLOCKED':
            errors.append({'source_id': sid, 'reason': segment['hound_reason']})
    _require([row for row in inventory if row['status'] == 'PYTHON_SEGMENT'] == expected_inventory,
             'CORPUS_CHECKPOINT_INVENTORY_MISMATCH')
    _require(result.get('hound_errors') == errors, 'CORPUS_CHECKPOINT_GAP_MISMATCH')
    _require(result.get('analysis_status') == ('PARSED_WITH_HOUND_GAPS' if errors else 'PARSED'),
             'CORPUS_CHECKPOINT_GAP_MISMATCH')
    return result
