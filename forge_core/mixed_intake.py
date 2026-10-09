"""Read-only, bounded mixed-container discovery. Never imports collected code.

Bodies in ``source_hex`` are transient worker transport, NOT persistent metadata.
Callers must remove them before checkpointing. Lines refer to the nearest text
member; notebook code uses cell-local lines. Cross-block dependencies are unknown.
"""
from __future__ import annotations
import ast,collections,hashlib,io,json,re,stat,tokenize,zipfile
from pathlib import PurePosixPath
from .common import Blocked,canonical,sha,loads

VERSION='mixed-intake-2-context'
DEFAULTS={'max_container_bytes':8_000_000,'max_member_bytes':2_000_000,'max_source_bytes':250_000,
          'max_total_bytes':25_000_000,'max_entries':2500,'max_depth':2,'max_segments':400,'max_ratio':200,'max_nodes':60_000}
GAP_STATES={'CONTAINER_SIZE_LIMIT','MEMBER_SIZE_LIMIT','SOURCE_SIZE_LIMIT','EXPANSION_LIMIT','ENTRY_LIMIT','DEPTH_LIMIT','RATIO_LIMIT','SEGMENT_LIMIT','UNSAFE_MEMBER','AMBIGUOUS_MEMBER','INVALID_ARCHIVE','ENCRYPTED_MEMBER','UNSUPPORTED_COMPRESSION','UNSUPPORTED_ENCODING','UNCLOSED_FENCE','PYTHON_SYNTAX_ERROR','AST_LIMIT','NOTEBOOK_LANGUAGE_UNSUPPORTED','NOTEBOOK_INVALID','READ_ERROR','LANGUAGE_NOT_ANALYZED','UNSUPPORTED_FORMAT'}
TEXT_EXT={'.txt','.md','.markdown','.rst'}
CODE_EXT={'.py','.pyi','.pyw'}
OTHER_CODE={'.js','.jsx','.ts','.tsx','.sh','.bash','.go','.rs','.c','.cpp','.h','.java','.cs','.sql','.yaml','.yml','.toml'}
VENDOR={'.git','.venv','venv','node_modules','site-packages','__pycache__','vendor','__macosx'}
# Accidental-disclosure lint: skip files whose names usually hold credentials. Name-based
# only, so it is not a credential-separation or DLP boundary (docs/THREAT_MODEL.md).
SECRET_NAMES={'.env','id_rsa','id_ed25519','credentials.py','credentials.json','secrets.json','secrets.py','tokens.json'}
STOP={'the','a','an','of','for','to','and','or','in','is','are','this','that','with','from','return','returns','self','none','true','false','def','class'}

def tokens(value):
    value=re.sub(r'([a-z])([A-Z])',r'\1 \2',value)
    return sorted(set(t for t in re.findall(r'[a-z][a-z0-9]{1,39}',value.lower()) if t not in STOP))

def _decode(data):
    if data.startswith((b'\xff\xfe',b'\xfe\xff')):return data.decode('utf-16'),'utf-16-bom'
    encoding,_=tokenize.detect_encoding(io.BytesIO(data).readline)
    return data.decode(encoding),encoding

def _path_ok(name):
    if not isinstance(name,str) or not name or len(name)>600 or '\\' in name or ':' in name or any(ord(c)<32 for c in name):return False
    p=PurePosixPath(name)
    return not p.is_absolute() and '..' not in p.parts and p.as_posix()==name.rstrip('/') and name.rstrip('/') not in ('','.','..')

def _excluded(name):
    p=PurePosixPath(name);low=p.name.lower()
    if low in SECRET_NAMES or low.startswith('.env.') or low.endswith(('.pem','.key','.p12','.pfx')):return 'SENSITIVE_PATH_EXCLUDED'
    if set(x.lower() for x in p.parts)&VENDOR:return 'DEPENDENCY_OR_GENERATED_EXCLUDED'
    return None

