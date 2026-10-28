"""Stage 2x DDS resizing and verified ACV repacks; never install over originals."""

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .acv_pack import pack_archive
from .acv_unpack import decode_entry, read_index, safe_relative_path


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def dds_info(raw):
    if len(raw) < 128 or raw[:4] != b'DDS ' or struct.unpack_from('<I', raw, 4)[0] != 124:
        raise ValueError('Invalid DDS header')
    height, width = struct.unpack_from('<2I', raw, 12)
    if not width or not height:
        raise ValueError('Zero DDS dimension')
    # shortcut: legacy 2D DDS only; add explicit handling if a cube/volume/DX10 is encountered.
    if raw[84:88] == b'DX10' or struct.unpack_from('<I', raw, 112)[0]:
        raise ValueError('DX10, cubemap or volume DDS requires explicit handling')
    return width, height, raw[80:108]


def target_size(width, height):
    scale = min(2, 2048 / max(width, height))
    if scale <= 1:
        return width, height
    return max(1, int(width * scale)), max(1, int(height * scale))


AI_PIPELINE = 'realesrgan-x4plus-20220424-2x-cap2048-v1'
RENDERER = 'v64-tagged-dimensions-v1'
MAGIC = b'ACVUI2X\0'


def pipeline_identity(texconv, esrgan):
    if not esrgan:
        return 'cubic-2x-cap2048-v1'
    files = [texconv, esrgan, esrgan.parent / 'models/realesrgan-x4plus.bin',
             esrgan.parent / 'models/realesrgan-x4plus.param']
    fingerprint = hashlib.sha256(''.join(digest(path) for path in files).encode()).hexdigest()
    return AI_PIPELINE + '-' + fingerprint[:16]


def run_tool(command):
    result = subprocess.run([str(arg) for arg in command], capture_output=True,
                            text=True, errors='replace', timeout=1800)
    if result.returncode:
        raise ValueError(f'Tool failed: {command[0]}: {result.stdout} {result.stderr}')
    return result


def dds_format(raw):
    fourcc = bytes(raw[84:88])
    formats = {b'DXT1': 'BC1_UNORM', b'DXT3': 'BC2_UNORM', b'DXT5': 'BC3_UNORM'}
    if fourcc in formats:
        return formats[fourcc]
    flags, _, bits, red, green, blue, alpha = struct.unpack_from('<7I', raw, 80)
    if flags & 4:
        raise ValueError(f'Unsupported DDS FourCC: {fourcc!r}')
    mapping = {
        (32, 0xff0000, 0xff00, 0xff, 0xff000000): 'B8G8R8A8_UNORM',
        (32, 0xff0000, 0xff00, 0xff, 0): 'B8G8R8X8_UNORM',
        (24, 0xff0000, 0xff00, 0xff, 0): 'B8G8R8X8_UNORM',
        (32, 0xff, 0xff00, 0xff0000, 0xff000000): 'R8G8B8A8_UNORM',
        (16, 0xf800, 0x7e0, 0x1f, 0): 'B5G6R5_UNORM',
        (16, 0x7c00, 0x3e0, 0x1f, 0x8000): 'B5G5R5A1_UNORM',
        (16, 0x7c00, 0x3e0, 0x1f, 0): 'B5G5R5A1_UNORM',
        (16, 0xf00, 0xf0, 0xf, 0xf000): 'B4G4R4A4_UNORM',
        (8, 0, 0, 0, 0xff): 'A8_UNORM',
    }
    key = (bits, red, green, blue, alpha)
    if key not in mapping:
        raise ValueError(f'Unsupported DDS channel masks: {key}')
    return mapping[key]


def tag_dds(native, enhanced):
    """Keep physical DDS dimensions; record native layout dimensions for the renderer."""
    width, height, pixel_format = dds_info(native)
    physical_width, physical_height, enhanced_format = dds_info(enhanced)
    if native[32:40] == MAGIC:
        raise ValueError('Source DDS already has renderer metadata; use the original archive')
    if pixel_format != enhanced_format:
        raise ValueError('Renderer metadata requires preserved DDS pixel format')
    if (width, height) == (physical_width, physical_height):
        return None
    if width > physical_width or height > physical_height or max(physical_width, physical_height) > 16384:
        raise ValueError('Renderer metadata requires enlarged, supported DDS dimensions')
    if struct.unpack_from('<I', enhanced, 76)[0] != 32:
        raise ValueError('Invalid DDS pixel-format header size')
    dds_format(enhanced)  # Keep tagged formats within the shim's supported alpha decoders.
    if any(enhanced[32:76]):
        raise ValueError('DDS reserved fields are already in use; refusing to overwrite them')
    result = bytearray(enhanced)
    struct.pack_into('<8s5I', result, 32, MAGIC, 1, width, height, physical_width, physical_height)
    return bytes(result)


