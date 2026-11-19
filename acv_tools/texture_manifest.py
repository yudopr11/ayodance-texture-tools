"""Inventory verified DDS outputs and reuse them when staging a game update."""

import argparse
import hashlib
import json
import os
import struct
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .acv_unpack import decode_entry, decode_payload, read_index, safe_relative_path
from .acv_upscale import (
    MAGIC,
    SUPPORTED_RENDERERS,
    dds_format,
    dds_info,
    digest,
    pipeline_identity,
    preserved_image_format,
    process,
    receipt_path,
    source_dds,
    tag_dds,
    target_size,
)

_IO_BUFFER = 1024 * 1024
_MAX_INFLIGHT_BYTES = 64 * 1024 * 1024


def _default_workers(dds_count):
    if dds_count < 8:
        return 1
    return min(8, os.cpu_count() or 1)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    pending.replace(path)


def describe(raw):
    view = source_dds(raw)
    width, height, _ = dds_info(view)
    result = {'width': width, 'height': height, 'mips': max(1, struct.unpack_from('<I', view, 28)[0]),
              'format': ('PSD' if raw[:4] == b'8BPS' else
                         'BC7_UNORM' if raw[84:88] == b'DX10' else dds_format(view)),
              'pixel_format': None if raw[:4] == b'8BPS' else raw[76:108].hex()}
    if raw[:4] == b'DDS ' and struct.unpack_from('<I', raw, 112)[0] == 0x200000:
        result['depth'] = struct.unpack_from('<I', raw, 24)[0]
    return result


def untag(native, enhanced):
    if native[32:40] == MAGIC:
        raise ValueError('Use untouched original textures, not renderer-tagged inputs')
    width, height, _ = dds_info(native)
    if target_size(width, height) == (width, height):
        if native[84:88] == b'DX10':
            raise ValueError('At-cap BC7 requires a separate same-size conversion workflow')
        if enhanced != native:
            raise ValueError('At-cap texture changed')
        return None
    if dds_info(enhanced)[:2] != target_size(width, height):
        raise ValueError('Unexpected enhanced texture dimensions')
    expected_mips = (max(target_size(width, height)).bit_length()
                     if struct.unpack_from('<I', source_dds(native), 28)[0] > 1 else 1)
    if max(1, struct.unpack_from('<I', enhanced, 28)[0]) != expected_mips:
        raise ValueError('Enhanced texture mip policy changed')
    plain = enhanced[:32] + bytes(28) + enhanced[60:]
    if tag_dds(native, plain) != enhanced:
        raise ValueError('Renderer metadata or DDS format mismatch')
    return plain


def _read_exact(stream, offset, size, name):
    if stream.tell() != offset:
        stream.seek(offset)
    data = stream.read(size)
    if len(data) != size:
        raise ValueError(f'Truncated payload: {name}')
    return data


def _inspect_dds_pair(archive_name, index, path_posix, old, new, old_packed, new_packed, preserved_set):
    native = decode_payload(old_packed, old[3], old[5], old[6])
    enhanced = decode_payload(new_packed, new[3], new[5], new[6])
    preserved_format = preserved_image_format(native)
    native_hash = sha256(native)
    if preserved_format and (old[0], preserved_format, native_hash) in preserved_set:
        if old[2:4] != new[2:4] or old_packed != new_packed:
            raise ValueError(f'Preserved {preserved_format} stored payload changed: {old[0]!r}')
        return ('preserved', {'path': old[0], 'format': preserved_format, 'source_sha256': native_hash})
    plain = untag(native, enhanced)
    return ('texture', {
        'archive': archive_name, 'entry_index': index, 'texture': old[0],
        'path': path_posix, 'source_sha256': native_hash,
        'output_sha256': sha256(enhanced),
        'cache_sha256': sha256(plain) if plain is not None else None,
        'original': describe(native), 'output': describe(enhanced),
        'status': 'enhanced' if plain is not None else 'at_cap',
    })


