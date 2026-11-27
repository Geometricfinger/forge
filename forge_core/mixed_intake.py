"""Read-only, bounded mixed-container discovery. Never imports collected code.

Bodies in ``source_hex`` are transient worker transport, NOT persistent metadata.
Callers must remove them before checkpointing. Lines refer to the nearest text
member; notebook code uses cell-local lines. Cross-block dependencies are unknown.
"""
from __future__ import annotations
import ast,bisect,collections,hashlib,io,json,re,stat,time,tokenize,zipfile
from pathlib import PurePosixPath
from .common import Blocked,canonical,sha,loads

VERSION='mixed-intake-7-unit-capacity'
MAX_RESULT_BYTES=16_000_000
# Optional structural growth must leave the original bounded result available.
# These are additional growth budgets, not higher parser/worker permissions.
EXTENDED_RESULT_BYTES=14_000_000
EXTENSION_CPU_SECONDS=3.0
EXTENSION_CPU_WINDOW=7.0
FALLBACK_POLICIES={'RESULT_BUDGET':'unit-capacity-v2-bytes',
                   'CPU_BUDGET':'unit-capacity-v2-cpu',
                   'RESOURCE_LIMIT':'unit-capacity-v2-resource'}
DEFAULTS={'max_container_bytes':8_000_000,'max_member_bytes':2_000_000,'max_source_bytes':250_000,
          'max_module_bytes':2_000_000,'max_total_bytes':25_000_000,'max_entries':2500,'max_depth':2,'max_segments':400,'max_ratio':200,'max_nodes':60_000}
GAP_STATES={'CONTAINER_SIZE_LIMIT','MEMBER_SIZE_LIMIT','SOURCE_SIZE_LIMIT','MODULE_SIZE_LIMIT','DEFINITION_SIZE_LIMIT','EXPANSION_LIMIT','ENTRY_LIMIT','DEPTH_LIMIT','RATIO_LIMIT','SEGMENT_LIMIT','UNSAFE_MEMBER','AMBIGUOUS_MEMBER','INVALID_ARCHIVE','ENCRYPTED_MEMBER','UNSUPPORTED_COMPRESSION','UNSUPPORTED_ENCODING','UNCLOSED_FENCE','PYTHON_SYNTAX_ERROR','AST_LIMIT','AST_UNIT_LIMIT','MODULE_CONTEXT_LIMIT','STRUCTURAL_UNIT_COUNT_LIMIT','NOTEBOOK_LANGUAGE_UNSUPPORTED','NOTEBOOK_INVALID','READ_ERROR','LANGUAGE_NOT_ANALYZED','UNSUPPORTED_FORMAT'}
TEXT_EXT={'.txt','.md','.markdown','.rst'}
CODE_EXT={'.py','.pyi','.pyw'}
OTHER_CODE={'.js','.jsx','.ts','.tsx','.sh','.bash','.go','.rs','.c','.cpp','.h','.java','.cs','.sql','.yaml','.yml','.toml'}
VENDOR={'.git','.venv','venv','node_modules','site-packages','__pycache__','vendor','__macosx'}
# Accidental-disclosure lint: skip files whose names usually hold credentials. Name-based
# only, so it is not a credential-separation or DLP boundary (docs/THREAT_MODEL.md).
SECRET_NAMES={'.env','id_rsa','id_ed25519','credentials.py','credentials.json','secrets.json','secrets.py','tokens.json'}
STOP={'the','a','an','of','for','to','and','or','in','is','are','this','that','with','from','return','returns','self','none','true','false','def','class'}

class _ExtensionAborted(Exception):
    """Optional growth stopped; never turn this into an AST or syntax gap."""

def _module_key(path,chain,fmt,start_line,cell):
    return path,canonical(chain),fmt,start_line,cell

def _segment_key(segment):
    return _module_key(segment['path'],segment['member_chain'],segment['format'],
                       segment['start_line'],segment['cell_index'])

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

def _decorator_statements(code):
    """Find whole decorator statements, whose AST expression may start later.

    Normalize physical newlines only in this transient token view. Source
    windows still slice the original UTF-8 bytes without rewriting them.
    Only an @ at the beginning of a logical statement is a decorator; matrix
    operators and text inside strings cannot supply a declaration boundary.
    """
    starts=[];ends=[];first=True;start=None
    text=re.sub(r'\r\n|\r','\n',code)
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type==tokenize.NEWLINE:
                if start is not None:starts.append(start);ends.append(token.end[0])
                first=True;start=None
            elif token.type in (tokenize.NL,tokenize.COMMENT,tokenize.INDENT,tokenize.DEDENT,tokenize.ENDMARKER):
                continue
            elif first:
                if token.type==tokenize.OP and token.string=='@':start=token.start[0]
                first=False
    except (tokenize.TokenError,IndentationError,SyntaxError) as exc:
        raise Blocked('DECORATOR_SOURCE_BOUNDARY') from exc
    return starts,ends

