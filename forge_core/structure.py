"""Source-bound lexical dependencies. Does not execute or resolve runtime dispatch.

Calls, imports and annotations describe syntax. A link is a context lead, not a
proof that a callee executes. Adjacent transcript blocks/cells are never merged.
"""
from __future__ import annotations
import ast
import copy
from pathlib import PurePosixPath
from .common import Blocked
from .lexical import counts

VERSION = 'lexical-context-5-module-envelope'
MAX_CALLS = 128
MAX_DECLARATIONS = 64
MAX_BINDING_CANDIDATES = 8

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

def _declaration_inputs(node, annotations=True):
    """Expressions in the defining scope, never the new function/class body.

    Generic declarations may add annotation scopes; callers keep their effects
    unresolved. Future annotations do not bind names in the defining scope.
    """
    expressions = list(node.decorator_list)
    if isinstance(node, ast.ClassDef):
        return expressions + list(node.bases) + [x.value for x in node.keywords]
    expressions += [*node.args.defaults, *[x for x in node.args.kw_defaults if x is not None]]
    if annotations:
        parameters = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                      *([node.args.vararg] if node.args.vararg else []),
                      *([node.args.kwarg] if node.args.kwarg else [])]
        expressions += [x.annotation for x in parameters if x.annotation is not None]
        if node.returns is not None: expressions.append(node.returns)
    return expressions

def _declaration_writes(nodes, postponed_annotations=False):
    """Name-write syntax omitted by own_nodes' nested-body boundary.

    Shadow guards retain these names even when detailed binding capture reaches
    its limit. Lambda bodies and comprehension loop targets cannot bind here.
    """
    for node in own_nodes(nodes):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stack = _declaration_inputs(node, not postponed_annotations)
        elif isinstance(node, ast.Lambda):
            stack = [*node.args.defaults, *[x for x in node.args.kw_defaults if x is not None]]
        else: continue
        while stack:
            expression = stack.pop()
            if isinstance(expression, ast.Lambda):
                stack.extend([*expression.args.defaults,
                              *[x for x in expression.args.kw_defaults if x is not None]])
                continue
            if isinstance(expression, ast.NamedExpr) and isinstance(expression.target, ast.Name):
                yield expression.target.id
            stack.extend(ast.iter_child_nodes(expression))

