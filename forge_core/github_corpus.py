"""Pinned public-source envelope intake. No HTTP, extraction, imports or execution.

A caller supplies exact selected files and connector-derived identities. Hash
agreement proves byte consistency, not authorship, public access or reuse rights.
These local selected-file ZIPs must never be presented as full repositories.
"""
from __future__ import annotations
import io, re, stat, zipfile
from pathlib import PurePosixPath
from .common import Blocked, canonical, git_sha, hash40, integer, path_in_repo, repo_name, sha
from .mixed_intake import DEFAULTS, _excluded

KIND = 'LOCAL_SELECTED_FILE_ENVELOPE_NOT_FULL_REPOSITORY_ARCHIVE'

def validate_source(row):
    if not isinstance(row, dict) or row.get('provider') != 'github':
        raise Blocked('GITHUB_CORPUS_SOURCE')
    repo = repo_name(row.get('repository')); commit = hash40(row.get('commit'))
    # Patch: one commit may be split into several bounded envelopes
    # (`:selected-files:part-001` ...); each part is still a pinned subset and
    # duplicate file_ids/paths remain rejected by the manifest validator.
    base = f'github:{repo}@{commit}:selected-files'
    file_id = row.get('file_id')
    if not isinstance(file_id, str) or not (file_id == base or re.fullmatch(re.escape(base) + r':part-[0-9]{3}', file_id)):
        raise Blocked('GITHUB_CORPUS_ID')
    if row.get('source_url') != f'https://github.com/{repo}/tree/{commit}':
        raise Blocked('GITHUB_CORPUS_URL')
    if row.get('container_kind') != KIND:
        raise Blocked('GITHUB_CORPUS_CONTAINER_KIND')
    path_in_repo(row.get('path'))
    if not row['path'].endswith('.zip'): raise Blocked('GITHUB_CORPUS_ZIP_REQUIRED')
    integer(row.get('size'), 1, DEFAULTS['max_container_bytes'])
    if not isinstance(row.get('sha256'),str) or not re.fullmatch('[0-9a-f]{64}',row['sha256']):
        raise Blocked('GITHUB_CORPUS_SHA')
    members = row.get('members'); paths = row.get('selected_paths')
    if not isinstance(members,list) or not 1 <= len(members) <= DEFAULTS['max_segments']:
        raise Blocked('GITHUB_CORPUS_MEMBERS')
    if not isinstance(paths,list) or len(paths) != len(members): raise Blocked('GITHUB_CORPUS_PATHS')
    for selected in paths:path_in_repo(selected)
    seen=set(); total=0
    for member in members:
        if not isinstance(member,dict) or set(member) != {'path','size','sha256','git_blob_sha1'}:
            raise Blocked('GITHUB_CORPUS_MEMBER_FIELDS')
        p=path_in_repo(member['path'])
        if PurePosixPath(p).suffix not in {'.py','.pyi','.pyw'} or _excluded(p):
            raise Blocked('GITHUB_CORPUS_MEMBER_SCOPE')
        if p in seen: raise Blocked('GITHUB_CORPUS_DUPLICATE_MEMBER')
        seen.add(p);total+=integer(member['size'],0,DEFAULTS['max_source_bytes'])
        hash40(member['git_blob_sha1'])
        if not isinstance(member['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',member['sha256']):
            raise Blocked('GITHUB_CORPUS_MEMBER_SHA')
    if total>DEFAULTS['max_total_bytes'] or len(set(paths))!=len(paths) or set(paths)!=seen:
        raise Blocked('GITHUB_CORPUS_MEMBER_SET_OR_BYTES')
    return row

def validate_payload(data, row):
    validate_source(row)
    if not isinstance(data,bytes) or len(data)!=row['size'] or sha(data)!=row['sha256']:
        raise Blocked('GITHUB_CORPUS_ENVELOPE_CHANGED')
    expected={m['path']:m for m in row['members']}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries=archive.infolist()
            if len(entries)!=len(expected) or len({e.filename for e in entries})!=len(entries):
                raise Blocked('GITHUB_CORPUS_MEMBER_SET')
            if {e.filename for e in entries}!=set(expected): raise Blocked('GITHUB_CORPUS_MEMBER_SET')
            for info in entries:
                mode=info.external_attr>>16
                if info.is_dir() or info.flag_bits&1 or (stat.S_IFMT(mode) not in (0,stat.S_IFREG)):
                    raise Blocked('GITHUB_CORPUS_MEMBER_MODE')
                if info.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):
                    raise Blocked('GITHUB_CORPUS_COMPRESSION')
                member=expected[info.filename]
                if info.file_size!=member['size'] or info.file_size>DEFAULTS['max_ratio']*max(info.compress_size,1):
                    raise Blocked('GITHUB_CORPUS_MEMBER_SIZE_OR_RATIO')
                with archive.open(info) as stream: body=stream.read(member['size']+1)
                if len(body)!=member['size'] or sha(body)!=member['sha256'] or git_sha(body)!=member['git_blob_sha1']:
                    raise Blocked('GITHUB_CORPUS_MEMBER_CHANGED')
    except (zipfile.BadZipFile,RuntimeError,NotImplementedError,OSError) as error:
        raise Blocked('GITHUB_CORPUS_ARCHIVE') from error
    return {'status':'PINNED_SELECTED_BYTES_VERIFIED','members_verified':len(expected),
            'whole_repository_scanned':False,'provider_claim_authenticated':False,
            'reuse_approved':False,'upstream_code_executed':False}