def restore_rgb24(converted, original):
    """DXGI has no RGB24 format; strip X bytes from all BGRX mip levels."""
    width, height, _ = dds_info(converted)
    levels = max(1, struct.unpack_from('<I', converted, 28)[0])
    expected = sum(max(1, width >> level) * max(1, height >> level) * 4
                   for level in range(levels))
    pixels = bytearray(converted[128:])
    if len(pixels) != expected:
        raise ValueError('Unexpected BGRX mip payload size')
    del pixels[3::4]
    header = bytearray(converted[:128])
    header[76:108] = original[76:108]
    flags = struct.unpack_from('<I', header, 8)[0]
    struct.pack_into('<I', header, 8, (flags & ~0x80000) | 8)
    struct.pack_into('<I', header, 20, width * 3)
    return bytes(header + pixels)


def ai_image(decoded, enlarged, size):
    from PIL import Image

    with Image.open(decoded) as original, Image.open(enlarged) as enhanced:
        if enhanced.size != (original.width * 4, original.height * 4):
            raise ValueError('ESRGAN output is not 4x the source dimensions')
        rgb = enhanced.convert('RGB').resize(size, Image.Resampling.LANCZOS)
        alpha = original.convert('RGBA').getchannel('A').resize(size, Image.Resampling.LANCZOS)
        rgb.putalpha(alpha)
        rgb.save(enlarged)


def resize_dds(raw, source, destination, texconv, esrgan=None, cache=None):
    width, height, pixel_format = dds_info(raw)
    size = target_size(width, height)
    if size == (width, height):
        return False
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / source.name
    cached = None
    if esrgan and cache:
        cache.mkdir(parents=True, exist_ok=True)
        cached = cache / (hashlib.sha256(raw).hexdigest() + '.dds')
        checksum = cached.with_suffix('.sha256')
        if cached.exists() and checksum.exists() and digest(cached) == checksum.read_text():
            shutil.copyfile(cached, output)
            return True
    source.write_bytes(raw)
    # Retain no-mip UI textures; regenerate a complete chain for mipmapped inputs.
    mips = 0 if struct.unpack_from('<I', raw, 28)[0] > 1 else 1
    command = [str(texconv), '-nologo', '-dx9', '-if', 'CUBIC', '-w', str(size[0]),
               '-h', str(size[1]), '-m', str(mips), '-o', str(destination), str(source)]
    if esrgan:
        decoded = destination / 'input.png'
        enhanced = destination / 'enhanced.png'
        run_tool([texconv, '-nologo', '-y', '-ft', 'png', '-f', 'R8G8B8A8_UNORM',
                  '-m', '1', '-o', destination, source])
        # Feed RGB only; AI must not hallucinate the texture's transparency.
        from PIL import Image
        with Image.open(decoded) as image:
            image.convert('RGB').save(destination / 'rgb.png')
        run_tool([esrgan, '-i', destination / 'rgb.png', '-o', enhanced,
                  '-n', 'realesrgan-x4plus', '-s', '4', '-t', '128',
                  '-m', esrgan.parent / 'models', '-g', '0'])
        ai_image(decoded, enhanced, size)
        command = [texconv, '-nologo', '-dx9', '-y', '-if', 'CUBIC', '-f', dds_format(raw),
                   '-m', str(mips), '-o', destination, enhanced]
    run_tool(command)
    if esrgan:
        (destination / 'enhanced.dds').replace(output)
    if not output.exists():
        raise ValueError('texconv did not produce a DDS file')
    converted = output.read_bytes()
    if esrgan and struct.unpack_from('<I', raw, 88)[0] == 24:
        converted = restore_rgb24(converted, raw)
        output.write_bytes(converted)
    elif esrgan and struct.unpack_from('<5I', raw, 88) == (16, 0x7c00, 0x3e0, 0x1f, 0):
        # DXGI exposes RGB555 only with alpha; restore the original unused-X header.
        converted = converted[:76] + raw[76:108] + converted[108:]
        output.write_bytes(converted)
    new_width, new_height, new_format = dds_info(converted)
    if (new_width, new_height) != size or new_format != pixel_format:
        raise ValueError('Resized DDS dimensions or pixel format changed unexpectedly')
    if cached:
        pending = cached.with_suffix('.pending')
        shutil.copyfile(output, pending)
        pending.replace(cached)
        cached.with_suffix('.sha256').write_text(digest(cached))
    return True


def verify_archive(source, output, replacements):
    with source.open('rb') as original, output.open('rb') as rebuilt:
        before, after = read_index(original), read_index(rebuilt)
        if len(before) != len(after):
            raise ValueError('Repacked entry count mismatch')
        for old, new in zip(before, after, strict=True):
            if (old[0], old[1], old[5], old[6]) != (new[0], new[1], new[5], new[6]):
                raise ValueError('Repacked entry metadata mismatch')
            replacement = replacements / safe_relative_path(old[0])
            if replacement.is_file():
                if decode_entry(rebuilt, new) != replacement.read_bytes():
                    raise ValueError(f'Repacked texture mismatch: {old[0]}')
            else:
                original.seek(old[4])
                rebuilt.seek(new[4])
                if old[2:4] != new[2:4] or original.read(old[2]) != rebuilt.read(new[2]):
                    raise ValueError(f'Unchanged stored payload mismatch: {old[0]}')


