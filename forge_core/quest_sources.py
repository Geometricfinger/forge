"""Reacquire explicitly selected source files without extraction or execution."""
from io import BytesIO
from pathlib import PurePosixPath
import re
import stat
from zipfile import BadZipFile, ZipFile, ZIP_STORED, ZIP_DEFLATED, ZIP_BZIP2, ZIP_LZMA

from .common import Blocked, path_in_repo, read, safe_path, sha, text
from .mixed_intake import DEFAULTS, _decode, _excluded, _path_ok


ASSET_LIMIT = 64
IMPORTABLE_SUFFIXES = {'.py', '.pyi', '.pyw', '.pyc', '.pyo', '.so', '.pyd', '.dll',
                      '.dylib', '.pth', '.zip', '.egg', '.whl'}


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise Blocked('QUEST_SOURCE_REFERENCE')


def _path(value):
    path_in_repo(value)
    if not _path_ok(value) or _excluded(value):
        raise Blocked('QUEST_SOURCE_PATH')
    return value


def chain(value):
    if not isinstance(value, list) or len(value) > DEFAULTS['max_depth'] + 1:
        raise Blocked('QUEST_SOURCE_REFERENCE')
    for member in value:
        if not isinstance(member, dict) or set(member) != {'path', 'sha256'}:
            raise Blocked('QUEST_SOURCE_REFERENCE')
        _path(member['path'])
        _digest(member['sha256'])
    return value


def validate(recipe, plain_module, component_limit):
    """Validate explicit mappings; references are resolved against the corpus later."""
    mappings = recipe.get('module_files', [])
    if not isinstance(mappings, list) or len(mappings) > component_limit:
        raise Blocked('QUEST_MODULE_FILES')
    seen_ids, seen_paths = set(), set()
    for row in mappings:
        if (not isinstance(row, dict) or set(row) != {'source_id', 'path'}
                or not isinstance(row['source_id'], str)
                or not re.fullmatch('source_[0-9a-f]{64}', row['source_id'])
                or not plain_module(_path(row['path']))
                or row['source_id'] in seen_ids or row['path'] in seen_paths):
            raise Blocked('QUEST_MODULE_FILES')
        seen_ids.add(row['source_id']); seen_paths.add(row['path'])
    assets = recipe.get('assets', [])
    if not isinstance(assets, list) or len(assets) > ASSET_LIMIT:
        raise Blocked('QUEST_ASSETS')
    for row in assets:
        if (not isinstance(row, dict)
                or set(row) != {'file_id', 'member_chain', 'path', 'bytes', 'sha256', 'role'}):
            raise Blocked('QUEST_ASSETS')
        text(row['file_id'], 200)
        chain(row['member_chain'])
        _path(row['path'])
        _digest(row['sha256'])
        if (type(row['bytes']) is not int or not 0 <= row['bytes'] <= DEFAULTS['max_member_bytes']
                or row['role'] not in ('resource', 'notice')
                or row['path'] in seen_paths):
            raise Blocked('QUEST_ASSETS')
        if PurePosixPath(row['path']).suffix.lower() in IMPORTABLE_SUFFIXES:
            raise Blocked('QUEST_ASSET_EXECUTABLE')
        seen_paths.add(row['path'])
    return mappings, assets


def check_paths(paths, module_paths):
    """Refuse overlapping files/directories and ambiguous Python package layouts."""
    folded = {path.casefold() for path in paths}
    if len(folded) != len(paths):
        raise Blocked('QUEST_PRODUCT_PATH_COLLISION')
    modules = {path.casefold() for path in module_paths}
    for path in paths:
        for parent in PurePosixPath(path).parents:
            if parent == PurePosixPath('.'):
                break
            if parent.as_posix().casefold() in folded:
                raise Blocked('QUEST_PRODUCT_PATH_COLLISION')
            if (parent.as_posix() + '.py').casefold() in modules:
                raise Blocked('QUEST_IMPORT_PATH_COLLISION')


