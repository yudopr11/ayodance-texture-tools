"""Receipt layout/progress lifecycle tests, without converter or model inference."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from acv_tools.acv_upscale import (
    finalize_metadata,
    process,
    receipt_path,
    verify_archive,
)
from tests.test_acv_upscale import tiny_dds
from tests.test_texture_manifest import archive


class StagingMetadataTests(unittest.TestCase):
    def fixture(self, root):
        source, staged = root / '001.acv', root / 'stage'
        staged.mkdir()
        archive(source, [(f'gui/{n}.dds', tiny_dds()) for n in range(11)])
        return source, staged, staged / '001.acv.progress.json'

    def test_success_publishes_metadata_then_removes_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, staged, progress = self.fixture(root)

            def verify(*args):
                self.assertTrue(progress.is_file())
                verify_archive(*args)

            with patch('acv_tools.acv_upscale.resize_dds', return_value=False), \
                    patch('acv_tools.acv_upscale.verify_archive', side_effect=verify):
                result = process(source, staged, root / 'converter', pipeline='test')
            self.assertEqual(json.loads((staged / '.metadata/001.acv.json').read_text()), result)
            self.assertFalse((staged / '001.acv.json').exists())
            self.assertFalse(progress.exists())

    def test_processing_and_receipt_publication_failures_keep_progress(self):
        for failure in ('texture', 'receipt'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, staged, progress = self.fixture(root)
                if failure == 'texture':
                    resize = patch('acv_tools.acv_upscale.resize_dds',
                                   side_effect=[False] * 10 + [ValueError('bad texture')])
                    publication = patch('acv_tools.acv_upscale.finalize_metadata')
                else:
                    resize = patch('acv_tools.acv_upscale.resize_dds', return_value=False)
                    publication = patch('pathlib.Path.replace', side_effect=OSError('receipt publication'))
                with resize, publication, self.assertRaises((ValueError, OSError)):
                    process(source, staged, root / 'converter', pipeline='test')
                self.assertTrue(progress.is_file())
                self.assertFalse(receipt_path(staged / source.name).exists())
                if failure == 'receipt':
                    self.assertTrue((staged / source.name).is_file())

    def test_verified_resume_migrates_legacy_receipt_without_reprocessing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, staged, progress = self.fixture(root)
            with patch('acv_tools.acv_upscale.resize_dds', return_value=False):
                result = process(source, staged, root / 'converter', pipeline='test')
            current = receipt_path(staged / source.name)
            original_receipt = current.read_bytes()
            legacy = staged / '001.acv.json'
            current.rename(legacy)
            progress.write_text('old progress')
            with patch('acv_tools.acv_upscale.resize_dds', side_effect=AssertionError('No rerun')):
                self.assertEqual(process(source, staged, root / 'converter', pipeline='test'), result)
            self.assertEqual(current.read_bytes(), original_receipt)
            self.assertFalse(legacy.exists())
            self.assertFalse(progress.exists())
            progress.write_text('keep if rejected')
            with self.assertRaisesRegex(ValueError, 'cannot be safely resumed'):
                process(source, staged, root / 'converter', pipeline='different')
            self.assertTrue(progress.exists())

    def test_ambiguous_receipts_are_rejected_without_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, staged, progress = self.fixture(root)
            with patch('acv_tools.acv_upscale.resize_dds', return_value=False):
                process(source, staged, root / 'converter', pipeline='test')
            current = receipt_path(staged / source.name)
            legacy = staged / '001.acv.json'
            legacy.write_text('different')
            progress.write_text('keep')
            before = current.read_bytes()
            with self.assertRaisesRegex(ValueError, 'Multiple archive receipts'):
                process(source, staged, root / 'converter', pipeline='test')
            self.assertEqual(current.read_bytes(), before)
            self.assertEqual(legacy.read_text(), 'different')
            self.assertTrue(progress.exists())

    def test_no_receipt_cannot_remove_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, staged, progress = self.fixture(root)
            progress.write_text('keep')
            with self.assertRaisesRegex(ValueError, 'published receipt'):
                finalize_metadata(staged / source.name)
            self.assertTrue(progress.exists())


if __name__ == '__main__':
    unittest.main()
