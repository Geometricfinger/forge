"""Advisory packaging checks on exact Python bytes; never execute the product.

The fixed worker uses Python's compiler for packaging compatibility, not Hound's
API analysis. Import/resource syntax supplies review leads, not dependency closure.
"""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
from pathlib import PurePosixPath
import sys
import time
import unicodedata

from .common import Blocked, canonical, loads, sha
from .engine import ROOT, run_fixed
from .structure import dotted
from .structural_units import node_count


VERSION = 'product-preflight-1'
MAX_REFERENCES = 256
MAX_ISSUES = 512
MAX_SECONDS = 45
GUIDANCE = {
    'PYTHON_BYTES_NOT_COMPILABLE': 'Review the original encoding or syntax for the target Python version; packaged bytes were not rewritten.',
    'PREFLIGHT_AST_LIMIT': 'Review this module separately; its syntax exceeds the bounded preflight AST allowance.',
    'PREFLIGHT_COMPILER_LIMIT': 'Review this module separately; the compiler reached a resource limit.',
    'PREFLIGHT_REFERENCE_LIMIT': 'Review remaining imports and resources separately; reference capture reached its limit.',
    'PREFLIGHT_WORKER_UNAVAILABLE': 'Repeat or review this module separately; the bounded compiler worker did not provide a usable result.',
    'MODULE_NAME_NORMALIZATION': 'Review the module mapping: ordinary Python identifiers use the listed NFKC-normalized name.',
    'NORMALIZED_MODULE_COLLISION': 'Choose distinct module mappings after Python identifier normalization.',
    'STDLIB_NAME_SHADOW': 'Review this top-level name: it can hide the standard-library module in the product.',
    'RELATIVE_IMPORT_OUTSIDE_PACKAGE': 'Review the package mapping and this relative import depth.',
    'MISSING_LOCAL_IMPORT': 'Select and map the missing module, or review why this import is not required at runtime.',
    'EXTERNAL_IMPORT_UNVERIFIED': 'Verify this dependency in the target environment; Forge does not import or install it.',
    'RESOURCE_REFERENCE_UNRESOLVED': 'Review the resource package and path manually; this expression could not be matched to selected files.',
    'ZIP_NAMESPACE_RESOURCE': 'Review ZIP resource compatibility; explicitly select an original package initializer or change the reviewed loader.',
    'MISSING_PACKAGE_RESOURCE': 'Select the exact resource bytes at the requested package path, or review the conditional use.',
    'EAGER_IMPORT_CYCLE': 'Review initialization order in these modules; the imports may request values before they are defined.',
}


def _package(path):
    return '.'.join(PurePosixPath(path).parts[:-1])


def _module(path):
    file = PurePosixPath(path)
    return _package(path) if file.name == '__init__.py' else '.'.join((*file.parts[:-1], file.stem))


def _imports(tree):
    stack = [(tree, False)]
    while stack:
        node, delayed = stack.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node, delayed
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            delayed = True
        stack.extend((child, delayed) for child in reversed(list(ast.iter_child_nodes(node))))


def _aliases(tree):
    """Only unambiguous imports, with conservative whole-file shadow guards."""
    aliases = {}
    writes = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            writes[node.id] += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            writes[node.name] += 1
        elif isinstance(node, ast.arg):
            writes[node.arg] += 1
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            writes[node.name] += 1
        elif isinstance(node, ast.MatchMapping) and node.rest:
            writes[node.rest] += 1
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = alias.asname or (alias.name.split('.')[0] if isinstance(node, ast.Import) else alias.name)
                writes[name] += 1
    # A conditional or function-local import cannot establish a global alias.
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split('.')[0]
                aliases[name] = alias.name if alias.asname else name
        elif isinstance(node, ast.ImportFrom) and not node.level:
            for alias in node.names:
                aliases[alias.asname or alias.name] = (node.module or '') + '.' + alias.name
    return ({name: target for name, target in aliases.items() if writes[name] == 1 and not writes['*']}, writes)


