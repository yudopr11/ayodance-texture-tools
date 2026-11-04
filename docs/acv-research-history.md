# ACV research background

These notes summarize the investigation that preceded the maintained decoder.
They are background, not setup instructions or a statement of current limitations.
For supported formats and reproducible commands, use the
[archive guide](acv-unpacking.md), [upscaling guide](upscaling.md) and
[research prerequisites](diagnostics.md).

## From plain archives to tagged formats

The initial extractor understood only the plain format: a little-endian entry
count, 176-byte records, CP949 filenames and zlib payloads. Tagged archives needed
separate index and payload transformations; a structurally valid index did not
mean its encrypted payloads had been decoded.

Early comparisons established useful constraints without identifying the complete
format:

- Variant-1 payload sizes and repeated-block relationships supported an
  eight-byte block transform. Matching filenames and raw sizes were not proof
  of matching contents or a recovered key.
- Variants 2/3 used a bounded encrypted index envelope. Similar ciphertext
  repetition patterns were evidence of related structure, not plaintext recovery.
- Variants 5/6 masked the count and embedded index length. Alignment and
  repeated-count comparisons narrowed candidate boundaries, but did not establish
  a universal offset formula or payload signature.
- Simple XOR/addition, guessed keys and bounded PRNG position models did not
  recover the decoder. Constants found in unrelated music/ZIP components did
  not establish an ACV connection.

The maintained implementation instead follows recovered native archive routines.
It supports plain archives and tagged formats 1, 2, 3, 5 and 6, with separate
index/payload handling, custom Blowfish tables and Rijndael-256 block semantics.
See [format details](acv-unpacking.md#recovered-format-and-implementation),
`acv_tools/acv_crypto.py`, `acv_tools/acv_unpack.py` and `acv_tools/acv_pack.py`.
Legacy format ciphers/constants must not be replaced as a security modernization.

## Verification lessons

- Detect formats from validated headers and structures, not archive numbers,
  filenames or another tool's generation labels.
- Successful decompression needs exact declared sizes, checksum/EOF validation
  and bounded padding. Ciphertext copied to disk is not an extracted texture.
- Preserve record order, metadata, tags, IVs and untouched stored payloads during
  repacking. Verify replacement bytes after decoding and retain original inputs.
- Container checks, image decoding and live renderer behavior are separate tests.
  None establishes publisher permission.
- A static call path, string or cipher constant is evidence with a limited scope,
  not proof of the failing runtime path or a complete algorithm.
- Native emulation must distinguish executed instructions from modeled heap,
  crypto and Windows APIs. An opaque/unreplayed record is not a passing case.
- Treat unknown third-party binaries as untrusted. Claims about standalone
  extraction or repacking require independent verification, not disabled
  protection, administrator access or a live-game experiment.

## Renderer research

Enlarged DDS pixels need native-layout metadata and compatible runtime handling
because the client uses dimensions for both upload and UI/hit-test geometry.
The [renderer guide](../renderer_patch/README.md) describes the scoped matching
two-DLL bundle, its inactive comprehensive counterpart and validation limits.
Offline verification does not establish full live-game compatibility or identify
the cause of every earlier crash.

## External references

- [Historical plain-format discussion](https://zenhax.com/viewtopic.php@t=1182.html)
- [Historical ACV tool discussion](https://forum.ragezone.com/threads/acv-tool-release-acv-unpacker.456025/page-2)
- [Microsoft DirectXTex texconv documentation](https://github.com/microsoft/DirectXTex/wiki/Texconv)

Historical forum claims describe different tool/client generations and are not
format specifications or endorsements. The maintained source and regression
tests, not obsolete commands or commercial-tool claims, define this project's
supported workflow.
