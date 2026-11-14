"""Reacquire a complete definition without importing or rewriting inspected code."""
from .common import Blocked,canonical,integer,read,safe_path,sha
from .mixed_intake import DEFAULTS


def inspect_definition(report,definition_id,source_root,max_bytes=250_000):
    from . import corpus
    integer(max_bytes,1,DEFAULTS['max_source_bytes'])
    context=corpus.dependency_context(report,definition_id)
    matches=[(r['source'],s,d) for r in report['sources'] for s in r['segments']
             for d in s['definitions'] if d.get('definition_id')==definition_id]
    if len(matches)!=1:raise Blocked('CONTEXT_DEFINITION_ID')
    source,segment,definition=matches[0]
    window=definition.get('source_window')
    if not isinstance(window,dict):raise Blocked('DEFINITION_REINDEX_REQUIRED')
    if window.get('status')!='AVAILABLE' or type(window.get('bytes')) is not int or window['bytes']>max_bytes:
        raise Blocked('DEFINITION_SIZE_LIMIT')
    corpus.manifest({'schema':1,'files':[source]})
    root=safe_path(source_root)
    if not root.is_dir():raise Blocked('SOURCE_ROOT')
    path=safe_path(root/source['path'])
    if not path.is_relative_to(root):raise Blocked('SOURCE_ESCAPE')
    data=read(path,DEFAULTS['max_container_bytes'])
    if len(data)!=source['size'] or sha(data)!=source['sha256']:raise Blocked('SOURCE_VERSION_MISMATCH')
    # Reuse the fixed, bounded parser for ZIP members, fences and notebook cells.
    # Never use a pathname from metadata to extract an archive onto the filesystem.
    # The fixed worker uses stdin/stdout and -B; it needs no temporary files.
    # In particular, ambient TMPDIR may point inside the read-only target.
    if root.parent==root:raise Blocked('CORPUS_SEPARATE_OUTPUT')
    parsed=corpus._parser(data,source['path'],root.parent)
    if parsed.get('container_sha256')!=source['sha256'] or parsed.get('upstream_code_executed') is not False:
        raise Blocked('CORPUS_PARSE_BINDING')
    fresh=[s for s in parsed['segments'] if s['segment_local_id']==segment['segment_local_id']]
    if len(fresh)!=1:raise Blocked('DEFINITION_SOURCE_BINDING')
    fresh=fresh[0]
    payload=bytes.fromhex(fresh.pop('source_hex'))
    sid='source_'+sha(canonical({'file_id':source['file_id'],'container_sha256':source['sha256'],
                               'segment':fresh['segment_local_id']}))
    if sid!=segment['source_id'] or sha(payload)!=segment['source_sha256'] or canonical(fresh.get('analysis_view'))!=canonical(segment.get('analysis_view')):
        raise Blocked('DEFINITION_SOURCE_BINDING')
    found=[d for d in fresh['definitions'] if (d['name'],d['start_line'],d['end_line'])==
           (definition['name'],definition['start_line'],definition['end_line'])]
    if len(found)!=1 or found[0]['source_window']!=window:raise Blocked('DEFINITION_SOURCE_BINDING')
    did='definition_'+sha(canonical([sid,found[0]['name'],found[0]['start_line'],found[0]['end_line']]))
    if did!=definition_id:raise Blocked('DEFINITION_SOURCE_BINDING')
    excerpt=payload[window['byte_start']:window['byte_end']]
    if len(excerpt)!=window['bytes'] or sha(excerpt)!=window['sha256'] or window['module_sha256']!=sha(payload):
        raise Blocked('DEFINITION_SOURCE_BINDING')
    if sha(read(path,DEFAULTS['max_container_bytes']))!=source['sha256']:raise Blocked('SOURCE_CHANGED_DURING_READ')
    return {'status':'COMPLETE_DEFINITION_SOURCE','definition_id':definition_id,'name':definition['name'],
            'source_id':sid,'source_sha256':segment['source_sha256'],'container_sha256':source['sha256'],
            'file_id':source['file_id'],'path':segment['path'],'member_chain':segment['member_chain'],
            'start_line':definition['member_start_line'],'end_line':definition['member_end_line'],
            'source_window':window,'source_text':excerpt.decode('utf-8'),'context':context,
            **({'analysis_view':segment['analysis_view']} if 'analysis_view' in segment else {}),
            'source_code_executed':False,'source_bodies_persisted':False,'dependency_closure_complete':False,
            'interpretation':'Original definition text with its module context; not a standalone executable component'}