def _scope_syntax(nodes, name, kind, line=1, parameters=(), type_parameters=(),
                  postponed_annotations=False):
    """Bounded binding/read syntax for one scope, never a runtime symbol table."""
    result = {'name': name, 'kind': kind, 'line': line, 'bindings': [], 'reads': [],
              'globals': [], 'nonlocals': [], 'truncated': False, 'bindings_truncated': False, 'omissions': [],
              'type_parameters': list(type_parameters)[:MAX_CALLS]}
    if len(type_parameters) > MAX_CALLS:
        result['truncated'] = result['bindings_truncated'] = True
    def add(key, value):
        if len(result[key]) < MAX_CALLS: result[key].append(value)
        else:
            result['truncated'] = True
            if key != 'reads': result['bindings_truncated'] = True
    def binding(local_name, where, binding_kind, **extra):
        if len(local_name) <= 240:
            add('bindings', {'name': local_name, 'line': where, 'kind': binding_kind, **extra})
        else: result['truncated'] = result['bindings_truncated'] = True
    for symbol in parameters: binding(symbol, line, 'parameter')
    stack = [(node, False, False) for node in reversed(nodes)]
    while stack:
        node, comprehension, uncertain_declaration = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            binding(node.name, node.lineno, 'class' if isinstance(node, ast.ClassDef) else 'function')
            generic = bool(getattr(node, 'type_params', ()))
            if generic and 'GENERIC_DECLARATION_SCOPE_NOT_RESOLVED' not in result['omissions']:
                result['omissions'].append('GENERIC_DECLARATION_SCOPE_NOT_RESOLVED')
            for expression in reversed(_declaration_inputs(node, not postponed_annotations)):
                uncertain = generic and expression not in node.decorator_list
                stack.append((expression, comprehension, uncertain_declaration or uncertain))
            continue
        if isinstance(node, ast.Lambda):
            if 'LAMBDA_BODY_NOT_CAPTURED' not in result['omissions']:
                result['omissions'].append('LAMBDA_BODY_NOT_CAPTURED')
            # Lambda defaults execute in the containing scope, unlike its body.
            stack.extend((child, comprehension, uncertain_declaration) for child in reversed(
                [*node.args.defaults, *[x for x in node.args.kw_defaults if x is not None]]))
            continue
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            comprehension = True
            if 'COMPREHENSION_BINDINGS_UNRESOLVED' not in result['omissions']:
                result['omissions'].append('COMPREHENSION_BINDINGS_UNRESOLVED')
        if isinstance(node, ast.Name):
            if len(node.id) > 240:
                result['truncated'] = True
                if not isinstance(node.ctx, ast.Load): result['bindings_truncated'] = True
            elif isinstance(node.ctx, ast.Load):
                add('reads', {'name': node.id, 'line': node.lineno,
                              'scope_uncertain': comprehension,
                              'declaration_scope_uncertain': uncertain_declaration})
            elif not comprehension:
                binding(node.id, node.lineno, 'declaration_binding_effect_unresolved' if uncertain_declaration
                        else 'delete' if isinstance(node.ctx, ast.Del) else 'assignment')
        elif isinstance(node, ast.Import):
            for alias in node.names:
                binding(alias.asname or alias.name.split('.')[0], node.lineno, 'import',
                        module=alias.name[:240], symbol=None, level=0)
                if len(alias.name) > 240: result['truncated'] = result['bindings_truncated'] = True
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                binding(alias.asname or alias.name, node.lineno, 'from_import',
                        module=(node.module or '')[:240], symbol=alias.name[:240], level=node.level)
                if len(node.module or '') > 240 or len(alias.name) > 240:
                    result['truncated'] = result['bindings_truncated'] = True
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for symbol in node.names:
                if len(symbol) <= 240: add('globals' if isinstance(node, ast.Global) else 'nonlocals', symbol)
                else: result['truncated'] = result['bindings_truncated'] = True
        elif isinstance(node, ast.NamedExpr) and comprehension and isinstance(node.target, ast.Name):
            binding(node.target.id, node.target.lineno, 'declaration_binding_effect_unresolved' if uncertain_declaration
                    else 'comprehension_assignment_expression')
        elif isinstance(node, ast.ExceptHandler) and node.name:
            binding(node.name, node.lineno, 'exception_target')
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            binding(node.name, node.lineno, 'pattern_capture')
        elif isinstance(node, ast.MatchMapping) and node.rest:
            binding(node.rest, node.lineno, 'pattern_capture')
        stack.extend((child, comprehension, uncertain_declaration)
                     for child in reversed(list(ast.iter_child_nodes(node))))
    return result

def _scope_chain(scopes):
    # A method body does not close over class-local names. A declaration directly
    # in a class body can nevertheless read that class namespace.
    return [scope for i, scope in enumerate(reversed(scopes))
            if i == 0 or scope['kind'] != 'class']

