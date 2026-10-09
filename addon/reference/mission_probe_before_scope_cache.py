"""Opt-in, data-configured API discovery using the unchanged Hound 0.5 engine.

Target profiles are data, not executable plugins or authorization to install APIs.
Results are declared-import call observations, not runtime/correctness claims.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import symtable

VERSION = '0.1.0'
PROFILE = 'forge.configured_api_observations'
ROOT = Path(__file__).resolve().parents[1]
MAX_SOURCE = 250_000
MAX_PACKET = 900_000
MAX_FINDINGS = 2000


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def strict_json(data):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError('DUPLICATE_JSON_KEY')
            out[key] = value
        return out
    def reject(value):
        raise ValueError('NONFINITE_JSON')
    return json.loads(data, object_pairs_hook=pairs, parse_constant=reject)


def validate_profile(profile):
    if not isinstance(profile, dict) or set(profile) != {'schema', 'id', 'title', 'targets'}:
        raise ValueError('PROFILE_SCHEMA')
    if type(profile['schema']) is not int or profile['schema'] != 1:
        raise ValueError('PROFILE_VERSION')
    if not isinstance(profile['id'], str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', profile['id']):
        raise ValueError('PROFILE_ID')
    if not isinstance(profile['title'], str) or not 1 <= len(profile['title'].strip()) <= 250:
        raise ValueError('PROFILE_TITLE')
    if not isinstance(profile['targets'], list) or not 1 <= len(profile['targets']) <= 128:
        raise ValueError('PROFILE_TARGET_LIMIT')
    seen = set()
    for row in profile['targets']:
        if not isinstance(row, dict) or set(row) != {'api', 'capability'}:
            raise ValueError('TARGET_SCHEMA')
        api = row['api']
        if not isinstance(api, str) or len(api) > 250 or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+', api, re.ASCII):
            raise ValueError('EXACT_API_REQUIRED')
        if api in seen:
            raise ValueError('DUPLICATE_TARGET')
        seen.add(api)
        if not isinstance(row['capability'], str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', row['capability']):
            raise ValueError('CAPABILITY_ID')
    return profile


def read_regular(path, limit):
    path = Path(path)
    # Trusted static directories only. This is not protection from hostile races.
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('REGULAR_FILE_REQUIRED')
    with path.open('rb') as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ValueError('BYTE_LIMIT')
    return data


def load_hound(root):
    root = Path(root).resolve()
    binding = strict_json((ROOT / 'contracts/hound_binding.json').read_bytes())
    for name, digest in binding.items():
        path = root / name
        if sha(read_regular(path, 500_000)) != digest:
            raise ValueError('HOUND_BINDING_MISMATCH')
    if 'hound' in sys.modules and Path(sys.modules['hound'].__file__).resolve().parent != root:
        raise ValueError('DIFFERENT_HOUND_ALREADY_LOADED')
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import hound
    return hound


class RegistryLibrary:
    def __init__(self, original, profile):
        self.original = original
        self.targets = {r['api']: r['capability'] for r in profile['targets']}

    def __getattr__(self, name):
        return getattr(self.original, name)

    def observe_call(self, flow, node, env, target, receiver, args, keywords, expanded):
        # Uses Hound's existing observed binding; does not trust spelling alone.
        api = target.value if target.kind == 'import' else None
        if api in self.targets:
            flow.add(PROFILE, 'DECLARED_API_CALL_CANDIDATE', node,
                     ['Exact configured API matched a supported declared-import binding.'],
                     resolved_api=api, capability=self.targets[api],
                     selection_basis='DECLARED_IMPORT_PATH',
                     limitations=['Runtime target, dynamic rebinding and dependency behavior are unverified.',
                                  'A named capability is the profile author\'s search label, not proof of implemented behavior.'])
        return self.original.observe_call(flow, node, env, target, receiver, args, keywords, expanded)



def compiler_global_imports(data, tree, fn, name, hound, module_writes):
    """Recover a narrow global-import case omitted for nested functions by Hound.

    CPython's symbol table determines whether the name is global rather than
    a closure or local. Only direct, unique, unassigned module imports qualify.
    This is a lexical candidate, never a guarantee of the runtime global value.
    """
    if '<locals>' not in name:
        return {}
    top = symtable.symtable(data.decode('utf-8'), '<approved-source>', 'exec')
    tables = []
    def walk(t):
        if t.get_name() == fn.name and t.get_lineno() == fn.lineno and t.get_type() == 'function':
            tables.append(t)
        for child in t.get_children():
            walk(child)
    walk(top)
    if len(tables) != 1:
        return {}
    direct = hound.base.imported_aliases([n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))])
    writes = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))}
    # Another definition or import using the same name is treated as ambiguity.
    import_counts = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                alias = a.asname or (a.name.split('.')[0] if isinstance(n, ast.Import) else a.name)
                import_counts[alias] = import_counts.get(alias, 0) + 1
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            writes.add(n.name)
    out = {}
    for symbol in tables[0].get_symbols():
        key = symbol.get_name()
        if (key in direct and key not in writes and key not in module_writes
                and import_counts.get(key) == 1 and symbol.is_global() and not symbol.is_assigned()):
            out[key] = hound.Value('import', direct[key])
    return out


def scan(data, source_id, profile, hound_root):
    profile = validate_profile(profile)
    if not isinstance(data, bytes) or len(data) > MAX_SOURCE:
        raise ValueError('SOURCE_BYTE_LIMIT')
    if not isinstance(source_id, str) or not 1 <= len(source_id) <= 2000:
        raise ValueError('SOURCE_ID')
    hound = load_hound(hound_root)
    tree = ast.parse(data, filename='<approved-source>')
    if sum(1 for _ in ast.walk(tree)) > hound.base.MAX_NODES:
        raise ValueError('AST_NODE_LIMIT')
    import_context = hound.library_rules.resolve_relative_imports(tree, source_id)
    writes = {hound.base.dotted(n).split('.')[0] for n in ast.walk(tree)
              if isinstance(n, ast.Attribute) and isinstance(n.ctx, (ast.Store, ast.Del)) and hound.base.dotted(n)}
    library = RegistryLibrary(hound.library_rules, profile)
    findings = []
    count = 0
    profile_hash = sha(canonical(profile))
    def collect(flow, scope_kind):
        for f in flow.findings:
            if f.get('profile_id') != PROFILE:
                continue
            if len(findings) >= MAX_FINDINGS:
                raise ValueError('FINDING_LIMIT')
            row = dict(f)
            if scope_kind == 'script':
                row.pop('qualified_name', None)
                row['source_scope_lines'] = row.pop('function_lines')
                row['scope_name'] = '<module>'
            row.update(scope_kind=scope_kind, source_id=source_id, source_sha256=sha(data),
                       registry_id=profile['id'], registry_sha256=profile_hash,
                       probe_version=VERSION, probe_code_sha256=sha(Path(__file__).read_bytes()), hound_engine_sha256=hound.engine_digest(),
                       runtime_verified=False, release_approved=False)
            row['finding_id'] = 'probe_' + sha(canonical(row))
            if row['finding_id'] not in {x['finding_id'] for x in findings}:
                findings.append(row)
    for name, fn in hound.base.functions(tree):
        count += 1
        flow = hound.LocalFlow(tree, fn, name, module_writes=writes, library=library)
        flow.env.update(compiler_global_imports(data, tree, fn, name, hound, writes))
        flow.sequence(fn.body, flow.env)
        collect(flow, 'function')
    module = ast.parse('def _scope():\n pass\n').body[0]
    module.body = tree.body
    module.lineno = 1
    module.end_lineno = max(1, len(data.splitlines()))
    flow = hound.LocalFlow(tree, module, '<module>', module_writes=set(), library=library)
    flow.env = {}
    flow.sequence(tree.body, flow.env)
    collect(flow, 'script')
    return {'schema': 1, 'source_id': source_id, 'source_sha256': sha(data),
            'registry_id': profile['id'], 'registry_sha256': profile_hash,
            'hound_engine_sha256': hound.engine_digest(), 'probe_version': VERSION,
            'probe_code_sha256': sha(Path(__file__).read_bytes()),
            'functions_inspected': count, 'findings': findings, 'import_context': import_context,
            'scope': 'Supported local declared-import observations only.',
            'source_body_retained': False, 'upstream_code_executed': False,
            'independent_model_used': False, 'release_approved': False}



def validate_result(result, data, source_id, profile, hound_root):
    """Check receipt consistency, not honesty of an untrusted producer."""
    hound = load_hound(hound_root)
    if not isinstance(result, dict):
        raise ValueError('RESULT_SCHEMA')
    expected = {'source_id': source_id, 'source_sha256': sha(data),
                'registry_id': profile['id'], 'registry_sha256': sha(canonical(profile)),
                'hound_engine_sha256': hound.engine_digest(), 'probe_version': VERSION,
                'probe_code_sha256': sha(Path(__file__).read_bytes())}
    if any(result.get(k) != v for k, v in expected.items()):
        raise ValueError('RESULT_BINDING')
    for flag in ('source_body_retained', 'upstream_code_executed', 'independent_model_used', 'release_approved'):
        if result.get(flag) is not False:
            raise ValueError('UNSUPPORTED_AUTHORITY')
    if type(result.get('functions_inspected')) is not int or result['functions_inspected'] < 0:
        raise ValueError('RESULT_COUNT')
    findings = result.get('findings')
    if not isinstance(findings, list) or len(findings) > MAX_FINDINGS:
        raise ValueError('RESULT_FINDINGS')
    tree = ast.parse(data)
    defs = {(name, fn.lineno, fn.end_lineno) for name, fn in hound.base.functions(tree)}
    if result['functions_inspected'] != len(defs):
        raise ValueError('FUNCTION_COUNT_MISMATCH')
    apis = {r['api']: r['capability'] for r in profile['targets']}
    seen = set()
    for f in findings:
        if not isinstance(f, dict) or f.get('profile_id') != PROFILE or f.get('status') != 'DECLARED_API_CALL_CANDIDATE':
            raise ValueError('FINDING_SCHEMA')
        if any(f.get(k) != v for k, v in expected.items() if k != 'source_id') or f.get('source_id') != source_id:
            raise ValueError('FINDING_BINDING')
        if f.get('resolved_api') not in apis or f.get('capability') != apis[f['resolved_api']]:
            raise ValueError('FINDING_TARGET')
        if f.get('runtime_verified') is not False or f.get('release_approved') is not False:
            raise ValueError('FINDING_AUTHORITY')
        if f.get('scope_kind') == 'function':
            span = f.get('function_lines')
            if not isinstance(span, list) or len(span) != 2 or (f.get('qualified_name'), *span) not in defs:
                raise ValueError('FUNCTION_RANGE')
        elif f.get('scope_kind') == 'script':
            if {'function_lines', 'qualified_name', 'function_id', 'card_id'} & f.keys():
                raise ValueError('INVENTED_SCRIPT_FUNCTION')
            span = f.get('source_scope_lines')
            if span != [1, max(1,len(data.splitlines()))]:
                raise ValueError('SCRIPT_RANGE')
        else:
            raise ValueError('FINDING_SCOPE')
        if not isinstance(f.get('evidence'), list) or not f['evidence']:
            raise ValueError('FINDING_EVIDENCE')
        for e in f['evidence']:
            if (not isinstance(e, dict) or type(e.get('line_start')) is not int or type(e.get('line_end')) is not int
                    or not span[0] <= e['line_start'] <= e['line_end'] <= span[1]):
                raise ValueError('EVIDENCE_RANGE')
        fid = f.get('finding_id')
        if fid != 'probe_' + sha(canonical({k:v for k,v in f.items() if k != 'finding_id'})) or fid in seen:
            raise ValueError('FINDING_DIGEST_OR_DUPLICATE')
        seen.add(fid)
    return result


def isolated_scan(data, source_id, profile, hound_root):
    packet = canonical({'source_hex': data.hex(), 'source_id': source_id, 'profile': profile})
    if len(packet) > MAX_PACKET:
        raise ValueError('PACKET_LIMIT')
    env = {k: os.environ[k] for k in ('PATH', 'LANG', 'TMPDIR') if k in os.environ}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    p = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--worker', '--hound', str(hound_root)],
                       input=packet, capture_output=True, timeout=15, env=env)
    if p.returncode != 0:
        raise ValueError('PROBE_WORKER_FAILED_' + str(p.returncode))
    if len(p.stdout) > 8_000_000:
        raise ValueError('OUTPUT_LIMIT')
    result = strict_json(p.stdout)
    return validate_result(result, data, source_id, profile, hound_root)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--hound', type=Path, required=True)
    p.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--file', type=Path)
    p.add_argument('--source-id')
    p.add_argument('--expected-sha256')
    p.add_argument('--profile', type=Path)
    p.add_argument('--out', type=Path)
    args = p.parse_args()
    try:
        if args.worker:
            if sys.platform.startswith('linux'):
                import resource
                resource.setrlimit(resource.RLIMIT_AS, (768*1024*1024, 768*1024*1024))
                resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
                resource.setrlimit(resource.RLIMIT_NOFILE, (64,64))
            packet = strict_json(sys.stdin.buffer.read(MAX_PACKET + 1))
            result = scan(bytes.fromhex(packet['source_hex']), packet['source_id'], packet['profile'], args.hound)
            sys.stdout.buffer.write(canonical(result))
        else:
            if not all([args.file, args.source_id, args.expected_sha256, args.profile, args.out]):
                raise ValueError('REQUIRED_ARGUMENTS')
            if args.out.exists() or args.out.is_symlink():
                raise ValueError('NEW_OUTPUT_REQUIRED')
            data = read_regular(args.file, MAX_SOURCE)
            if sha(data) != args.expected_sha256:
                raise ValueError('SOURCE_VERSION_MISMATCH')
            profile = validate_profile(strict_json(read_regular(args.profile, 100_000)))
            result = isolated_scan(data, args.source_id, profile, args.hound)
            if sha(read_regular(args.file, MAX_SOURCE)) != args.expected_sha256:
                raise ValueError('SOURCE_CHANGED_DURING_SCAN')
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open('xb') as f:
                f.write(canonical(result))
            print(json.dumps({'findings': len(result['findings']), 'release_approved': False}))
        return 0
    except (ValueError, OSError, SyntaxError, RecursionError, KeyError, TypeError, subprocess.TimeoutExpired):
        print('{"status":"BLOCKED_INSPECTION","details":"Inspect approved inputs and limits; no candidate approval."}', file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