def _definitions(tree,code,decorator_statements=None):
    rows=[];decorated=[]
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
            if n.decorator_list:decorated.append((rows[-1],min(d.lineno for d in n.decorator_list)))
            self.parents.append(n.name);self.generic_visit(n);self.parents.pop()
        visit_AsyncFunctionDef=visit_FunctionDef
    Walk().visit(tree)
    if decorated:
        starts,ends=decorator_statements if decorator_statements is not None else _decorator_statements(code)
        for row,expression_line in decorated:
            index=bisect.bisect_right(starts,expression_line)-1
            if index<0 or expression_line>ends[index]:raise Blocked('DECORATOR_SOURCE_BOUNDARY')
            row['start_line']=starts[index]
    return rows

def _source_windows(data,definitions,limit):
    """Complete original lines, in UTF-8 bytes; no dedenting or executable slices.

    Python line numbers recognize CR/LF, not all Unicode str.splitlines breaks.
    Windows can overlap (nested definitions); their byte lengths are not coverage.
    """
    offsets=[0,*[m.end() for m in re.finditer(b'\r\n|\r|\n',data)]]
    if offsets[-1]!=len(data):offsets.append(len(data))
    module_hash=sha(data)
    for definition in definitions:
        start=offsets[definition['start_line']-1]
        end=offsets[definition['end_line']]
        definition['source_window']={
            'status':'AVAILABLE' if end-start<=limit else 'DEFINITION_SIZE_LIMIT',
            'byte_start':start,'byte_end':end,'bytes':end-start,
            'sha256':sha(data[start:end]),'module_sha256':module_hash,
            'byte_basis':'UTF8_SEGMENT_SOURCE','complete':True}

