"""Independent fixture access to the two explicit internal cache formats."""
import gzip
import json

from forge_core.common import canonical


def cache_files(directory):
    return iter(sorted([*directory.glob('*.json'), *directory.glob('*.json.gz')]))


def cache_bytes(path):
    data = path.read_bytes()
    return gzip.decompress(data) if path.name.endswith('.json.gz') else data


def cache_read(path):
    return json.loads(cache_bytes(path))


def cache_write(path, value):
    data = canonical(value)
    if path.name.endswith('.json.gz'):
        data = gzip.compress(data, mtime=0)
    path.write_bytes(data)
