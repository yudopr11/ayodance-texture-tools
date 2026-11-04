"""Stage 2x DDS resizing and verified ACV repacks; never install over originals."""

import argparse
import hashlib
import io
import json
import shutil
import struct
import subprocess
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from importlib.metadata import version
from pathlib import Path

from .acv_pack import pack_archive
from .acv_unpack import MAX_RAW, decode_entry, read_index, safe_relative_path


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def dds_info(raw):
    raw = source_dds(raw)
    if len(raw) < 128 or raw[:4] != b'DDS ' or struct.unpack_from('<I', raw, 4)[0] != 124:
        raise ValueError('Invalid DDS header')
    height, width = struct.unpack_from('<2I', raw, 12)
    if not width or not height:
        raise ValueError('Zero DDS dimension')
    # shortcut: legacy 2D DDS only; add explicit handling if a cube/volume/DX10 is encountered.
    if raw[84:88] == b'DX10' or struct.unpack_from('<I', raw, 112)[0]:
        raise ValueError('DX10, cubemap or volume DDS requires explicit handling')
    return width, height, raw[80:108]


def is_preserved_psd(raw):
    """Recognize valid mislabeled PSDs; retain them instead of changing their format."""
    if raw[:4] != b'8BPS':
        return False
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as image:
        if image.format != 'PSD' or image.width * image.height * 4 > MAX_RAW:
            raise ValueError('Unsupported or oversized PSD payload')
        image.load()
    return True