class Reader:
    def __init__(self,name,limits,*,unit_limit=None,check_budget=None):
        from .structural_units import MAX_UNITS
        self.name=name;self.limits=DEFAULTS|dict(limits or {});self.segments=[];self.inventory=[];self.expanded=0;self.entries=0
        self.unit_limit=MAX_UNITS if unit_limit is None else unit_limit
        self.check_budget=check_budget;self.module_inventory={};self.syntax_refusals={}
        if set(self.limits)!=set(DEFAULTS):raise Blocked('UNKNOWN_INTAKE_LIMIT')
        for k,v in self.limits.items():
            if type(v) is not int or not 1<=v<=DEFAULTS[k]:raise Blocked('INVALID_INTAKE_LIMIT')
    def note(self,path,status,chain,**extra):
        self.inventory.append({'path':path,'status':status,'member_chain':chain,**extra})
    def add(self,code,path,chain,fmt,start_line=1,end_line=None,hint=None,cell=None,encoding='utf-8',fence_indent=0):
        if self.check_budget is not None:self.check_budget()
        inventory_start=len(self.inventory)
        module_key=_module_key(path,chain,fmt,start_line,cell)
        b=code.encode('utf-8')
        # Preserve one module identity and AST context. Large modules are not
        # rewritten as standalone function programs and never sent to Hound.
        if len(b)>self.limits['max_module_bytes']:
            self.note(path,'MODULE_SIZE_LIMIT',chain,stage='structural_parsing',source_bytes=len(b),start_line=start_line);return
        analysis=b;analysis_view=None
        if fence_indent:
            if fmt!='python_fence':raise Blocked('FENCE_ANALYSIS_VIEW')
            from .source_views import fence_view
            analysis,analysis_view=fence_view(b,fence_indent)
            if len(analysis)>self.limits['max_module_bytes']:
                self.note(path,'MODULE_SIZE_LIMIT',chain,stage='structural_parsing',source_bytes=len(b),
                          analysis_bytes=len(analysis),start_line=start_line);return
        analysis_code=analysis.decode('utf-8')
        large=max(len(b),len(analysis))>self.limits['max_source_bytes']
        if large:self.note(path,'SOURCE_SIZE_LIMIT',chain,stage='api_analysis',source_bytes=len(b),
                           **({'analysis_bytes':len(analysis)} if analysis_view else {}),start_line=start_line,structural_analysis='ATTEMPTED')
        if len(self.segments)>=self.limits['max_segments']:self.note(path,'SEGMENT_LIMIT',chain);return
        from .structure import describe, declared_apis, prepare_module
        from .structural_units import FAST_MODULE_BYTES, admitted_trees, node_count, plan
        planned=None
        try:
            tree=None
            # Complete units avoid a combined AST across distinct units. An
            # indivisible unit may span the file under fixed worker limits.
            # Keep the single-parse path for admitted ordinary-size modules.
            if max(len(b),len(analysis))<=FAST_MODULE_BYTES:
                tree=ast.parse(analysis_code)
                if node_count(tree,self.limits['max_nodes'])>self.limits['max_nodes']:tree=None
            if tree is None:
                planned=plan(analysis_code,self.limits['max_nodes'],self.unit_limit,
                             check_budget=self.check_budget)
                module_context=prepare_module(planned['envelope'],planned['metadata']['module_context_complete'])
                module_structure=module_context[0]
                definition_structure={};definitions=[];imports=[]
                decorators=_decorator_statements(analysis_code)
                for tree in admitted_trees(analysis_code,planned,check_budget=self.check_budget):
                    _,details=describe(tree,module_context)
                    if self.check_budget is not None:self.check_budget()
                    definition_structure.update(details)
                    definitions.extend(_definitions(tree,analysis_code,decorators))
                    for n in ast.walk(tree):
                        if isinstance(n,ast.Import):imports.extend(a.name for a in n.names)
                        elif isinstance(n,ast.ImportFrom):imports.extend('.'*n.level+(n.module+'.' if n.module else '')+a.name for a in n.names)
                    del tree
                meaningful=planned['meaningful'];script_present=planned['script_present']
            else:
                meaningful=any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Import,ast.ImportFrom,ast.Assign,ast.AnnAssign,ast.AugAssign,ast.Call)) for n in ast.walk(tree))
                module_structure,definition_structure=describe(tree)
                definitions=_definitions(tree,analysis_code)
                imports=[]
                for n in ast.walk(tree):
                    if isinstance(n,ast.Import):imports.extend(a.name for a in n.names)
                    elif isinstance(n,ast.ImportFrom):imports.extend('.'*n.level+(n.module+'.' if n.module else '')+a.name for a in n.names)
                script_present=any(not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Import,ast.ImportFrom)) and not (isinstance(n,ast.Expr) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str)) for n in tree.body)
        except (SyntaxError,ValueError,RecursionError,MemoryError,Blocked) as e:
            if self.check_budget is not None and isinstance(e,(MemoryError,RecursionError)):
                raise _ExtensionAborted('RESOURCE_LIMIT') from None
            status='AST_LIMIT' if isinstance(e,(MemoryError,RecursionError,Blocked)) else 'CONTEXT_ONLY' if fmt=='python_text' else 'PYTHON_SYNTAX_ERROR'
            self.note(path,status,chain,start_line=start_line,syntax_line=getattr(e,'lineno',None))
            if isinstance(e,(SyntaxError,ValueError)):
                self.syntax_refusals[module_key]=self.inventory[inventory_start:]
            return
        if fmt=='python_text' and not meaningful:self.note(path,'CONTEXT_ONLY',chain);return
        if planned is not None:
            if analysis_view is not None:
                from .source_views import partition_source_windows
                planned['metadata']=partition_source_windows(planned['metadata'],b,analysis,analysis_view)
            for unit in planned['metadata']['units']:
                if unit['status']=='ANALYZED':continue
                extra={k:v for k,v in unit.items() if k not in ('status','start_line','end_line','index')}
                self.note(path,unit['status'],chain,stage='structural_parsing',unit_index=unit['index'],
                          start_line=start_line+unit['start_line']-1,end_line=start_line+unit['end_line']-1,
                          definition_count_known=False,**extra)
            if planned['metadata']['envelope_limited']:
                self.note(path,'MODULE_CONTEXT_LIMIT',chain,stage='module_context',start_line=start_line,
                          max_nodes=self.limits['max_nodes'],shared_bindings_complete=False)
        _source_windows(b,definitions,self.limits['max_source_bytes'])
        for definition in definitions:
            if definition['source_window']['status']!='AVAILABLE':
                self.note(path,'DEFINITION_SIZE_LIMIT',chain,stage='definition_inspection',name=definition['name'],
                          start_line=start_line+definition['start_line']-1,end_line=start_line+definition['end_line']-1,
                          source_bytes=definition['source_window']['bytes'])
        for definition in definitions:
            detail = definition_structure[(definition['name'], definition['definition_line'])]
            detail['declared_api_leads'] = declared_apis(detail, module_structure)
            definition['structure'] = detail
        line_count=len(re.findall(r'\r\n|\r|\n',code))+int(bool(code) and not code.endswith(('\n','\r')))
        end_line=end_line if end_line is not None else start_line+max(0,line_count-1)
        locator={'member_chain':chain,'format':fmt,'start_line':start_line,'end_line':end_line,'cell_index':cell,'source_sha256':sha(b)}
        seg={'segment_local_id':'segment_'+sha(canonical(locator)),**locator,'path':path,'logical_path_hint':hint,'encoding':encoding,'source_hex':b.hex(),
             'large_module':large,'source_bytes':len(b),
             'definitions':definitions,'imports':sorted(set(imports)), 'module_structure':module_structure,
             'script_present':script_present,
             'dependency_scope':'isolated_segment; adjacent blocks are NOT concatenated',
             'test_path':any(p.lower() in ('test','tests') for p in PurePosixPath(hint or path).parts) or PurePosixPath(hint or path).name.lower().startswith('test_')}
        if planned is not None:seg['structural_partition']=planned['metadata']
        if analysis_view is not None:seg['analysis_view']=analysis_view
        self.segments.append(seg);self.note(path,'PYTHON_SEGMENT',chain,segment_local_id=seg['segment_local_id'],start_line=start_line,end_line=end_line,cell_index=cell)
        self.module_inventory[module_key]=self.inventory[inventory_start:]
    def text(self,data,path,chain,ext):
        try:t,enc=_decode(data)
        except (UnicodeError,SyntaxError,LookupError):self.note(path,'UNSUPPORTED_ENCODING',chain);return
        if '\x00' in t:self.note(path,'UNSUPPORTED_ENCODING',chain);return
        if ext in CODE_EXT:
            self.add(t,path,chain,'python_file',encoding=enc);return
        # Fences are explicit source boundaries, not instructions to execute.
        lines=[line for line in re.findall(r'[^\r\n]*(?:\r\n|\r|\n|$)',t) if line];active=None;seen_fence=False
        for i,line in enumerate(lines):
            match=re.match(r'^( {0,3})(`{3,}|~{3,})([^\r\n]*)[\r\n]*$',line)
            if not match:continue
            indentation,mark,info=match.groups();info=info.strip()
            if active:
                if mark[0]==active['char'] and len(mark)>=active['length'] and not info:
                    if active['python']:
                        self.add(''.join(lines[active['line']+1:i]),path,chain,'python_fence',active['line']+2,i,active['hint'],encoding=enc,fence_indent=active['indent'])
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
            active={'line':i,'char':mark[0],'length':len(mark),'language':language,'hint':hint,'indent':len(indentation),'python':language in ('python','py','python3') or (not language and hint is not None)}
        if active:self.note(path,'UNCLOSED_FENCE',chain,start_line=active['line']+2)
        if not seen_fence:
            # Classification uses the same bounded path; do not pre-parse a
            # potentially large plain-text Python module as one giant AST.
            self.add(t,path,chain,'python_text',encoding=enc)
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
        if self.check_budget is not None:self.check_budget()
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
                        if (e.orig_filename!=label or not _path_ok(e.orig_filename)
                                or stat.S_IFMT(e.external_attr>>16) not in (0,stat.S_IFREG,stat.S_IFDIR)):
                            self.note(e.orig_filename,'UNSAFE_MEMBER',chain);continue
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