def _resource_references(tree, path):
    aliases, writes = _aliases(tree)

    def api(node):
        name = dotted(node)
        if not name:
            return None
        first, _, rest = name.partition('.')
        return aliases[first] + ('.' + rest if rest else '') if first in aliases else None

    def literal(node):
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    def package(node):
        value = literal(node)
        if value is not None:
            return value
        if isinstance(node, ast.Name) and node.id == '__package__' and not writes[node.id]:
            return _package(path)
        return None

    def argument(node, position, names):
        if len(node.args) > position:
            return node.args[position]
        return next((keyword.value for keyword in node.keywords if keyword.arg in names), None)

    def traversable(node):
        def append(base, parts):
            if (base[1] is None or any(part is None or PurePosixPath(part).is_absolute()
                                       or '..' in PurePosixPath(part).parts or '\\' in part for part in parts)):
                return base[0], None
            return base[0], '/'.join([*([base[1]] if base[1] else []), *parts])

        if isinstance(node, ast.Call):
            if api(node.func) == 'importlib.resources.files':
                return package(argument(node, 0, {'package', 'anchor'})), ''
            if isinstance(node.func, ast.Attribute) and node.func.attr == 'joinpath':
                base = traversable(node.func.value)
                parts = [literal(arg) for arg in node.args]
                if base:
                    return append(base, parts) if not node.keywords and parts else (base[0], None)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            base, part = traversable(node.left), literal(node.right)
            if base:
                return append(base, [part])
        return None

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Call, ast.BinOp)):
            continue
        reference = traversable(node)
        if reference:
            yield {'api': 'importlib.resources.files', 'package': reference[0],
                   'resource': reference[1], 'line': node.lineno}
        elif isinstance(node, ast.Call):
            name = api(node.func)
            if name in {'pkgutil.get_data', 'importlib.resources.read_text', 'importlib.resources.read_binary',
                        'importlib.resources.open_text', 'importlib.resources.open_binary'}:
                yield {'api': name, 'package': package(argument(node, 0, {'package'})),
                       'resource': literal(argument(node, 1, {'resource'})), 'line': node.lineno}


def inspect_module(data, path):
    """Run only inside the fixed worker for collected bytes; tests use owned text."""
    result = {'path': path, 'sha256': sha(data), 'imports': [], 'resources': [], 'gaps': [],
              'raw_compilation': 'NOT_CHECKED', 'source_code_executed': False}
    try:
        tree = compile(data, path, 'exec', flags=ast.PyCF_ONLY_AST, dont_inherit=True)
        if node_count(tree, 60_000) > 60_000:
            result['gaps'].append({'code': 'PREFLIGHT_AST_LIMIT'})
            return result
        compile(tree, path, 'exec', dont_inherit=True)
    except (SyntaxError, ValueError, UnicodeError, LookupError) as error:
        result['raw_compilation'] = 'REJECTED'
        result['gaps'].append({'code': 'PYTHON_BYTES_NOT_COMPILABLE',
                               'line': getattr(error, 'lineno', None),
                               'reason': type(error).__name__})
        return result
    except (MemoryError, RecursionError):
        result['gaps'].append({'code': 'PREFLIGHT_COMPILER_LIMIT'})
        return result
    result['raw_compilation'] = 'ACCEPTED'
    references = 0
    for node, delayed in _imports(tree):
        names = [alias.name for alias in node.names]
        module = node.module or '' if isinstance(node, ast.ImportFrom) else ''
        references += len(names)
        if references > MAX_REFERENCES or any(
                len(name) > 240 for name in [module, *names]):
            result['gaps'].append({'code': 'PREFLIGHT_REFERENCE_LIMIT'})
            break
        result['imports'].append({'kind': 'from' if isinstance(node, ast.ImportFrom) else 'import',
                                  'module': module, 'names': names, 'line': node.lineno,
                                  'level': node.level if isinstance(node, ast.ImportFrom) else 0,
                                  'delayed': delayed})
    for row in _resource_references(tree, path):
        if len(result['resources']) >= MAX_REFERENCES or any(
                value is not None and len(value) > 600 for value in (row['package'], row['resource'])):
            result['gaps'].append({'code': 'PREFLIGHT_REFERENCE_LIMIT'})
            break
        result['resources'].append(row)
    return result


