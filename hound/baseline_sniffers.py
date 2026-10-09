"""Read-only, source-bound structural code finders. No target code is executed.

This is a candidate detector, not a correctness/security proof or a sandbox.
Only explicit, reviewed Python files are accepted by the CLI. No crawling,
network requests, imports of target modules, automatic installation, or fixes.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterator

VERSION = '0.1.0'
MAX_SOURCE_BYTES = 256_000
MAX_NODES = 50_000


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def scoped_nodes(statements: list[ast.stmt]) -> Iterator[ast.AST]:
    """Nodes in this function body, excluding bodies of nested definitions."""
    pending: list[ast.AST] = list(reversed(statements))
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        pending.extend(reversed(list(ast.iter_child_nodes(node))))


def functions(tree: ast.AST) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    def visit(node: ast.AST, parents: tuple[str, ...]):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = '.'.join((*parents, child.name))
                yield name, child
                yield from visit(child, (*parents, child.name, '<locals>'))
            elif isinstance(child, ast.ClassDef):
                yield from visit(child, (*parents, child.name))
            else:
                yield from visit(child, parents)
    yield from visit(tree, ())


def imported_aliases(statements: list[ast.stmt]) -> dict[str, str]:
    """Only unconditional imports directly in the given body are resolved."""
    aliases: dict[str, str] = {}
    for s in statements:
        if isinstance(s, ast.Import):
            for a in s.names:
                aliases[a.asname or a.name.split('.')[0]] = a.name if a.asname else a.name.split('.')[0]
        elif isinstance(s, ast.ImportFrom) and s.level == 0 and s.module:
            for a in s.names:
                if a.name != '*':
                    aliases[a.asname or a.name] = s.module + '.' + a.name
    return aliases


def bindings(nodes: Iterator[ast.AST] | list[ast.AST]) -> set[str]:
    result: set[str] = set()
    for n in nodes:
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            result.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            result.add(n.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            result.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                result.add(a.asname or a.name.split('.')[0])
    return result


def alias_map(tree: ast.Module, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    aliases = imported_aliases(tree.body)
    # Conservative: any non-import rebinding at module level suppresses resolution.
    module_nonimports = [s for s in tree.body if not isinstance(s, (ast.Import, ast.ImportFrom))]
    for name in bindings(scoped_nodes(module_nonimports)):
        aliases.pop(name, None)
    nodes = list(scoped_nodes(fn.body))
    local_imports = imported_aliases(fn.body)
    args = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
    if fn.args.vararg: args.append(fn.args.vararg)
    if fn.args.kwarg: args.append(fn.args.kwarg)
    blocked = bindings(nodes) | {a.arg for a in args}
    for name in blocked:
        aliases.pop(name, None)
    # A local import is allowed only without another binding anywhere in the scope.
    nonimports = [n for n in nodes if not isinstance(n, (ast.Import, ast.ImportFrom))]
    rebound = bindings(nonimports) | {a.arg for a in args}
    for name, target in local_imports.items():
        if name not in rebound:
            aliases[name] = target
    return aliases


def dotted(n: ast.AST) -> str | None:
    if isinstance(n, ast.Name): return n.id
    if isinstance(n, ast.Attribute):
        base = dotted(n.value)
        return base + '.' + n.attr if base else None
    return None


def resolve(n: ast.AST, aliases: dict[str, str]) -> str | None:
    name = dotted(n)
    if not name: return None
    head, sep, tail = name.partition('.')
    if head not in aliases: return None
    return aliases[head] + (sep + tail if sep else '')


def evidence(node: ast.AST, observation: str) -> dict[str, Any]:
    return {'line_start': node.lineno, 'line_end': getattr(node, 'end_lineno', node.lineno),
            'observation': observation}


def direct_raise(body: list[ast.stmt]) -> bool:
    return any(isinstance(n, ast.Raise) for n in body)


def read_profiles(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, list) or not value:
        raise ValueError('A nonempty profile list is required')
    ids: set[str] = set()
    for p in value:
        if not isinstance(p, dict) or not all(k in p for k in ('id', 'version', 'detector', 'purpose', 'limitations')):
            raise ValueError('Malformed profile')
        if p['id'] in ids: raise ValueError('Duplicate profile ID')
        ids.add(p['id'])
        if p['detector'] not in ('literal_call_contract', 'unit_finite_guard', 'resolved_path_guard'):
            raise ValueError('Unknown detector; no arbitrary plugin execution is allowed')
        if p['detector'] == 'literal_call_contract':
            if not isinstance(p.get('target'), str) or not isinstance(p.get('required_keywords'), dict):
                raise ValueError('Call profile requires target and keyword contract')
            if not p['required_keywords'] or any(type(v) is not bool for v in p['required_keywords'].values()):
                raise ValueError('Only explicit boolean keyword contracts are supported')
    return value


def call_findings(fn, aliases, profile):
    for n in scoped_nodes(fn.body):
        if not isinstance(n, ast.Call) or resolve(n.func, aliases) != profile['target']:
            continue
        kws = {k.arg: k.value for k in n.keywords if k.arg}
        missing: list[str] = []
        conflicts: list[str] = []
        for key, wanted in profile['required_keywords'].items():
            v = kws.get(key)
            if isinstance(v, ast.Constant) and type(v.value) is bool:
                if v.value is not wanted: conflicts.append(key)
            else: missing.append(key)
        if any(k.arg is None for k in n.keywords): missing.append('expanded_keyword_arguments')
        status = ('CONTRADICTED_CALL_CONTRACT' if conflicts else
                  'NEEDS_CONTEXT' if missing else 'STATIC_CANDIDATE')
        yield {'status': status,
               'evidence': [evidence(n, 'Resolved imported call: ' + profile['target'])],
               'matching_keywords': {k: wanted for k, wanted in profile['required_keywords'].items()
                                     if k not in missing and k not in conflicts},
               'contradicted_keywords': conflicts, 'unresolved': missing,
               'scope_note': 'Applies only to this call expression, not the whole function or its runtime behavior.'}


def unit_findings(fn, aliases, profile):
    nodes = list(scoped_nodes(fn.body))
    unit_nodes = []
    finite_nodes = []
    for n in nodes:
        if not isinstance(n, ast.If) or not direct_raise(n.body): continue
        for x in ast.walk(n.test):
            if (isinstance(x, ast.Compare) and any(isinstance(op, ast.NotIn) for op in x.ops)
                and isinstance(x.left, ast.Call) and isinstance(x.left.func, ast.Attribute)
                and x.left.func.attr == 'get' and x.left.args
                and isinstance(x.left.args[0], ast.Constant) and x.left.args[0].value == 'unit'):
                unit_nodes.append(evidence(n, 'Unit-membership comparison in a raising conditional'))
            if isinstance(x, ast.Call) and resolve(x.func, aliases) in ('math.isfinite', 'numpy.isfinite'):
                finite_nodes.append(evidence(n, 'Finite-number check in a raising conditional'))
    if unit_nodes and finite_nodes:
        yield {'status': 'STATIC_CANDIDATE', 'evidence': unit_nodes + finite_nodes,
               'unresolved': ['Membership and finiteness checks are not a physical-unit conversion or dimensional-analysis proof.'],
               'scope_note': 'Co-occurring syntax only; no general control-flow or data-flow proof.'}


def path_findings(fn, aliases, profile):
    # Deliberately narrow idiom: x = ...resolve(); if not x.is_relative_to(root): raise.
    assignments: dict[str, ast.AST] = {}
    for n in scoped_nodes(fn.body):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            if isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == 'resolve':
                assignments[n.targets[0].id] = n
        if not isinstance(n, ast.If) or not direct_raise(n.body) or not isinstance(n.test, ast.UnaryOp): continue
        if not isinstance(n.test.op, ast.Not) or not isinstance(n.test.operand, ast.Call): continue
        call = n.test.operand
        if not isinstance(call.func, ast.Attribute) or call.func.attr != 'is_relative_to': continue
        receiver = call.func.value
        if isinstance(receiver, ast.Name) and receiver.id in assignments and assignments[receiver.id].lineno < n.lineno:
            yield {'status': 'STATIC_CANDIDATE',
                   'evidence': [evidence(assignments[receiver.id], 'Resolved-path assignment'),
                                evidence(n, 'Negative containment check with direct raise')],
                   'unresolved': ['Receiver type, reassignments, all execution paths, callees, and concurrent path changes need review.'],
                   'scope_note': 'A containment-guard idiom was found, not proof of filesystem security.'}


DETECTORS = {'literal_call_contract': call_findings, 'unit_finite_guard': unit_findings,
             'resolved_path_guard': path_findings}


def scan_bytes(data: bytes, *, source_id: str, profiles: list[dict[str, Any]]) -> dict[str, Any]:
    if len(data) > MAX_SOURCE_BYTES: raise ValueError('Source exceeds the inspection byte limit')
    if not source_id or len(source_id) > 2000: raise ValueError('Invalid source identity')
    tree = ast.parse(data, filename='<reviewed-source>')
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES: raise ValueError('AST exceeds node limit')
    source_sha = hashlib.sha256(data).hexdigest()
    found = []
    fn_count = 0
    for name, fn in functions(tree):
        fn_count += 1
        aliases = alias_map(tree, fn)
        if '<locals>' in name:
            # Do not guess closed-over bindings from enclosing functions.
            # Only explicit imports directly in this nested function survive.
            local_imports = imported_aliases(fn.body)
            aliases = {k: v for k, v in aliases.items() if local_imports.get(k) == v}
        fid = digest({'source_id': source_id, 'qualified_name': name, 'definition_line': fn.lineno})
        for p in profiles:
            for match in DETECTORS[p['detector']](fn, aliases, p):
                record = {'profile_id': p['id'], 'profile_version': p['version'],
                          'profile_sha256': digest(p), 'detector_version': VERSION,
                          'source_id': source_id, 'source_sha256': source_sha,
                          'function_id': fid, 'qualified_name': name,
                          'function_lines': [fn.lineno, fn.end_lineno],
                          'limitations': p['limitations'], 'runtime_verified': False, **match}
                record['finding_id'] = digest(record)
                found.append(record)
    return {'source_id': source_id, 'source_sha256': source_sha, 'source_bytes': len(data),
            'functions_inspected': fn_count, 'inspection': 'STATIC_ONLY', 'findings': found}


def scan_file(path: Path, *, source_id: str, profiles_path: Path, timeout: float = 5.0) -> dict[str, Any]:
    """Bounded subprocess; child never imports the target. Not a security sandbox."""
    if path.is_symlink() or not path.is_file(): raise ValueError('Explicit regular source file required')
    if path.stat().st_size > MAX_SOURCE_BYTES: raise ValueError('Source exceeds limit')
    data = path.read_bytes()
    before = hashlib.sha256(data).hexdigest()
    packet = {'source_id': source_id, 'source_hex': data.hex(), 'profiles': read_profiles(profiles_path)}
    proc = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--worker'],
                          input=json.dumps(packet), text=True, capture_output=True,
                          timeout=timeout, check=False)
    if proc.returncode != 0: raise ValueError('Parser worker failed; see scope and input validity')
    result = json.loads(proc.stdout)
    # Detect change during inspection; do not update or repair the source.
    if hashlib.sha256(path.read_bytes()).hexdigest() != before:
        raise ValueError('Source changed during inspection')
    result['source_preserved'] = True
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--file', type=Path)
    p.add_argument('--source-id')
    p.add_argument('--profiles', type=Path, default=Path(__file__).with_name('profiles.json'))
    args = p.parse_args()
    try:
        if args.worker:
            request = json.loads(sys.stdin.read(MAX_SOURCE_BYTES * 3))
            result = scan_bytes(bytes.fromhex(request['source_hex']), source_id=request['source_id'],
                                profiles=request['profiles'])
        else:
            if not args.file or not args.source_id:
                p.error('--file and --source-id are required')
            result = scan_file(args.file, source_id=args.source_id, profiles_path=args.profiles)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (ValueError, SyntaxError, OSError, RecursionError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'status': 'INSPECTION_FAILED', 'error_type': type(exc).__name__,
                          'runtime_verified': False}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