def _read_container(data,name,limits,unit_limit,check_budget=None):
    r=Reader(name,limits,unit_limit=unit_limit,check_budget=check_budget)
    if len(data)>r.limits['max_container_bytes']:r.note(name,'CONTAINER_SIZE_LIMIT',[])
    else:r.visit(data,name,[])
    return r

def _result(data,r):
    return {'schema':1,'version':VERSION,'container_sha256':sha(data),'segments':r.segments,'inventory':r.inventory,
            'gaps':[i for i in r.inventory if i['status'] in GAP_STATES],'expanded_bytes':r.expanded,'entries_seen':r.entries,
            'upstream_code_executed':False,'release_approved':False}

def _fits_result(result,limit,check_budget=None):
    from .streaming import canonical_chunks
    size=0
    for chunk in canonical_chunks(result):
        if check_budget is not None:check_budget()
        size+=len(chunk)
        if size>limit:return False
    return True

def _retain_syntax_refusals(retained,refusals):
    # A formerly unvalidated tail can expose a real syntax error. That module
    # must not reappear as a usable prefix merely because growth was abandoned.
    owners={id(row):key for key,rows in retained.module_inventory.items()
            if key in refusals for row in rows}
    inventory=[];inserted=set()
    for row in retained.inventory:
        key=owners.get(id(row))
        if key is None:inventory.append(row)
        elif key not in inserted:
            inventory.extend(refusals[key]);inserted.add(key)
    retained.inventory=inventory
    retained.segments=[s for s in retained.segments if _segment_key(s) not in refusals]

