"""Read-only ACV census and deterministic payload sampling; uses the ACV safety limit."""

import argparse
import hashlib
import io
import json
import random
import struct
import zlib
from pathlib import Path

from acv_tools.acv_unpack import MAX_RAW


def inspect_archive(stream, size, seed):
    stream.seek(0)
    header = stream.read(32)
    if len(header) < 4:
        return {"status": "invalid_header"}
    tagged = header[3] == 255
    variant = header[2] if tagged else 0
    result = {"variant": variant, "header_hex": header.hex()}
    if tagged and len(header) < 8:
        return {**result, "status": "invalid_header"}
    count = struct.unpack_from("<I", header, 4 if tagged else 0)[0]
    result["entry_count"] = count
    if tagged:
        result["archive_tag_number"] = struct.unpack_from("<H", header)[0]
    # shortcut: only plain and variant-1 indexes are understood; extend after decoding other variants.
    if variant not in (0, 1) or (tagged and variant == 0):
        return {**result, "status": "unsupported_index_format"}
    start, stride = (8, 180) if tagged else (4, 176)
    base = start + count * stride
    result["payload_base"] = base
    if base > size:
        return {**result, "status": "invalid_index_bounds"}
    stream.seek(start)
    entries = []
    issues = []
    expected_offset = 0
    for i in range(count):
        record = stream.read(stride)
        if len(record) != stride:
            return {**result, "status": "truncated_index"}
        name_bytes = record[:160].split(b"\0", 1)[0]
        name = name_bytes.decode("cp949", errors="backslashreplace")
        metadata, packed_size, raw_size, offset = struct.unpack_from("<4I", record, 160)
        if not name_bytes or b"\0" not in record[:160]:
            issues.append({"entry": i, "issue": "empty_or_unterminated_name"})
        if offset != expected_offset:
            issues.append({"entry": i, "issue": "noncontiguous_payload"})
        if base + offset + packed_size > size:
            issues.append({"entry": i, "issue": "payload_out_of_bounds"})
        if tagged and record[176:180] != header[:4]:
            issues.append({"entry": i, "issue": "unexpected_record_tag"})
        expected_offset = offset + packed_size
        entries.append((name, metadata, packed_size, raw_size, offset))
    if base + expected_offset != size:
        issues.append({"issue": "final_payload_end_differs_from_eof"})
    result["index_issues"] = issues
    result["dds_entries"] = sum(e[0].lower().endswith(".dds") for e in entries)
    if issues:
        return {**result, "status": "index_validation_failed"}
    selected = set()
    if count:
        selected.update([0, count // 4, count // 2, 3 * count // 4, count - 1])
        rng = random.Random(seed)
        selected.update(rng.sample(range(count), min(4, count)))
        dds = [i for i, e in enumerate(entries) if e[0].lower().endswith(".dds")]
        selected.update(rng.sample(dds, min(4, len(dds))))
    samples = []
    for i in sorted(selected):
        name, metadata, packed_size, raw_size, offset = entries[i]
        sample = {"entry": i, "name": name, "metadata": metadata,
                  "packed_size": packed_size, "raw_size": raw_size}
        samples.append(sample)
        if max(raw_size, packed_size) > MAX_RAW:
            sample["status"] = "skipped_size_limit"
            continue
        stream.seek(base + offset)
        packed = stream.read(packed_size)
        sample["packed_prefix_hex"] = packed[:16].hex()
        try:
            decoder = zlib.decompressobj()
            raw = decoder.decompress(packed, raw_size + 1)
            if len(raw) != raw_size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                sample["status"] = "zlib_size_or_stream_mismatch"
                continue
        except zlib.error as error:
            sample.update(status="not_direct_zlib", error=str(error))
            continue
        sample.update(status="decoded", sha256=hashlib.sha256(raw).hexdigest(),
                      raw_prefix_hex=raw[:16].hex())
        if name.lower().endswith(".dds"):
            if len(raw) < 128 or raw[:4] != b"DDS " or struct.unpack_from("<I", raw, 4)[0] != 124:
                sample["status"] = "decoded_but_invalid_dds_header"
            else:
                height, width = struct.unpack_from("<II", raw, 12)
                sample["dds"] = {"width": width, "height": height,
                                 "mip_count": struct.unpack_from("<I", raw, 28)[0],
                                 "fourcc_hex": raw[84:88].hex()}
    result["samples"] = samples
    result["status"] = ("sampled_successfully" if samples and all(s["status"] == "decoded" for s in samples)
                        else "no_entries" if not samples else "samples_not_all_decoded")
    return result


def self_test():
    raw = b"DDS " + struct.pack("<I", 124) + bytes(120)
    packed = zlib.compress(raw)
    record = b"data\\test.dds".ljust(160, b"\0") + struct.pack("<4I", 0, len(packed), len(raw), 0)
    archive = struct.pack("<I", 1) + record + packed
    result = inspect_archive(io.BytesIO(archive), len(archive), 1)
    assert result["status"] == "sampled_successfully", result
    assert result["samples"][0]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert inspect_archive(io.BytesIO(archive), len(archive) - 1, 1)["status"] == "index_validation_failed"
    tag = bytes.fromhex("240001ff")
    encrypted = tag + struct.pack("<I", 1) + record + tag + bytes(len(packed))
    assert inspect_archive(io.BytesIO(encrypted), len(encrypted), 1)["samples"][0]["status"] == "not_direct_zlib"
    assert inspect_archive(io.BytesIO(bytes.fromhex("020005ff01000000")), 8, 1)["status"] == "unsupported_index_format"
    print("Self-check passed: plain DDS, invalid bounds, transformed payload, unknown variant.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("../DATA"))
    parser.add_argument("--report", type=Path, default=Path("diagnostics/reports/acv-sampling.json"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    files = sorted(args.data.glob("*.acv"))
    if not files:
        parser.error("No ACV files found")
    if args.report.resolve() in [p.resolve() for p in files]:
        parser.error("Report must not overwrite an input archive")
    results = []
    for path in files:
        stat = path.stat()
        with path.open("rb") as stream:
            result = inspect_archive(stream, stat.st_size, path.name)
        result.update(archive=str(path.resolve()), bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
        results.append(result)
    summary = {}
    for result in results:
        key = f"variant_{result['variant']}:{result['status']}"
        summary[key] = summary.get(key, 0) + 1
    report = {"method": "All headers; all understood index records; first, quartiles, last, four seeded random entries and four seeded random DDS entries per readable index (deduplicated). Direct zlib only. DDS header checks are not full image decoding.",
              "seed": "archive filename", "max_sample_bytes": MAX_RAW,
              "archive_count": len(results), "summary": summary, "archives": results}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=True)
        stream.write("\n")
    print(json.dumps(summary, indent=2))
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