def capture(source, staged, workers=None):
    """Read both archives; certify names/order, DDS metadata and opaque payloads."""
    if workers is not None and workers < 1:
        raise ValueError('Worker count must be positive')
    source, staged = source.resolve(), staged.resolve()
    output = staged / source.name
    try:
        receipt = json.loads(receipt_path(output).read_text(encoding='utf-8'))
    except json.JSONDecodeError as error:
        raise ValueError(f'Invalid verified receipt JSON: {source.name}') from error
    before_hash, after_hash = digest(source), digest(output)
    if (receipt.get('status') != 'verified' or receipt.get('renderer') not in SUPPORTED_RENDERERS
            or receipt.get('source_sha256') != before_hash
            or receipt.get('output_sha256') != after_hash):
        raise ValueError(f'Unverified or changed archive: {source.name}')
    textures = []
    preserved = []
    seen = set()
    preserved_set = {
        (item.get('path'), item.get('format'), item.get('source_sha256'))
        for item in receipt.get('preserved_images', [])
    }
    with source.open('rb', buffering=_IO_BUFFER) as original, output.open('rb', buffering=_IO_BUFFER) as rebuilt:
        before, after = read_index(original), read_index(rebuilt)
        if len(before) != len(after):
            raise ValueError('Archive entry count changed')
        dds_count = sum(1 for old in before if old[0].lower().endswith('.dds'))
        thread_count = _default_workers(dds_count) if workers is None else min(workers, max(1, dds_count))

        def record_result(kind, payload):
            if kind == 'preserved':
                preserved.append(payload)
            else:
                textures.append(payload)

        if thread_count <= 1:
            for index, (old, new) in enumerate(zip(before, after, strict=True)):
                path = safe_relative_path(old[0])
                if (old[0], old[1], old[5], old[6]) != (new[0], new[1], new[5], new[6]):
                    raise ValueError('Archive entry metadata changed')
                if path.suffix.lower() != '.dds':
                    if (old[2:4] != new[2:4]
                            or _read_exact(original, old[4], old[2], old[0]) != _read_exact(rebuilt, new[4], new[2], new[0])):
                        raise ValueError(f'Opaque payload changed: {old[0]!r}')
                    continue
                key = path.as_posix().casefold()
                if key in seen:
                    raise ValueError('Duplicate DDS archive paths')
                seen.add(key)
                old_packed = _read_exact(original, old[4], old[2], old[0])
                new_packed = _read_exact(rebuilt, new[4], new[2], new[0])
                kind, item = _inspect_dds_pair(
                    source.name, index, path.as_posix(), old, new, old_packed, new_packed, preserved_set
                )
                record_result(kind, item)
        else:
            max_in_flight = thread_count * 2
            pending = deque()
            pending_bytes = 0
            with ThreadPoolExecutor(max_workers=thread_count) as pool:
                try:
                    for index, (old, new) in enumerate(zip(before, after, strict=True)):
                        path = safe_relative_path(old[0])
                        if (old[0], old[1], old[5], old[6]) != (new[0], new[1], new[5], new[6]):
                            raise ValueError('Archive entry metadata changed')
                        if path.suffix.lower() != '.dds':
                            if (old[2:4] != new[2:4]
                                    or _read_exact(original, old[4], old[2], old[0]) != _read_exact(rebuilt, new[4], new[2], new[0])):
                                raise ValueError(f'Opaque payload changed: {old[0]!r}')
                            continue
                        key = path.as_posix().casefold()
                        if key in seen:
                            raise ValueError('Duplicate DDS archive paths')
                        seen.add(key)
                        old_packed = _read_exact(original, old[4], old[2], old[0])
                        new_packed = _read_exact(rebuilt, new[4], new[2], new[0])
                        est_bytes = old[2] + old[3] + new[2] + new[3]
                        while pending and (len(pending) >= max_in_flight or pending_bytes + est_bytes > _MAX_INFLIGHT_BYTES):
                            done_est, future = pending.popleft()
                            pending_bytes -= done_est
                            record_result(*future.result())
                        future = pool.submit(
                            _inspect_dds_pair,
                            source.name, index, path.as_posix(), old, new, old_packed, new_packed, preserved_set,
                        )
                        pending.append((est_bytes, future))
                        pending_bytes += est_bytes
                    while pending:
                        done_est, future = pending.popleft()
                        pending_bytes -= done_est
                        record_result(*future.result())
                except BaseException:
                    for _, future in pending:
                        future.cancel()
                    raise
    if digest(source) != before_hash or digest(output) != after_hash:
        raise ValueError('Archive changed during inventory')
    if len(textures) != receipt.get('textures'):
        raise ValueError('Texture count differs from verified receipt')
    if preserved != receipt.get('preserved_images', []):
        raise ValueError('Preserved images differ from verified receipt')
    record = {'schema': 1, 'archive': source.name, 'source': str(source), 'output': str(output),
            'source_sha256': before_hash, 'output_sha256': after_hash,
            'pipeline': receipt['pipeline'], 'renderer': receipt['renderer'],
            'model': receipt.get('model'), 'device': receipt.get('device'),
            'status': 'verified', 'textures': textures}
    if preserved:
        record['preserved_images'] = preserved
    return record