def process(source, staging, texconv, esrgan=None, pipeline=None):
    start = time.monotonic()
    output = staging / source.name
    receipt = staging / (source.name + '.json')
    source_hash = digest(source)
    pipeline = pipeline or pipeline_identity(texconv, esrgan)
    saved = None
    if receipt.exists():
        try:
            saved = json.loads(receipt.read_text())
        except (OSError, ValueError):
            saved = None
    if isinstance(saved, dict):
        if (saved.get('status') == 'verified' and saved.get('pipeline') == pipeline
                and saved.get('source_sha256') == source_hash
                and output.exists() and saved.get('output_sha256') == digest(output)):
            if saved.get('renderer') == RENDERER:
                return saved
            # Rebuild legacy untagged repacks below, reusing the unchanged image cache.
        else:
            raise ValueError(f'Existing receipt/output cannot be safely resumed: {receipt}')
    if output.exists() or receipt.exists():
        # A crash may publish the ACV before its receipt; keep it aside, then rebuild.
        recovery = Path(tempfile.mkdtemp(prefix=source.stem + '-', dir=staging))
        for path in (output, receipt):
            if path.exists():
                path.rename(recovery / path.name)
    result = {'archive': source.name, 'textures': 0, 'resized': 0, 'at_cap': 0,
              'source_sha256': source_hash, 'pipeline': pipeline, 'renderer': RENDERER}
    with tempfile.TemporaryDirectory(prefix=source.stem + '-', dir=staging) as temporary:
        work = Path(temporary)
        replacements = work / 'replacements'
        replacements.mkdir()
        with source.open('rb') as stream:
            entries = read_index(stream)
            paths = [safe_relative_path(e[0]) for e in entries]
            keys = [p.as_posix().casefold() for p in paths]
            if len(keys) != len(set(keys)):
                raise ValueError('Duplicate archive paths')
            for entry, path in zip(entries, paths, strict=True):
                if path.suffix.lower() != '.dds':
                    continue
                raw = decode_entry(stream, entry)
                result['textures'] += 1
                incoming = work / 'input.dds'
                converted_dir = work / 'converted'
                try:
                    resized = resize_dds(raw, incoming, converted_dir, texconv, esrgan,
                                         staging / '.texture-cache' / pipeline)
                except Exception as error:
                    raise ValueError(f'{entry[0]}: {error}') from error
                if resized:
                    target = replacements / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    converted = converted_dir / incoming.name
                    marked = tag_dds(raw, converted.read_bytes())
                    if marked is None:
                        raise ValueError('Resizer reported a change without enlarged DDS dimensions')
                    converted.write_bytes(marked)
                    converted.rename(target)
                    result['resized'] += 1
                else:
                    result['at_cap'] += 1
                if result['textures'] % 10 == 0:
                    (staging / (source.name + '.progress.json')).write_text(json.dumps(result))
        partial = work / source.name
        if result['resized']:
            pack_archive(source, replacements, partial)
        else:
            shutil.copyfile(source, partial)
        verify_archive(source, partial, replacements)
        if digest(source) != source_hash:
            raise ValueError('Original archive changed during processing')
        result.update(status='verified', output_sha256=digest(partial),
                      output_bytes=partial.stat().st_size, seconds=round(time.monotonic() - start, 2))
        partial.rename(output)
        pending_receipt = work / 'receipt.json'
        pending_receipt.write_text(json.dumps(result, indent=2) + '\n')
        pending_receipt.replace(receipt)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('staging', type=Path)
    parser.add_argument('--texconv', type=Path, default=Path('tools/texconv.exe'))
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--esrgan', type=Path, help='NCNN Vulkan executable; enables real AI upscaling')
    args = parser.parse_args()
    source, staging, texconv = args.source.resolve(), args.staging.resolve(), args.texconv.resolve(strict=True)
    esrgan = args.esrgan.resolve(strict=True) if args.esrgan else None
    if esrgan and args.workers != 1:
        parser.error('Use one archive worker for the GPU pipeline and shared texture cache')
    source_dir = source if source.is_dir() else source.parent
    if staging == source_dir or staging.is_relative_to(source_dir):
        parser.error('Staging must be outside the original archive directory')
    archives = sorted(source.glob('*.acv')) if source.is_dir() else [source]
    if not archives or args.workers < 1:
        parser.error('Need archives and a positive worker count')
    staging.mkdir(parents=True, exist_ok=True)
    pipeline = pipeline_identity(texconv, esrgan)
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process, archive, staging, texconv, esrgan, pipeline): archive for archive in archives}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as error:
                result = {'archive': futures[future].name, 'status': 'failed', 'error': str(error)}
            results.append(result)
            print(json.dumps({'completed': len(results), 'total': len(archives), **result}), flush=True)
    report = {'archives': len(archives), 'verified': sum(r['status'] == 'verified' for r in results),
              'results': sorted(results, key=lambda r: r['archive'])}
    (staging / 'batch-report.json').write_text(json.dumps(report, indent=2) + '\n')
    return int(report['verified'] != len(archives))


if __name__ == '__main__':
    raise SystemExit(main())
