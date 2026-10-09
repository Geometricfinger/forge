"""Source-bound lexical dependencies. Does not execute or resolve runtime dispatch.

Calls, imports and annotations describe syntax. A link is a context lead, not a
proof that a callee executes. Adjacent transcript blocks/cells are never merged.
"""
from __future__ import annotations
import ast
from pathlib import PurePosixPath
from .common import Blocked
from .lexical import counts

VERSION = 'lexical-context-2-class-state'
MAX_CALLS = 128

def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted(node.value)
        return parent + '.' + node.attr if parent else None
    return None

def own_nodes(nodes):
    stack = list(reversed(nodes))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))

def metadata(nodes, parameters=()):
    calls, imports, stores = [], [], set(parameters)
    attributes, comparisons = [], []
    for node in own_nodes(nodes):
        if isinstance(node, ast.AugAssign) and isinstance(node.target,ast.Attribute):
            name=dotted(node.target)
            if name and len(name)<=240:
                attributes.append({'name':name,'line':node.lineno,'evidence':'AUGMENTED_TARGET_READ_SYNTAX_NOT_RUNTIME_READ'})
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            name = dotted(node)
            if name and len(name) <= 240:
                attributes.append({'name': name, 'line': node.lineno,
                                   'evidence': 'ATTRIBUTE_LOAD_SYNTAX_NOT_RUNTIME_READ'})
        if isinstance(node, ast.Compare):
            comparisons.append({'line': node.lineno,
                                'operators': [type(op).__name__ for op in node.ops],
                                'evidence': 'COMPARISON_SYNTAX_NOT_ENFORCED_CONTRACT'})
        if isinstance(node, ast.Call):
            name = dotted(node.func)
            if (isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Call)
                and isinstance(node.func.value.func,ast.Name) and node.func.value.func.id=='super'
                and not node.func.value.args and not node.func.value.keywords):
                name='super().'+node.func.attr
            if name and len(name) <= 240:
                calls.append({'name': name, 'line': node.lineno,
                              'evidence': 'CALL_SYNTAX_NOT_RUNTIME_RESOLUTION'})
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({'module': alias.name, 'symbol': None, 'level': 0,
                                'alias': alias.asname or alias.name.split('.')[0],
                                'line': node.lineno, 'kind': 'import'})
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imports.append({'module': node.module or '', 'symbol': alias.name,
                                'level': node.level, 'alias': alias.asname or alias.name,
                                'line': node.lineno, 'kind': 'from'})
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            stores.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stores.add(node.name)
    return {'calls': calls[:MAX_CALLS], 'imports': imports[:MAX_CALLS],
            'shadowed_roots': sorted(stores),
            'attribute_reads': attributes[:MAX_CALLS], 'comparisons': comparisons[:MAX_CALLS],
            'truncated': any(len(x) > MAX_CALLS for x in (calls, imports, attributes, comparisons)),
            'runtime_resolution': 'NOT_ESTABLISHED'}

