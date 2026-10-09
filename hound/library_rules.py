"""Reviewed library mappings and bounded context, not dynamic import or API proof.

All observations refer to declared imports. Dependency bytes, backend identity,
monkey patches and algorithmic correctness are not established by a library name.
"""
from __future__ import annotations
import ast
import math
import re
from pathlib import PurePosixPath
import baseline_sniffers as base
from flow_core import Value, UNKNOWN, PRIMITIVES

OBJECT_FACTOR = 'geometry.object_factor_context'
EXTRA_APIS={
 'numpy.linalg.solve':('linear_system_solve','linear_algebra'),
 'scipy.linalg.solve':('linear_system_solve','linear_algebra'),
 'scipy.linalg.svd':('singular_value_decomposition','decomposition'),
 'scipy.linalg._decomp_svd.svd':('singular_value_decomposition','decomposition'),
 'scipy.linalg.eigh':('symmetric_eigendecomposition','decomposition')}
FACTORIES=frozenset({'scipy._lib._array_api.array_namespace','array_api_compat.array_namespace'})
# Reviewed contracts permit namespace use, not arbitrary argument mutation.
NAMESPACE_CONSUMERS=frozenset({'scipy._lib._array_api._asarray','scipy._lib._array_api.is_numpy'})
ARRAY_APIS={'array_api.linalg.solve':('linear_system_solve','linear_algebra'),'array_api.linalg.svd':('singular_value_decomposition','decomposition'),
            'array_api.linalg.eigh':('symmetric_eigendecomposition','decomposition')}


def package_from_source_id(source_id):
 """Only a pinned, explicit GitHub source location establishes this package path.

 It supplies lexical context, not permission, source authenticity or importability.
 Standalone/synthetic sources without package context stay unresolved.
 """
 m=re.fullmatch(r'github:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}:(.+)',source_id)
 if not m:return None
 path=PurePosixPath(m.group(1))
 if path.is_absolute() or '..' in path.parts or '\\' in str(path) or path.suffix!='.py':return None
 parts=list(path.with_suffix('').parts)
 if not all(p.isidentifier() for p in parts):return None
 return parts[:-1]  # __init__.py also has the directory itself as its package.


def resolve_relative_imports(tree,source_id):
 """Canonicalize declared relative import paths in the AST only. No I/O/imports."""
 package=package_from_source_id(source_id); records=[]
 for node in ast.walk(tree):
  if not isinstance(node,ast.ImportFrom) or not node.level:continue
  original='.'*node.level+(node.module or '')
  row={'line':node.lineno,'declared_from':original,'runtime_import_verified':False}
  if package is None or node.level>len(package):
   row['status']='PACKAGE_CONTEXT_UNRESOLVED'
  else:
   prefix=package[:len(package)-node.level+1]
   target='.'.join([*prefix,*((node.module or '').split('.') if node.module else [])])
   if target and all(p.isidentifier() for p in target.split('.')):
    node.module=target;node.level=0
    row.update(status='DECLARED_RELATIVE_PATH_RESOLVED',resolved_from=target)
   else:row['status']='PACKAGE_CONTEXT_UNRESOLVED'
  records.append(row)
 return records


NAMESPACE_OPERATIONS=frozenset({'array_api.asarray','array_api.mean','array_api.conj','array_api.linalg.matrix_norm','array_api.linalg.svd','array_api.linalg.eigh','array_api.linalg.solve','array_api.sum','array_api.square'})

def namespace_consumer(target):
 return ((target.kind=='import' and target.value in NAMESPACE_CONSUMERS) or
         (target.kind=='namespace' and target.value in NAMESPACE_OPERATIONS))


def observe_call(flow,node,env,target,receiver,args,keywords,expanded):
 """Called once, AFTER argument evaluation. None means no abstract return value."""
 # Unresolved callbacks may mutate captured library objects without receiving an explicit argument.
 if target.kind not in ('import','namespace'):
  for key,value in list(env.items()):
   if value.kind=='namespace':env[key]=UNKNOWN
 api=target.value if target.kind in ('import','namespace') else None
 known=(ARRAY_APIS if target.kind=='namespace' else EXTRA_APIS).get(api)
 if known:
  operation,category=known
  extra={'resolution':'DECLARED_IMPORT_PATH_NOT_RUNTIME_VERIFIED'}
  if target.kind=='namespace':
   extra.update(backend='RUNTIME_SELECTED_NOT_IDENTIFIED',namespace_factory=list(target.origin),
                namespace_definition_lines=list(target.lines))
  flow.add(PRIMITIVES,'COMPUTATIONAL_PRIMITIVE_OBSERVED',node,
      ['Reviewed API-path mapping observed: '+api],operation=operation,category=category,
      resolved_api=api,limitations=['A declared call is a capability clue; library implementation, backend, reachability and numerical behavior remain unverified.'],**extra)
 if target.kind=='import' and api in FACTORIES:
  return Value('namespace','array_api',origin=(api,),lines=(node.lineno,),identity=(node.lineno,node.col_offset))
 return None


def _self_attr(node,receiver):
 return node.attr if isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name) and node.value.id==receiver else None

def _defaults(fn):
 args=[*fn.args.posonlyargs,*fn.args.args]
 pairs=list(zip(args[len(args)-len(fn.args.defaults):],fn.args.defaults))+list(zip(fn.args.kwonlyargs,fn.args.kw_defaults))
 return {a.arg:v for a,v in pairs if v is not None}

