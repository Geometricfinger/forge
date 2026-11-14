"""Deterministic analysis views bound to unchanged original segment bytes.

Only the admitted Markdown fence indentation is removed. This is not Python
dedenting, code repair, source reconstruction, or permission to execute source.
"""
import re

from .common import Blocked, canonical, sha

FENCE_POLICY = 'commonmark-fence-indent-1'


def fence_view(source, indent):
    """Apply CommonMark's opening-fence indentation rule, preserving newlines.

    Block indentation uses four-column tab stops. A partially consumed leading
    tab leaves spaces; tabs beyond the removed prefix remain literal bytes.
    """
    if not isinstance(source, bytes) or type(indent) is not int or not 0 <= indent <= 3:
        raise Blocked('FENCE_ANALYSIS_VIEW')
    output = []
    for match in re.finditer(rb'[^\r\n]*(?:\r\n|\r|\n|$)', source):
        line = match.group()
        if not line:
            continue
        offset = 0
        columns = 0
        while offset < len(line) and columns < indent:
            if line[offset] == 32:
                columns += 1
            elif line[offset] == 9:
                columns += 4 - columns % 4
            else:
                break
            offset += 1
        output.append(b' ' * max(0, columns - indent) + line[offset:])
    analysis = b''.join(output)
    metadata = {'schema': 1, 'policy': FENCE_POLICY, 'indent_columns': indent,
                'source_sha256': sha(source), 'source_bytes': len(source),
                'analysis_sha256': sha(analysis), 'analysis_bytes': len(analysis),
                'line_mapping': 'IDENTITY', 'column_basis': 'ANALYSIS_VIEW_UTF8',
                'source_byte_basis': 'UTF8_SEGMENT_SOURCE'}
    return analysis, metadata


def analysis_bytes(segment, source):
    """Recompute the admitted view and reject any mismatched policy/binding."""
    if 'analysis_view' not in segment:
        return source
    view = segment['analysis_view']
    if segment.get('format') != 'python_fence' or not isinstance(view, dict):
        raise Blocked('FENCE_ANALYSIS_BINDING')
    try:
        analysis, expected = fence_view(source, view.get('indent_columns'))
        if canonical(view) != canonical(expected) or segment.get('source_sha256') != expected['source_sha256']:
            raise Blocked('FENCE_ANALYSIS_BINDING')
    except (TypeError, ValueError, Blocked) as exc:
        raise Blocked('FENCE_ANALYSIS_BINDING') from exc
    return analysis


def partition_source_windows(metadata, source, analysis, view):
    """Map complete-unit public windows back to original physical source lines.

    Parser-unit hashes/offsets retain their own labeled analysis window. Internal
    parse-plan slices are not changed; this returns separate public metadata.
    """
    segment = {'format': 'python_fence', 'source_sha256': sha(source), 'analysis_view': view}
    if analysis_bytes(segment, source) != analysis:
        raise Blocked('FENCE_PARTITION_BINDING')
    offsets = []
    for data in (source, analysis):
        positions = [0, *[match.end() for match in re.finditer(b'\r\n|\r|\n', data)]]
        if positions[-1] != len(data):
            positions.append(len(data))
        offsets.append(positions)
    raw_offsets, analysis_offsets = offsets
    if len(raw_offsets) != len(analysis_offsets):
        raise Blocked('FENCE_PARTITION_BINDING')
    units = []
    for unit in metadata['units']:
        start, end = unit['start_line'], unit['end_line']
        if type(start) is not int or type(end) is not int or not 1 <= start <= end < len(raw_offsets):
            raise Blocked('FENCE_PARTITION_BINDING')
        a, b = analysis_offsets[start - 1], analysis_offsets[end]
        if (unit['byte_start'] != a or unit['byte_end'] != b or
                unit['bytes'] != b - a or unit['sha256'] != sha(analysis[a:b])):
            raise Blocked('FENCE_PARTITION_BINDING')
        analysis_window = {key: unit[key] for key in ('byte_start', 'byte_end', 'bytes', 'sha256')}
        analysis_window.update(byte_basis='UTF8_ANALYSIS_VIEW', module_sha256=view['analysis_sha256'])
        a, b = raw_offsets[start - 1], raw_offsets[end]
        units.append(unit | {'byte_start': a, 'byte_end': b, 'bytes': b - a, 'sha256': sha(source[a:b]),
                             'byte_basis': 'UTF8_SEGMENT_SOURCE', 'analysis_window': analysis_window})
    return metadata | {'units': units, 'byte_basis': 'UTF8_SEGMENT_SOURCE',
                       'source_sha256': view['source_sha256'], 'analysis_view_sha256': sha(canonical(view))}