def _cycles(edges):
    """Strongly connected groups; module count is bounded by the recipe."""
    seen, order = set(), []

    def visit(node):
        if node in seen:
            return
        seen.add(node)
        for target in sorted(edges[node]):
            visit(target)
        order.append(node)

    for node in sorted(edges):
        visit(node)
    reverse = {node: set() for node in edges}
    for node, targets in edges.items():
        for target in targets:
            reverse[target].add(node)
    seen = set()
    for node in reversed(order):
        if node in seen:
            continue
        pending, group = [node], []
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current); group.append(current)
            pending.extend(sorted(reverse[current]))
        if len(group) > 1:
            yield sorted(group)


def review(facts, assets, *, stdlib=None, builtin=None):
    """Evaluate bounded compiler facts against the selected product paths only."""
    stdlib = set(sys.stdlib_module_names if stdlib is None else stdlib)
    builtin = set(sys.builtin_module_names if builtin is None else builtin)
    paths = {row['path'] for row in facts}
    modules = {_module(path): path for path in sorted(paths)}
    packages = {'.'.join(PurePosixPath(path).parts[:i]) for path in paths
                for i in range(1, len(PurePosixPath(path).parts))}
    available = set(modules) | packages
    roots = {name.split('.')[0] for name in available}
    normalized_roots = {unicodedata.normalize('NFKC', name) for name in roots}
    all_files = paths | set(assets)
    all_paths = all_files | {str(parent) for path in all_files for parent in PurePosixPath(path).parents}
    issues, seen, omitted = [], set(), 0

    def issue(code, path, line=None, **detail):
        nonlocal omitted
        row = {'code': code, 'path': path, 'line': line, 'guidance': GUIDANCE[code], **detail}
        key = canonical(row)
        if key in seen:
            return
        if len(issues) == MAX_ISSUES:
            omitted += 1
            return
        seen.add(key); issues.append(row)

    normalized = defaultdict(list)
    for path in sorted(paths):
        name = _module(path)
        normal = unicodedata.normalize('NFKC', name)
        normalized[normal].append(path)
        if name != normal:
            issue('MODULE_NAME_NORMALIZATION', path, normalized_module=normal)
        if name != '__main__' and '.' not in name and name in stdlib - builtin:
            issue('STDLIB_NAME_SHADOW', path, module=name)
    for name, matches in sorted(normalized.items()):
        if len(matches) > 1:
            issue('NORMALIZED_MODULE_COLLISION', matches[0], module=name, paths=sorted(matches))

    edges = {name: set() for name in modules}
    for fact in facts:
        path = fact['path']; name = _module(path)
        for gap in fact['gaps']:
            issue(gap['code'], path, **{key: value for key, value in gap.items() if key != 'code'})
        for row in fact['imports']:
            targets = list(row['names']) if row['kind'] == 'import' else [row['module']]
            if row['level']:
                package = _package(path).split('.') if _package(path) else []
                if row['level'] > len(package):
                    issue('RELATIVE_IMPORT_OUTSIDE_PACKAGE', path, row['line'])
                    continue
                anchor = package[:len(package) - row['level'] + 1]
                targets = ['.'.join([*anchor, *([row['module']] if row['module'] else [])])]
            for target in targets:
                root = target.split('.')[0]
                if target not in available and root in roots | normalized_roots:
                    issue('MISSING_LOCAL_IMPORT', path, row['line'], module=target, delayed=row['delayed'])
                elif target not in available and root not in stdlib:
                    issue('EXTERNAL_IMPORT_UNVERIFIED', path, row['line'], module=target, delayed=row['delayed'])
                if not row['delayed']:
                    if target in modules:
                        edges[name].add(target)
                    if row['kind'] == 'from':
                        edges[name].update(target + '.' + item for item in row['names']
                                           if target + '.' + item in modules)
        for row in fact['resources']:
            package, resource = row['package'], row['resource']
            if (not package or package not in packages or resource is None
                    or row['api'].split('.')[0] in roots):
                issue('RESOURCE_REFERENCE_UNRESOLVED', path, row['line'], api=row['api'], package=package)
                continue
            base = package.replace('.', '/')
            if package in packages and base + '/__init__.py' not in paths:
                issue('ZIP_NAMESPACE_RESOURCE', path, row['line'], package=package)
            if not resource:
                continue
            relative = PurePosixPath(resource)
            if relative.is_absolute() or '..' in relative.parts or '\\' in resource:
                issue('RESOURCE_REFERENCE_UNRESOLVED', path, row['line'], api=row['api'], package=package)
            elif base + '/' + relative.as_posix() not in all_paths:
                issue('MISSING_PACKAGE_RESOURCE', path, row['line'], package=package, resource=resource)
    for group in _cycles(edges):
        issue('EAGER_IMPORT_CYCLE', modules[group[0]], modules=group)
    issues.sort(key=lambda row: (row['path'], row['line'] or 0, row['code'], canonical(row)))
    incomplete = omitted > 0 or any(row['code'].startswith('PREFLIGHT_') for row in issues)
    return {
        'schema': 1, 'version': VERSION,
        'status': 'INCOMPLETE' if incomplete else 'REVIEW_REQUIRED' if issues else 'NO_ISSUES_OBSERVED',
        'source_code_executed': False, 'runtime_validation': 'NOT_RUN',
        'python_version': '.'.join(map(str, sys.version_info[:3])),
        'files': [{'path': fact['path'], 'sha256': fact['sha256'], 'raw_compilation': fact['raw_compilation']}
                  for fact in facts],
        'issues': issues, 'omitted_issues': omitted,
        'limits': {'ast_nodes_per_module': 60_000, 'references_per_kind_per_module': MAX_REFERENCES,
                   'issues': MAX_ISSUES, 'wall_seconds': MAX_SECONDS},
        'limitations': [
            'Advisory static packaging review on this Python version; no product code was executed.',
            'Imports may be conditional. Eager cycles are syntax leads, not proven runtime failures.',
            'External availability, imported attributes, dynamic imports/resources, rebinding and runtime paths are not resolved.',
            'Standard-library names describe this interpreter; submodule availability and another operating system are not verified.',
            'Resource checks cover literal importlib.resources and pkgutil calls with unambiguous module-level import aliases.',
            'Resource objects stored in variables and function-local resource imports are not followed.',
            'No issues observed does not establish dependency closure, portability or successful execution.',
        ],
    }