class Sources:
    """One bounded container cache; all output bodies remain under the product cap."""
    def __init__(self, report, root):
        self.root = root
        self.records = {record['source']['file_id']: record for record in report['sources']}
        self.segments = {segment['source_id']: (record['source'], segment)
                         for record in report['sources'] for segment in record['segments']}
        self.used = set()
        self.cached = None

    def _container(self, file_id):
        if file_id not in self.records:
            raise Blocked('QUEST_UNINDEXED_SOURCE')
        source = self.records[file_id]['source']
        from .corpus import manifest
        manifest({'schema': 1, 'files': [source]})
        _path(source['path'])
        path = safe_path(self.root / source['path'])
        if not path.is_relative_to(self.root):
            raise Blocked('SOURCE_ESCAPE')
        self.used.add(file_id)
        if self.cached is not None and self.cached[0] == file_id:
            return source, self.cached[1]
        data = read(path, DEFAULTS['max_container_bytes'])
        if len(data) != source['size'] or sha(data) != source['sha256']:
            raise Blocked('SOURCE_VERSION_MISMATCH')
        self.cached = (file_id, data)
        return source, data

    def bytes(self, file_id, members):
        chain(members)
        source, data = self._container(file_id)
        parent_path = source['path']
        entries = expanded = 0
        for member in members:
            if PurePosixPath(parent_path).suffix.lower() != '.zip':
                raise Blocked('QUEST_SOURCE_ARCHIVE')
            try:
                with ZipFile(BytesIO(data)) as archive:
                    inventory = archive.infolist()
                    entries += len(inventory)
                    if entries > DEFAULTS['max_entries']:
                        raise Blocked('QUEST_SOURCE_LIMIT')
                    found = [item for item in inventory if item.filename == member['path']]
                    if len(found) != 1:
                        raise Blocked('QUEST_SOURCE_MEMBER')
                    item = found[0]
                    # ZipInfo truncates exposed names at NUL. A selected path
                    # must identify the complete original archive name exactly.
                    if (item.orig_filename != item.filename or not _path_ok(item.orig_filename)
                            or item.is_dir() or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG)
                            or item.flag_bits & 1
                            or item.compress_type not in (ZIP_STORED, ZIP_DEFLATED, ZIP_BZIP2, ZIP_LZMA)):
                        raise Blocked('QUEST_SOURCE_MEMBER')
                    if (item.file_size > DEFAULTS['max_member_bytes']
                            or item.file_size / max(1, item.compress_size) > DEFAULTS['max_ratio']
                            or expanded + item.file_size > DEFAULTS['max_total_bytes']):
                        raise Blocked('QUEST_SOURCE_LIMIT')
                    with archive.open(item) as stream:
                        data = stream.read(item.file_size + 1)
                    if len(data) != item.file_size or sha(data) != member['sha256']:
                        raise Blocked('QUEST_SOURCE_BINDING')
                    expanded += len(data)
            except (BadZipFile, OSError, ValueError, RuntimeError, NotImplementedError) as exc:
                if isinstance(exc, Blocked):
                    raise
                raise Blocked('QUEST_SOURCE_ARCHIVE') from exc
            parent_path = member['path']
        return data

    def origin(self, file_id, members):
        source = self.records[file_id]['source']
        return {'file_id': file_id, 'container_path': source['path'],
                'container_sha256': source['sha256'], 'member_chain': members}

    def module(self, source_id):
        if source_id not in self.segments:
            raise Blocked('QUEST_UNINDEXED_MODULE')
        source, segment = self.segments[source_id]
        if (segment['format'] != 'python_file' or PurePosixPath(segment['path']).suffix != '.py'):
            raise Blocked('QUEST_WHOLE_MODULE_REQUIRED')
        data = self.bytes(source['file_id'], segment['member_chain'])
        try:
            normalized = _decode(data)[0].encode('utf-8')
        except (UnicodeError, SyntaxError, LookupError) as exc:
            raise Blocked('QUEST_SOURCE_BINDING') from exc
        if sha(normalized) != segment['source_sha256']:
            raise Blocked('QUEST_SOURCE_BINDING')
        origin = self.origin(source['file_id'], segment['member_chain'])
        origin.update(source_id=source_id, analysis_source_sha256=segment['source_sha256'])
        return data, origin

    def asset(self, reference):
        source, _ = self._container(reference['file_id'])
        original = (reference['member_chain'][-1]['path'] if reference['member_chain'] else source['path'])
        if PurePosixPath(original).suffix.lower() in IMPORTABLE_SUFFIXES:
            raise Blocked('QUEST_ASSET_EXECUTABLE')
        data = self.bytes(reference['file_id'], reference['member_chain'])
        if len(data) != reference['bytes'] or sha(data) != reference['sha256']:
            raise Blocked('QUEST_SOURCE_BINDING')
        return data, self.origin(reference['file_id'], reference['member_chain'])

    def initializer(self, source_id, original_path):
        """Return an existing indexed sibling initializer, or block if unindexed."""
        source, segment = self.segments[source_id]
        members = segment['member_chain']
        for key, (candidate_source, candidate) in self.segments.items():
            same_container = (candidate_source['file_id'] == source['file_id']
                              and candidate['member_chain'][:-1] == members[:-1])
            same_plain_root = not members and not candidate['member_chain']
            if ((same_container or same_plain_root) and candidate['path'] == original_path
                    and candidate['format'] == 'python_file'):
                return key
        if not members:
            exists = safe_path(self.root / original_path).exists()
        else:
            data = self.bytes(source['file_id'], members[:-1])
            try:
                with ZipFile(BytesIO(data)) as archive:
                    exists = any(item.filename == original_path for item in archive.infolist())
            except (BadZipFile, OSError, ValueError) as exc:
                raise Blocked('QUEST_SOURCE_ARCHIVE') from exc
        if exists:
            raise Blocked('QUEST_SELECT_PACKAGE_INITIALIZER')
        return None

    def verify_unchanged(self):
        for file_id in sorted(self.used):
            self.cached = None
            self._container(file_id)
