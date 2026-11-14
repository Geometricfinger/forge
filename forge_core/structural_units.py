"""Bounded complete-statement parsing with one original module identity.

The token view locates top-level units. Only those original slices are parsed;
they are never imported, executed, or presented as standalone components. A
compact declaration envelope supplies the shared module scope in a second pass.
"""
from __future__ import annotations

import ast
import io
import re
import tokenize

from .common import Blocked, sha

VERSION = 'complete-top-level-units-1'
MAX_UNITS = 2048
FAST_MODULE_BYTES = 250_000
DECLARATIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
MEANINGFUL = (*DECLARATIONS, ast.Import, ast.ImportFrom, ast.Assign,
              ast.AnnAssign, ast.AugAssign, ast.Call)
CONTINUATIONS = {'elif', 'else', 'except', 'finally'}


def node_count(tree, limit):
    """Return an exact admitted count or the first over-budget witness."""
    for count, _ in enumerate(ast.walk(tree), 1):
        if count > limit:
            return count
    return count


def _unit_lines(code):
    # tokenize does not recognize bare CR as a physical newline; AST does.
    # Normalize this transient view only. All slices use original coordinates.
    view = re.sub(r'\r\n|\r', '\n', code)
    indent = 0
    first = True
    decorators = False
    current = None
    try:
        for token in tokenize.generate_tokens(io.StringIO(view).readline):
            if token.type == tokenize.INDENT:
                indent += 1
                continue
            if token.type == tokenize.DEDENT:
                indent -= 1
                continue
            if token.type == tokenize.NEWLINE:
                first = True
                continue
            if token.type in (tokenize.NL, tokenize.COMMENT, tokenize.ENDMARKER):
                continue
            if first and indent == 0:
                new = False
                if token.string in CONTINUATIONS:
                    pass
                elif token.string == '@':
                    new = not decorators
                    decorators = True
                elif decorators:
                    decorators = False
                else:
                    new = True
                if new:
                    if current is not None:
                        yield current, token.start[0]
                    # Leading comments/whitespace belong to the first unit.
                    current = 1 if current is None else token.start[0]
            first = False
    except tokenize.TokenError as exc:
        message, position = exc.args
        raise SyntaxError(message, ('<source>', position[0], position[1] + 1, '')) from exc
    yield current or 1, None


def _envelope(tree):
    """Discard complete declaration bodies, retaining their exact headers."""
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, DECLARATIONS):
            node.body = [ast.copy_location(ast.Pass(), node.body[0])]
            continue
        stack.extend(ast.iter_child_nodes(node))
    return tree


def _parse(code, unit):
    try:
        tree = ast.parse(code[unit['char_start']:unit['char_end']])
    except SyntaxError as exc:
        if exc.lineno is not None:
            exc.lineno += unit['start_line'] - 1
        if exc.end_lineno is not None:
            exc.end_lineno += unit['start_line'] - 1
        raise
    ast.increment_lineno(tree, unit['start_line'] - 1)
    return tree


