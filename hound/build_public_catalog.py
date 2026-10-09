"""Build a separate metadata-only Atlas for a reviewed list of pinned public inputs.

This is a fixed-trial ingestion tool, not a live GitHub crawler. Verify both
SHA-256 and Git blob identities before cataloging. Never import target code.
"""
from __future__ import annotations
import ast,argparse,hashlib,json,os,sqlite3
from pathlib import Path
import hound,atlas_hunt
ROOT=Path(__file__).resolve().parent

def manifest(path):
 """Read a reviewed entry list: [{file, repository, commit, path, source_id, sha256, git_blob_sha1, url}, ...]."""
 rows=json.loads(Path(path).read_text())
 if not isinstance(rows,list):raise ValueError('MANIFEST_LIST_REQUIRED')
 return rows

def build(source_root,out,entries=None):
 if entries is None:raise ValueError('NONEMPTY_BOUNDED_ENTRIES_REQUIRED')
 if not isinstance(entries,list) or not entries or len(entries)>100:raise ValueError('NONEMPTY_BOUNDED_ENTRIES_REQUIRED')
 if out.exists() or out.is_symlink() or out.resolve().is_relative_to(source_root.resolve()) or out.resolve().is_relative_to(ROOT):raise ValueError('NEW_SEPARATE_OUTPUT_REQUIRED')
 sources=[];cards=[];locators={};seen=set();bodies=[]
 for e in entries:
  sid=e['source_id']
  if sid in seen:raise ValueError('DUPLICATE_SOURCE_ID')
  seen.add(sid)
  if sid!=f"github:{e['repository']}@{e['commit']}:{e['path']}" or hound.library_rules.package_from_source_id(sid) is None:raise ValueError('SOURCE_LOCATOR_CONTEXT')
  data=atlas_hunt.bounded_read(atlas_hunt.source_path(source_root,e['file']),atlas_hunt.MAX_SOURCE)
  if hound.sha(data)!=e['sha256'] or hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()!=e['git_blob_sha1']:raise ValueError('SOURCE_VERSION_MISMATCH')
  tree=ast.parse(data)
  if sum(1 for _ in ast.walk(tree))>hound.base.MAX_NODES:raise ValueError('AST_NODE_LIMIT')
  oid='observation_'+hound.sha(hound.canonical([sid,e['sha256']]))
  project=e['repository']+'@'+e['commit']
  s={'source_id':sid,'observation_id':oid,'project_id':project,'sha256':e['sha256'],'relative_path':e['path'],'display_path':e['repository']+'/'+e['path'],'url':e['url'],'git_blob_sha1':e['git_blob_sha1'],'commit':e['commit'],'runtime_verified':False}
  sources.append(s);locators[sid]={'path':e['file']};bodies.append((e['file'],data))
  for name,fn in hound.base.functions(tree):
   start=min([fn.lineno,*[d.lineno for d in fn.decorator_list]])
   fid='function_'+hound.sha(hound.canonical([sid,name,fn.lineno,fn.end_lineno]));cid='card_'+hound.sha(hound.canonical([fid,oid]))
   cards.append({'card_id':cid,'function_id':fid,'source_id':sid,'observation_id':oid,'project_id':project,'qualified_name':name,'relative_path':e['path'],'source_range':{'start':start,'end':fn.end_lineno},'parameters':[a.arg for a in (*fn.args.posonlyargs,*fn.args.args,*fn.args.kwonlyargs)],'contract':{'units':'UNKNOWN','runtime_tests':'NOT_RUN','source_scope':'selected public file'},'module_imports':[],'lexical_calls':[],'local_bindings':[]})
 for name,data in bodies:
  if atlas_hunt.bounded_read(atlas_hunt.source_path(source_root,name),atlas_hunt.MAX_SOURCE)!=data:raise ValueError('SOURCE_CHANGED_DURING_CAPTURE')
 out.mkdir(parents=True,mode=0o700);db=out/'public_catalog.sqlite'
 c=sqlite3.connect(db)
 try:
  c.executescript('CREATE TABLE sources(id TEXT PRIMARY KEY,current_observation TEXT,active INT,payload TEXT); CREATE TABLE cards(id TEXT PRIMARY KEY,source_id TEXT,observation_id TEXT,payload TEXT);')
  c.executemany('INSERT INTO sources VALUES(?,?,?,?)',[(s['source_id'],s['observation_id'],1,json.dumps(s)) for s in sources])
  c.executemany('INSERT INTO cards VALUES(?,?,?,?)',[(x['card_id'],x['source_id'],x['observation_id'],json.dumps(x)) for x in cards]);c.commit()
  if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('CATALOG_INTEGRITY')
 finally:c.close()
 db.chmod(0o600)
 (out/'source_locators.json').write_text(json.dumps(locators,indent=2))
 receipt={'sources':len(sources),'cards':len(cards),'catalog_sha256':hound.sha(db.read_bytes()),'source_bodies_retained':False,'source_executed':False,'rights_reviewed':False,'scope':'Reviewed pinned source list; not a recursive or live repository inventory.'}
 (out/'capture_receipt.json').write_text(json.dumps(receipt,indent=2));return receipt

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--source-root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 try:print(json.dumps(build(a.source_root,a.out,manifest(a.manifest)),indent=2))
 except (ValueError,OSError,KeyError,SyntaxError) as e:print(json.dumps({'status':'BLOCKED','reason':str(e)}));raise SystemExit(2)
