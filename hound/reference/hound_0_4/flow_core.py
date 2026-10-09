"""Conservative, bounded local observations. Never evaluates target Python.

This is an abstract interpreter for a documented subset, NOT a proof system.
Unknown calls, branch joins, loop-carried locals and mutation must not become
positive evidence. Runtime copy semantics and arbitrary monkey patching remain
out of scope. Only allowlisted fields/API names enter findings.
"""
from __future__ import annotations
import ast
from dataclasses import dataclass
from typing import Any
import baseline_sniffers as base

RIGID = 'geometry.rigid_icp_controls'
SCALE = 'geometry.scale_receiver_lineage'
ABSTAIN = 'identification.explicit_nondecision'
PATH = 'storage.resolved_path_guard'
PRIMITIVES = 'geometry.computational_primitives'
DIMENSION = 'geometry.dimension_rejection_pattern'
SCALED_MATRIX = 'geometry.scaled_matrix_expression'
NONDECISIONS = frozenset({'unknown','ambiguous','insufficient_geometry','insufficient_data',
    'no_match','review','reconciliation_required','conflicting','conflict','disagreement','abstain'})
FIELDS = frozenset({'status','decision','assessment','pass_fail','physical_uncertainty_mm'})
APIS = {
    'trimesh.registration.icp': ('surface_registration','registration'),
    'trimesh.proximity.closest_point': ('point_to_surface_distance','distance'),
    'scipy.spatial.cKDTree': ('spatial_index_construction','spatial_index'),
    'scipy.spatial.KDTree': ('spatial_index_construction','spatial_index'),
    'scipy.spatial.cKDTree.query': ('nearest_neighbor_query','distance'),
    'scipy.spatial.KDTree.query': ('nearest_neighbor_query','distance'),
    'scipy.spatial.distance.cdist': ('pairwise_distance','distance'),
    'scipy.spatial.distance.pdist': ('pairwise_distance','distance'),
    'numpy.linalg.svd': ('singular_value_decomposition','decomposition'),
    'numpy.linalg.eigh': ('symmetric_eigendecomposition','decomposition'),
    'numpy.fft.rfft': ('real_fourier_transform','spectral'),
    'numpy.fft.fft': ('fourier_transform','spectral'),
    'scipy.optimize.least_squares': ('nonlinear_least_squares','fitting'),
    'scipy.optimize.linear_sum_assignment': ('linear_assignment','correspondence'),
}

@dataclass(frozen=True)
class Value:
    kind: str = 'unknown'
    value: Any = None
    origin: tuple[str, ...] = ()
    lines: tuple[int, ...] = ()
    identity: tuple[int, int] | None = None
    contained: tuple = ()

UNKNOWN = Value()


def merge_value(a: Value, b: Value) -> Value:
    # Source line provenance is not part of semantic equality.
    if (a.kind,a.value,a.origin,a.identity,a.contained) == (b.kind,b.value,b.origin,b.identity,b.contained):
        return Value(a.kind,a.value,a.origin,tuple(sorted(set(a.lines+b.lines))),a.identity,a.contained)
    return UNKNOWN


def join(a: dict, b: dict) -> dict:
    return {k:merge_value(a.get(k,UNKNOWN),b.get(k,UNKNOWN)) for k in a.keys()|b.keys()}


@dataclass
class Flow:
    env: dict
    falls_through: bool = True