def _definitions(tree):
    rows=[]
    class Walk(ast.NodeVisitor):
        def __init__(self):self.parents=[]
        def visit_ClassDef(self,n):
            self.parents.append(n.name);self.generic_visit(n);self.parents.pop()
        def visit_FunctionDef(self,n):
            args=n.args
            params=[a.arg for a in [*args.posonlyargs,*args.args,*args.kwonlyargs]]
            if args.vararg:params.append('*'+args.vararg.arg)
            if args.kwarg:params.append('**'+args.kwarg.arg)
            doc=ast.get_docstring(n) or ''
            # A declaration, not an AI interpretation or execution result.
            rows.append({'name':'.'.join([*self.parents,n.name]),'start_line':min([n.lineno,*[d.lineno for d in n.decorator_list]]),'definition_line':n.lineno,'end_line':n.end_lineno,'parameters':params,
                         'declared_terms':tokens(n.name+' '+doc)[:100],'evidence':'STRUCTURE_AND_DOCUMENTED_WORDS','runtime_status':'NOT_RUN'})
            self.parents.append(n.name);self.generic_visit(n);self.parents.pop()
        visit_AsyncFunctionDef=visit_FunctionDef
    Walk().visit(tree)
    return rows

class Reader:
    def __init__(self,name,limits):
        self.name=name;self.limits=DEFAULTS|dict(limits or {});self.segments=[];self.inventory=[];self.expanded=0;self.entries=0
        if set(self.limits)!=set(DEFAULTS):raise Blocked('UNKNOWN_INTAKE_LIMIT')
        for k,v in self.limits.items():
            if type(v) is not int or not 1<=v<=DEFAULTS[k]:raise Blocked('INVALID_INTAKE_LIMIT')
    def note(self,path,status,chain,**extra):
        self.inventory.append({'path':path,'status':status,'member_chain':chain,**extra})
    def add(self,code,path,chain,fmt,start_line=1,end_line=None,hint=None,cell=None,encoding='utf-8'):
        b=code.encode('utf-8')
        if len(b)>self.limits['max_source_bytes']:self.note(path,'SOURCE_SIZE_LIMIT',chain);return
        if len(self.segments)>=self.limits['max_segments']:self.note(path,'SEGMENT_LIMIT',chain);return
        try:
            tree=ast.parse(code)
            if sum(1 for _ in ast.walk(tree))>self.limits['max_nodes']:raise Blocked('AST_LIMIT')
        except (SyntaxError,ValueError,RecursionError,MemoryError,Blocked) as e:
            self.note(path,'AST_LIMIT' if isinstance(e,(MemoryError,RecursionError,Blocked)) else 'PYTHON_SYNTAX_ERROR',chain,start_line=start_line,syntax_line=getattr(e,'lineno',None));return
        meaningful=any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Import,ast.ImportFrom,ast.Assign,ast.AnnAssign,ast.AugAssign,ast.Call)) for n in ast.walk(tree))
        if fmt=='python_text' and not meaningful:self.note(path,'CONTEXT_ONLY',chain);return
        from .structure import describe, declared_apis
        module_structure, definition_structure = describe(tree)
        definitions = _definitions(tree)
        for definition in definitions:
            detail = definition_structure[(definition['name'], definition['definition_line'])]
            detail['declared_api_leads'] = declared_apis(detail, module_structure)
            definition['structure'] = detail
        imports=[]
        for n in ast.walk(tree):
            if isinstance(n,ast.Import):imports.extend(a.name for a in n.names)
            elif isinstance(n,ast.ImportFrom):imports.extend('.'*n.level+(n.module+'.' if n.module else '')+a.name for a in n.names)
        lines=code.splitlines();end_line=end_line if end_line is not None else start_line+max(0,len(lines)-1)
        locator={'member_chain':chain,'format':fmt,'start_line':start_line,'end_line':end_line,'cell_index':cell,'source_sha256':sha(b)}
        seg={'segment_local_id':'segment_'+sha(canonical(locator)),**locator,'path':path,'logical_path_hint':hint,'encoding':encoding,'source_hex':b.hex(),
             'definitions':definitions,'imports':sorted(set(imports)), 'module_structure':module_structure,
             'script_present':any(not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Import,ast.ImportFrom)) and not (isinstance(n,ast.Expr) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str)) for n in tree.body),
             'dependency_scope':'isolated_segment; adjacent blocks are NOT concatenated',
             'test_path':any(p.lower() in ('test','tests') for p in PurePosixPath(hint or path).parts) or PurePosixPath(hint or path).name.lower().startswith('test_')}
        self.segments.append(seg);self.note(path,'PYTHON_SEGMENT',chain,segment_local_id=seg['segment_local_id'],start_line=start_line,end_line=end_line,cell_index=cell)
    def text(self,data,path,chain,ext):
        try:t,enc=_decode(data)
        except (UnicodeError,SyntaxError,LookupError):self.note(path,'UNSUPPORTED_ENCODING',chain);return
        if '\x00' in t:self.note(path,'UNSUPPORTED_ENCODING',chain);return
        if ext in CODE_EXT:
            self.add(t,path,chain,'python_file',encoding=enc);return
        # Fences are explicit source boundaries, not instructions to execute.
        lines=t.splitlines(keepends=True);active=None;seen_fence=False
        for i,line in enumerate(lines):
            match=re.match(r'^ {0,3}(`{3,}|~{3,})([^\r\n]*)[\r\n]*$',line)
            if not match:continue
            mark,info=match.groups();info=info.strip()
            if active:
                if mark[0]==active['char'] and len(mark)>=active['length'] and not info:
                    if active['python']:
                        self.add(''.join(lines[active['line']+1:i]),path,chain,'python_fence',active['line']+2,i,active['hint'],encoding=enc)
                    else:self.note(path,'LANGUAGE_NOT_ANALYZED' if active['language'] not in ('','text','txt') else 'NON_CODE_BLOCK',chain,start_line=active['line']+2,end_line=i,declared_language=active['language'])
                    active=None
                continue
            seen_fence=True
            preceding=next((x.strip() for x in reversed(lines[max(0,i-4):i]) if x.strip()),'')
            # Only a standalone filename heading labels an untyped fence. An
            # incidental filename in prose must not classify math/logs as code.
            h=re.fullmatch(r'(?:#{1,6}\s*)?(?:[0-9]+[.)]\s*)?`?([\w./-]+\.(?:py|pyi|pyw))`?(?:\s*\([^\r\n]*\))?\s*:?',preceding)
            hint=h.group(1) if h and _path_ok(h.group(1)) else None
            language=info.lower().split()[0] if info else ''
            active={'line':i,'char':mark[0],'length':len(mark),'language':language,'hint':hint,'python':language in ('python','py','python3') or (not language and hint is not None)}
        if active:self.note(path,'UNCLOSED_FENCE',chain,start_line=active['line']+2)
        if not seen_fence:
            try:tree=ast.parse(t)
            except (SyntaxError,ValueError,RecursionError,MemoryError):self.note(path,'CONTEXT_ONLY',chain);return
            if any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Import,ast.ImportFrom,ast.Assign,ast.AnnAssign,ast.Call)) for n in ast.walk(tree)):
                self.add(t,path,chain,'python_text',encoding=enc)
            else:self.note(path,'CONTEXT_ONLY',chain)
        else:self.note(path,'TEXT_CONTAINER_INSPECTED',chain)
    def notebook(self,data,path,chain):
        try:
            o=loads(data)
            if not isinstance(o,dict):raise ValueError()
            m=o.get('metadata',{});lang=m.get('language_info',{}).get('name',m.get('kernelspec',{}).get('language'))
            if lang not in ('python','python3'):self.note(path,'NOTEBOOK_LANGUAGE_UNSUPPORTED',chain);return
            cells=o['cells']
            if not isinstance(cells,list) or len(cells)>500:raise ValueError()
            for i,c in enumerate(cells):
                if c.get('cell_type')!='code':continue
                src=c.get('source','');src=''.join(src) if isinstance(src,list) and all(isinstance(x,str) for x in src) else src
                if not isinstance(src,str):raise ValueError()
                self.add(src,path,chain,'notebook_cell',cell=i)
        except (ValueError,TypeError,KeyError,AttributeError):self.note(path,'NOTEBOOK_INVALID',chain)
    def visit(self,data,path,chain,depth=0):
        excluded=_excluded(path)
        if excluded:self.note(path,excluded,chain);return
        ext=PurePosixPath(path).suffix.lower()
        if ext=='.zip':
            if depth>self.limits['max_depth']:self.note(path,'DEPTH_LIMIT',chain);return
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    es=z.infolist()
                    if self.entries+len(es)>self.limits['max_entries']:self.note(path,'ENTRY_LIMIT',chain);return
                    self.entries+=len(es);counts=collections.Counter(e.filename for e in es)
                    for e in es:
                        label=e.filename
                        if not _path_ok(label) or stat.S_IFMT(e.external_attr>>16) not in (0,stat.S_IFREG,stat.S_IFDIR):self.note(label,'UNSAFE_MEMBER',chain);continue
                        if counts[label]!=1:self.note(label,'AMBIGUOUS_MEMBER',chain);continue
                        if e.is_dir():continue
                        ex=_excluded(label)
                        if ex:self.note(label,ex,chain);continue
                        suffix=PurePosixPath(label).suffix.lower()
                        if suffix not in CODE_EXT|TEXT_EXT|{'.zip','.ipynb'}:
                            self.note(label,'LANGUAGE_NOT_ANALYZED' if suffix in OTHER_CODE else 'NON_CODE_PAYLOAD_NOT_READ',chain,declared_size=e.file_size);continue
                        if e.flag_bits&1:self.note(label,'ENCRYPTED_MEMBER',chain);continue
                        if e.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED,zipfile.ZIP_BZIP2,zipfile.ZIP_LZMA):self.note(label,'UNSUPPORTED_COMPRESSION',chain);continue
                        if e.file_size>self.limits['max_member_bytes']:self.note(label,'MEMBER_SIZE_LIMIT',chain);continue
                        if e.file_size/max(1,e.compress_size)>self.limits['max_ratio']:self.note(label,'RATIO_LIMIT',chain);continue
                        if self.expanded+e.file_size>self.limits['max_total_bytes']:self.note(label,'EXPANSION_LIMIT',chain);continue
                        try:
                            with z.open(e) as f:b=f.read(min(self.limits['max_member_bytes'],e.file_size)+1)
                            if len(b)!=e.file_size:self.note(label,'READ_ERROR',chain);continue
                        except (OSError,ValueError,RuntimeError,zipfile.BadZipFile,NotImplementedError):self.note(label,'READ_ERROR',chain);continue
                        self.expanded+=len(b)
                        self.visit(b,label,[*chain,{'path':label,'sha256':sha(b)}],depth+1)
            except (zipfile.BadZipFile,OSError,ValueError,NotImplementedError):self.note(path,'INVALID_ARCHIVE',chain)
        elif ext in CODE_EXT|TEXT_EXT:self.text(data,path,chain,ext)
        elif ext=='.ipynb':self.notebook(data,path,chain)
        elif not ext and data.startswith((b'#!/usr/bin/env python',b'#!/usr/bin/python')):self.text(data,path,chain,'.py')
        else:self.note(path,'LANGUAGE_NOT_ANALYZED' if ext in OTHER_CODE else 'UNSUPPORTED_FORMAT',chain)

def inspect_container(data,name,limits=None):
    if not isinstance(data,bytes) or not _path_ok(name):raise Blocked('CONTAINER_INPUT')
    r=Reader(name,limits)
    if len(data)>r.limits['max_container_bytes']:r.note(name,'CONTAINER_SIZE_LIMIT',[])
    else:r.visit(data,name,[])
    return {'schema':1,'version':VERSION,'container_sha256':sha(data),'segments':r.segments,'inventory':r.inventory,
            'gaps':[i for i in r.inventory if i['status'] in GAP_STATES],'expanded_bytes':r.expanded,'entries_seen':r.entries,
            'upstream_code_executed':False,'release_approved':False}
