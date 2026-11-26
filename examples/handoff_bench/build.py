"""Package the reviewed handoff example without executing or fetching source."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
from zipfile import ZIP_STORED, ZipFile, ZipInfo


HERE = Path(__file__).resolve().parent
CORE_SHA256 = '555a3fd37e87912d164550a8e915f4fd5052d1e0e2da32b7c4402da4e381d1d5'
SPEC_SHA256 = '8f6944ea0559e1cf2398dbb4a12b1066d0d6cbdc17d5ab53d4be13491d0fa338'
APP_BYTES = 8_000_000


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode() + b'\n'


def source_file(path, maximum=APP_BYTES):
    path = Path(path)
    if any(part.is_symlink() for part in [path, *path.parents]):
        raise ValueError('Symlink source path')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
        raise ValueError('Non-regular or oversized source')
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) != info.st_size or len(raw) > maximum:
        raise ValueError('Source changed or oversized')
    return raw


def build_product(out_dir, bench_root=HERE):
    bench_root = Path(bench_root).absolute()
    out = Path(out_dir).absolute()
    if out.exists() or out.is_symlink():
        raise ValueError('New output directory required')
    if out.is_relative_to(bench_root) or bench_root.is_relative_to(out):
        raise ValueError('Build output must be separate from example sources')
    repository = HERE.parents[1]
    if (repository / 'forge.py').is_file() and out.is_relative_to(repository):
        raise ValueError('Build output must be outside the FORGE checkout')
    if any(part.is_symlink() for part in out.parents):
        raise ValueError('Symlink output path')
    pins = json.loads(source_file(bench_root / 'preservation_pins.json', 64_000))
    if pins.get('schema') != 1 or not isinstance(pins.get('files'), list):
        raise ValueError('Preservation inventory')
    preserved = {}
    for pin in pins['files']:
        relative = Path(pin['path'])
        if relative.is_absolute() or '..' in relative.parts or str(relative) in preserved:
            raise ValueError('Preservation path')
        raw = source_file(bench_root / relative)
        if len(raw) != pin['bytes'] or digest(raw) != pin['sha256']:
            raise ValueError('Preserved source identity changed')
        preserved[str(relative)] = raw
    if digest(preserved['planner_core.py']) != CORE_SHA256 or digest(preserved['SPEC.md']) != SPEC_SHA256:
        raise ValueError('Original optimizer or specification changed')
    members = {'planner_core.py': preserved['planner_core.py']}
    for path, raw in preserved.items():
        if path.startswith('vendor/'):
            members[path[len('vendor/'):]] = raw
    vendor_files = {str(path.relative_to(bench_root)) for path in (bench_root / 'vendor').rglob('*')
                    if path.is_file() and '__pycache__' not in path.parts}
    if vendor_files != {path for path in preserved if path.startswith('vendor/')}:
        raise ValueError('Unpinned vendor inventory')
    for name in ['handoff.py', 'burden.py', 'SOURCE_FIELDS.json']:
        members[name] = source_file(bench_root / name)
    members['__main__.py'] = source_file(bench_root / 'entrypoint.py')
    if sum(map(len, members.values())) > APP_BYTES:
        raise ValueError('Product source size limit')
    for path, raw in members.items():
        if path.endswith('.py'):
            compile(raw, path, 'exec', dont_inherit=True)
    # Fixed metadata and no compressor make the same reviewed source bytes
    # reproducible across the supported Python versions.
    from io import BytesIO
    stream = BytesIO()
    with ZipFile(stream, 'w', compression=ZIP_STORED) as archive:
        for path, raw in sorted(members.items()):
            info = ZipInfo(path, (2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw)
    product = stream.getvalue()
    if len(product) > APP_BYTES:
        raise ValueError('Product archive size limit')
    receipt = {'schema': 1, 'status': 'REVIEWED_EXAMPLE_PACKAGED',
               'product': {'path': 'product.pyz', 'bytes': len(product), 'sha256': digest(product)},
               'core_sha256': CORE_SHA256, 'spec_sha256': SPEC_SHA256,
               'members': [{'path': path, 'bytes': len(raw), 'sha256': digest(raw)}
                           for path, raw in sorted(members.items())],
               'source_executed': False, 'network_requests': 0}
    out.mkdir(parents=True, exist_ok=False)
    (out / 'product.pyz').write_bytes(product)
    (out / 'build.json').write_bytes(canonical(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(build_product(args.out), indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