def _read_context(reads, scopes, *, declaration=False, uncertain_names=()):
    references = []; truncated = False
    for read in reads[:MAX_CALLS]:
        name = read['name']; candidates = []; status = 'UNRESOLVED_OR_BUILTIN_OR_EXTERNAL'
        chain = list(scopes)
        explicit_nonlocal = bool(chain and name in chain[0]['nonlocals'])
        if read.get('scope_uncertain'):
            status = 'COMPREHENSION_SCOPE_UNRESOLVED'; chain = []
        elif read.get('declaration_scope_uncertain'):
            status = 'DECLARATION_SCOPE_UNRESOLVED'; chain = []
        elif name in uncertain_names or any(name in scope['type_parameters'] for scope in chain):
            status = 'TYPE_PARAMETER_SCOPE_UNRESOLVED'; chain = []
        elif chain and name in chain[0]['globals']:
            chain = [scope for scope in chain if scope['kind'] == 'module']
            status = 'GLOBAL_BINDING_NOT_CAPTURED'
        elif chain and name in chain[0]['nonlocals']:
            chain = [scope for scope in chain[1:] if scope['kind'] == 'function']
            status = 'NONLOCAL_BINDING_NOT_CAPTURED'
        for scope in chain:
            # A truncated scope could contain a closer binding or a global/
            # nonlocal declaration; do not infer through that missing evidence.
            if scope['bindings_truncated']:
                status = 'BINDING_CAPTURE_TRUNCATED'; truncated = True; break
            if any(x['kind'] == 'from_import' and x.get('symbol') == '*' for x in scope['bindings']):
                status = 'WILDCARD_IMPORT_BINDING_UNRESOLVED'; break
            if name in scope['globals'] and scope['kind'] != 'module':
                if explicit_nonlocal:
                    status = 'NONLOCAL_BINDING_NOT_CAPTURED'; break
                # An enclosing global declaration prevents capture from a
                # still-outer function, even if the module binding is absent.
                module = next((x for x in scopes if x['kind'] == 'module'), None)
                if module is None:
                    status = 'GLOBAL_BINDING_NOT_CAPTURED'; break
                if module['bindings_truncated']:
                    status = 'BINDING_CAPTURE_TRUNCATED'; truncated = True; break
                scope = module
                matches = [x for x in scope['bindings'] if x['name'] == name]
                if not matches:
                    status = 'GLOBAL_BINDING_NOT_CAPTURED'; break
            elif name in scope['nonlocals']:
                continue
            else:
                matches = [x for x in scope['bindings'] if x['name'] == name]
            if not matches: continue
            candidates = [{**site, 'scope': scope['name'], 'scope_kind': scope['kind'],
                           'scope_line': scope['line'], 'binding_effect_proven': False}
                          for site in matches[:MAX_BINDING_CANDIDATES]]
            truncated |= len(matches) > MAX_BINDING_CANDIDATES
            status = 'LEXICAL_BINDING_CANDIDATE' if len(matches) == 1 else 'AMBIGUOUS_OR_REBOUND_BINDING'
            if any(x['kind'] == 'comprehension_assignment_expression' for x in matches):
                status = 'COMPREHENSION_BINDING_EFFECT_UNRESOLVED'
            if any(x['kind'] == 'declaration_binding_effect_unresolved' for x in matches):
                status = 'DECLARATION_BINDING_EFFECT_UNRESOLVED'
            if declaration and any(x['line'] >= read['line'] for x in matches):
                status = 'DECLARATION_BINDING_ORDER_UNRESOLVED'
            break
        references.append({'name': name, 'line': read['line'], 'status': status,
                           'candidates': candidates, 'runtime_resolution': 'NOT_ESTABLISHED'})
    return references, truncated or len(reads) > MAX_CALLS

def _binding_context(scope, scopes):
    uncertain_names = [name for enclosing in scopes for name in enclosing['type_parameters']]
    references, truncated = _read_context(scope['reads'], _scope_chain(scopes), uncertain_names=uncertain_names)
    return {'references': references, 'global_names': scope['globals'],
            'nonlocal_names': scope['nonlocals'], 'scope_omissions': scope['omissions'],
            'truncated': truncated or scope['truncated'],
            'runtime_resolution': 'NOT_ESTABLISHED', 'dependency_closure_complete': False,
            'evidence': 'NAME_LOAD_AND_BINDING_SYNTAX_NOT_RUNTIME_RESOLUTION'}