def describe(tree):
    module = metadata(tree.body)
    module['class_definitions'] = []
    bindings={}
    for n in own_nodes(tree.body):
        name=(n.id if isinstance(n,ast.Name) and isinstance(n.ctx,(ast.Store,ast.Del)) else
              n.name if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)) else None)
        if name:bindings[name]=bindings.get(name,0)+1
    module['top_level_binding_counts']=bindings
    definitions = {}
    class Walker(ast.NodeVisitor):
        def __init__(self):
            self.parents = []; self.functions = []; self.classes = []; self.kinds = []
        def visit_ClassDef(self, node):
            name = '.'.join([*self.parents, node.name])
            doc = node.body[0] if (node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)) else None
            record = {'name': name, 'start_line': node.lineno, 'end_line': node.end_lineno,
                      'documentation_start_line': doc.lineno if doc else None,
                      'documentation_end_line': doc.end_lineno if doc else None,
                      'documentation_terms': counts(ast.get_docstring(node) or ''),
                      'bases': [dotted(x) for x in node.bases[:8]],
                      'bases_truncated': len(node.bases) > 8,
                      'decorated': bool(node.decorator_list), 'keywords_present': bool(node.keywords),
                      'top_level_unconditional':node in tree.body,
                      'evidence': 'CLASS_DOCUMENTATION_NOT_BEHAVIOR_PROOF',
                      'runtime_resolution': 'NOT_ESTABLISHED'}
            module['class_definitions'].append(record)
            self.parents.append(node.name); self.classes.append(record); self.kinds.append('class')
            for child in node.body: self.visit(child)
            self.kinds.pop(); self.classes.pop(); self.parents.pop()
        def visit_FunctionDef(self, node):
            name = '.'.join([*self.parents, node.name])
            params = [x.arg for x in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]]
            if node.args.vararg: params.append(node.args.vararg.arg)
            if node.args.kwarg: params.append(node.args.kwarg.arg)
            owner = self.classes[-1] if self.kinds and self.kinds[-1] == 'class' else None
            role = ('CONSTRUCTOR' if node.name in ('__init__', '__new__') else
                    'CALL_OPERATION' if node.name == '__call__' else 'METHOD') if owner else 'FUNCTION'
            body=[x for x in node.body if not (isinstance(x,ast.Expr) and isinstance(x.value,ast.Constant) and isinstance(x.value.value,str))]
            stub=not body or all(isinstance(x,ast.Pass) or (isinstance(x,ast.Expr) and isinstance(x.value,ast.Constant) and x.value.value is Ellipsis) or (isinstance(x,ast.Raise) and ((isinstance(x.exc,ast.Name) and x.exc.id=='NotImplementedError') or (isinstance(x.exc,ast.Call) and dotted(x.exc.func)=='NotImplementedError'))) for x in body)
            m = metadata(node.body, params)
            m['implementation_kind']='DECLARATION_OR_STUB' if stub else 'BODY_PRESENT_NOT_VERIFIED'
            m.update({'enclosing_functions': list(self.functions),
                      'enclosing_class': self.classes[-1]['name'] if self.classes else None,
                      'class_context': owner, 'method_role': role,
                      'method_role_evidence': 'DIRECT_DEFINITION_NAME_AND_ENCLOSING_SCOPE_NOT_RUNTIME_SEMANTICS',
                      'parameter_roots': params,
                      'term_frequencies': {
                          'symbol': counts(node.name),
                          'documentation': counts(ast.get_docstring(node) or ''),
                          'parameters': counts(' '.join(params))}})
            definitions[(name, node.lineno)] = m
            self.parents.append(node.name); self.functions.append(name); self.kinds.append('function')
            for child in node.body: self.visit(child)
            self.kinds.pop(); self.functions.pop(); self.parents.pop()
        visit_AsyncFunctionDef = visit_FunctionDef
    Walker().visit(tree)
    return module, definitions

def declared_apis(meta, module):
    """Unverified import-based leads only; rebinding/ambiguous aliases are withheld."""
    by_alias = {}
    for row in module.get('imports', []) + meta.get('imports', []):
        by_alias.setdefault(row['alias'], []).append(row)
    shadow = set(meta.get('shadowed_roots', [])) | set(module.get('shadowed_roots', []))
    output = []
    for call in meta.get('calls', []):
        pieces = call['name'].split('.'); alias = pieces[0]; rows = by_alias.get(alias, [])
        if alias in shadow or len(rows) != 1 or rows[0]['symbol'] == '*': continue
        imp = rows[0]
        # Local imports appearing after a call are not candidate evidence for it.
        if imp in meta.get('imports', []) and imp['line'] >= call['line']: continue
        if imp['kind'] == 'import' and imp['alias'] == imp['module'].split('.')[0]:
            resolved = call['name']
        else:
            base = '.' * imp['level'] + imp['module']
            if imp['symbol']: base += ('.' if imp['module'] else '') + imp['symbol']
            resolved = '.'.join([base, *pieces[1:]])
        output.append({'name': resolved, 'line': call['line'], 'import_line': imp['line'],
                       'evidence': 'DECLARED_IMPORT_CALL_LEAD_NOT_EXECUTED'})
    return output

def _module_path(segment):
    value = segment.get('logical_path_hint') or segment['path']
    path = PurePosixPath(value)
    if path.suffix not in ('.py', '.pyi', '.pyw'): return None
    return path

def _namespace(source, segment):
    chain = segment.get('member_chain', [])
    # Last member is this file, earlier members identify its archive namespace.
    return (source['file_id'], source['sha256'],
            tuple((x['path'], x['sha256']) for x in chain[:-1]))

