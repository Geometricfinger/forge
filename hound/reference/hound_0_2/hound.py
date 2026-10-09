"""FORGE Hound 0.2: bounded, source-bound static hunt; never imports target code.

Recognizes selected syntax and local value-flow only. No runtime correctness,
whole-program scale guarantee, copy-depth proof, rights clearance or novelty score.
"""
from __future__ import annotations
import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
import baseline_sniffers as base

VERSION = '0.2.0'
RIGID = 'geometry.rigid_icp_controls'
SCALE = 'geometry.scale_receiver_lineage'
ABSTAIN = 'identification.explicit_nondecision'
PROFILE_IDS = (RIGID, SCALE, ABSTAIN, 'validation.explicit_units_finite_values', 'storage.resolved_path_guard')


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def engine_digest() -> str:
    return sha(Path(__file__).read_bytes() + Path(base.__file__).read_bytes())


@dataclass(frozen=True)
class Value:
    kind: str = 'unknown'
    value: Any = None
    origin: tuple[str, ...] = ()
    lines: tuple[int, ...] = ()

UNKNOWN = Value()


def join(a: dict, b: dict) -> dict:
    return {k: a[k] if k in a and k in b and a[k] == b[k] else UNKNOWN for k in a.keys() | b.keys()}


class LocalFlow:
    """One-function abstract interpreter; unsupported flow is conservatively unknown."""
    def __init__(self, tree: ast.Module, fn, name: str):
        self.fn, self.name, self.findings = fn, name, []
        aliases = base.alias_map(tree, fn)
        if '<locals>' in name:
            local = base.imported_aliases(fn.body)
            aliases = {k:v for k,v in aliases.items() if local.get(k) == v}
        # Attribute writes may monkey-patch a library; suppress this root entirely.
        modified_roots = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and isinstance(n.ctx, (ast.Store, ast.Del)):
                d = base.dotted(n)
                if d: modified_roots.add(d.split('.')[0])
        aliases = {k:v for k,v in aliases.items() if k not in modified_roots}
        self.env = {k:Value('import', v) for k,v in aliases.items()}
        args = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
        if fn.args.vararg: args.append(fn.args.vararg)
        if fn.args.kwarg: args.append(fn.args.kwarg)
        for a in args: self.env[a.arg] = Value('input', origin=(a.arg,))
        self.blocked_names: set[str] = set()

    def value(self, node, env):
        if isinstance(node, ast.Constant) and type(node.value) is bool:
            return Value('bool', node.value)
        if isinstance(node, ast.Name):
            return UNKNOWN if node.id in self.blocked_names else env.get(node.id, UNKNOWN)
        if isinstance(node, ast.Attribute):
            v = self.value(node.value, env)
            if v.kind == 'import': return Value('import', v.value + '.' + node.attr, lines=v.lines)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            v = self.value(node.operand, env)
            if v.kind == 'bool': return Value('bool', not v.value, lines=v.lines)
        if isinstance(node, ast.IfExp):
            t = self.value(node.test, env)
            if t.kind == 'bool': return self.value(node.body if t.value else node.orelse, env)
            a,b = self.value(node.body,env),self.value(node.orelse,env)
            if a == b:return a
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'copy' and not node.args and not node.keywords):
            source = self.value(node.func.value, env)
            if source.kind in ('input','copy'):
                return Value('copy', origin=source.origin, lines=(*source.lines,node.lineno))
        return UNKNOWN

    def add(self, profile, status, node, observations, **kw):
        self.findings.append({'profile_id':profile,'status':status,
            'qualified_name':self.name,'function_lines':[self.fn.lineno,self.fn.end_lineno],
            'evidence':[base.evidence(node,x) for x in observations],
            'runtime_verified':False,'scope':'selected local syntax/value flow; not runtime reachability',**kw})

    def expr(self, node, env):
        if node is None or isinstance(node,(ast.Lambda,ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):return
        if isinstance(node,ast.Call):
            target = self.value(node.func,env)
            if target.kind=='import' and target.value=='trimesh.registration.icp':
                kws = {k.arg:k.value for k in node.keywords if k.arg}
                known, unresolved, conflicts, assignment_lines = {},[],[],set()
                for key in ('scale','reflection'):
                    v=self.value(kws.get(key),env)
                    if v.kind=='bool':
                        known[key]=v.value;assignment_lines.update(v.lines)
                        if v.value:conflicts.append(key)
                    else:unresolved.append(key)
                if any(k.arg is None for k in node.keywords):unresolved.append('expanded_keywords')
                status='CONTRADICTED_CALL_CONTRACT' if conflicts else 'NEEDS_CONTEXT' if unresolved else 'STATIC_CANDIDATE'
                self.add(RIGID,status,node,['Imported Trimesh ICP call; literal/local boolean controls inspected.'],
                    known_controls=known,contradicted_controls=conflicts,unresolved=unresolved,
                    value_definition_lines=sorted(assignment_lines),
                    limitations=['Applies only to the call, not its caller, data units, physical accuracy or library implementation.'])
            if isinstance(node.func,ast.Attribute) and node.func.attr=='apply_scale':
                v=self.value(node.func.value,env)
                status={'input':'INPUT_ALIAS_RECEIVER','copy':'COPY_CALL_RESULT_RECEIVER'}.get(v.kind,'UNRESOLVED_RECEIVER')
                self.add(SCALE,status,node,['apply_scale method called; receiver provenance classified within this function.'],
                    origin_parameters=list(v.origin),value_definition_lines=list(v.lines),
                    unresolved=['Receiver runtime type and copy semantics unverified; returned data and callees need review.'],
                    limitations=['A copy() call is observed, not proof of a fresh or independent object. Unit conversion may be intentional.'])
        # Avoid laundering expression-local bindings from comprehensions/walrus into facts.
        if isinstance(node,(ast.ListComp,ast.SetComp,ast.DictComp,ast.GeneratorExp,ast.NamedExpr)):
            before=self.blocked_names.copy()
            self.blocked_names.update(base.bindings(ast.walk(node)))
            for child in ast.iter_child_nodes(node):self.expr(child,env)
            self.blocked_names=before
            return
        for child in ast.iter_child_nodes(node):self.expr(child,env)

    def assign(self,target,value,env,line):
        if isinstance(target,ast.Name):
            env[target.id] = Value(value.kind,value.value,value.origin,tuple(sorted(set((*value.lines,line))))) if value.kind!='unknown' else UNKNOWN
        elif isinstance(target,(ast.Tuple,ast.List)):
            for elt in target.elts:self.assign(elt,UNKNOWN,env,line)

    def nondecision(self,node):
        if not isinstance(node,ast.Dict):return
        allowed={'unknown','ambiguous','insufficient_geometry','no_match','review','reconciliation_required'}
        fields=[]
        for k,v in zip(node.keys,node.values):
            if not isinstance(k,ast.Constant) or not isinstance(k.value,str):continue
            if k.value in {'status','decision','assessment'} and isinstance(v,ast.Constant) and isinstance(v.value,str) and v.value.lower() in allowed:
                fields.append({'field':k.value,'value':v.value})
            elif k.value in {'pass_fail','physical_uncertainty_mm'} and isinstance(v,ast.Constant) and v.value is None:
                fields.append({'field':k.value,'value':None})
        if fields:
            self.add(ABSTAIN,'EXPLICIT_NONDECISION_LITERAL',node,['Return dictionary contains explicit uncertainty/nondecision literals.'],
                fields=fields,unresolved=['Conditions, caller interpretation and calibration of decisions not established.'],
                limitations=['A literal outcome is not evidence that all hard cases abstain correctly.'])

    def sequence(self,statements,env):
        env=env.copy()
        for s in statements:
            if isinstance(s,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                env[s.name]=UNKNOWN;continue
            if isinstance(s,ast.Return):
                self.expr(s.value,env);self.nondecision(s.value);break
            if isinstance(s,ast.Raise):self.expr(s,env);break
            if isinstance(s,ast.If):
                self.expr(s.test,env);condition=self.value(s.test,env)
                if condition.kind=='bool':env=self.sequence(s.body if condition.value else s.orelse,env)
                else:env=join(self.sequence(s.body,env),self.sequence(s.orelse,env))
            elif isinstance(s,(ast.Assign,ast.AnnAssign)):
                self.expr(s.value,env);v=self.value(s.value,env)
                for target in (s.targets if isinstance(s,ast.Assign) else [s.target]):self.assign(target,v,env,s.lineno)
            elif isinstance(s,ast.AugAssign):
                self.expr(s.value,env);self.assign(s.target,UNKNOWN,env,s.lineno)
            elif isinstance(s,(ast.For,ast.AsyncFor,ast.While,ast.Try,ast.TryStar,ast.With,ast.AsyncWith,ast.Match)):
                # Inspect literals inside unsupported flow, never infer stability of modified locals.
                changed=base.bindings(base.scoped_nodes([s]));before=self.blocked_names.copy()
                self.blocked_names.update(changed)
                branches=[]
                for field in ('body','orelse','finalbody'):
                    b=getattr(s,field,None)
                    if b:branches.append(b)
                for handler in getattr(s,'handlers',[]):branches.append(handler.body)
                for case in getattr(s,'cases',[]):branches.append(case.body)
                for b in branches:self.sequence(b,env)
                self.blocked_names=before
                for k in changed:env[k]=UNKNOWN
            elif isinstance(s,(ast.Import,ast.ImportFrom)):
                # Resolve only imports allowed by the conservative initial scope map.
                pass
            else:
                self.expr(s,env)
                for k in base.bindings(base.scoped_nodes([s])):env[k]=UNKNOWN
        return env


def scan_bytes(data:bytes,*,source_id:str)->dict:
    if not isinstance(data,bytes) or len(data)>base.MAX_SOURCE_BYTES:raise ValueError('SOURCE_BYTE_LIMIT')
    if not isinstance(source_id,str) or not source_id or len(source_id)>2000:raise ValueError('INVALID_SOURCE_ID')
    tree=ast.parse(data,filename='<approved-source>')
    if sum(1 for _ in ast.walk(tree))>base.MAX_NODES:raise ValueError('AST_NODE_LIMIT')
    results=[];count=0;digest=sha(data)
    for name,fn in base.functions(tree):
        count+=1;flow=LocalFlow(tree,fn,name);flow.sequence(fn.body,flow.env)
        # Keep the earlier unit and path detectors as compatibility profiles.
        aliases={k:v.value for k,v in flow.env.items() if v.kind=='import'}
        legacy=base.read_profiles(Path(__file__).with_name('profiles')/'baseline.json')
        for profile in legacy[1:]:
            for match in base.DETECTORS[profile['detector']](fn,aliases,profile):
                flow.findings.append({'profile_id':profile['id'],'qualified_name':name,
                    'function_lines':[fn.lineno,fn.end_lineno],'limitations':profile['limitations'],
                    'inherited_detector_version':base.VERSION,'runtime_verified':False,**match})
        for r in flow.findings:
            r.update(source_id=source_id,source_sha256=digest,detector_version=VERSION)
            r['finding_id']='finding_'+sha(canonical(r));results.append(r)
    return {'source_id':source_id,'source_sha256':digest,'functions_inspected':count,
        'evidence_level':'STATIC_ONLY','source_body_retained':False,'findings':results}


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--file',type=Path);p.add_argument('--source-id')
    a=p.parse_args()
    try:
        if a.worker:
            packet=json.loads(sys.stdin.buffer.read(base.MAX_SOURCE_BYTES*3+1))
            result=scan_bytes(bytes.fromhex(packet['source_hex']),source_id=packet['source_id'])
        else:
            if not a.file or not a.source_id:p.error('--file and --source-id required')
            from atlas_hunt import bounded_read, inspect_isolated
            result=inspect_isolated(bounded_read(a.file,base.MAX_SOURCE_BYTES),a.source_id)
        print(json.dumps(result,indent=2,allow_nan=False));return 0
    except (ValueError,SyntaxError,OSError,RecursionError,KeyError,TypeError) as exc:
        print(json.dumps({'status':'INSPECTION_FAILED','error_type':type(exc).__name__}),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