def _declaration_context(node, scopes, postponed_annotations=False):
    expressions = []; truncated = False; budget = MAX_CALLS; omissions = set()
    if any('MODULE_STRUCTURE_INCOMPLETE' in scope['omissions'] for scope in scopes):
        omissions.add('MODULE_STRUCTURE_INCOMPLETE')
    type_parameters = [p.name for p in getattr(node, 'type_params', ())]
    type_parameters += [name for scope in scopes for name in scope['type_parameters']]
    if type_parameters: omissions.add('TYPE_PARAMETER_SCOPE_NOT_RESOLVED')
    def add(kind, expression, parameter=None):
        nonlocal truncated, budget
        if expression is None: return
        if postponed_annotations and 'annotation' in kind:
            omissions.add('POSTPONED_ANNOTATION_EXPRESSION_NOT_EVALUATED')
        if len(expressions) >= MAX_DECLARATIONS:
            truncated = True; return
        syntax = _scope_syntax([expression], '<declaration>', 'expression')
        reads = syntax['reads'][:budget]; budget -= len(reads)
        references, cut = _read_context(reads, _scope_chain(scopes), declaration=True,
                                        uncertain_names=type_parameters)
        truncated |= syntax['truncated'] or cut or len(reads) < len(syntax['reads'])
        omissions.update(syntax['omissions'])
        if 'annotation' in kind and any(isinstance(x, ast.Constant) and isinstance(x.value, str)
                                        for x in ast.walk(expression)):
            omissions.add('STRING_ANNOTATION_CONTENT_NOT_RESOLVED')
        symbol = dotted(expression.func) if isinstance(expression, ast.Call) else dotted(expression)
        expressions.append({'kind': kind, 'parameter': parameter,
                            'line': expression.lineno, 'end_line': expression.end_lineno,
                            'symbol': symbol if symbol and len(symbol) <= 240 else None,
                            'references': references})
    for expression in node.decorator_list: add('decorator', expression)
    for parameter in getattr(node, 'type_params', ()):
        add('type_parameter_bound', getattr(parameter, 'bound', None), parameter.name)
        add('type_parameter_default', getattr(parameter, 'default_value', None), parameter.name)
    if isinstance(node, ast.ClassDef):
        for expression in node.bases: add('class_base', expression)
        for keyword in node.keywords:
            add('metaclass' if keyword.arg == 'metaclass' else 'class_keyword', keyword.value, keyword.arg)
    else:
        positional = [*node.args.posonlyargs, *node.args.args]
        for parameter, expression in zip(positional[len(positional) - len(node.args.defaults):], node.args.defaults):
            add('positional_default', expression, parameter.arg)
        for parameter, expression in zip(node.args.kwonlyargs, node.args.kw_defaults):
            add('keyword_default', expression, parameter.arg)
        for parameter in [*positional, *node.args.kwonlyargs, *([node.args.vararg] if node.args.vararg else []),
                          *([node.args.kwarg] if node.args.kwarg else [])]:
            add('parameter_annotation', parameter.annotation, parameter.arg)
        add('return_annotation', node.returns)
    return {'expressions': expressions, 'scope_omissions': sorted(omissions), 'truncated': truncated,
            'runtime_resolution': 'NOT_ESTABLISHED', 'dependency_closure_complete': False,
            'evidence': 'DECLARATION_EXPRESSION_SYNTAX_NOT_EVALUATED'}

def metadata(nodes, parameters=(), postponed_annotations=False):
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
    stores.update(_declaration_writes(nodes, postponed_annotations))
    return {'calls': calls[:MAX_CALLS], 'imports': imports[:MAX_CALLS],
            'shadowed_roots': sorted(stores),
            'attribute_reads': attributes[:MAX_CALLS], 'comparisons': comparisons[:MAX_CALLS],
            'truncated': any(len(x) > MAX_CALLS for x in (calls, imports, attributes, comparisons)),
            'runtime_resolution': 'NOT_ESTABLISHED'}

def prepare_module(tree, complete=True):
    """Prepare one shared module scope from a complete declaration envelope."""
    postponed_annotations = any(isinstance(node, ast.ImportFrom) and node.module == '__future__'
                                and any(alias.name == 'annotations' for alias in node.names)
                                for node in tree.body)
    module = metadata(tree.body, postponed_annotations=postponed_annotations)
    module['class_definitions'] = []
    module_scope = _scope_syntax(tree.body, '<module>', 'module', postponed_annotations=postponed_annotations)
    if not complete:
        module_scope['bindings_truncated'] = module_scope['truncated'] = True
        module_scope['omissions'].append('MODULE_STRUCTURE_INCOMPLETE')
        module['structural_context_complete'] = False
        module['truncated'] = True
    module['binding_context'] = _binding_context(module_scope, [module_scope])
    bindings={}
    for n in own_nodes(tree.body):
        name=(n.id if isinstance(n,ast.Name) and isinstance(n.ctx,(ast.Store,ast.Del)) else
              n.name if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)) else None)
        if name:bindings[name]=bindings.get(name,0)+1
    for name in _declaration_writes(tree.body, postponed_annotations):
        bindings[name]=bindings.get(name,0)+1
    module['top_level_binding_counts']=bindings
    return module, module_scope, postponed_annotations


