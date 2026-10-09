"""Mission policy and generated endpoint contracts. Collected text never makes requests."""
import re
from urllib.parse import urlencode,quote,urlsplit,parse_qs
from pathlib import Path
from .common import *
ROOT=Path(__file__).resolve().parents[1]
ALLOWED_LICENSES={'MIT','Apache-2.0','BSD-2-Clause','BSD-3-Clause','CC0-1.0','Unlicense'}
EXCLUDED={'.git','.venv','venv','node_modules','__pycache__','vendor','site-packages','fixtures','test','tests'}

def validate_profile(p):
    if not isinstance(p,dict) or set(p)!={'schema','id','title','targets'} or type(p['schema']) is not int or p['schema']!=1:raise Blocked('PROFILE_SCHEMA')
    if not re.fullmatch('[a-z0-9][a-z0-9_-]{0,79}',text(p['id'],80)):raise Blocked('PROFILE_ID')
    text(p['title'],250)
    if not isinstance(p['targets'],list) or not 1<=len(p['targets'])<=128:raise Blocked('TARGET_COUNT')
    seen=set()
    for row in p['targets']:
        if not isinstance(row,dict) or set(row)!={'api','capability'}:raise Blocked('TARGET_SCHEMA')
        api=text(row['api'],250)
        if not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+',api,re.ASCII) or api in seen:raise Blocked('EXACT_API_REQUIRED')
        if not re.fullmatch('[a-z][a-z0-9_]{0,79}',text(row['capability'],80)):raise Blocked('CAPABILITY_LABEL')
        seen.add(api)
    return p

def query(q):
    text(q,220)
    if any(s in q.lower() for s in ['http:','https:','password','api_key','token=','ghp_','github_pat_','-----begin','is:private']):raise Blocked('PUBLIC_QUERY_REQUIRED')
    if not re.fullmatch(r'[A-Za-z0-9_ :,".\-]+',q):raise Blocked('QUERY_CHARACTERS')
    return q+' is:public fork:false archived:false'

def validate(m):
    expected={'schema','template','title','public_brief','queries','path_terms','profile','limits','permitted_licenses'}
    if not isinstance(m,dict) or set(m)!=expected or type(m['schema']) is not int or m['schema']!=1:raise Blocked('MISSION_SCHEMA')
    for k in ['template','title','public_brief']:text(m[k],500)
    if not isinstance(m['queries'],list) or not 1<=len(m['queries'])<=4:raise Blocked('QUERY_COUNT')
    for q in m['queries']:query(q)
    if not isinstance(m['path_terms'],list) or not 1<=len(m['path_terms'])<=12:raise Blocked('PATH_TERMS')
    for s in m['path_terms']:
        if not re.fullmatch('[a-z0-9_-]{1,30}',s):raise Blocked('PATH_TERMS')
    bounds={'max_requests':(1,100),'max_repos':(1,10),'max_files':(1,30),'files_per_repo':(1,10),'max_source_bytes':(1,250000),'max_total_source_bytes':(1,4000000),'max_response_bytes':(100,2000000),'max_attempts':(1,2)}
    if set(m['limits'])!=set(bounds):raise Blocked('LIMIT_KEYS')
    for k,(lo,hi) in bounds.items():integer(m['limits'][k],lo,hi)
    ls=m['permitted_licenses']
    if not isinstance(ls,list) or not ls or len(set(ls))!=len(ls) or not set(ls)<=ALLOWED_LICENSES:raise Blocked('LICENSE_POLICY')
    validate_profile(m['profile']);return m

def template(name):
    if name not in {'understanding','reliability','evaluation'}:raise Blocked('TEMPLATE_NOT_FOUND')
    return validate(loads(read(ROOT/'templates'/f'{name}.json')))

def endpoint(kind,p):
    if kind=='search':
        integer(p['page'],1,2);return '/search/repositories?'+urlencode({'q':query(p['query']),'per_page':3,'page':p['page']})
    r=repo_name(p['repo']);base='/repos/'+r
    if kind=='repo':return base
    if kind=='commit':
        branch=text(p['branch'],200)
        if any(x in branch for x in ['..','\\','?','#']):raise Blocked('BRANCH')
        return base+'/commits/'+quote(branch,safe='')
    if kind=='tree':return base+'/git/trees/'+hash40(p['tree'])+'?recursive=1'
    if kind=='file':return base+'/contents/'+quote(path_in_repo(p['path']),safe='/')+'?'+urlencode({'ref':hash40(p['commit'])})
    raise Blocked('REQUEST_KIND')

def url_for(kind,p):return 'https://api.github.com'+endpoint(kind,p)

def eligible(path,size,mode,max_size):
    path_in_repo(path)
    return (path.endswith('.py') and mode in {'100644','100755'} and type(size) is int and 0<size<=max_size
            and not EXCLUDED.intersection(Path(path).parts) and not Path(path).name.startswith(('test_','.')))

def priority(path,terms):
    low=path.lower();return (-sum(t in low for t in terms),len(path),path)