def _keeps_original_definitions(retained,extended):
    current={_segment_key(s):s for s in extended.segments}
    for segment in retained.segments:
        key=_segment_key(segment)
        if key in extended.syntax_refusals:continue
        found=current.get(key)
        if found is None:return False
        identity=lambda d:(d['name'],d['definition_line'],d['end_line'],d['source_window']['sha256'])
        if not {identity(d) for d in segment['definitions']} <= {identity(d) for d in found['definitions']}:
            return False
    return True

def _fallback(data,retained,reason,*,compact=False,check_budget=None):
    from .structural_units import LEGACY_MAX_UNITS,MAX_UNITS
    partitions=[s['structural_partition'] for s in retained.segments
                if s.get('structural_partition',{}).get('units_total',0)>LEGACY_MAX_UNITS]
    if not compact and check_budget is not None:
        try:check_budget()
        except _ExtensionAborted:compact=True
    if not compact:
        for partition in partitions:
            partition['capacity_fallback']={'reason':reason,'scope':'WHOLE_CONTAINER',
                'requested_max_units':MAX_UNITS,'retained_max_units':LEGACY_MAX_UNITS,
                'extension_result_budget_bytes':EXTENDED_RESULT_BYTES,
                'extension_cpu_seconds':EXTENSION_CPU_SECONDS,
                'per_call_cpu_window_seconds':EXTENSION_CPU_WINDOW}
    result=_result(data,retained)
    if not compact:
        try:
            compact=not _fits_result(result,MAX_RESULT_BYTES,check_budget)
        except _ExtensionAborted:
            compact=True
    if compact:
        # A receipt must not push a fitting old result past the hard ceiling.
        # These documented policy codes are shorter than the regular policy;
        # they also need no extra traversal after the CPU allowance is spent.
        for partition in partitions:
            partition.pop('capacity_fallback',None)
            partition['policy']=FALLBACK_POLICIES[reason]
    return result

def inspect_container(data,name,limits=None):
    if not isinstance(data,bytes) or not _path_ok(name):raise Blocked('CONTAINER_INPUT')
    from .structural_units import LEGACY_MAX_UNITS,MAX_UNITS
    started=time.process_time()
    retained=_read_container(data,name,limits,LEGACY_MAX_UNITS)
    result=_result(data,retained)
    if not any(s.get('structural_partition',{}).get('units_total',0)>LEGACY_MAX_UNITS
               for s in retained.segments):
        return result
    elapsed=time.process_time()-started
    allowance=min(EXTENSION_CPU_SECONDS,EXTENSION_CPU_WINDOW-elapsed)
    if allowance<=elapsed:return _fallback(data,retained,'CPU_BUDGET',compact=True)
    def remaining_budget():
        if time.process_time()-started>=EXTENSION_CPU_WINDOW:
            raise _ExtensionAborted('CPU_BUDGET')
    try:
        if not _fits_result(result,EXTENDED_RESULT_BYTES,remaining_budget):
            return _fallback(data,retained,'RESULT_BUDGET',check_budget=remaining_budget)
    except _ExtensionAborted:
        return _fallback(data,retained,'CPU_BUDGET',compact=True)
    elapsed=time.process_time()-started
    allowance=min(EXTENSION_CPU_SECONDS,EXTENSION_CPU_WINDOW-elapsed)
    # Repeating even the old prefix would consume the remaining growth budget.
    # Retain it now, reserving worker CPU for serialization and bounded steps.
    if allowance<=elapsed:return _fallback(data,retained,'CPU_BUDGET',compact=True)
    deadline=time.process_time()+allowance
    def check_budget():
        if time.process_time()>=deadline:raise _ExtensionAborted('CPU_BUDGET')
    extended=Reader(name,limits,unit_limit=MAX_UNITS,check_budget=check_budget)
    candidate=None;reason=None
    try:
        extended.visit(data,name,[])
        check_budget()
        candidate=_result(data,extended)
        if not _keeps_original_definitions(retained,extended):reason='RESOURCE_LIMIT'
        elif not _fits_result(candidate,EXTENDED_RESULT_BYTES,check_budget):reason='RESULT_BUDGET'
    except _ExtensionAborted as exc:
        reason=str(exc)
    except (MemoryError,RecursionError):
        reason='RESOURCE_LIMIT'
    if reason is None:return candidate
    _retain_syntax_refusals(retained,extended.syntax_refusals)
    candidate=None;extended=None
    return _fallback(data,retained,reason,check_budget=remaining_budget)