def describe(tree, module_context=None):
    # A partitioned module prepares this context once, then supplies each
    # complete original top-level unit. Nested scopes remain within that unit.
    module, module_scope, postponed_annotations = module_context or prepare_module(tree)
    definitions = {}
    class Walker(ast.NodeVisitor):
        def __init__(self):
            self.parents = []; self.functions = []; self.classes = []; self.kinds = []
            self.scopes = [module_scope]
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
                      'declaration_context': _declaration_context(node, self.scopes, postponed_annotations),
                      'evidence': 'CLASS_DOCUMENTATION_NOT_BEHAVIOR_PROOF',
                      'runtime_resolution': 'NOT_ESTABLISHED'}
            scope = _scope_syntax(node.body, name, 'class', node.lineno,
                                  type_parameters=[p.name for p in getattr(node, 'type_params', ())],
                                  postponed_annotations=postponed_annotations)
            if module.get('structural_context_complete') is False:
                scope['bindings_truncated'] = scope['truncated'] = True
                scope['omissions'].append('MODULE_STRUCTURE_INCOMPLETE')
            record['binding_context'] = _binding_context(scope, [*self.scopes, scope])
            module['class_definitions'].append(record)
            self.parents.append(node.name); self.classes.append(record); self.kinds.append('class')
            self.scopes.append(scope)
            for child in node.body: self.visit(child)
            self.scopes.pop()
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
            m = metadata(node.body, params, postponed_annotations)
            scope = _scope_syntax(node.body, name, 'function', node.lineno, params,
                                  [p.name for p in getattr(node, 'type_params', ())], postponed_annotations)
            if module.get('structural_context_complete') is False:
                scope['bindings_truncated'] = scope['truncated'] = True
                scope['omissions'].append('MODULE_STRUCTURE_INCOMPLETE')
            m['implementation_kind']='DECLARATION_OR_STUB' if stub else 'BODY_PRESENT_NOT_VERIFIED'
            m.update({'enclosing_functions': list(self.functions),
                      'declaration_context': _declaration_context(node, self.scopes, postponed_annotations),
                      'binding_context': _binding_context(scope, [*self.scopes, scope]),
                      'enclosing_class': self.classes[-1]['name'] if self.classes else None,
                      # The class body context remains on its module record;
                      # copying it into every method would multiply that data.
                      'class_context': {k: v for k, v in owner.items() if k != 'binding_context'} if owner else None,
                      'method_role': role,
                      'method_role_evidence': 'DIRECT_DEFINITION_NAME_AND_ENCLOSING_SCOPE_NOT_RUNTIME_SEMANTICS',
                      'parameter_roots': params,
                      'term_frequencies': {
                          'symbol': counts(node.name),
                          'documentation': counts(ast.get_docstring(node) or ''),
                          'parameters': counts(' '.join(params))}})
            definitions[(name, node.lineno)] = m
            self.parents.append(node.name); self.functions.append(name); self.kinds.append('function')
            self.scopes.append(scope)
            for child in node.body: self.visit(child)
            self.scopes.pop()
            self.kinds.pop(); self.functions.pop(); self.parents.pop()
        visit_AsyncFunctionDef = visit_FunctionDef
    Walker().visit(tree)
    return module, definitions

