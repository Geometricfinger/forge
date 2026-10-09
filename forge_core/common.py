"""Small shared contracts. Local trusted-directory guards, not hostile-host isolation."""
from __future__ import annotations
import hashlib,json,math,os,re,stat,tempfile
from pathlib import Path, PurePosixPath

class Blocked(ValueError): pass

def canonical(obj): return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
def sha(data): return hashlib.sha256(data).hexdigest()
def git_sha(data): return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
def loads(data):
    if isinstance(data,bytes): data=data.decode('utf-8')
    def pairs(rows):
        d={}
        for k,v in rows:
            if k in d: raise Blocked('DUPLICATE_JSON_KEY')
            d[k]=v
        return d
    def reject(s):raise Blocked('NONFINITE_JSON')
    def finite(x):
        if isinstance(x,float) and not math.isfinite(x):raise Blocked('NONFINITE_JSON')
        if isinstance(x,dict):
            for v in x.values():finite(v)
        elif isinstance(x,list):
            for v in x:finite(v)
    try:r=json.loads(data,object_pairs_hook=pairs,parse_constant=reject);finite(r);return r
    except (json.JSONDecodeError,UnicodeError,RecursionError) as e:raise Blocked('INVALID_JSON') from e

def integer(n,low,high):
    if type(n) is not int or not low<=n<=high:raise Blocked('INTEGER_BOUNDS')
    return n

def text(s,limit=2000):
    if not isinstance(s,str) or not s.strip() or len(s)>limit or any(ord(c)<32 for c in s):raise Blocked('TEXT_BOUNDS')
    return s

def safe_path(p):
    p=Path(p).absolute()
    for n in [*reversed(p.parents),p]:
        if n.is_symlink():raise Blocked('SYMLINK_PATH')
    # Check lexical ancestors first so '..' cannot hide a traversed symlink.
    return p.resolve(strict=False)

def read(p,limit=4_000_000):
    p=safe_path(p);s=p.stat()
    if not stat.S_ISREG(s.st_mode) or s.st_size>limit:raise Blocked('INPUT_FILE_BOUNDS')
    with p.open('rb') as f:b=f.read(limit+1)
    if len(b)>limit:raise Blocked('INPUT_FILE_BOUNDS')
    return b

def write(p,data,new=False):
    p=safe_path(p);p.parent.mkdir(parents=True,exist_ok=True)
    if p.exists() and (new or not p.is_file() or p.stat().st_nlink!=1):raise Blocked('OUTPUT_CONFLICT')
    fd,t=tempfile.mkstemp(prefix='.forge-',dir=p.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        if new:
            os.link(t,p);os.unlink(t)
        else:os.replace(t,p)
    finally:
        if os.path.exists(t):os.unlink(t)

def path_in_repo(s):
    text(s,500);p=PurePosixPath(s)
    if p.is_absolute() or '..' in p.parts or '\\' in s or str(p)!=s or ':' in s or s=='.':raise Blocked('REPOSITORY_PATH')
    return s

def repo_name(s):
    if not isinstance(s,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',s):raise Blocked('REPOSITORY_NAME')
    return s

def hash40(s):
    if not isinstance(s,str) or not re.fullmatch('[0-9a-f]{40}',s):raise Blocked('GIT_SHA')
    return s
