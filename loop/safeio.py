"""Local consistency and path checks. Trusted directories; not a hostile-user sandbox."""
from __future__ import annotations
import hashlib, json, math, os, re, stat, tempfile
from pathlib import Path, PurePosixPath

class Blocked(ValueError):
    """A contract or preservation condition was not met."""

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')

def digest(data):
    return hashlib.sha256(data).hexdigest()

def strict_json(data):
    def pairs(items):
        result={}
        for k,v in items:
            if k in result: raise Blocked('DUPLICATE_JSON_KEY')
            result[k]=v
        return result
    def bad(_): raise Blocked('NONFINITE_JSON')
    def finite(value):
        if isinstance(value,float) and not math.isfinite(value): raise Blocked('NONFINITE_JSON')
        if isinstance(value,dict):
            for v in value.values(): finite(v)
        elif isinstance(value,list):
            for v in value: finite(v)
    result=json.loads(data, object_pairs_hook=pairs, parse_constant=bad)
    finite(result)
    return result

def text(value, name, maximum=2000):
    if not isinstance(value,str) or not value.strip() or len(value)>maximum: raise Blocked(name)
    return value

def integer(value, name, lo, hi):
    if type(value) is not int or not lo<=value<=hi: raise Blocked(name)
    return value

def shape(value, keys, name):
    if not isinstance(value,dict) or set(value)!=set(keys): raise Blocked(name)
    return value

def sha_string(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value): raise Blocked('DIGEST_SYNTAX')
    return value

def relative(value):
    text(value,'RELATIVE_PATH',500)
    p=PurePosixPath(value)
    if p.is_absolute() or '\\' in value or '..' in p.parts or ':' in value or str(p)!=value or value=='.':
        raise Blocked('RELATIVE_PATH')
    return p

def no_links(path):
    p=Path(path).absolute()
    for item in [*reversed(p.parents),p]:
        if item.is_symlink(): raise Blocked('LINK_PATH')
    return p

def read(path, limit=8_000_000):
    p=no_links(path)
    st=p.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_size>limit: raise Blocked('FILE_TYPE_OR_SIZE')
    with p.open('rb') as f: data=f.read(limit+1)
    if len(data)>limit: raise Blocked('FILE_SIZE')
    return data

def write_new(path, data):
    p=no_links(path)
    if p.exists(): raise Blocked('OUTPUT_EXISTS')
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('xb') as f:
        os.chmod(p,0o600); f.write(data); f.flush(); os.fsync(f.fileno())

def atomic_json(path,value):
    p=no_links(path); p.parent.mkdir(parents=True,exist_ok=True)
    if p.exists() and not p.is_file(): raise Blocked('OUTPUT_TYPE')
    fd,name=tempfile.mkstemp(prefix='.publishing-',dir=p.parent)
    try:
        with os.fdopen(fd,'wb') as f: f.write(canonical(value));f.flush();os.fsync(f.fileno())
        os.replace(name,p)
        if os.name=='posix':
            d=os.open(p.parent,os.O_RDONLY)
            try:os.fsync(d)
            finally:os.close(d)
    finally:
        if os.path.exists(name):os.unlink(name)

def file_binding(paths):
    return {str(Path(p).resolve()):digest(read(p,30_000_000)) for p in sorted(paths,key=str)}

def check_binding(binding):
    for p,h in binding.items():
        if digest(read(p,30_000_000))!=h: raise Blocked('PROTECTED_INPUT_CHANGED')