def _super_leads(call,meta,module,segment,universe):
    reason='SUPER_RUNTIME_MRO_UNRESOLVED'
    owner=meta.get('class_context'); member=call['name'][8:]
    if not owner or meta.get('parameter_roots',[])[:1]!=['self'] or 'super' in meta.get('shadowed_roots',[]) or 'super' in module.get('shadowed_roots',[]) or any(i['alias']=='super' for i in module.get('imports',[])+meta.get('imports',[])):
        return [],reason
    classes=module.get('class_definitions',[]); current=owner; seen=set()
    for _ in range(8):
        if current['name'] in seen:return [],reason
        seen.add(current['name'])
        if current.get('decorated') or current.get('keywords_present') or not current.get('top_level_unconditional') or current.get('bases_truncated') or len(current.get('bases',[]))!=1:return [],reason
        base=current['bases'][0]
        if not base or '.' in base or module.get('top_level_binding_counts',{}).get(base)!=1 or any(i['alias']==base for i in module.get('imports',[])):return [],reason
        parents=[c for c in classes if c['name']==base and c['start_line']<current['start_line'] and c.get('top_level_unconditional')]
        if len(parents)!=1:return [],reason
        parent=parents[0]
        if parent.get('decorated') or parent.get('keywords_present'):return [],reason
        candidates=[(src,seg,d) for src,seg,d in universe if seg['source_id']==segment['source_id'] and d['name']==base+'.'+member]
        if candidates:return candidates,'DECLARED_SINGLE_BASE_CANDIDATE_RUNTIME_MRO_UNRESOLVED'
        current=parent
    return [],'SUPER_CONTEXT_DEPTH_LIMIT'

