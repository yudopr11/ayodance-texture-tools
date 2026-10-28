"""Read-only experiment: locate candidate XOR-masked scalar fields in ACV tags 5/6.

This does not decode indexes or textures. Run with --self-test for synthetic checks;
otherwise print a JSON report for ../DATA (or a supplied directory).
"""

import io
import json
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

from acv_tools.acv_unpack import MAX_RAW


def scan_candidates(stream):
    stream.seek(0)
    header = stream.read(16)
    if len(header) < 16 or header[3] != 255 or header[2] not in (5, 6):
        raise ValueError("Expected a complete variant-5/6 header")
    count, mask, encoded_count = struct.unpack_from("<3I", header, 4)
    if ((mask ^ encoded_count) & 0x7fffffff) != count:
        raise ValueError("Header does not satisfy the observed count-mask relation")
    limit = 8 * count + 4
    if limit > MAX_RAW:
        raise ValueError("Candidate search exceeds the safety limit")
    data = stream.read(limit)
    flag = (mask ^ encoded_count) >> 31
    candidates = []
    # Hypothesis only: a second scalar lies in count-dependent padding.
    for relative in range(max(0, len(data) - 3)):
        value = struct.unpack_from("<I", data, relative)[0] ^ mask
        length = value & 0x7fffffff
        if value >> 31 == flag and 8 * count <= length <= 32 * count + 256:
            candidates.append({"offset": 16 + relative, "value_low31": length})
    return {"variant": header[2], "count": count, "mask": mask,
            "flag": flag, "candidates": candidates,
            "block_alignment": 16 if header[2] == 6 else 8,
            "block_aligned_candidates": [c for c in candidates
                                         if (c["value_low31"] - count) %
                                         (16 if header[2] == 6 else 8) == 0]}


def self_test():
    for variant, flag in ((5, 0), (5, 1), (6, 0), (6, 1)):
        count, mask = 25, 0x1cf6d128
        data = bytearray(220)
        data[:4] = bytes([254, 0, variant, 255])
        struct.pack_into("<3I", data, 4, count, mask, mask ^ count ^ (flag << 31))
        struct.pack_into("<I", data, 56, mask ^ 521 ^ (flag << 31))
        result = scan_candidates(io.BytesIO(data))
        assert result["candidates"] == [{"offset": 56, "value_low31": 521}], result
        assert result["block_aligned_candidates"] == result["candidates"]
        struct.pack_into("<I", data, 56, mask ^ 522 ^ (flag << 31))
        unaligned = scan_candidates(io.BytesIO(data))
        assert unaligned["candidates"] and not unaligned["block_aligned_candidates"]
        struct.pack_into("<I", data, 56, mask ^ 529 ^ (flag << 31))
        eight_aligned = scan_candidates(io.BytesIO(data))
        assert bool(eight_aligned["block_aligned_candidates"]) == (variant == 5)
        struct.pack_into("<I", data, 56, mask ^ 521 ^ ((1 - flag) << 31))
        assert not scan_candidates(io.BytesIO(data))["candidates"]
    for invalid in (b"", bytes(16), bytes.fromhex("fe0005ff") + bytes(8)):
        try:
            scan_candidates(io.BytesIO(invalid))
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid header accepted")
    print("Self-check passed: masked fields, both flags, wrong flag, truncated headers.")


def main():
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        return
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../DATA")
    results = []
    groups = defaultdict(list)
    for path in sorted(root.glob("*.acv")):
        with path.open("rb") as stream:
            header = stream.read(4)
            if len(header) != 4 or header[3] != 255 or header[2] not in (5, 6):
                continue
            result = {"archive": str(path.resolve()), **scan_candidates(stream)}
        results.append(result)
        groups[result["count"]].append(result)
    if not results:
        raise ValueError("No variant-5/6 archives found")
    repeated = []
    for count, archives in sorted(groups.items()):
        if len(archives) > 1:
            offsets = [{c["offset"] for c in a["candidates"]} for a in archives]
            repeated.append({"count": count, "archives": [a["archive"] for a in archives],
                             "common_offsets": sorted(set.intersection(*offsets))})
    print(json.dumps({"warning": "Candidate scalar fields only; not decoded lengths or proven index boundaries.",
                      "candidate_count_histogram": dict(Counter(len(a["candidates"]) for a in results)),
                      "block_aligned_candidate_histogram": dict(Counter(
                          len(a["block_aligned_candidates"]) for a in results)),
                      "archives": results, "repeated_counts": repeated}, indent=2))


if __name__ == "__main__":
    main()
