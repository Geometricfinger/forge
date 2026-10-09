"""Separate source-scope observations; no invented function or executable source.

A synthetic AST scope lets the existing local interpreter inspect module/main-
guard statements without importing the module or attributing them to a function.
These are possible lexical operations, not assertions that the script ran.
"""
from __future__ import annotations
import ast, hashlib, json
import baseline_sniffers as base
from flow_core import LocalFlow, PRIMITIVES

def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(v):return hashlib.sha256(v).hexdigest()

def inspect(tree,data,sid,version,library):
 scope=ast.parse('def _analysis_scope():\n pass\n').body[0]
 scope.body=tree.body;scope.lineno=1;scope.end_lineno=max(1,len(data.splitlines()))
 flow=LocalFlow(tree,scope,'<module>',module_writes=set(),library=library)
 flow.env={};flow.sequence(scope.body,flow.env)
 out=[]
 for f in flow.findings:
  if f['profile_id']!=PRIMITIVES:continue
  f.pop('qualified_name',None);f.pop('function_lines',None)
  f.update(scope_kind='script',scope_name='<module>',source_scope_lines=[1,scope.end_lineno],
           source_id=sid,source_sha256=sha(data),detector_version=version,
           execution_status='NOT_EXECUTED_CONDITIONAL_ENTRY_PATHS_POSSIBLE')
  f['finding_id']='finding_'+sha(canonical(f));out.append(f)
 return out

def validate(records,sid,digest,profiles):
 if not isinstance(records,list):raise ValueError('SCRIPT_FINDING_LIST')
 seen=set()
 for f in records:
  if not isinstance(f,dict) or {'card_id','function_id','function_lines','qualified_name'}&set(f):raise ValueError('SCRIPT_NOT_A_FUNCTION')
  if f.get('scope_kind')!='script' or f.get('scope_name')!='<module>' or f.get('source_id')!=sid or f.get('source_sha256')!=digest or f.get('runtime_verified') is not False:raise ValueError('SCRIPT_BINDING')
  if f.get('profile_id') not in profiles:raise ValueError('SCRIPT_PROFILE')
  r=f.get('source_scope_lines')
  if not isinstance(r,list) or len(r)!=2 or any(type(v) is not int for v in r) or r[0]!=1 or r[1]<1:raise ValueError('SCRIPT_RANGE')
  ev=f.get('evidence')
  if not isinstance(ev,list) or not ev:raise ValueError('SCRIPT_EVIDENCE')
  for e in ev:
   if not isinstance(e,dict) or any(type(e.get(k)) is not int for k in ('line_start','line_end')) or not 1<=e['line_start']<=e['line_end']<=r[1]:raise ValueError('SCRIPT_EVIDENCE_RANGE')
  core={k:v for k,v in f.items() if k!='finding_id'};fid='finding_'+sha(canonical(core))
  if f.get('finding_id')!=fid or fid in seen:raise ValueError('SCRIPT_FINDING_DIGEST_OR_DUPLICATE')
  seen.add(fid)

def bind(records,source,data,profiles):
 sid=source['source_id'];digest=sha(data)
 if digest!=source['sha256']:raise ValueError('SOURCE_VERSION_MISMATCH')
 validate(records,sid,digest,profiles)
 out=[]
 for f in records:
  if f['source_scope_lines']!=[1,max(1,len(data.splitlines()))]:raise ValueError('SCRIPT_RANGE_MISMATCH')
  row={**f,'observation_id':source['observation_id'],'project_id':source['project_id'],
       'source_location':source.get('url'),'display_path':source.get('display_path')}
  row.pop('finding_id');row['finding_id']='finding_'+sha(canonical(row));out.append(row)
 return out