class LocalFlow:
    def __init__(self, tree: ast.Module, fn, name: str, *, module_writes=None, capabilities=None):
        self.fn,self.name,self.findings=fn,name,[]
        aliases=base.alias_map(tree,fn)
        local=base.imported_aliases(fn.body)
        if '<locals>' in name: aliases={k:v for k,v in aliases.items() if local.get(k)==v}
        if module_writes is None:
            module_writes={base.dotted(n).split('.')[0] for n in ast.walk(tree)
                if isinstance(n,ast.Attribute) and isinstance(n.ctx,(ast.Store,ast.Del)) and base.dotted(n)}
        aliases={k:v for k,v in aliases.items() if k not in module_writes}
        self.allowed_imports=aliases
        # Imports local to this function are NOT bound until execution reaches them.
        self.env={k:Value('import',v) for k,v in aliases.items() if k not in local}
        args=[*fn.args.posonlyargs,*fn.args.args,*fn.args.kwonlyargs]
        if fn.args.vararg: args.append(fn.args.vararg)
        if fn.args.kwarg: args.append(fn.args.kwarg)
        for a in args:self.env[a.arg]=Value('input',origin=(a.arg,))
        self.capabilities=capabilities
        self.return_review_depth=0
        # A callback can invoke nested writers that share enclosing local cells.
        # We do not interpret the nested callable: invalidate its writable cells.
        self.nonlocal_cells={name for node in ast.walk(fn) if isinstance(node,ast.Nonlocal) for name in node.names}
        self.is_generator=any(isinstance(n,(ast.Yield,ast.YieldFrom)) for n in base.scoped_nodes(fn.body))

    def add(self,profile,status,node,observations,**kw):
        self.findings.append({'profile_id':profile,'status':status,'qualified_name':self.name,
            'function_lines':[self.fn.lineno,self.fn.end_lineno],
            'evidence':[base.evidence(node,x) for x in observations],
            'runtime_verified':False,'scope':'bounded local source observations; runtime reachability/type unverified',**kw})

    def object_ids(self, value):
        """Track only abstract object identities; no arbitrary keys/source text."""
        ids=set()
        if value.kind in ('dict','list') and value.identity is not None:
            ids.add(value.identity)
        children=list(value.contained)
        if value.kind in ('tuple','list'):children.extend(value.value)
        elif value.kind=='dict':children.extend(v for _,v in value.value)
        for child in children:ids.update(self.object_ids(child))
        return ids

    def invalidate_object(self, value, env):
        affected=self.object_ids(value)
        if not affected:return
        for key,current in list(env.items()):
            if self.object_ids(current)&affected:env[key]=UNKNOWN

    def invalidate_mutables(self, env):
        for key,value in list(env.items()):
            if self.object_ids(value):env[key]=UNKNOWN

    def assign(self,target,value,env,line):
        if isinstance(target,ast.Name):
            env[target.id]=(Value(value.kind,value.value,value.origin,
                tuple(sorted(set(value.lines+(line,)))),value.identity,value.contained) if value.kind!='unknown' else UNKNOWN)
        elif isinstance(target,(ast.Tuple,ast.List)):
            values=value.value if value.kind in ('tuple','list') and len(value.value)==len(target.elts) else [UNKNOWN]*len(target.elts)
            for elt,v in zip(target.elts,values):self.assign(elt,v,env,line)
        elif isinstance(target,(ast.Subscript,ast.Attribute)):
            self.invalidate_object(self.eval(target.value,env),env)
            if isinstance(target,ast.Subscript):self.eval(target.slice,env)

    def scalar(self,node):
        if not isinstance(node,ast.Constant):return UNKNOWN
        if type(node.value) is bool:return Value('bool',node.value)
        if node.value is None:return Value('none')
        # Retain only fixed vocabulary, not arbitrary source strings or secrets.
        if isinstance(node.value,str) and node.value.lower() in NONDECISIONS:
            return Value('status',node.value)
        if type(node.value) in (int,float) and node.value in (25.4,1/25.4):
            return Value('unit_factor',node.value)
        return UNKNOWN

    def eval(self,node,env):
        if node is None:return UNKNOWN
        if isinstance(node,ast.Constant):return self.scalar(node)
        if isinstance(node,ast.Name):return env.get(node.id,UNKNOWN)
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda)):return UNKNOWN
        if isinstance(node,ast.NamedExpr):
            v=self.eval(node.value,env);self.assign(node.target,v,env,node.lineno);return v
        if isinstance(node,ast.Attribute):
            v=self.eval(node.value,env)
            if v.kind=='import':return Value('import',v.value+'.'+node.attr,lines=v.lines)
            if v.kind=='spatial_index' and node.attr=='query':return Value('import',v.value+'.query',lines=v.lines)
            return UNKNOWN
        if isinstance(node,ast.UnaryOp):
            v=self.eval(node.operand,env)
            if isinstance(node.op,ast.Not) and v.kind=='bool':return Value('bool',not v.value,lines=v.lines)
            return UNKNOWN
        if isinstance(node,ast.IfExp):
            condition=self.eval(node.test,env)
            if condition.kind=='bool':return self.eval(node.body if condition.value else node.orelse,env)
            ae,be=env.copy(),env.copy();a=self.eval(node.body,ae);b=self.eval(node.orelse,be)
            env.update(join(ae,be));return merge_value(a,b)
        if isinstance(node,ast.BoolOp):
            result=self.eval(node.values[0],env)
            for part in node.values[1:]:
                if result.kind=='bool':
                    stop=(isinstance(node.op,ast.And) and not result.value) or (isinstance(node.op,ast.Or) and result.value)
                    if stop:return result
                    result=self.eval(part,env)
                else:
                    branch=env.copy();value=self.eval(part,branch);env.update(join(env,branch));result=merge_value(result,value)
            return result
        if isinstance(node,ast.Dict):
            fields={};contained=[]
            for k,v in zip(node.keys,node.values):
                self.eval(k,env);value=self.eval(v,env)
                if self.object_ids(value):contained.append(value)
                if k is None:
                    # A runtime mapping can replace any earlier key.
                    fields.clear()
                elif isinstance(k,ast.Constant) and isinstance(k.value,str):
                    if k.value in FIELDS:fields[k.value]=value
                else:fields.clear() # Unknown key can alias a retained field.
            return Value('dict',tuple(sorted(fields.items())),identity=(node.lineno,node.col_offset),contained=tuple(contained))
        if isinstance(node,(ast.Tuple,ast.List)):
            return Value('list' if isinstance(node,ast.List) else 'tuple',
                tuple(self.eval(x,env) for x in node.elts),
                identity=(node.lineno,node.col_offset) if isinstance(node,ast.List) else None)
        if isinstance(node,(ast.Yield,ast.YieldFrom,ast.Await)):
            self.eval(node.value,env)
            self.invalidate_mutables(env)
            return UNKNOWN
        if isinstance(node,ast.Call):
            # Python resolves the callable before evaluating arguments.
            receiver=UNKNOWN
            if isinstance(node.func,ast.Attribute):
                receiver=self.eval(node.func.value,env)
                target=(Value('import',receiver.value+'.'+node.func.attr,lines=receiver.lines) if receiver.kind=='import'
                    else Value('import',receiver.value+'.query',lines=receiver.lines) if receiver.kind=='spatial_index' and node.func.attr=='query'
                    else UNKNOWN)
            else:target=self.eval(node.func,env)
            arguments=[self.eval(x,env) for x in node.args]
            keywords={}
            for k in node.keywords:
                v=self.eval(k.value,env)
                if k.arg is not None:keywords[k.arg]=v
            if target.kind=='import' and target.value=='trimesh.registration.icp':
                known,unresolved,conflicts,lines={},[],[],set()
                for key in ('scale','reflection'):
                    v=keywords.get(key,UNKNOWN)
                    if v.kind=='bool':
                        known[key]=v.value;lines.update(v.lines)
                        if v.value:conflicts.append(key)
                    else:unresolved.append(key)
                if any(k.arg is None for k in node.keywords):unresolved.append('expanded_keywords')
                status='CONTRADICTED_CALL_CONTRACT' if conflicts else 'NEEDS_CONTEXT' if unresolved else 'STATIC_CANDIDATE'
                self.add(RIGID,status,node,['Imported ICP call with local controls inspected.'],known_controls=known,
                    contradicted_controls=conflicts,unresolved=unresolved,value_definition_lines=sorted(lines),
                    limitations=['Call controls only; not a full pipeline, copy, accuracy, or physical-unit guarantee.'])
            if target.kind=='import' and target.value in APIS:
                operation,category=APIS[target.value]
                self.add(PRIMITIVES,'COMPUTATIONAL_PRIMITIVE_OBSERVED',node,
                    ['Resolved allowlisted computation API: '+target.value],operation=operation,category=category,
                    resolved_api=target.value,limitations=['API usage is a search clue, not proof of an enclosing algorithm or correct results.'])
            if isinstance(node.func,ast.Attribute) and node.func.attr=='apply_scale':
                status={'input':'INPUT_ALIAS_RECEIVER','copy':'COPY_CALL_RESULT_RECEIVER'}.get(receiver.kind,'UNRESOLVED_RECEIVER')
                extra={}
                if arguments and arguments[0].kind=='unit_factor':
                    extra={'unit_conversion_clue':{'factor':arguments[0].value,'status':'FACTOR_ONLY_UNITS_NOT_PROVEN'}}
                self.add(SCALE,status,node,['apply_scale receiver lineage observed.'],origin_parameters=list(receiver.origin),
                    value_definition_lines=list(receiver.lines),unresolved=['Runtime type, storage independence and output/callee behavior unverified.'],
                    limitations=['copy() is an observed call, not proof of independent storage. Scale may be intentional unit conversion.'],**extra)
            if target.kind=='unknown':
                for name in self.nonlocal_cells:
                    if name in env:env[name]=UNKNOWN
            # A passed mutable dictionary may be changed through ANY visible alias.
            for v in [receiver,*arguments,*keywords.values()]:self.invalidate_object(v,env)
            if isinstance(node.func,ast.Attribute) and not node.args and not node.keywords:
                if node.func.attr=='copy' and receiver.kind in ('input','copy'):
                    return Value('copy',origin=receiver.origin,lines=receiver.lines+(node.lineno,))
                if node.func.attr=='resolve':
                    return Value('resolved_path',lines=(node.lineno,),identity=(node.lineno,node.col_offset))
            if target.kind=='import' and target.value in ('scipy.spatial.cKDTree','scipy.spatial.KDTree'):
                return Value('spatial_index',target.value,lines=(node.lineno,))
            return UNKNOWN
        if isinstance(node,(ast.ListComp,ast.SetComp,ast.DictComp,ast.GeneratorExp)):
            local=env.copy();reachable=True
            for g in node.generators:
                origin=self.eval(g.iter,local)
                if origin.kind in ('tuple','list') and not origin.value:
                    reachable=False;break
                self.assign(g.target,self.element(origin,g.iter),local,
                            g.lineno if hasattr(g,'lineno') else node.lineno)
                for cond in g.ifs:
                    v=self.eval(cond,local)
                    if v.kind=='bool' and not v.value:
                        reachable=False;break
                if not reachable:break
            if reachable:
                for attr in ('elt','key','value'):
                    if hasattr(node,attr):self.eval(getattr(node,attr),local)
            # Calls in a comprehension may mutate outer referenced objects.
            for key,value in list(env.items()):
                if self.object_ids(value) and local.get(key,UNKNOWN).kind=='unknown':env[key]=UNKNOWN
            # Walrus scope is complex: invalidate rather than assuming it executed.
            for x in ast.walk(node):
                if isinstance(x,ast.NamedExpr):
                    for key in base.bindings(ast.walk(x.target)):env[key]=UNKNOWN
            return UNKNOWN
        if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Mult):
            # A deliberately structural clue: multiplication wrapped around @.
            # Not a type proof, a fitted-scale proof, or an output/data-flow proof.
            if any(isinstance(x,ast.BinOp) and isinstance(x.op,ast.MatMult) for x in ast.walk(node)):
                self.add(SCALED_MATRIX,'SCALED_MATRIX_EXPRESSION_OBSERVED',node,
                    ['Multiplication expression contains matrix multiplication. Inspect whether this scales transformed coordinates.'],
                    limitations=['Structural clue only. Scalar type, factor, affected output, and intended unit changes are unverified.'])
        for child in ast.iter_child_nodes(node):self.eval(child,env)
        return UNKNOWN

    def element(self,v,node):
        if v.kind in ('input','copy'):
            return Value('input',origin=tuple(x+'[*]' for x in v.origin),lines=v.lines+(node.lineno,))
        return UNKNOWN

    def nondecision(self,value,node):
        if value.kind!='dict':return
        fields=[]
        for k,v in value.value:
            if k in {'status','decision','assessment'} and v.kind=='status':fields.append({'field':k,'value':v.value})
            elif k in {'pass_fail','physical_uncertainty_mm'} and v.kind=='none':fields.append({'field':k,'value':None})
        status=('NONDECISION_REQUIRES_CONTROL_FLOW_REVIEW' if self.return_review_depth else
                'GENERATOR_RETURN_REQUIRES_REVIEW' if self.is_generator else 'EXPLICIT_NONDECISION_LITERAL')
        if fields:self.add(ABSTAIN,status,node,
            ['Returned mapping has explicit retained nondecision fields.'],fields=fields,
            value_definition_lines=list(value.lines),unresolved=['Decision conditions, downstream interpretation and calibrated rejection not established.'],
            limitations=['Local returned-value observation only; not correctness of a full decision policy.'])

    def guaranteed_rejection(self,body):
        """Narrow unconditional raise before any potentially escaping statement."""
        for node in body:
            if isinstance(node,ast.Raise):return True
            if isinstance(node,(ast.Return,ast.Break,ast.Continue,ast.If,ast.Try,ast.TryStar,
                                ast.For,ast.While,ast.With,ast.AsyncWith,ast.Match)):
                return False
        return False

    def positive_disjuncts(self,node):
        # bad_dimension OR other implies rejection; NOT/AND do not establish it.
        if isinstance(node,ast.BoolOp) and isinstance(node.op,ast.Or):
            for value in node.values:yield from self.positive_disjuncts(value)
        else:yield node

    def dimension_guard(self,s):
        if not self.guaranteed_rejection(s.body):return
        for n in self.positive_disjuncts(s.test):
            if not (isinstance(n,ast.Compare) and len(n.ops)==1 and isinstance(n.ops[0],ast.NotEq)):continue
            left,right=n.left,n.comparators[0]
            if isinstance(left,ast.Constant):left,right=right,left
            if not (isinstance(left,ast.Subscript) and isinstance(left.value,ast.Attribute) and left.value.attr=='shape'
                    and isinstance(left.slice,ast.Constant) and type(left.slice.value) is int and left.slice.value==1
                    and isinstance(right,ast.Constant) and type(right.value) is int and right.value in (2,3)):continue
            self.add(DIMENSION,'DIMENSION_REJECTION_PATTERN',n,
                ['Second shape dimension compared with a fixed column count in a raising conditional.'],
                expected_columns=right.value,
                limitations=['This is a guard-pattern observation, not proof of all execution paths, types, units or correspondence.'])

    def path_guard(self,s,env):
        if not (self.guaranteed_rejection(s.body) and isinstance(s.test,ast.UnaryOp) and isinstance(s.test.op,ast.Not)):return
        call=s.test.operand
        if not (isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute) and call.func.attr=='is_relative_to'):return
        receiver=call.func.value
        v=env.get(receiver.id,UNKNOWN) if isinstance(receiver,ast.Name) else UNKNOWN
        if v.kind=='resolved_path':
            self.add(PATH,'STATIC_CANDIDATE',s,['Current receiver comes from observed resolve(); negative containment branch raises.'],
                value_definition_lines=list(v.lines),unresolved=['Runtime filesystem/type, concurrent replacement and caller behavior unverified.'],
                limitations=['Narrow live-local syntax; not a filesystem security certificate.'])

    def sequence(self, statements, env):
        env=env.copy()
        for s in statements:
            if isinstance(s,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                env[s.name]=UNKNOWN;continue
            if isinstance(s,ast.Return):
                v=self.eval(s.value,env);self.nondecision(v,s.value or s);return Flow(env,False)
            if isinstance(s,ast.Raise):self.eval(s,env);return Flow(env,False)
            if isinstance(s,(ast.Break,ast.Continue)):return Flow(env,False)
            if isinstance(s,ast.If):
                self.dimension_guard(s);self.path_guard(s,env);condition=self.eval(s.test,env)
                if condition.kind=='bool':
                    result=self.sequence(s.body if condition.value else s.orelse,env)
                else:
                    a,b=self.sequence(s.body,env),self.sequence(s.orelse,env)
                    result=(Flow(join(a.env,b.env)) if a.falls_through and b.falls_through else a if a.falls_through else b)
                env=result.env
                if not result.falls_through:return result
            elif isinstance(s,(ast.Assign,ast.AnnAssign)):
                v=self.eval(s.value,env)
                for t in s.targets if isinstance(s,ast.Assign) else [s.target]:self.assign(t,v,env,s.lineno)
            elif isinstance(s,ast.AugAssign):
                self.eval(s.value,env);self.assign(s.target,UNKNOWN,env,s.lineno)
            elif isinstance(s,(ast.For,ast.AsyncFor,ast.While)):
                iterator=self.eval(s.iter,env) if isinstance(s,(ast.For,ast.AsyncFor)) else self.eval(s.test,env)
                if ((isinstance(s,ast.While) and iterator.kind=='bool' and not iterator.value) or
                    (isinstance(s,(ast.For,ast.AsyncFor)) and iterator.kind in ('tuple','list') and not iterator.value)):
                    result=self.sequence(s.orelse,env);env=result.env
                    if not result.falls_through:return result
                    continue
                if (isinstance(s,ast.For) and iterator.kind in ('tuple','list') and iterator.value
                        and s.body and isinstance(s.body[0],ast.Break)):
                    self.assign(s.target,iterator.value[0],env,s.lineno)
                    continue # Guaranteed first-iteration break skips the else suite.
                changed=base.bindings(base.scoped_nodes([s]));loop_env=env.copy()
                for key in changed:loop_env[key]=UNKNOWN
                if isinstance(s,(ast.For,ast.AsyncFor)):
                    self.assign(s.target,self.element(iterator,s.iter),loop_env,s.lineno)
                body=self.sequence(s.body,loop_env)
                # Iteration may occur zero times; assignments may have carried over.
                env=join(env,body.env)
                for key in changed:env[key]=UNKNOWN
                has_break=any(isinstance(n,ast.Break) for n in base.scoped_nodes(s.body))
                otherwise=self.sequence(s.orelse,env)
                if has_break:
                    env=join(env,otherwise.env)
                else:
                    env=otherwise.env
                    if not otherwise.falls_through:return otherwise
                if isinstance(s,ast.While) and iterator.kind=='bool' and iterator.value and not any(isinstance(n,ast.Break) for n in base.scoped_nodes(s.body)):
                    return Flow(env,False)
            elif isinstance(s,(ast.Import,ast.ImportFrom)):
                for name,target in base.imported_aliases([s]).items():
                    env[name]=Value('import',target,lines=(s.lineno,)) if self.allowed_imports.get(name)==target else UNKNOWN
            elif isinstance(s,(ast.Try,ast.TryStar,ast.With,ast.AsyncWith,ast.Match)):
                changed=base.bindings(base.scoped_nodes([s]));local=env.copy()
                for key in changed:local[key]=UNKNOWN
                if isinstance(s,(ast.With,ast.AsyncWith)):
                    for item in s.items:self.eval(item.context_expr,local)
                if isinstance(s,ast.Match):self.eval(s.subject,local)
                branches=[getattr(s,x,[]) for x in ('body','orelse','finalbody')]
                branches += [h.body for h in getattr(s,'handlers',[])] + [c.body for c in getattr(s,'cases',[])]
                # Returns pending under finalization/context exit are clues only.
                needs_review=isinstance(s,(ast.With,ast.AsyncWith)) or bool(getattr(s,'finalbody',[]))
                if needs_review:self.return_review_depth+=1
                try:
                    for branch in branches:self.sequence(branch,local)
                finally:
                    if needs_review:self.return_review_depth-=1
                for key in changed:env[key]=UNKNOWN
                # Unsupported exceptional/context-manager effects cannot certify retained mutable results.
                for key,v in list(env.items()):
                    if self.object_ids(v):env[key]=UNKNOWN
            elif isinstance(s,ast.Delete):
                for t in s.targets:self.assign(t,UNKNOWN,env,s.lineno)
            else:
                self.eval(s,env)
        return Flow(env)
