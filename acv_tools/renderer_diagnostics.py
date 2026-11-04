"""Map a renderer upload's byte count and CRC32 sample to candidate ACV texture paths."""
import argparse
import hashlib
import json
import zlib
from pathlib import Path

from .acv_unpack import MAX_RAW, decode_entry, read_index


def find_upload(source, size, crc32):
    if not 128 <= size <= MAX_RAW or not 0 <= crc32 <= 0xffffffff:
        raise ValueError('Expected a DDS byte count from 128 to 64 MiB and a 32-bit CRC')
    archives = sorted(source.glob('*.acv')) if source.is_dir() else [source]
    if not archives:
        raise ValueError('No ACV files found')
    matches = []
    for archive in archives:
        with archive.open('rb') as stream:
            for index, entry in enumerate(read_index(stream)):
                if entry[3] != size:
                    continue
                raw = decode_entry(stream, entry)
                if raw.startswith(b'DDS ') and zlib.crc32(raw[:4096]) == crc32:
                    matches.append({'archive': str(archive), 'entry_index': index, 'path': entry[0],
                                    'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    return matches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='An installed/staged ACV or directory; read-only')
    parser.add_argument('--bytes', dest='size', required=True, type=int, help='bytes= from the upload log')
    parser.add_argument('--crc32', required=True, type=lambda value: int(value, 16), help='crc32= from the log')
    args = parser.parse_args()
    try:
        matches = find_upload(args.source, args.size, args.crc32)
    except (OSError, ValueError, zlib.error) as error:
        parser.error(str(error))
    print(json.dumps({'matches': matches, 'note': 'CRC32 covers at most 4096 bytes; matches are candidates, not proof of causation.'},
                     indent=2, ensure_ascii=True))


if __name__ == '__main__':
    main()
