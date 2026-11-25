"""Bounded declared return names; no evaluation, alias or subtype resolution."""
import ast
import re

from . import direction
from .lexical import ALIASES


NAME = re.compile(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\Z')
ROLE = re.compile(r':(?:py:)?class:`([^`]+)`')
POSITIVE_RETURN = re.compile(r'^(?:Returns?\s+)?(?:an?\s+)?' + ROLE.pattern +
    r"(?:\s+(?:object|instance|value)(?![\w'’]))?(?:\s*[.,]|\s*$|\s+(?:representing|containing|with|for|that|whose)\b)", re.I)
FIELDS = re.compile(r'(?m)^[ \t]*:(rtype|returns?|param\b[^:]*|type\b[^:]*|raises?\b[^:]*|yields?):[ \t]*')


def simple_name(value):
    value = value.strip().strip('`\'"').removeprefix('~')
    if (len(value) > 160 or not NAME.fullmatch(value)
            or value.rsplit('.', 1)[-1] in {'Any', 'None', 'NoneType', 'NoReturn', 'Never'}):
        return None
    return value


def annotation_name(node):
    if isinstance(node, ast.Name):
        return simple_name(node.id)
    if isinstance(node, ast.Attribute):
        parent = annotation_name(node.value)
        return simple_name(parent + '.' + node.attr) if parent else None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return simple_name(node.value)
    return None


def capture(node):
    """Capture simple annotations and explicitly typed documentation fields.

    Unsupported/nullable shapes, disagreements, generators and async callables
    cannot earn a type preference. Nested callable bodies do not define the
    outer function's call shape.
    """
    declarations = []
    unsupported = False
    if node.returns is not None:
        name = annotation_name(node.returns)
        if name:
            declarations.append({'basis': 'RETURN_ANNOTATION', 'type': name})
        else:
            unsupported = True
    doc = ast.get_docstring(node, clean=False) or ''
    fields = list(FIELDS.finditer(doc[:4096]))
    for index, field in enumerate(fields):
        kind = field.group(1)
        if kind not in ('rtype', 'return', 'returns'):
            continue
        end = fields[index + 1].start() if index + 1 < len(fields) else min(len(doc), 4096)
        block = re.split(r'\n[ \t]*\n', doc[field.end():end].strip(), 1)[0]
        roles = ROLE.findall(block)
        if kind == 'rtype':
            name = simple_name(roles[0]) if len(roles) == 1 and ROLE.fullmatch(block) else simple_name(block)
            basis = 'DOCSTRING_RTYPE'
        elif roles:
            name = (simple_name(roles[0]) if len(roles) == 1 and POSITIVE_RETURN.match(block) and
                    not re.search(r"\b(?:or|and|either|optional|union|None|not|no|never|without|[a-z]+n['’]t)\b", block, re.I) else None)
            basis = 'DOCSTRING_RETURN_CLASS'
        else:
            continue
        if name:
            declarations.append({'basis': basis, 'type': name})
        else:
            unsupported = True
    if not declarations and not unsupported:
        return None
    if len(doc) > 4096 or len(declarations) > 4:
        unsupported = True
    stack = list(node.body)
    while stack:
        item = stack.pop()
        if isinstance(item, (ast.Yield, ast.YieldFrom)):
            unsupported = True
            break
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(item))
    unsupported |= isinstance(node, ast.AsyncFunctionDef)
    if len({row['type'] for row in declarations}) > 1:
        unsupported = True
    return {'status': 'UNSUPPORTED_OR_AMBIGUOUS' if unsupported else 'CAPTURED',
            'declarations': declarations[:4]}


def request(query):
    if direction.phrase(query) is None:
        return None
    found = re.search(r'(?:\binto\b|\bto\b|->|→)\s+(?:(?:a|an)\s+)?'
                      r'([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)[.!?]?\s*$', query, re.I)
    return simple_name(found.group(1)) if found else None


def compare(requested, captured, implementation_kind):
    if (not requested or not captured or captured.get('status') != 'CAPTURED'
            or implementation_kind == 'DECLARATION_OR_STUB'):
        return None
    declarations = captured.get('declarations', ())
    if not declarations:
        return None
    def normalized(name):
        # Python type identifiers are case-sensitive. Only the explicit lowercase
        # spelling families can normalize; Integer is not asserted to be int.
        return ALIASES.get(name, [name])[0]
    names = [row['type'] for row in declarations]
    qualified = '.' in requested
    match = all(normalized(name if qualified else name.rsplit('.', 1)[-1]) == normalized(requested) for name in names)
    return {'requested_type': requested, 'declarations': declarations, 'name_match': match,
            'evidence': 'DECLARED_RETURN_NAME_NOT_BEHAVIOR_PROOF',
            'runtime_type_relationship': 'NOT_ESTABLISHED'}
