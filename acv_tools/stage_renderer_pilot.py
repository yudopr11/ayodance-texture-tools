"""Mark an already-upscaled ACV for the experimental v64 renderer; no AI rerun/install."""
import argparse
import json
import tempfile
from pathlib import Path

from .acv_pack import pack_archive
from .acv_unpack import decode_entry, read_index, safe_relative_path
from .acv_upscale import RENDERER, digest, tag_dds, verify_archive


def stage(original, enhanced, output):
    if output.resolve().parent in (original.resolve().parent, enhanced.resolve().parent):
        raise ValueError('Pilot output must use a separate directory')
    if output.exists() or output.with_suffix('.acv.json').exists():
        raise FileExistsError('Refusing to overwrite existing pilot output or receipt')
    try:
        receipt = json.loads(enhanced.with_suffix('.acv.json').read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f'Cannot read verified AI receipt: {error}') from error
    if not isinstance(receipt, dict):
        raise ValueError('AI receipt must be a JSON object')
    original_hash, enhanced_hash = digest(original), digest(enhanced)
    if (receipt.get('status') != 'verified' or receipt.get('source_sha256') != original_hash
            or receipt.get('output_sha256') != enhanced_hash):
        raise ValueError('Original/upscaled archive hashes do not match the verified AI receipt')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='renderer-pilot-', dir=output.parent) as temporary:
        replacements = Path(temporary)
        with original.open('rb') as before, enhanced.open('rb') as after:
            old, new = read_index(before), read_index(after)
            if len(old) != len(new):
                raise ValueError('Archive entry counts differ')
            tagged = 0
            for source, upgraded in zip(old, new, strict=True):
                if (source[0], source[1], source[5], source[6]) != (upgraded[0], upgraded[1], upgraded[5], upgraded[6]):
                    raise ValueError('Archive metadata differs')
                if not source[0].lower().endswith('.dds'):
                    continue
                marked = tag_dds(decode_entry(before, source), decode_entry(after, upgraded))
                if marked is not None:
                    path = replacements / safe_relative_path(source[0])
                    if path.exists():
                        raise ValueError('Duplicate texture path')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(marked)
                    tagged += 1
        if not tagged:
            raise ValueError('No enlarged DDS textures to mark')
        result = pack_archive(enhanced, replacements, output)
        verify_archive(enhanced, output, replacements)
    result.update(status='experimental-verified-repack-not-game-tested', tagged_textures=tagged,
                  original_sha256=original_hash, enhanced_sha256=enhanced_hash,
                  output_sha256=digest(output), renderer=RENDERER)
    output.with_suffix('.acv.json').write_text(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original', type=Path)
    parser.add_argument('enhanced', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(stage(args.original, args.enhanced, args.output), indent=2))
    except (OSError, ValueError) as error:
        parser.exit(1, f'Pilot staging failed: {error}\nAny output without a verified receipt is partial; do not install it.\n')


if __name__ == '__main__':
    main()