def build(source, staged, destination, workers=None):
    """Backfill without converters or AI. Destination must be a new directory."""
    if workers is not None and workers < 1:
        raise ValueError('Worker count must be positive')
    source, staged, destination = source.resolve(), staged.resolve(), destination.resolve()
    source_directory = source.parent if source.is_file() else source
    if destination == source_directory or destination.is_relative_to(source_directory):
        raise ValueError('Manifest output must be outside the original archive directory')
    if (staged / '.batch.lock').exists():
        raise ValueError('A staging batch lock exists; inspect the live job first')
    receipts = {p.name for directory in (staged, staged / '.metadata')
                for p in directory.glob('*.acv.json')}
    sources = ([source] if source.is_file() else
               [source / name.removesuffix('.json') for name in sorted(receipts)])
    if not sources:
        raise ValueError('No staged archive receipts found')
    destination.mkdir(parents=True, exist_ok=False)
    results = []
    for archive in sources:
        record = capture(archive, staged, workers=workers)
        name = archive.name + '.json'
        save(destination / name, record)
        results.append({'file': name, 'sha256': digest(destination / name),
                        'archive': archive.name, 'textures': len(record['textures']),
                        'pipeline': record['pipeline']})
        print(json.dumps({'archive': archive.name, 'textures': len(record['textures']),
                          'status': 'inventoried'}), flush=True)
    index = {'schema': 1, 'archives': results,
             'textures': sum(r['textures'] for r in results)}
    save(destination / 'index.json', index)
    return index


def load_records(manifest):
    try:
        index = json.loads((manifest / 'index.json').read_text(encoding='utf-8'))
    except json.JSONDecodeError as error:
        raise ValueError(f'Invalid manifest index JSON: {manifest}') from error
    if index.get('schema') != 1:
        raise ValueError('Unsupported manifest schema')
    records = []
    for item in index['archives']:
        relative = safe_relative_path(item['file'])
        if len(relative.parts) != 1:
            raise ValueError('Manifest filenames must be basenames')
        path = manifest / relative
        data = path.read_bytes()
        if path.is_symlink() or sha256(data) != item['sha256']:
            raise ValueError(f'Manifest changed: {path}')
        try:
            record = json.loads(data)
        except json.JSONDecodeError as error:
            raise ValueError(f'Invalid manifest JSON: {path}') from error
        if record.get('schema') != 1 or record.get('status') != 'verified' or record.get('renderer') not in SUPPORTED_RENDERERS:
            raise ValueError(f'Unverified manifest: {path}')
        records.append(record)
    return records