def declared_apis(meta, module):
    """Unverified import-based leads only; rebinding/ambiguous aliases are withheld."""
    if module.get('structural_context_complete') is False:
        return []
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
    if 'analysis_view' in segment:base['analysis_view'] = copy.deepcopy(segment['analysis_view'])
    if 'structure' not in definition:
        return base | {'status': 'STRUCTURAL_CONTEXT_NOT_CAPTURED_REINDEX_REQUIRED'}
    meta = definition['structure']; mod = segment.get('module_structure', {})
    # Keep offsets explicit: these additive records refer to the same original
    # segment as existing lexical evidence, including fenced text/notebook cells.
    base['declaration_context'] = copy.deepcopy(meta.get('declaration_context', {
        'status': 'NOT_CAPTURED_REINDEX_REQUIRED', 'dependency_closure_complete': False}))
    base['binding_context'] = copy.deepcopy(meta.get('binding_context', {
        'status': 'NOT_CAPTURED_REINDEX_REQUIRED', 'dependency_closure_complete': False}))
    owner = meta.get('class_context')
    if owner:
        base['class_declaration_context'] = copy.deepcopy(owner.get('declaration_context', {
            'status': 'NOT_CAPTURED_REINDEX_REQUIRED', 'dependency_closure_complete': False}))
    base['additional_context_line_basis'] = 'SEGMENT_LOCAL'
    base['additional_context_member_start_line'] = segment['start_line']
    if mod.get('structural_context_complete') is False:
        base['limitations'].append('A complete structural unit or module context exceeded its budget; shared bindings are unknown.')
        base['references'] = [
            {'call': call['name'], 'line': segment['start_line'] + call['line'] - 1,
             'status': 'MODULE_CONTEXT_INCOMPLETE', 'candidates': [],
             'callee_execution_proven': False}
            for call in meta.get('calls', [])[:max_references]]
        return base | {'status': 'MODULE_CONTEXT_INCOMPLETE',
                       'references_truncated': meta.get('truncated', False) or len(meta.get('calls', [])) > max_references}
    path = _module_path(segment)
    universe = []; module_segments = []
    for source_row in report['sources']:
        src = source_row['source']
        for seg in source_row['segments']:
            if _namespace(src, seg) != _namespace(source, segment): continue
            if seg.get('format') == 'notebook_cell' and seg['source_id'] != segment['source_id']: continue
            module_segments.append((src, seg))
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
                    seen_targets = set(); incomplete_target = False
                    for module_name, target_name in candidates_to_check:
                        wanted = None
                        if imp['level']:
                            parent = path.parent
                            for _ in range(imp['level'] - 1): parent = parent.parent
                            wanted = (parent / module_name.replace('.', '/')).as_posix()
                        suffix = module_name.replace('.', '/')
                        for src, seg in module_segments:
                            p = _module_path(seg)
                            if not p: continue
                            stem = p.with_suffix('').as_posix()
                            match = stem in (wanted, (wanted or '') + '/__init__') if wanted is not None else (
                                stem in (suffix, suffix + '/__init__') or stem.endswith('/' + suffix) or stem.endswith('/' + suffix + '/__init__'))
                            if not match: continue
                            # An omitted target unit may rebind a retained name
                            # or contain the missing definition itself. Check
                            # module coverage before looking for that name.
                            if seg.get('module_structure', {}).get('structural_context_complete') is False:
                                incomplete_target = True
                                continue
                            for d in seg['definitions']:
                                if d['name'] == target_name and d['definition_id'] not in seen_targets:
                                    candidates.append((src, seg, d)); seen_targets.add(d['definition_id'])
                    if incomplete_target:
                        # A package export and a submodule may both be plausible;
                        # missing structure in either leaves the import unresolved.
                        candidates = []; reason = 'TARGET_MODULE_CONTEXT_INCOMPLETE'
                    else:
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
                 **({'analysis_view': copy.deepcopy(seg['analysis_view'])} if 'analysis_view' in seg else {}),
                 'file_id': src['file_id'], 'path': str(_module_path(seg) or seg['path']),
                 'start_line': d.get('member_start_line', d['start_line']),
                 'end_line': d.get('member_end_line', d['end_line'])} for src, seg, d in candidates]
        base['references'].append({'call': call['name'], 'line': segment['start_line'] + call['line'] - 1,
                                   'status': 'AMBIGUOUS_CANDIDATES' if len(refs) > 1 else reason,
                                   'candidates': refs, 'callee_execution_proven': False})
    return base | {'status': 'STATIC_CONTEXT_LEADS',
                   'references_truncated': meta.get('truncated', False) or len(meta.get('calls', [])) > max_references}