def plan(code, max_nodes, max_units=MAX_UNITS):
    """First pass: validate complete units and build bounded shared context.

    A syntax/lexical error aborts the whole module. Resource omissions remain
    explicit and make all shared binding resolution conservative. Unit records
    contain offsets/hashes, never source bodies or ASTs.
    """
    if type(max_nodes) is not int or max_nodes < 1:
        raise Blocked('STRUCTURAL_NODE_LIMIT')
    if type(max_units) is not int or not 1 <= max_units <= MAX_UNITS:
        raise Blocked('STRUCTURAL_UNIT_LIMIT')
    char_offsets = [0, *[m.end() for m in re.finditer(r'\r\n|\r|\n', code)]]
    byte_offsets = [0]
    for start, end in zip(char_offsets, char_offsets[1:]):
        byte_offsets.append(byte_offsets[-1] + len(code[start:end].encode('utf-8')))
    total_bytes = len(code.encode('utf-8'))
    line_count = len(char_offsets) - 1 + int(bool(code) and not code.endswith(('\n', '\r')))
    units = []
    envelope = []
    envelope_nodes = 1
    envelope_limited = False
    omitted = 0
    total = 0
    tail = None
    meaningful = False
    script_present = False
    syntax_complete = True
    for start_line, next_line in _unit_lines(code):
        total += 1
        start = char_offsets[start_line - 1]
        end = char_offsets[next_line - 1] if next_line is not None else len(code)
        end_byte = byte_offsets[next_line - 1] if next_line is not None else total_bytes
        record = {'index': total - 1, 'start_line': start_line,
                  'end_line': next_line - 1 if next_line is not None else line_count,
                  'byte_start': byte_offsets[start_line - 1], 'byte_end': end_byte,
                  'char_start': start, 'char_end': end}
        if total > max_units:
            omitted += 1
            syntax_complete = False
            if tail is None:
                tail = record | {'status': 'STRUCTURAL_UNIT_COUNT_LIMIT', 'unit_count': 0}
            tail.update({'end_line': record['end_line'], 'byte_end': end_byte, 'char_end': end})
            tail['unit_count'] += 1
            continue
        payload = code[start:end].encode('utf-8')
        record.update({'bytes': len(payload), 'sha256': sha(payload)})
        try:
            tree = _parse(code, record)
        except (MemoryError, RecursionError):
            record['status'] = 'AST_UNIT_LIMIT'
            record['reason'] = 'PARSER_RESOURCE_LIMIT'
            syntax_complete = False
            omitted += 1
            units.append(record)
            continue
        count = node_count(tree, max_nodes)
        meaningful |= any(isinstance(n, MEANINGFUL) for n in ast.walk(tree))
        script_present |= any(not isinstance(n, (*DECLARATIONS, ast.Import, ast.ImportFrom)) and
                              not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                                   and isinstance(n.value.value, str)) for n in tree.body)
        record['kind'] = type(tree.body[0]).__name__ if len(tree.body) == 1 else 'STATEMENT_GROUP'
        if len(tree.body) == 1 and isinstance(tree.body[0], DECLARATIONS):
            record['name'] = tree.body[0].name
        if count > max_nodes:
            record.update({'status': 'AST_UNIT_LIMIT', 'node_count_lower_bound': count})
            omitted += 1
        else:
            record.update({'status': 'ANALYZED', 'node_count': count})
            if not envelope_limited:
                projected = _envelope(tree)
                envelope_nodes += node_count(projected, max_nodes) - 1
                if envelope_nodes > max_nodes:
                    envelope_limited = True
                    envelope = []
                else:
                    envelope.extend(projected.body)
                del projected
        units.append(record)
        del tree
    if tail is not None:
        payload = code[tail['char_start']:tail['char_end']].encode('utf-8')
        tail.update({'bytes': len(payload), 'sha256': sha(payload)})
        units.append(tail)
    complete = not omitted and not envelope_limited
    public_units = [{k: v for k, v in unit.items() if not k.startswith('char_')} for unit in units]
    metadata = {'schema': 1, 'policy': VERSION, 'mode': 'COMPLETE_TOP_LEVEL_UNITS',
                'units_total': total, 'units_analyzed': total - omitted,
                'units_omitted': omitted, 'module_context_complete': complete,
                'syntax_validation_complete': syntax_complete,
                'envelope_node_count': envelope_nodes if not envelope_limited else None,
                'envelope_limited': envelope_limited,
                'limits': {'max_units': max_units, 'max_unit_nodes': max_nodes,
                           'max_envelope_nodes': max_nodes}, 'units': public_units}
    return {'metadata': metadata, 'units': units,
            'envelope': ast.Module(body=envelope, type_ignores=[]),
            'meaningful': meaningful, 'script_present': script_present}


def admitted_trees(code, planned):
    """Second pass: yield one admitted AST at a time with original positions."""
    limit = planned['metadata']['limits']['max_unit_nodes']
    for unit in planned['units']:
        if unit['status'] != 'ANALYZED':
            continue
        payload = code[unit['char_start']:unit['char_end']].encode('utf-8')
        if len(payload) != unit['bytes'] or sha(payload) != unit['sha256']:
            raise Blocked('STRUCTURAL_UNIT_BINDING')
        tree = _parse(code, unit)
        if node_count(tree, limit) != unit['node_count']:
            raise Blocked('STRUCTURAL_UNIT_BINDING')
        yield tree
        del tree
