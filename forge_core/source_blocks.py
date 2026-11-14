"""Lossless bounded byte blocks. These are transport units, not Python programs.

Hashes detect changed/missing content; they cannot regenerate content. Retain the
blocks to restore a source after its original disappears. Corpus reports retain
only manifests, never these byte bodies. No parsing, importing or execution.
"""
from bisect import bisect_right
from .common import Blocked,integer,sha

MAX_SOURCE_BYTES=8_000_000
MAX_BLOCK_BYTES=250_000
MAX_BLOCKS=4096


def deconstruct(data,max_block_bytes=MAX_BLOCK_BYTES,preferred_boundaries=()):
    if not isinstance(data,bytes):raise Blocked('SOURCE_BLOCK_BYTES')
    integer(max_block_bytes,1,MAX_BLOCK_BYTES)
    integer(len(data),0,MAX_SOURCE_BYTES)
    if not isinstance(preferred_boundaries,(list,tuple)) or len(preferred_boundaries)>60_000:
        raise Blocked('SOURCE_BLOCK_BOUNDARIES')
    if any(type(n) is not int or not 0<n<len(data) for n in preferred_boundaries):
        raise Blocked('SOURCE_BLOCK_BOUNDARIES')
    if (len(data)+max_block_bytes-1)//max_block_bytes>MAX_BLOCKS:raise Blocked('SOURCE_BLOCK_COUNT_LIMIT')
    boundaries=sorted(set(preferred_boundaries))
    bodies=[];records=[];start=0
    while start<len(data):
        if len(records)>=MAX_BLOCKS:raise Blocked('SOURCE_BLOCK_COUNT_LIMIT')
        end=min(start+max_block_bytes,len(data))
        if end<len(data):
            i=bisect_right(boundaries,end)-1
            if i>=0 and boundaries[i]>start:end=boundaries[i]
        body=data[start:end]
        records.append({'index':len(records),'start':start,'end':end,'size':len(body),'sha256':sha(body)})
        bodies.append(body);start=end
    return {'manifest':{'schema':1,'source_sha256':sha(data),'source_bytes':len(data),
                        'max_block_bytes':max_block_bytes,'blocks':records},'blocks':bodies}


def reconstruct(manifest,blocks):
    if not isinstance(manifest,dict) or set(manifest)!={'schema','source_sha256','source_bytes','max_block_bytes','blocks'}:
        raise Blocked('SOURCE_BLOCK_MANIFEST')
    if type(manifest['schema']) is not int or manifest['schema']!=1:raise Blocked('SOURCE_BLOCK_MANIFEST')
    integer(manifest['source_bytes'],0,MAX_SOURCE_BYTES)
    integer(manifest['max_block_bytes'],1,MAX_BLOCK_BYTES)
    records=manifest['blocks']
    if not isinstance(blocks,(list,tuple)) or not isinstance(records,list) or len(records)!=len(blocks) or len(records)>MAX_BLOCKS:
        raise Blocked('SOURCE_BLOCK_MEMBERSHIP')
    chunks=[];position=0
    for index,(row,body) in enumerate(zip(records,blocks)):
        if not isinstance(row,dict) or set(row)!={'index','start','end','size','sha256'} or not isinstance(body,bytes):
            raise Blocked('SOURCE_BLOCK_RECORD')
        if any(type(row[k]) is not int for k in ('index','start','end','size')):
            raise Blocked('SOURCE_BLOCK_RECORD')
        if row['index']!=index or row['start']!=position or row['end']!=position+len(body) or row['size']!=len(body):
            raise Blocked('SOURCE_BLOCK_ORDER')
        if not 1<=len(body)<=manifest['max_block_bytes'] or row['end']>manifest['source_bytes']:
            raise Blocked('SOURCE_BLOCK_SIZE')
        if sha(body)!=row['sha256']:raise Blocked('SOURCE_BLOCK_HASH')
        chunks.append(body);position=row['end']
    if position!=manifest['source_bytes']:raise Blocked('SOURCE_BLOCK_TOTAL')
    result=b''.join(chunks)
    if sha(result)!=manifest['source_sha256']:raise Blocked('SOURCE_BLOCK_SOURCE_HASH')
    return result
