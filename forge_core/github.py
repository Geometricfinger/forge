"""GET-only native transport. No URL from a repository is ever followed."""
from dataclasses import dataclass
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError,URLError
import time
from email.utils import parsedate_to_datetime
from .common import *
from .policy import url_for
@dataclass
class Response:
    status:int
    body:bytes
    headers:dict
    provenance:str='native_github_https'
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None
class GitHub:
    def __init__(self,token=None):
        self.token=token
        self.opener=build_opener(NoRedirect())
    def get(self,packet):
        # Reconstruct from validated task fields, never trust supplied packet URL.
        url=url_for(packet['kind'],packet['payload']);headers={'User-Agent':'FORGE-Workbench/0.3','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}
        if self.token:headers['Authorization']='Bearer '+self.token
        req=Request(url,headers=headers,method='GET');limit=packet['max_response_bytes']
        try:
            with self.opener.open(req,timeout=15) as r:
                body=r.read(limit+1)
                if len(body)>limit:raise Blocked('RESPONSE_BYTE_LIMIT')
                return Response(r.status,body,{k.lower():v for k,v in r.headers.items()})
        except HTTPError as e:
            # Do not emit response bodies or token-bearing URLs into exceptions/logs.
            return Response(e.code,b'',{k.lower():v for k,v in e.headers.items()})
        except (URLError,TimeoutError,OSError):raise Blocked('NETWORK_UNAVAILABLE') from None

def delay_from(headers,now):
    def number(key,default):
        try:
            f=float(headers.get(key,default))
            return f if math.isfinite(f) else default
        except (TypeError,ValueError):return default
    retry=number('retry-after',0)
    if not retry:
        try:retry=max(0,parsedate_to_datetime(headers.get('retry-after','')).timestamp()-now)
        except (TypeError,ValueError,OverflowError):retry=0
    # Do not shorten a provider deadline to our per-job retry budget.
    # The controller holds all requests when this exceeds its automatic-wait policy.
    return max(60,retry,number('x-ratelimit-reset',now)-now)
