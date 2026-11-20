"""Link explicit Markdown code spans to indexed Python definition occurrences."""
from collections import defaultdict
import keyword
from pathlib import PurePosixPath
import re

from . import corpus
from .common import Blocked, integer, sha


MAX_DOCUMENT_BYTES = 256_000
MAX_REFERENCES = 256
FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')
HEADING = re.compile(r'^ {0,3}(#{1,6})(?:[ \t]+(.*)|$)')
TICKS = re.compile(r'`+')
CANDIDATE_FIELDS = (
    'definition_id', 'name', 'path', 'start_line', 'end_line', 'file_id',
    'container', 'container_sha256', 'member_chain', 'source_id', 'source_sha256',
    'implementation_kind',
)


def _is_reference(value):
    symbol = value.removesuffix('()')
    return (len(value) <= 200 and '.' in symbol and
            all(part.isidentifier() and not keyword.iskeyword(part) for part in symbol.split('.')))


def _sections(raw):
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise Blocked('DOC_LINK_DOCUMENT_SIZE')
    try:
        document = raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise Blocked('DOC_LINK_DOCUMENT_ENCODING') from exc
    sections = []
    current = {'heading': None, 'heading_line': None, 'level': 0, 'links': []}
    count = 0
    fence = None
    opening = None
    comment = False
    for line_no, line in enumerate(re.split(r'\r\n|\r|\n', document), 1):
        marker = FENCE.fullmatch(line)
        if fence:
            if marker and marker[1].startswith(fence) and not marker[2].strip():
                fence = None
            continue
        # A line containing a comment boundary is omitted in full.
        if comment or '<!--' in line:
            last_open = line.rfind('<!--')
            last_close = line.rfind('-->')
            if last_open >= 0 or last_close >= 0:
                comment = last_open > last_close
            opening = None
            continue
        if marker and (marker[1].startswith('~') or '`' not in marker[2]):
            fence = marker[1]
            opening = None
            continue
        if not line.strip() or line.startswith(('    ', '\t')) or line.lstrip().startswith('>'):
            opening = None
            continue
        heading = HEADING.fullmatch(line)
        if heading:
            title = re.sub(r'(?:^|[ \t]+)#+[ \t]*$', '', heading[2] or '').strip()
            current = {'heading': title, 'heading_line': line_no,
                       'level': len(heading[1]), 'links': []}
            opening = None
        for tick in TICKS.finditer(line):
            width = tick.end() - tick.start()
            if opening is None:
                start = tick.start()
                while start and line[start - 1] == '\\':
                    start -= 1
                if (tick.start() - start) % 2 == 0:
                    opening = (width, line_no, tick.end())
                continue
            if width != opening[0]:
                continue
            if opening[1] == line_no:
                spelling = line[opening[2]:tick.start()]
                if _is_reference(spelling):
                    count += 1
                    if count > MAX_REFERENCES:
                        raise Blocked('DOC_LINK_REFERENCE_LIMIT')
                    if not current['links']:
                        sections.append(current)
                    current['links'].append({'reference': spelling, 'doc_line': line_no})
            opening = None
    return sections


def _candidates(report, spellings, include_tests, limit):
    # Each split requires an exact source-local definition name, plus every
    # component of the module qualification supplied by the document.
    wanted = defaultdict(dict)
    candidates = {name: [] for name in spellings}
    counts = dict.fromkeys(spellings, 0)
    for name in spellings:
        parts = name.split('.')
        for index in range(len(parts)):
            wanted['.'.join(parts[index:])][tuple(parts[:index])] = name
    if not wanted:
        return counts, candidates
    for row in corpus.rows(report):
        by_module = wanted.get(row['name'])
        if not by_module or row['format'] != 'python_file' or (row['test_path'] and not include_tests):
            continue
        path = PurePosixPath(row['path']).with_suffix('')
        module = path.parts[:-1] if path.name == '__init__' else path.parts
        # An empty module prefix means the document gave an exact dotted
        # source-local method/nested function name, such as Worker.emit.
        for size in range(len(module) + 1):
            name = by_module.get(module[len(module) - size:])
            if name is None:
                continue
            counts[name] += 1
            if len(candidates[name]) < limit:
                candidates[name].append({key: row[key] for key in CANDIDATE_FIELDS})
    return counts, candidates


def resolve(report, raw, *, include_tests=False, limit=10):
    """Return section-local references; candidate limits apply after qualification."""
    if type(include_tests) is not bool:
        raise Blocked('SEARCH_BOOL')
    integer(limit, 1, 20)
    sections = _sections(raw)
    names = {link['reference'].removesuffix('()') for section in sections for link in section['links']}
    counts, candidates = _candidates(report, names, include_tests, limit)
    coverage = report['coverage'].get('definitions', {}).get('discovery_complete')
    for section in sections:
        linked = []
        for link in section['links']:
            name = link['reference'].removesuffix('()')
            count = counts[name]
            if count > limit:
                state = 'lookup_truncated'
            elif count > 1:
                state = 'ambiguous'
            elif coverage is not True:
                state = 'coverage_unknown'
            elif count:
                state = 'linked'
                linked.append(candidates[name][0]['definition_id'])
            else:
                state = 'unresolved'
            link.update(state=state, candidate_count=count, candidates=candidates[name])
        section['linked_definition_ids'] = list(dict.fromkeys(linked))
    return {'schema': 1, 'document_sha256': sha(raw), 'sections': sections,
            'corpus_manifest_sha256': report['corpus_manifest_sha256'],
            'coverage_status': report['status'], 'definition_coverage': coverage,
            'include_tests': include_tests, 'limit_per_reference': limit,
            'scope': 'Indexed Python file functions and methods. Section groups record document co-mentions.',
            'runtime_validation': 'NOT_RUN'}