def run(home, modules, assets, entry):
    files = {**modules, '__main__.py': entry}
    facts = []
    deadline = time.monotonic() + MAX_SECONDS
    for path, data in sorted(files.items()):
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Blocked('TOOL_TIMEOUT')
            raw = run_fixed([sys.executable, '-I', '-B', str(ROOT/'forge_core/quest_preflight_worker.py'), path],
                            home, min(10, remaining), data, 2_000_000)
            fact = loads(raw)
            if (not isinstance(fact, dict) or set(fact) != {'path', 'sha256', 'imports', 'resources', 'gaps',
                                                          'raw_compilation', 'source_code_executed'}
                    or fact['path'] != path or fact['sha256'] != sha(data) or fact['source_code_executed'] is not False
                    or any(not isinstance(fact[key], list) for key in ('imports', 'resources', 'gaps'))):
                raise Blocked('QUEST_PREFLIGHT_BINDING')
        except Blocked as error:
            fact = {'path': path, 'sha256': sha(data), 'imports': [], 'resources': [],
                    'raw_compilation': 'NOT_CHECKED', 'source_code_executed': False,
                    'gaps': [{'code': 'PREFLIGHT_WORKER_UNAVAILABLE', 'reason': str(error)}]}
        facts.append(fact)
    return review(facts, assets)


def summary(report, raw):
    return {'status': report['status'], 'report_sha256': sha(raw), 'files': len(report['files']),
            'issues': len(report['issues']), 'omitted_issues': report['omitted_issues'],
            'source_code_executed': False, 'runtime_validation': 'NOT_RUN'}