def context(report, definition_id, max_references=64):
    if type(max_references) is not int or not 1 <= max_references <= MAX_CALLS:
        raise Blocked('CONTEXT_LIMIT')
    found = []
    for source in report.get('sources', []):
        for segment in source['segments']:
            for definition in segment['definitions']:
                if definition.get('definition_id') == definition_id:
                    found.append((source['source'], segment, definition))
    if len(found) != 1: raise Blocked('CONTEXT_ID_NOT_UNIQUE_OR_MISSING')
    source, segment, definition = found[0]
    base = {'schema': 1, 'definition_id': definition_id, 'source_id': segment['source_id'],
            'source_sha256': segment['source_sha256'], 'container_sha256': source['sha256'],
            'name': definition['name'], 'references': [], 'runtime_resolution': 'NOT_ESTABLISHED',
            'dependency_closure_complete': False, 'source_code_executed': False,
            'release_approved': False,
            'limitations': ['Lexical candidate edges, not a call graph proven at runtime.',
                            'No cross-container, cross-notebook-cell or installed dependency inference.',
                            'Decorators, conditional bindings, inheritance and dynamic dispatch need review.']}
    if 'structure' not in definition:
        return base | {'status': 'STRUCTURAL_CONTEXT_NOT_CAPTURED_REINDEX_REQUIRED'}
    meta = definition['structure']; mod = segment.get('module_structure', {})
    path = _module_path(segment)
    universe = []
    for source_row in report['sources']:
        src = source_row['source']
        for seg in source_row['segments']:
            if _namespace(src, seg) != _namespace(source, segment): continue
            if seg.get('format') == 'notebook_cell' and seg['source_id'] != segment['source_id']: continue
            for d in seg['definitions']:
                universe.append((src, seg, d))
    imports = {}
    for imp in mod.get('imports', []) + meta.get('imports', []):
        imports.setdefault(imp['alias'], []).append(imp)
    shadows = set(meta.get('shadowed_roots', [])) | set(mod.get('shadowed_roots', []))
    for call in meta.get('calls', [])[:max_references]:
        parts = call['name'].split('.'); root = parts[0]; candidates = []; reason = 'UNRESOLVED_OR_EXTERNAL'
        ims = imports.get(root, [])
        if call['name'].startswith('super().'):
            candidates,reason=_super_leads(call,meta,mod,segment,universe)
        elif ims:
            if root in shadows or len(ims) != 1 or ims[0]['symbol'] == '*':
                reason = 'AMBIGUOUS_OR_REBOUND_IMPORT'
            else:
                imp = ims[0]
                if imp in meta.get('imports', []) and imp['line'] >= call['line']:
                    reason = 'IMPORT_AFTER_CALL_REQUIRES_REVIEW'
                else:
                    # A from-import points at an explicit module and its symbol.
                    module = imp['module']; target = '.'.join(parts[1:])
                    if imp['kind'] == 'from':
                        if not module and target:
                            module = imp['symbol']
                        else:
                            target = '.'.join([imp['symbol'], *parts[1:]])
                    elif imp['alias'] == module.split('.')[0] and '.' in module:
                        target = '.'.join(parts[len(module.split('.')):])
                    candidates_to_check = [(module, target)]
                    if imp['kind'] == 'from' and imp['module'] and len(parts) > 1:
                        # ``from pkg import b; b.h()`` may refer to a submodule
                        # or an exported class/object. Keep both source leads.
                        candidates_to_check.append((imp['module'] + '.' + imp['symbol'], '.'.join(parts[1:])))
                    if imp['level'] and (path is None or imp['level'] > len(path.parent.parts)):
                        candidates_to_check = []
                    seen_targets = set()
                    for module_name, target_name in candidates_to_check:
                        wanted = None
                        if imp['level']:
                            parent = path.parent
                            for _ in range(imp['level'] - 1): parent = parent.parent
                            wanted = (parent / module_name.replace('.', '/')).as_posix()
                        suffix = module_name.replace('.', '/')
                        for src, seg, d in universe:
                            p = _module_path(seg)
                            if not p or d['name'] != target_name: continue
                            stem = p.with_suffix('').as_posix()
                            match = stem in (wanted, (wanted or '') + '/__init__') if wanted is not None else (
                                stem in (suffix, suffix + '/__init__') or stem.endswith('/' + suffix) or stem.endswith('/' + suffix + '/__init__'))
                            if match and d['definition_id'] not in seen_targets:
                                candidates.append((src, seg, d)); seen_targets.add(d['definition_id'])
                    reason = 'DECLARED_IMPORT_CANDIDATE' if candidates else 'DECLARED_IMPORT_TARGET_NOT_IN_SNAPSHOT'
        elif len(parts) == 1:
            # A stored local/parameter value must not be mistaken for a callable peer.
            if root in meta.get('parameter_roots', []) or root in meta.get('shadowed_roots', []) and not any(
                    d['name'] == definition['name'] + '.' + root for _, seg, d in universe if seg['source_id'] == segment['source_id']):
                reason = 'LOCAL_OR_PARAMETER_BINDING_UNRESOLVED'
            else:
                names = [definition['name'] + '.' + root]
                names += [p + '.' + root for p in reversed(meta.get('enclosing_functions', []))]
                names.append(root)
                for name in names:
                    candidates = [(src, seg, d) for src, seg, d in universe
                                  if seg['source_id'] == segment['source_id'] and d['name'] == name]
                    if candidates: break
                reason = 'LEXICAL_DEFINITION_CANDIDATE' if candidates else reason
        elif root == 'self' and root in meta.get('parameter_roots', []) and meta.get('enclosing_class'):
            name = meta['enclosing_class'] + '.' + '.'.join(parts[1:])
            candidates = [(src, seg, d) for src, seg, d in universe
                          if seg['source_id'] == segment['source_id'] and d['name'] == name]
            reason = 'SAME_CLASS_CANDIDATE_DYNAMIC_DISPATCH_UNRESOLVED' if candidates else reason
        refs = [{'definition_id': d['definition_id'], 'name': d['name'],
                 'source_id': seg['source_id'], 'source_sha256': seg['source_sha256'],
                 'file_id': src['file_id'], 'path': str(_module_path(seg) or seg['path']),
                 'start_line': d.get('member_start_line', d['start_line']),
                 'end_line': d.get('member_end_line', d['end_line'])} for src, seg, d in candidates]
        base['references'].append({'call': call['name'], 'line': segment['start_line'] + call['line'] - 1,
                                   'status': 'AMBIGUOUS_CANDIDATES' if len(refs) > 1 else reason,
                                   'candidates': refs, 'callee_execution_proven': False})
    return base | {'status': 'STATIC_CONTEXT_LEADS',
                   'references_truncated': meta.get('truncated', False) or len(meta.get('calls', [])) > max_references}