def stage_update(source, destination, manifest, texconv, model, reuse_pipelines=()):
    """Reuse only exact hashes and explicitly compatible pipelines; never install."""
    source, destination = source.resolve(), destination.resolve()
    if destination == source.parent or destination.is_relative_to(source.parent):
        raise ValueError('Update staging must be outside the original archive directory')
    pipeline = pipeline_identity(texconv, model=model)
    allowed = {pipeline, *reuse_pipelines}
    candidates = {}
    records = sorted(load_records(manifest), key=lambda r: r['pipeline'] != pipeline)
    for record in records:
        if record['pipeline'] not in allowed:
            continue
        # A compatibility override must not accidentally import another model/backend.
        device = 'directml:0' if model.suffix.lower() == '.onnx' else 'cpu'
        if record['model'] != model.name or record['device'] != device:
            raise ValueError('Compatible pipeline has a different model or backend')
        for texture in record['textures']:
            if texture['status'] == 'enhanced':
                key = texture['source_sha256']
                existing = candidates.get(key)
                if existing is None:
                    candidates[key] = record, texture
                elif existing[0]['pipeline'] == pipeline and record['pipeline'] != pipeline:
                    continue
                elif existing[1]['cache_sha256'] != texture['cache_sha256']:
                    raise ValueError(f'Conflicting enhancements for source texture: {key}')
    destination.mkdir(parents=True, exist_ok=False)
    cache = destination / '.texture-cache' / pipeline
    reused, enhanced, at_cap = 0, 0, 0
    certified = {}
    source_hash_before = digest(source)
    reused_from = {}
    seen = set()
    with source.open('rb') as stream:
        entries = read_index(stream)
        for entry in entries:
            path = safe_relative_path(entry[0])
            if path.suffix.lower() != '.dds':
                continue
            key = path.as_posix().casefold()
            if key in seen:
                raise ValueError('Duplicate DDS archive paths')
            seen.add(key)
            native = decode_entry(stream, entry)
            if (preserved_image_format(native) == 'DDS-volume'
                    and struct.unpack_from('<I', native, 24)[0] > 1):
                continue
            width, height, _ = dds_info(native)
            if native[32:40] == MAGIC:
                raise ValueError('Use untouched original textures')
            if target_size(width, height) == (width, height):
                at_cap += 1
                continue
            source_hash = sha256(native)
            candidate = candidates.get(source_hash)
            if candidate is None:
                enhanced += 1
                continue
            record, texture = candidate
            previous, original = Path(record['output']), Path(record['source'])
            identity = (str(original), str(previous), record['source_sha256'],
                        record['output_sha256'], record['pipeline'], record['model'], record['device'])
            if identity not in certified:
                if digest(previous) != record['output_sha256']:
                    raise ValueError(f'Previous enhanced archive changed: {previous}')
                if digest(original) != record['source_sha256']:
                    raise ValueError(f'Previous original archive changed: {original}')
                try:
                    receipt = json.loads(receipt_path(previous).read_bytes())
                except json.JSONDecodeError as error:
                    raise ValueError(f'Invalid verified receipt JSON: {previous}') from error
                fields = ('source_sha256', 'output_sha256', 'pipeline', 'model', 'device', 'renderer')
                if receipt.get('status') != 'verified' or any(receipt.get(k) != record[k] for k in fields):
                    raise ValueError('Manifest differs from previous verified receipt')
                with previous.open('rb') as archive, original.open('rb') as old_source:
                    certified[identity] = read_index(old_source), read_index(archive)
            before, after = certified[identity]
            old_entry, new_entry = before[texture['entry_index']], after[texture['entry_index']]
            if old_entry[0] != texture['texture'] or new_entry[0] != texture['texture']:
                raise ValueError('Manifest texture location changed')
            with original.open('rb') as old_source:
                if decode_entry(old_source, old_entry) != native:
                    raise ValueError('Manifest source texture hash binding mismatch')
            with previous.open('rb') as archive:
                output = decode_entry(archive, new_entry)
            plain = untag(native, output)
            if (plain is None or sha256(output) != texture['output_sha256']
                    or sha256(plain) != texture['cache_sha256']):
                raise ValueError('Previous enhanced texture hash mismatch')
            cache.mkdir(parents=True, exist_ok=True)
            cached = cache / (source_hash + '.dds')
            if cached.exists() and cached.read_bytes() != plain:
                raise ValueError('Conflicting cached enhancements for identical input')
            cached.write_bytes(plain)
            cached.with_suffix('.sha256').write_text(sha256(plain), encoding='utf-8')
            reused += 1
            reused_from[source_hash] = record['pipeline']
    if digest(source) != source_hash_before:
        raise ValueError('Updated original archive changed during cache preparation')
    result = process(source, destination, texconv, pipeline=pipeline, model=model)
    build(source, destination, destination / 'texture-manifest')
    report = {'archive': source.name, 'reused': reused, 'new_or_changed': enhanced,
              'at_cap': at_cap, 'pipeline': pipeline, 'reused_from': reused_from,
              'result': result}
    save(destination / 'update-report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    inventory = commands.add_parser('build', help='Backfill verified archives; no AI or converter')
    inventory.add_argument('source', type=Path)
    inventory.add_argument('staged', type=Path)
    inventory.add_argument('destination', type=Path)
    inventory.add_argument('--workers', type=int, default=None, help='Worker threads per archive (default: auto)')
    update = commands.add_parser('update', help='Stage one updated original ACV into a NEW directory')
    update.add_argument('source', type=Path)
    update.add_argument('destination', type=Path)
    update.add_argument('--manifest', type=Path, required=True)
    update.add_argument('--texconv', type=Path, default=Path('tools/texconv.exe'))
    update.add_argument('--model', type=Path, required=True)
    update.add_argument('--reuse-pipeline', action='append', default=[],
                        help='Explicitly approve an audited older pipeline; never use for model/tool changes')
    args = parser.parse_args()
    try:
        if args.command == 'build':
            result = build(args.source.resolve(), args.staged.resolve(), args.destination.resolve(),
                           workers=args.workers)
            print(json.dumps({'archives': len(result['archives']), 'textures': result['textures']}))
        else:
            result = stage_update(args.source, args.destination, args.manifest,
                                  args.texconv.resolve(strict=True), args.model.resolve(strict=True),
                                  args.reuse_pipeline)
            print(json.dumps(result))
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        parser.exit(1, f'error: {error}\n')


if __name__ == '__main__':
    main()