def preserved_image_format(raw, *, allow_tag=False):
    """Validate exceptional formats for enhancement and historical receipts."""
    if is_preserved_psd(raw):
        return 'PSD'
    if len(raw) >= 128 and raw[:4] == b'DDS ' and struct.unpack_from('<I', raw, 112)[0] & 0x200000:
        flags, height, width, _, depth, mips = struct.unpack_from('<6I', raw, 8)
        size, pf_flags, fourcc = struct.unpack_from('<3I', raw, 76)
        caps = struct.unpack_from('<I', raw, 108)[0]
        if (struct.unpack_from('<I', raw, 4)[0] != 124 or size != 32 or pf_flags != 4
                or caps & 0x1008 != 0x1008
                or (mips > 1 and (not caps & 0x400000 or not flags & 0x20000))
                or raw[84:88] not in (b'DXT1', b'DXT3', b'DXT5')
                or flags & 0x801007 != 0x801007
                or struct.unpack_from('<I', raw, 112)[0] != 0x200000):
            raise ValueError('Unsupported volume DDS header')
        if not allow_tag and raw[32:40] == MAGIC:
            raise ValueError('Use untouched original textures, not renderer-tagged volume inputs')
        if not width or not height or not depth:
            raise ValueError('Zero volume DDS dimension')
        if len(raw) > MAX_RAW or width * height * depth * 4 > MAX_RAW:
            raise ValueError('Oversized volume DDS')
        levels = max(1, mips)
        if levels > max(width, height, depth).bit_length():
            raise ValueError('Invalid volume DDS mip count')
        block = 8 if fourcc == int.from_bytes(b'DXT1', 'little') else 16
        expected = 128 + sum(((max(1, width >> level) + 3) // 4)
                             * ((max(1, height >> level) + 3) // 4)
                             * max(1, depth >> level) * block for level in range(levels))
        if len(raw) != expected:
            raise ValueError('Invalid volume DDS mip payload size')
        return 'DDS-volume'
    if len(raw) < 84 or raw[:4] != b'DDS ' or not struct.unpack_from('<I', raw, 80)[0] & 0x20:
        return None
    if len(raw) < 128:
        raise ValueError('Truncated palette-indexed DDS header')
    height, width = struct.unpack_from('<2I', raw, 12)
    if (not width or not height or struct.unpack_from('<I', raw, 4)[0] != 124
            or struct.unpack_from('<I', raw, 24)[0] or struct.unpack_from('<I', raw, 112)[0]):
        raise ValueError('Invalid palette-indexed DDS dimensions or resource type')
    if not allow_tag and raw[32:40] == MAGIC:
        raise ValueError('Use untouched original textures, not renderer-tagged P8 inputs')
    if struct.unpack_from('<8I', raw, 76) != (32, 0x20, 0, 8, 0, 0, 0, 0):
        raise ValueError('Unsupported palette-indexed DDS header')
    levels = max(1, struct.unpack_from('<I', raw, 28)[0])
    if len(raw) > MAX_RAW or width * height * 4 > MAX_RAW:
        raise ValueError('Oversized palette-indexed DDS')
    if levels > max(width, height).bit_length():
        raise ValueError('Invalid palette-indexed DDS mip count')
    expected = 128 + 1024 + sum(max(1, width >> level) * max(1, height >> level)
                                for level in range(levels))
    if len(raw) != expected:
        raise ValueError('Invalid palette-indexed DDS palette or mip payload size')
    return 'P8'


def rgba_dds(image, levels=1):
    """Represent decoded pixels in the renderer's existing BGRA8 format."""
    from PIL import Image

    width, height = image.size
    if (levels < 1 or levels > max(width, height).bit_length()
            or 128 + sum(max(1, width >> level) * max(1, height >> level) * 4
                         for level in range(levels)) > MAX_RAW):
        raise ValueError('Oversized RGBA DDS or invalid mip count')
    header = bytearray(128)
    header[:4] = b'DDS '
    struct.pack_into('<7I', header, 4, 124, 0x2100f if levels > 1 else 0x100f,
                     height, width, width * 4, 0, levels)
    struct.pack_into('<8I', header, 76, 32, 0x41, 0, 32, 0xff0000, 0xff00, 0xff, 0xff000000)
    struct.pack_into('<I', header, 108, 0x401008 if levels > 1 else 0x1000)
    rgba = image.convert('RGBA')
    return bytes(header) + b''.join(
        rgba.resize((max(1, width >> level), max(1, height >> level)), Image.Resampling.LANCZOS)
        .tobytes('raw', 'BGRA') for level in range(levels))


def source_dds(raw):
    """A 2D view for offline tools, never discard slices from a real 3D volume."""
    if raw[:4] == b'8BPS':
        is_preserved_psd(raw)
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as image:
            return rgba_dds(image)
    if raw[:4] == b'DDS ' and raw[84:88] == b'DX10':
        if len(raw) < 148:
            raise ValueError('Truncated DX10 DDS header')
        flags, height, width, _, depth, mips = struct.unpack_from('<6I', raw, 8)
        caps = struct.unpack_from('<I', raw, 108)[0]
        if (struct.unpack_from('<I', raw, 4)[0] != 124
                or struct.unpack_from('<8I', raw, 76) != (32, 4, int.from_bytes(b'DX10', 'little'), 0, 0, 0, 0, 0)
                or struct.unpack_from('<5I', raw, 128) != (98, 3, 0, 1, 1)
                or struct.unpack_from('<I', raw, 112)[0] or depth not in (0, 1)
                or flags & 0x1007 != 0x1007 or flags & 0x800000 or not caps & 0x1000
                or (mips > 1 and (caps & 0x400008 != 0x400008 or not flags & 0x20000))):
            raise ValueError('Unsupported DX10 DDS: require single 2D BC7_UNORM with straight alpha')
        if raw[32:40] == MAGIC:
            raise ValueError('Use untouched original textures, not renderer-tagged DX10 inputs')
        levels = max(1, mips)
        if (not width or not height or levels > max(width, height).bit_length()
                or len(raw) > MAX_RAW or 128 + sum(max(1, width >> level) * max(1, height >> level) * 4
                    for level in range(levels)) > MAX_RAW):
            raise ValueError('Invalid or oversized BC7 dimensions/mip count')
        expected = 148 + sum(((max(1, width >> level) + 3) // 4)
                             * ((max(1, height >> level) + 3) // 4) * 16 for level in range(levels))
        if len(raw) != expected:
            raise ValueError('Invalid BC7 mip payload size')
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            return rgba_dds(image, levels)
    if len(raw) >= 128 and raw[:4] == b'DDS ' and struct.unpack_from('<I', raw, 112)[0] & 0x200000:
        preserved_image_format(raw, allow_tag=True)
        if struct.unpack_from('<I', raw, 24)[0] != 1:
            raise ValueError('True 3D DDS enhancement requires a validated volume renderer')
        view = bytearray(raw)
        struct.pack_into('<I', view, 8, struct.unpack_from('<I', raw, 8)[0] & ~0x800000)
        struct.pack_into('<I', view, 24, 0)
        struct.pack_into('<I', view, 112, 0)
        return bytes(view)
    return raw


def encode_p8(image, original):
    """One shared RGBA palette for the complete mip chain, with transparent coverage."""
    from PIL import Image
    width, height = image.size
    levels = max(width, height).bit_length() if struct.unpack_from('<I', original, 28)[0] > 1 else 1
    images = [image.convert('RGBA').resize((max(1, width >> level), max(1, height >> level)),
                                         Image.Resampling.LANCZOS) for level in range(levels)]
    pixels = b''.join(mip.tobytes() for mip in images)
    if len(pixels) + 1152 > MAX_RAW:
        raise ValueError('Oversized palette mip chain')
    with Image.frombytes('RGBA', (len(pixels) // 4, 1), pixels) as strip:
        quantized = strip.quantize(colors=255, method=Image.Quantize.FASTOCTREE,
                                   dither=Image.Dither.NONE)
    colors = quantized.getpalette('RGBA')
    if colors is None:
        raise ValueError('Quantizer did not produce a palette')
    palette = bytearray(colors)
    palette.extend(bytes(1024 - len(palette)))
    # Index 255 is reserved for exactly transparent pixels; nonzero coverage stays nonzero.
    for index in range(255):
        palette[index * 4 + 3] = max(1, palette[index * 4 + 3])
    palette[1020:1024] = bytes(4)
    indices = bytearray(quantized.tobytes())
    for index, alpha in enumerate(pixels[3::4]):
        if alpha == 0:
            indices[index] = 255
    header = bytearray(original[:128])
    struct.pack_into('<I', header, 8, 0x2100f if levels > 1 else 0x100f)
    struct.pack_into('<3I', header, 12, height, width, width)
    struct.pack_into('<I', header, 28, levels)
    struct.pack_into('<I', header, 108, 0x401008 if levels > 1 else 0x1000)
    return bytes(header + palette + indices)


def target_size(width, height):
    scale = min(2, 2048 / max(width, height))
    if scale <= 1:
        return width, height
    return max(1, int(width * scale)), max(1, int(height * scale))


AI_PIPELINE = 'realesrgan-x4plus-20220424-2x-cap2048-v1'
RENDERER = 'v64-tagged-dimensions-v2'
SUPPORTED_RENDERERS = (RENDERER, 'v64-tagged-dimensions-v1')
MAGIC = b'ACVUI2X\0'


def pipeline_identity(texconv, esrgan=None, model=None):
    if model:
        from . import dat_upscale as backend

        files = [texconv, model, Path(__file__), Path(backend.__file__)]
        onnx = model.suffix.lower() == '.onnx'
        packages = (['onnxruntime-directml', 'numpy', 'Pillow'] if onnx else
                    ['torch', 'torchvision', 'spandrel', 'numpy', 'Pillow'])
        identity = {'files': [digest(p) for p in files],
                    'packages': {p: version(p) for p in packages},
                    'device': 'directml:0' if onnx else 'cpu',
                    'tile': backend.TILE, 'border': backend.BORDER, 'precision': 'fp32'}
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        return 'dat4x-all-2x-cap2048-v1-' + fingerprint[:16]
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
    raw = source_dds(raw)
    if struct.unpack_from('<I', raw, 80)[0] == 0x20:
        return 'P8'
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
    kind = preserved_image_format(native)
    if kind == 'DDS-volume' and enhanced[112:116] != native[112:116]:
        raise ValueError('Volume DDS resource type changed')
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
    tag_version = 2 if kind in ('P8', 'DDS-volume') else 1
    struct.pack_into('<8s5I', result, 32, MAGIC, tag_version, width, height, physical_width, physical_height)
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
            raise ValueError('AI output is not 4x the source dimensions')
        rgb = enhanced.convert('RGB').resize(size, Image.Resampling.LANCZOS)
        alpha = original.convert('RGBA').getchannel('A').resize(size, Image.Resampling.LANCZOS)
        rgb.putalpha(alpha)
        rgb.save(enlarged)


def resize_dds(raw, source, destination, texconv, esrgan=None, cache=None, model=None):
    if esrgan and model:
        raise ValueError('Choose one AI model backend')
    prepared = bool(esrgan or model)
    native = raw
    kind = preserved_image_format(native)
    raw = source_dds(raw)
    width, height, pixel_format = dds_info(raw)
    if raw[32:40] == MAGIC:
        raise ValueError('Source DDS already has renderer metadata; use the original archive')
    size = target_size(width, height)
    if size == (width, height):
        if native[84:88] == b'DX10':
            raise ValueError('At-cap BC7 requires a separate same-size conversion workflow')
        return False
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / source.name
    cached = None
    if prepared and cache:
        cache.mkdir(parents=True, exist_ok=True)
        cached = cache / (hashlib.sha256(native).hexdigest() + '.dds')
        checksum = cached.with_suffix('.sha256')
        if cached.exists() and checksum.exists() and digest(cached) == checksum.read_text():
            shutil.copyfile(cached, output)
            return True
    source.write_bytes(raw)
    enhanced = destination / 'enhanced.png'
    # Retain no-mip UI textures; regenerate a complete chain for mipmapped inputs.
    mips = 0 if struct.unpack_from('<I', raw, 28)[0] > 1 else 1
    command = [str(texconv), '-nologo', '-dx9', '-if', 'CUBIC', '-w', str(size[0]),
               '-h', str(size[1]), '-m', str(mips), '-o', str(destination), str(source)]
    if prepared:
        decoded = destination / 'input.png'
        if kind == 'P8':
            from PIL import Image
            with Image.open(io.BytesIO(raw)) as image:
                image.convert('RGBA').save(decoded)
        else:
            run_tool([texconv, '-nologo', '-y', '-ft', 'png', '-f', 'R8G8B8A8_UNORM',
                      '-m', '1', '-o', destination, source])
        # Feed RGB only; AI must not hallucinate the texture's transparency.
        if model:
            from .dat_upscale import upscale_image
            upscale_image(decoded, enhanced, model)
        else:
            from PIL import Image
            if esrgan is None:
                raise ValueError('AI enhancement requires a model backend')
            with Image.open(decoded) as image:
                image.convert('RGB').save(destination / 'rgb.png')
            run_tool([esrgan, '-i', destination / 'rgb.png', '-o', enhanced,
                      '-n', 'realesrgan-x4plus', '-s', '4', '-t', '128',
                      '-m', esrgan.parent / 'models', '-g', '0'])
        ai_image(decoded, enhanced, size)
        command = [texconv, '-nologo', '-dx9', '-y', '-if', 'CUBIC', '-f', dds_format(raw),
                   '-m', str(mips), '-o', destination, enhanced]
    if kind == 'P8':
        from PIL import Image
        with Image.open(enhanced if prepared else io.BytesIO(raw)) as image:
            rgba = image.convert('RGBA')
            if not prepared:
                rgba = rgba.resize(size, Image.Resampling.BICUBIC)
            output.write_bytes(encode_p8(rgba, raw))
    else:
        run_tool(command)
        if prepared:
            (destination / 'enhanced.dds').replace(output)
    if not output.exists():
        raise ValueError('texconv did not produce a DDS file')
    converted = output.read_bytes()
    compressed = bool(struct.unpack_from('<I', raw, 80)[0] & 4)
    if (compressed
            and raw[84:88] in (b'DXT1', b'DXT3', b'DXT5')
            and converted[80:88] == raw[80:88]):
        # DXT RGB bit counts/masks are unused; retain native values that texconv clears.
        converted = converted[:88] + raw[88:108] + converted[108:]
        output.write_bytes(converted)
    elif prepared and not compressed and struct.unpack_from('<I', raw, 88)[0] == 24:
        converted = restore_rgb24(converted, raw)
        output.write_bytes(converted)
    elif prepared and not compressed and struct.unpack_from('<5I', raw, 88) == (16, 0x7c00, 0x3e0, 0x1f, 0):
        # DXGI exposes RGB555 only with alpha; restore the original unused-X header.
        converted = converted[:76] + raw[76:108] + converted[108:]
        output.write_bytes(converted)
    if kind == 'DDS-volume':
        header = bytearray(converted[:128])
        struct.pack_into('<I', header, 8, struct.unpack_from('<I', header, 8)[0] | 0x800000)
        struct.pack_into('<I', header, 24, 1)
        struct.pack_into('<I', header, 108, struct.unpack_from('<I', header, 108)[0] | 8)
        struct.pack_into('<I', header, 112, 0x200000)
        converted = bytes(header) + converted[128:]
        output.write_bytes(converted)
    if len(converted) > MAX_RAW:
        raise ValueError('Resized DDS exceeds the archive payload limit')
    if kind == 'P8':
        preserved_image_format(converted)
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


def receipt_path(output):
    """Read either layout; never choose silently between two receipts."""
    current = output.parent / '.metadata' / (output.name + '.json')
    legacy = output.with_name(output.name + '.json')
    if current.parent.is_symlink() or current.is_symlink() or legacy.is_symlink():
        raise ValueError('Linked archive metadata is not supported')
    if current.exists() and legacy.exists():
        raise ValueError(f'Multiple archive receipts: {output}')
    return legacy if legacy.exists() else current


def finalize_metadata(output):
    """Call only after the output and its published receipt have been verified."""
    receipt = receipt_path(output)
    if not receipt.is_file():
        raise ValueError(f'Cannot finish without a published receipt: {output}')
    target = output.parent / '.metadata' / receipt.name
    target.parent.mkdir(exist_ok=True)
    if receipt != target:
        receipt.rename(target)
    output.with_name(output.name + '.progress.json').unlink(missing_ok=True)


def process(source, staging, texconv, esrgan=None, pipeline=None, model=None):
    start = time.monotonic()
    output = staging / source.name
    receipt = receipt_path(output)
    source_hash = digest(source)
    pipeline = pipeline or pipeline_identity(texconv, esrgan, model)
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
                finalize_metadata(output)
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
    receipt = receipt_path(output)
    result = {'archive': source.name, 'textures': 0, 'resized': 0, 'at_cap': 0,
              'source_sha256': source_hash, 'pipeline': pipeline, 'renderer': RENDERER}
    if model:
        result['model'] = model.name
        result['device'] = 'directml:0' if model.suffix.lower() == '.onnx' else 'cpu'
    with tempfile.TemporaryDirectory(prefix=source.stem + '-', dir=staging) as temporary:
        work = Path(temporary)
        replacements = work / 'replacements'
        replacements.mkdir()
        with source.open('rb') as stream:
            entries = read_index(stream)
            paths = [safe_relative_path(e[0]) for e in entries]
            keys = [p.as_posix().casefold() for p in paths if p.suffix.lower() == '.dds']
            # Non-DDS duplicates stay opaque; only texture replacements must be unambiguous.
            if len(keys) != len(set(keys)):
                raise ValueError('Duplicate DDS archive paths')
            for entry, path in zip(entries, paths, strict=True):
                if path.suffix.lower() != '.dds':
                    continue
                raw = decode_entry(stream, entry)
                incoming = work / 'input.dds'
                converted_dir = work / 'converted'
                try:
                    preserved_format = preserved_image_format(raw)
                    if preserved_format == 'DDS-volume' and struct.unpack_from('<I', raw, 24)[0] > 1:
                        result.setdefault('preserved_images', []).append({
                            'path': entry[0], 'format': preserved_format,
                            'source_sha256': hashlib.sha256(raw).hexdigest()})
                        continue
                    result['textures'] += 1
                    resized = resize_dds(raw, incoming, converted_dir, texconv, esrgan,
                                         staging / '.texture-cache' / pipeline, model)
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
        receipt.parent.mkdir(exist_ok=True)
        pending_receipt.replace(receipt)
        finalize_metadata(output)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('staging', type=Path)
    parser.add_argument('--texconv', type=Path, default=Path('tools/texconv.exe'))
    parser.add_argument('--workers', type=int, default=1)
    backend = parser.add_mutually_exclusive_group()
    backend.add_argument('--esrgan', type=Path, help='NCNN Vulkan executable; enables Real-ESRGAN')
    backend.add_argument('--model', type=Path,
                         help='4x RGB DAT: .safetensors/.pth on CPU, .onnx on DirectML GPU 0; all textures')
    args = parser.parse_args()
    source, staging, texconv = args.source.resolve(), args.staging.resolve(), args.texconv.resolve(strict=True)
    esrgan = args.esrgan.resolve(strict=True) if args.esrgan else None
    model = args.model.resolve(strict=True) if args.model else None
    if (esrgan or model) and args.workers != 1:
        parser.error('Use one archive worker for the AI pipeline and shared texture cache')
    source_dir = source if source.is_dir() else source.parent
    if staging == source_dir or staging.is_relative_to(source_dir):
        parser.error('Staging must be outside the original archive directory')
    archives = sorted(source.glob('*.acv')) if source.is_dir() else [source]
    if not archives or args.workers < 1:
        parser.error('Need archives and a positive worker count')
    staging.mkdir(parents=True, exist_ok=True)
    pipeline = pipeline_identity(texconv, esrgan, model)
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process, archive, staging, texconv, esrgan, pipeline,
                               model): archive for archive in archives}
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
