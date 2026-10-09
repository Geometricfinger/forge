"""Bounded recovery of static-analysis attempts; source and rules never change.

The caller holds the corpus lock. A durable start consumes one of three attempts
before calling the analyzer. Failed immutable attempt records remain after a
successful retry. Unknown errors, identity failures and cleanup failures require
review, not automatic retry. This is a local cooperative protocol, not a sandbox.
"""
from __future__ import annotations
import re
from pathlib import Path
from .common import Blocked,canonical,sha,read,write,safe_path

MAX_ATTEMPTS = 3
RETRYABLE = frozenset({'TOOL_TIMEOUT','TOOL_IO_FAILED','ATTEMPT_INTERRUPTED'})

class Journal:
    def __init__(self, folder: Path, binding: dict):
        self.folder=safe_path(folder)
        self.binding=sha(canonical(binding))
        self.seal={'schema':1,'binding':self.binding,'max_attempts':MAX_ATTEMPTS}
        self.history=[]
        self.reload()

    def reload(self):
        """Validate complete history, including contiguous charged starts."""
        self.history=[]
        if not self.folder.exists():return
        seal=self.folder/'binding.json'
        if not seal.exists() or read(seal)!=canonical(self.seal):raise Blocked('RECOVERY_BINDING')
        files={p.name for p in self.folder.iterdir()}
        allowed={'binding.json'}
        for i in range(1,MAX_ATTEMPTS+1):
            allowed.update({f'{i:03d}.start.json',f'{i:03d}.finish.json'})
        if files-allowed:raise Blocked('RECOVERY_MEMBERS')
        previous=None
        for i in range(1,MAX_ATTEMPTS+1):
            start=self.folder/f'{i:03d}.start.json';finish=self.folder/f'{i:03d}.finish.json'
            if not start.exists():
                if finish.exists() or any(f'{j:03d}.start.json' in files for j in range(i+1,MAX_ATTEMPTS+1)):
                    raise Blocked('RECOVERY_SEQUENCE')
                break
            if previous is not None and (previous['state']!='FAILED' or previous['reason'] not in RETRYABLE):
                raise Blocked('RECOVERY_INVALID_CONTINUATION')
            expected={'schema':1,'binding':self.binding,'attempt':i,'phase':'START','source_code_executed':False}
            if read(start)!=canonical(expected):raise Blocked('RECOVERY_START')
            row={'attempt':i,'state':'STARTED','reason':'ATTEMPT_INTERRUPTED','result_sha256':None}
            if finish.exists():
                from .common import loads
                obj=loads(read(finish,10000))
                if not isinstance(obj,dict) or set(obj)!={'schema','binding','attempt','phase','state','reason','result_sha256','source_code_executed'}:
                    raise Blocked('RECOVERY_FINISH')
                if type(obj['schema']) is not int or obj['schema']!=1 or obj['binding']!=self.binding or type(obj['attempt']) is not int or obj['attempt']!=i or obj['phase']!='FINISH' or obj['source_code_executed'] is not False:
                    raise Blocked('RECOVERY_FINISH')
                if obj['state']=='SUCCEEDED':
                    if obj['reason'] is not None or not isinstance(obj['result_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',obj['result_sha256']):raise Blocked('RECOVERY_SUCCESS')
                elif obj['state']=='FAILED':
                    if not isinstance(obj['reason'],str) or not obj['reason'] or len(obj['reason'])>500 or obj['result_sha256'] is not None:raise Blocked('RECOVERY_FAILURE')
                else:raise Blocked('RECOVERY_STATE')
                row.update({k:obj[k] for k in ('state','reason','result_sha256')})
            self.history.append(row);previous=row

    def _create(self):
        if not self.folder.exists():
            self.folder.mkdir(parents=True,mode=0o700)
            write(self.folder/'binding.json',canonical(self.seal),new=True)

    def finish(self, state: str, reason=None, result_sha256=None):
        self.reload()
        if not self.history or self.history[-1]['state']!='STARTED':raise Blocked('RECOVERY_NO_ACTIVE_ATTEMPT')
        i=self.history[-1]['attempt']
        obj={'schema':1,'binding':self.binding,'attempt':i,'phase':'FINISH','state':state,'reason':reason,'result_sha256':result_sha256,'source_code_executed':False}
        # Values come from fixed caller paths, never arbitrary request data.
        if state=='SUCCEEDED':
            if reason is not None or not isinstance(result_sha256,str) or not re.fullmatch('[0-9a-f]{64}',result_sha256):raise Blocked('RECOVERY_SUCCESS')
        elif state=='FAILED':
            if not isinstance(reason,str) or not reason or len(reason)>500 or result_sha256 is not None:raise Blocked('RECOVERY_FAILURE')
        else:raise Blocked('RECOVERY_STATE')
        write(self.folder/f'{i:03d}.finish.json',canonical(obj),new=True);self.reload()

    def begin(self):
        self.reload()
        if self.history and self.history[-1]['state']=='STARTED':
            # The prior corpus owner released/lost the lock without publishing.
            self.finish('FAILED','ATTEMPT_INTERRUPTED')
        if self.history:
            last=self.history[-1]
            if last['state']=='SUCCEEDED':raise Blocked('RECOVERY_SUCCESS_CACHE_MISSING')
            if last['reason'] not in RETRYABLE:return False
        if len(self.history)>=MAX_ATTEMPTS:return False
        self._create();i=len(self.history)+1
        obj={'schema':1,'binding':self.binding,'attempt':i,'phase':'START','source_code_executed':False}
        write(self.folder/f'{i:03d}.start.json',canonical(obj),new=True);self.reload();return True

    def cached_success(self, digest):
        self.reload()
        if not self.history:return  # Older successful checkpoints stay readable.
        last=self.history[-1]
        if last['state']=='STARTED':
            # Result publication preceded the crash, but finish publication did not.
            self.finish('SUCCEEDED',result_sha256=digest)
        elif last['state']!='SUCCEEDED' or last['result_sha256']!=digest:
            raise Blocked('RECOVERY_CACHE_DISAGREEMENT')

    def summary(self, source_id):
        self.reload();last=self.history[-1] if self.history else None
        eligible=bool(last and last['state']!='SUCCEEDED' and last['reason'] in RETRYABLE and len(self.history)<MAX_ATTEMPTS)
        return {'source_id':source_id,'attempt_count':len(self.history),'max_attempts':MAX_ATTEMPTS,
                'retry_eligible':eligible,'exhausted':bool(last and last['state']!='SUCCEEDED' and len(self.history)>=MAX_ATTEMPTS),
                'recovered':bool(last and last['state']=='SUCCEEDED' and len(self.history)>1),
                'attempts':[dict(x) for x in self.history]}


def summary(root):
    """Read all local journals without treating absent or corrupt history as success."""
    from .common import loads
    records=[]
    root=safe_path(root)
    if root.exists():
        for folder in sorted(root.iterdir()):
            if not re.fullmatch('source_[0-9a-f]{64}',folder.name):raise Blocked('RECOVERY_SOURCE_ID')
            seal=loads(read(folder/'binding.json',10000))
            if not isinstance(seal,dict) or set(seal)!={'schema','binding','max_attempts'} or type(seal['schema']) is not int or seal['schema']!=1 or type(seal['max_attempts']) is not int or seal['max_attempts']!=MAX_ATTEMPTS or not isinstance(seal['binding'],str) or not re.fullmatch('[0-9a-f]{64}',seal['binding']):raise Blocked('RECOVERY_BINDING')
            j=object.__new__(Journal);j.folder=safe_path(folder);j.binding=seal['binding'];j.seal=seal;j.history=[]
            records.append(j.summary(folder.name))
    return {'policy':'STATIC_ANALYSIS_TRANSIENT_ONLY_V1','max_attempts_per_segment':MAX_ATTEMPTS,
            'retryable_reasons':sorted(RETRYABLE),'automatic_new_invocation_required':True,
            'attempts_charged':sum(x['attempt_count'] for x in records),'recovered_segments':sum(x['recovered'] for x in records),
            'retry_eligible_segments':sum(x['retry_eligible'] for x in records),'exhausted_segments':sum(x['exhausted'] for x in records),
            'segments':records,'source_code_executed':False,'release_approved':False}