def _literal(node):
 if isinstance(node,ast.Constant) and (node.value is None or type(node.value) in (bool,int,float)):
  if type(node.value) is float and not math.isfinite(node.value):return {'status':'UNRESOLVED'}
  return {'status':'DECLARED_LITERAL','value':node.value}
 return {'status':'UNRESOLVED'}

def _initializer(value,params):
 if isinstance(value,ast.Name) and value.id in params:
  return {'supplied_parameter':value.id,'declared_default':_literal(params[value.id]),'caller_value_retained':True}
 if isinstance(value,ast.IfExp) and isinstance(value.test,ast.Compare):
  c=value.test
  if (len(c.ops)==1 and isinstance(c.ops[0],ast.Is) and isinstance(c.left,ast.Name)
      and isinstance(c.comparators[0],ast.Constant) and c.comparators[0].value is None
      and isinstance(value.orelse,ast.Name) and value.orelse.id==c.left.id):
   return {'supplied_parameter':c.left.id,'when_none':_literal(value.body),
           'declared_default':_literal(params.get(c.left.id)),'caller_value_retained':True}
 return {'status':'COMPUTED_OR_UNRESOLVED'}

def add_object_factor_context(tree,fn,name,flow):
 """Link observed factor fields to same-class assignments, without executing methods.

 This is a syntactic candidate graph. It never certifies call order, fixed scale,
 descriptor/metaclass behavior, inheritance, dimensions, units, or independence.
 """
 classes=[]
 for node in ast.walk(tree):
  if isinstance(node,ast.ClassDef) and any(n is fn for n in node.body):classes.append(node)
 if len(classes)!=1:return
 cls=classes[0];pos=[*fn.args.posonlyargs,*fn.args.args]
 if not pos:return
 receiver=pos[0].arg
 # Static/class methods do not make the first parameter an ordinary instance.
 if any(base.dotted(d) in ('staticmethod','classmethod') for d in fn.decorator_list):return
 aliases=base.alias_map(tree,fn)
 parents={c:n for n in ast.walk(fn) for c in ast.iter_child_nodes(n)}
 def matrix_op(n):
  if isinstance(n,ast.BinOp) and isinstance(n.op,ast.MatMult):return True
  return isinstance(n,ast.Call) and base.resolve(n.func,aliases) in ('numpy.dot','numpy.matmul')
 writers={}
 for method in cls.body:
  if not isinstance(method,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
  args=[*method.args.posonlyargs,*method.args.args]
  if not args:continue
  recv=args[0].arg;pars=_defaults(method)
  method_name=name.rsplit('.',1)[0]+'.'+method.name
  mp={c:n for n in ast.walk(method) for c in ast.iter_child_nodes(n)}
  for assignment in base.scoped_nodes(method.body):
   if not isinstance(assignment,(ast.Assign,ast.AnnAssign,ast.AugAssign)):continue
   targets=assignment.targets if isinstance(assignment,ast.Assign) else [assignment.target]
   for target in targets:
    field=_self_attr(target,recv)
    if field is None:continue
    gates=[];p=mp.get(assignment)
    while p is not None and p is not method:
     if isinstance(p,ast.If):
      fields=sorted({_self_attr(x,recv) for x in ast.walk(p.test) if _self_attr(x,recv)})
      if fields:gates.append({'line':p.lineno,'fields':fields,'truth_not_proven':True})
     p=mp.get(p)
    writers.setdefault(field,[]).append({'qualified_name':method_name,'line_start':assignment.lineno,
       'line_end':assignment.end_lineno,'initializer':_initializer(assignment.value,pars),
       'conditional_context':gates,'assignment_kind':type(assignment).__name__})
 for n in base.scoped_nodes(fn.body):
  if not (isinstance(n,ast.BinOp) and isinstance(n.op,ast.Mult)):continue
  field=_self_attr(n.left,receiver);other=n.right
  if field is None:field=_self_attr(n.right,receiver);other=n.left
  if field is None or not matrix_op(other):continue
  p=parents.get(n);sink=None
  while p is not None and p is not fn:
   if isinstance(p,ast.Return):sink='RETURN_EXPRESSION';break
   if isinstance(p,(ast.Assign,ast.AnnAssign)):
    targets=p.targets if isinstance(p,ast.Assign) else [p.target]
    if any(_self_attr(t,receiver) for t in targets):sink='INSTANCE_FIELD_ASSIGNMENT'
    break
   p=parents.get(p)
  if sink is None:continue
  flow.add(OBJECT_FACTOR,'OBJECT_FACTOR_CONTEXT_REQUIRES_REVIEW',n,
    ['An instance field multiplies a matrix-operation result in a return or field assignment.'],
    operation='stateful_transform_factor',field_context={'field':field,'writers':writers.get(field,[])[:32],
      'writer_count':len(writers.get(field,[])),
      'control_fields':{key:writers.get(key,[])[:16] for key in sorted({key for w in writers.get(field,[]) for gate in w['conditional_context'] for key in gate['fields']})[:16]}},output_sink=sink,fixed_unity_proven=False,
    limitations=['Same-class references only, not an interprocedural value proof or scalar/type/coordinate proof.',
                 'Disabling a parameter update does not by itself establish the current factor equals one.',
                 'Methods, descriptors, inheritance and runtime call order remain unverified.'])
