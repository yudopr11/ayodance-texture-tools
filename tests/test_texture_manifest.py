"""Manifest/update regression tests: synthetic archives, no real converter or AI."""

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from acv_tools.acv_unpack import decode_entry, read_index
from acv_tools.acv_upscale import digest, process, receipt_path
from acv_tools.texture_manifest import build, capture, save, sha256, stage_update
from tests.test_acv_upscale import tiny_dds


def archive(path, entries):
    records, payloads = [], []
    offset = 0
    for name, raw in entries:
        payload = zlib.compress(raw)
        records.append(name.encode('cp949').ljust(160, b'\0') +
                       struct.pack('<4I', 7, len(payload), len(raw), offset))
        payloads.append(payload)
        offset += len(payload)
    path.write_bytes(struct.pack('<I', len(entries)) + b''.join(records + payloads))


def enlarged():
    raw = bytearray(tiny_dds()[:128])
    struct.pack_into('<2I', raw, 12, 8, 8)
    struct.pack_into('<I', raw, 20, 32)
    return bytes(raw) + bytes((90, 100, 110, 128)) * 64


class ManifestTests(unittest.TestCase):
    def baseline(self, root, pipeline='test-pipeline'):
        originals, staged = root / 'original', root / 'staged'
        originals.mkdir()
        staged.mkdir()
        source = originals / '001.acv'
        archive(source, [('model/상의.dds', tiny_dds()), ('decoy', b'one'), ('decoy', b'two')])

        def resize(raw, source, destination, *args):
            destination.mkdir(exist_ok=True)
            (destination / source.name).write_bytes(enlarged())
            return True

        with patch('acv_tools.acv_upscale.resize_dds', side_effect=resize):
            process(source, staged, root / 'texconv', pipeline=pipeline, model=root / 'model.onnx')
        manifest = root / 'manifest'
        build(originals, staged, manifest)
        return source, staged, manifest

    def test_backfill_names_hashes_metadata_and_no_ai(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch('acv_tools.acv_upscale.run_tool', side_effect=AssertionError('No converter')):
                source, staged, manifest = self.baseline(root)
            record = json.loads((manifest / '001.acv.json').read_text())
            self.assertEqual(record['pipeline'], 'test-pipeline')
            texture, = record['textures']
            self.assertEqual(texture['texture'], 'model/상의.dds')
            self.assertEqual(texture['archive'], '001.acv')
            self.assertEqual(texture['entry_index'], 0)
            self.assertEqual(texture['source_sha256'], sha256(tiny_dds()))
            self.assertEqual(texture['cache_sha256'], sha256(enlarged()))
            self.assertEqual(texture['original']['width'], 4)
            self.assertEqual(texture['output']['width'], 8)
            self.assertEqual(texture['original']['mips'], 1)
            self.assertEqual(record['output_sha256'], digest(staged / '001.acv'))
            with self.assertRaises(FileExistsError):
                build(source, staged, manifest)
            with self.assertRaisesRegex(ValueError, 'outside the original'):
                build(source, staged, source.parent / 'unsafe')
            (staged / '.batch.lock').write_text('123')
            with self.assertRaisesRegex(ValueError, 'batch lock'):
                build(source, staged, root / 'another')

    def test_legacy_moved_and_mixed_receipt_layouts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, staged, manifest = self.baseline(root)
            current = receipt_path(staged / source.name)
            legacy = staged / (source.name + '.json')
            current.rename(legacy)
            self.assertEqual(build(source.parent, staged, root / 'legacy-manifest')['textures'], 1)
            with patch('acv_tools.texture_manifest.pipeline_identity', return_value='test-pipeline'), \
                    patch('acv_tools.acv_upscale.run_tool', side_effect=AssertionError('No converter')):
                self.assertEqual(stage_update(source, root / 'from-legacy', manifest,
                                              root / 'texconv', root / 'model.onnx')['reused'], 1)
                legacy.rename(current)
                self.assertEqual(stage_update(source, root / 'after-move', manifest,
                                              root / 'texconv', root / 'model.onnx')['reused'], 1)
            second = source.parent / '002.acv'
            second.write_bytes(source.read_bytes())
            (staged / second.name).write_bytes((staged / source.name).read_bytes())
            data = json.loads(current.read_text())
            data['archive'] = second.name
            save(staged / (second.name + '.json'), data)
            index = build(source.parent, staged, root / 'mixed-manifest')
            self.assertEqual([r['archive'] for r in index['archives']], ['001.acv', '002.acv'])
            self.assertEqual(index['textures'], 2)

    def test_moved_texture_and_archive_reuse_real_cache_without_ai(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, staged, manifest = self.baseline(root)
            updated = source.parent / '009.acv'
            archive(updated, [('texture/gui/moved.dds', tiny_dds()), ('new/info', b'new info')])
            original_hash, previous_hash = digest(source), digest(staged / source.name)
            with patch('acv_tools.texture_manifest.pipeline_identity', return_value='test-pipeline'), \
                    patch('acv_tools.acv_upscale.run_tool', side_effect=AssertionError('No converter')), \
                    patch('acv_tools.dat_upscale.upscale_image', side_effect=AssertionError('No AI')):
                report = stage_update(updated, root / 'update', manifest, root / 'texconv', root / 'model.onnx')
            self.assertEqual((report['reused'], report['new_or_changed']), (1, 0))
            self.assertEqual(report['result']['status'], 'verified')
            with (root / 'update/009.acv').open('rb') as stream:
                entries = read_index(stream)
                self.assertEqual(entries[0][0], 'texture/gui/moved.dds')
                self.assertEqual(decode_entry(stream, entries[1]), b'new info')
            self.assertEqual(digest(source), original_hash)
            self.assertEqual(digest(staged / source.name), previous_hash)
            self.assertTrue((root / 'update/texture-manifest/009.acv.json').is_file())
            with self.assertRaises(FileExistsError), \
                    patch('acv_tools.texture_manifest.pipeline_identity', return_value='test-pipeline'):
                stage_update(updated, root / 'update', manifest, root / 'texconv', root / 'model.onnx')

    def test_only_new_or_changed_content_runs_enhancement(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _, manifest = self.baseline(root)
            changed = tiny_dds()[:-1] + b'\x01'
            updated = source.parent / '002.acv'
            archive(updated, [('same.dds', tiny_dds()), ('changed.dds', changed)])

            def tool(command):
                destination = Path(command[command.index('-o') + 1])
                if '-ft' in command:
                    Image.new('RGBA', (4, 4), (128, 64, 32, 128)).save(destination / 'input.png')
                else:
                    (destination / 'enhanced.dds').write_bytes(enlarged())

            def enhance(source, destination, model):
                Image.new('RGB', (16, 16)).save(destination)

            with patch('acv_tools.texture_manifest.pipeline_identity', return_value='test-pipeline'), \
                    patch('acv_tools.acv_upscale.run_tool', side_effect=tool) as converter, \
                    patch('acv_tools.dat_upscale.upscale_image', side_effect=enhance) as model:
                result = stage_update(updated, root / 'update', manifest, root / 'texconv', root / 'model.onnx')
            self.assertEqual((result['reused'], result['new_or_changed']), (1, 1))
            model.assert_called_once()
            self.assertEqual(converter.call_count, 2)

    def test_changed_pipeline_requires_explicit_compatibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _, manifest = self.baseline(root, pipeline='old-pipeline')
            with patch('acv_tools.texture_manifest.pipeline_identity', return_value='new-pipeline'), \
                    patch('acv_tools.acv_upscale.run_tool', side_effect=RuntimeError('Must enhance')):
                with self.assertRaisesRegex(ValueError, 'Must enhance'):
                    stage_update(source, root / 'refused', manifest, root / 'texconv', root / 'model.onnx')
                result = stage_update(source, root / 'approved', manifest, root / 'texconv', root / 'model.onnx',
                                      ['old-pipeline'])
                self.assertEqual(result['reused'], 1)
                with self.assertRaisesRegex(ValueError, 'different model'):
                    stage_update(source, root / 'wrong-model', manifest, root / 'texconv', root / 'other.onnx',
                                 ['old-pipeline'])

    def test_forged_content_binding_and_pipeline_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _, manifest = self.baseline(root)
            path = manifest / '001.acv.json'
            original_record = json.loads(path.read_text())
            index = json.loads((manifest / 'index.json').read_text())
            updated = source.parent / '002.acv'
            changed = tiny_dds()[:-1] + b'\x01'
            archive(updated, [('other.dds', changed)])
            forged = json.loads(json.dumps(original_record))
            forged['textures'][0]['source_sha256'] = sha256(changed)
            save(path, forged)
            index['archives'][0]['sha256'] = digest(path)
            save(manifest / 'index.json', index)
            with patch('acv_tools.texture_manifest.pipeline_identity', return_value='test-pipeline'), \
                    self.assertRaisesRegex(ValueError, 'hash binding mismatch'):
                stage_update(updated, root / 'bad-content', manifest, root / 'texconv', root / 'model.onnx')
            forged = original_record
            forged['pipeline'] = 'different-pipeline'
            save(path, forged)
            index['archives'][0]['sha256'] = digest(path)
            save(manifest / 'index.json', index)
            with patch('acv_tools.texture_manifest.pipeline_identity', return_value='different-pipeline'), \
                    self.assertRaisesRegex(ValueError, 'previous verified receipt'):
                stage_update(source, root / 'bad-pipeline', manifest, root / 'texconv', root / 'model.onnx')

    def test_conflicting_same_content_is_not_chosen_by_manifest_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _, manifest = self.baseline(root)
            path = manifest / '001.acv.json'
            record = json.loads(path.read_text())
            duplicate = dict(record['textures'][0])
            duplicate['cache_sha256'] = '0' * 64
            record['textures'].append(duplicate)
            save(path, record)
            index = json.loads((manifest / 'index.json').read_text())
            index['archives'][0]['sha256'] = digest(path)
            save(manifest / 'index.json', index)
            with patch('acv_tools.texture_manifest.pipeline_identity', return_value='test-pipeline'), \
                    self.assertRaisesRegex(ValueError, 'Conflicting enhancements'):
                stage_update(source, root / 'ambiguous', manifest, root / 'texconv', root / 'model.onnx')

    def test_current_pipeline_wins_over_approved_older_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _, manifest = self.baseline(root)
            record = json.loads((manifest / '001.acv.json').read_text())
            record['pipeline'] = 'older-pipeline'
            record['textures'][0]['cache_sha256'] = '0' * 64
            older = manifest / 'older.acv.json'
            save(older, record)
            index = json.loads((manifest / 'index.json').read_text())
            index['archives'].insert(0, {'file': older.name, 'sha256': digest(older)})
            save(manifest / 'index.json', index)
            with patch('acv_tools.texture_manifest.pipeline_identity', return_value='test-pipeline'), \
                    patch('acv_tools.acv_upscale.run_tool', side_effect=AssertionError('No converter')):
                result = stage_update(source, root / 'update', manifest, root / 'texconv', root / 'model.onnx',
                                      ['older-pipeline'])
            self.assertEqual(result['reused'], 1)
            self.assertEqual(set(result['reused_from'].values()), {'test-pipeline'})

    def test_at_cap_is_logged_and_never_enhanced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            originals, staged = root / 'original', root / 'staged'
            originals.mkdir()
            staged.mkdir()
            raw = bytearray(tiny_dds()[:128])
            struct.pack_into('<2I', raw, 12, 1, 2048)
            struct.pack_into('<I', raw, 20, 8192)
            source = originals / '001.acv'
            archive(source, [('cap.dds', bytes(raw) + bytes(8192))])
            with patch('acv_tools.acv_upscale.run_tool', side_effect=AssertionError('No converter')):
                process(source, staged, root / 'texconv', pipeline='test-pipeline', model=root / 'model.onnx')
                build(originals, staged, root / 'manifest')
                with patch('acv_tools.texture_manifest.pipeline_identity', return_value='test-pipeline'):
                    result = stage_update(source, root / 'update', root / 'manifest',
                                          root / 'texconv', root / 'model.onnx')
            self.assertEqual((result['at_cap'], result['reused'], result['new_or_changed']), (1, 0, 0))
            record = json.loads((root / 'manifest/001.acv.json').read_text())
            self.assertIsNone(record['textures'][0]['cache_sha256'])
            self.assertEqual(record['textures'][0]['status'], 'at_cap')

    def test_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, staged, manifest = self.baseline(root)
            with patch('acv_tools.texture_manifest.pipeline_identity', return_value='test-pipeline'):
                output = staged / source.name
                output.write_bytes(output.read_bytes() + b'changed')
                with self.assertRaisesRegex(ValueError, 'archive changed'):
                    stage_update(source, root / 'update', manifest, root / 'texconv', root / 'model.onnx')
                with self.assertRaisesRegex(ValueError, 'Unverified or changed archive'):
                    capture(source, staged)
                record = manifest / '001.acv.json'
                record.write_text(record.read_text() + '\n')
                with self.assertRaisesRegex(ValueError, 'Manifest changed'):
                    stage_update(source, root / 'update2', manifest, root / 'texconv', root / 'model.onnx')
                index = manifest / 'index.json'
                value = json.loads(index.read_text())
                value['archives'][0]['file'] = '../escape.json'
                save(index, value)
                with self.assertRaises(ValueError):
                    stage_update(source, root / 'escape', manifest, root / 'texconv', root / 'model.onnx')


if __name__ == '__main__':
    unittest.main()
