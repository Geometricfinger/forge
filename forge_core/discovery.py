"""Deterministic discovery planner. Models do not control endpoints or permissions."""
from __future__ import annotations
import base64,binascii,re,time
from .common import *
from . import policy
from .github import Response,delay_from

class Discovery:
    def __init__(self,store,analyzer,guard=lambda:None):self.store=store;self.analyzer=analyzer;self.guard=guard
    def accept(self,packet,response):
        self.guard()
        if not isinstance(response,Response) or type(response.status) is not int:raise Blocked('RESPONSE_ENVELOPE')
        headers={str(k).lower():v for k,v in response.headers.items()}
        if response.provenance not in {'native_github_https','operator_connector','synthetic_fixture'}:raise Blocked('PROVENANCE_LABEL')
        with self.store.tx() as c:
            row,m,s=self.store.owned(c,packet,allow_done=True)
            body=response.body
            if not isinstance(body,bytes) or len(body)>s['limits']['max_response_bytes']:raise Blocked('RESPONSE_BYTE_LIMIT')
            receipt=sha(canonical({'status':response.status,'body_sha256':sha(body),'provenance':response.provenance}))
            if row['state']=='DONE':
                if row['response_hash']!=receipt:raise Blocked('CHANGED_REPLAY')
                return {'duplicate':True}
        status=response.status;now=self.store.clock()
        if (status in (403,429) or str(headers.get('x-ratelimit-remaining'))=='0') and delay_from(headers,now)>86400:
            self.store.hold_provider(packet,'PROVIDER_DELAY_REQUIRES_REVIEW');return {'held':True}
        if status in (403,429):
            # Fail closed even where a 403 might be authorization instead of quota.
            self.store.fail(packet,'RATE_OR_ACCESS_LIMIT',retry=True,delay=delay_from(headers,now),global_wait=True)
            return {'paused':True}
        if status>=500:
            self.store.fail(packet,'PROVIDER_UNAVAILABLE',retry=True,delay=60,global_wait=True);return {'paused':True}
        if status!=200:
            self.store.fail(packet,'HTTP_'+str(status),retry=False);return {'blocked':True}
        obj=loads(body);kind=packet['kind'];p=packet['payload'];limits=s['limits']
        result={'outcome':'INSPECTED','provenance':response.provenance};children=[];candidate=None;used=0
        if kind=='search':
            if not isinstance(obj,dict) or not isinstance(obj.get('items'),list):raise Blocked('SEARCH_SHAPE')
            remaining=limits['max_repos']-self.store.task_count(packet['mission'],'repo')
            with self.store.tx() as c:
                seen={loads(r[0])['repo'].lower() for r in c.execute("SELECT payload FROM task WHERE mission=? AND kind='repo'",(packet['mission'],))}
            for repo in obj['items']:
                if remaining<=0:break
                name=policy.repo_name(repo.get('full_name'))
                if name.lower() in seen:continue
                seen.add(name.lower())
                # Metadata must be reread before any branch/tree/content request.
                children.append(('repo',{'repo':name}));remaining-=1
            result.update(outcome='SEARCH_PAGE_INSPECTED',returned=len(obj['items']),selected=len(children),query=p['query'])
            if obj.get('incomplete_results') is not False:result['gap']='Search completeness not established by provider.'
            total=obj.get('total_count')
            if type(total) is int and total>len(children):result['gap']='Top-K repository sample; unvisited search results remain.'
        elif kind=='repo':
            if not isinstance(obj,dict) or obj.get('full_name','').lower()!=p['repo'].lower():raise Blocked('REPO_IDENTITY')
            if obj.get('private') is not False:raise Blocked('PUBLIC_REPOSITORY_REQUIRED')
            if type(obj.get('id')) is not int:raise Blocked('REPOSITORY_STABLE_ID')
            license=(obj.get('license') or {}).get('spdx_id')
            result.update(repo=p['repo'],repository_id=obj['id'],declared_license=license,reuse_approved=False)
            if license not in s['permitted_licenses']:
                result.update(outcome='METADATA_ONLY_RIGHTS_REVIEW',gap='Source not collected: license absent or outside mission policy.')
            else:children.append(('commit',{'repo':p['repo'],'repository_id':obj['id'],'branch':text(obj.get('default_branch'),200),'license':license}))
        elif kind=='commit':
            commit=hash40(obj.get('sha'));tree=hash40(obj.get('commit',{}).get('tree',{}).get('sha'))
            result.update(commit=commit,tree=tree)
            children.append(('tree',{'repo':p['repo'],'repository_id':p['repository_id'],'commit':commit,'tree':tree,'license':p['license']}))
        elif kind=='tree':
            if obj.get('sha')!=p['tree'] or not isinstance(obj.get('tree'),list):raise Blocked('TREE_IDENTITY')
            if obj.get('truncated') is not False:result['gap']='Provider tree is truncated or completeness is unknown; not full repository coverage.'
            choices=[];seen=set()
            for item in obj['tree']:
                if not isinstance(item,dict):raise Blocked('TREE_ITEM')
                if item.get('type')!='blob':continue
                path=path_in_repo(item.get('path'))
                if path in seen:raise Blocked('DUPLICATE_TREE_PATH')
                seen.add(path)
                if policy.eligible(path,item.get('size'),item.get('mode'),limits['max_source_bytes']):
                    choices.append((path,hash40(item.get('sha')),item['size']))
            choices.sort(key=lambda x:policy.priority(x[0],s['path_terms']))
            n=min(limits['files_per_repo'],max(0,limits['max_files']-self.store.task_count(packet['mission'],'file')))
            selected=choices[:n]
            for path,blob,size in selected:children.append(('file',{**p,'path':path,'blob':blob,'size':size}))
            result.update(candidate_python_files=len(choices),selected_files=len(selected),outcome='BOUNDED_FILE_SELECTION')
            if len(choices)>n:result['gap']='Bounded Python-file sample; additional eligible files remain.'
        elif kind=='file':
            if obj.get('type')!='file' or obj.get('path')!=p['path'] or obj.get('sha')!=p['blob'] or obj.get('encoding')!='base64':raise Blocked('FILE_IDENTITY')
            size=integer(obj.get('size'),1,limits['max_source_bytes'])
            if size!=p['size']:raise Blocked('FILE_SIZE_CHANGED')
            encoded=obj.get('content')
            if not isinstance(encoded,str) or len(encoded)>size*2+1000:raise Blocked('SOURCE_ENCODING')
            try:data=base64.b64decode(''.join(encoded.split()),validate=True)
            except (ValueError,binascii.Error):raise Blocked('SOURCE_ENCODING') from None
            if len(data)!=size or git_sha(data)!=p['blob']:raise Blocked('SOURCE_HASH_MISMATCH')
            if m['source_bytes']+size>limits['max_total_source_bytes']:raise Blocked('MISSION_SOURCE_BUDGET')
            text_data=data.decode('utf-8');markers=re.findall(r'SPDX-License-Identifier:\s*([^\r\n]+)',text_data[:8192])
            source='https://github.com/'+p['repo']+'/blob/'+p['commit']+'/'+p['path']
            used=size;result.update(source_id=source,source_sha256=sha(data),git_blob_sha1=p['blob'],source_bytes=size)
            if markers and any(x.strip() not in s['permitted_licenses'] for x in markers):
                result.update(outcome='FILE_LICENSE_REVIEW',gap='File-level SPDX expression differs from permitted simple licenses; not parsed for reuse.')
            else:
                r=self.analyzer.scan(data,source,s['profile'])
                if r.get('source_id')!=source or r.get('source_sha256')!=sha(data) or r.get('registry_sha256')!=sha(canonical(s['profile'])):raise Blocked('ANALYSIS_BINDING')
                candidate={'source_id':source,'repository':p['repo'],'repository_id':p['repository_id'],'commit':p['commit'],'path':p['path'],'source_sha256':sha(data),'git_blob_sha1':p['blob'],'declared_license':p['license'],'rights_status':'DECLARATIONS_ONLY_REVIEW_REQUIRED','findings':r['findings'],'functions_inspected':r['functions_inspected'],'provenance':response.provenance,'profile_sha256':sha(canonical(s['profile'])),'runtime_verified':False,'reuse_approved':False,'release_approved':False}
                result.update(outcome='SOURCE_INSPECTED',functions_inspected=r['functions_inspected'],finding_count=len(r['findings']))
            del data,text_data
        else:raise Blocked('UNKNOWN_TASK')
        cooldown=0
        if str(headers.get('x-ratelimit-remaining'))=='0':cooldown=now+delay_from(headers,now)
        self.store.complete(packet,receipt,result,children,candidate,used,cooldown)
        return {'completed':True,'kind':kind,'new_leads':len(children),'findings':len(candidate['findings']) if candidate else 0}
    def run(self,mid,transport,steps=10,seconds=60):
        integer(steps,1,100);integer(seconds,1,600);start=time.monotonic();completed=0
        for _ in range(steps):
            if time.monotonic()-start>seconds:break
            self.guard();packet=self.store.claim(mid)
            if not packet:break
            try:
                response=transport.get(packet);self.accept(packet,response);completed+=1
            except Blocked as e:
                reason=str(e) if re.fullmatch('[A-Z_0-9:]{1,100}',str(e)) else 'INVALID_PROVIDER_DATA'
                try:self.store.fail(packet,reason,retry=reason=='NETWORK_UNAVAILABLE',delay=60 if reason=='NETWORK_UNAVAILABLE' else 0,global_wait=reason=='NETWORK_UNAVAILABLE')
                except Blocked as stale:
                    if str(stale) not in {'MISSION_CANCELLED','STALE_CLAIM'}:raise
                    break
                if reason=='NETWORK_UNAVAILABLE':break
            except (KeyError,TypeError,UnicodeError,ValueError,OSError):
                self.store.fail(packet,'INVALID_PROVIDER_DATA')
        return self.store.snapshot(mid)
