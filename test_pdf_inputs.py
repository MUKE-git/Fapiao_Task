import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from pdf_input import prepare_pdf


def make_pdf(path, modes):
    c = canvas.Canvas(str(path), pagesize=(600, 800))
    for mode in modes:
        if mode == 'text':
            c.drawString(40, 700, 'INVOICE: 12345678901234567890; Total: 123.45; Seller ABC Company')
        else:
            image = Image.new('RGB', (600, 800), 'white')
            ImageDraw.Draw(image).text((40, 100), 'INVOICE 12345678901234567890 Total 123.45', fill='black')
            c.drawImage(ImageReader(image), 0, 0, 600, 800)
        c.showPage()
    c.save()


class PdfInputTests(unittest.TestCase):
    def test_real_scan_and_mixed_pages_render_and_map(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'mixed.pdf'
            make_pdf(path, ['text', 'image'])
            before = path.read_bytes()
            result = prepare_pdf(str(path))
            self.assertEqual(result.page_count, 2)
            self.assertEqual(result.image_pages, (2,))
            self.assertIn('Total: 123.45', result.text)
            self.assertIn('image 1 = PDF page 2', result.text)
            self.assertEqual(Image.open(io.BytesIO(result.images[0][0])).format, 'PNG')
            self.assertEqual(path.read_bytes(), before)

    def test_text_pdf_keeps_text_path(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'text.pdf'; make_pdf(path, ['text'])
            self.assertEqual(prepare_pdf(str(path)).images, ())

    def test_corrupt_and_overlong_fail_without_partial_output(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'bad.pdf'; path.write_bytes(b'not a PDF')
            with self.assertRaises(Exception): prepare_pdf(str(path))
            make_pdf(path, ['text'] * 9)
            with self.assertRaisesRegex(ValueError, 'PDF_PAGE_LIMIT_EXCEEDED'): prepare_pdf(str(path))

    def test_visual_pdf_uses_vision_entry_and_keeps_file_identity(self):
        from attachment_processor import CollectedFile
        from data_model import FileRecord
        from config import RunConfig
        from pdf_processor import process_pdf
        from ai_client import ModelFileOutput
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'scan.pdf'; make_pdf(path, ['image'])
            item = CollectedFile(FileRecord('01', file_type='PDF', original_name='invoice.pdf', internal_name='scan.pdf'), str(path))
            with patch('ai_client.extract_single_file', return_value=ModelFileOutput(role='Invoice', fields={'total_amount':'123.45'})) as call:
                result = process_pdf(item, RunConfig('host','user','password'), '', '')
                self.assertEqual(result.fields['total_amount'], '123.45')
                self.assertEqual(call.call_args.args[0].internal_name, 'scan.pdf')
                self.assertTrue(call.call_args.args[0].images)
                self.assertEqual(item.meta.model_usage['pdf_image_pages'], [1])

    def test_vision_request_includes_page_text(self):
        from ai_client import ModelFileInput, extract_single_file
        captured = []
        def send(**kwargs):
            captured.append(kwargs)
            return SimpleNamespace(status_code=400)
        extract_single_file(ModelFileInput('01','x.pdf','x.pdf','PDF', text='page 1 text; image 1 = page 2', images=((b'fake','image/png'),)), SimpleNamespace(), transport=send)
        self.assertIn('page 1 text', captured[0]['messages'][1]['content'][-1]['text'])

    def test_scan_arbitration_sends_both_sources_and_requires_manual_review(self):
        import importlib.util
        from config import RunConfig
        from dedup_arbitrator import _choose_with_ai
        spec = importlib.util.spec_from_file_location('arbitration_fixtures', '02 build/T2-4/test_dedup_arbitrator.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        first, second = module.fixture(1, amount='100.00'), module.fixture(2, amount='200.00')
        with tempfile.TemporaryDirectory() as root:
            cfg = RunConfig('host', 'user', 'password', output_root=root)
            Path(cfg.extract_dir).mkdir(parents=True)
            for mail in (first, second):
                make_pdf(Path(cfg.extract_dir) / mail.files[0].internal_name, ['image'])
            with patch('model_gateway.model_call', return_value={'selected_record_id':second.records[0].record_id, 'evidence': []}) as call:
                choice = _choose_with_ai([(first,first.records[0]),(second,second.records[0])], cfg, None, None)
                self.assertIsNone(choice)
                self.assertTrue(call.call_args.kwargs['vision'])
                self.assertEqual(call.call_args.kwargs['model'], cfg.dashscope_vision_model)
                parts = call.call_args.kwargs['messages'][1]['content']
                self.assertEqual(sum('image' in p for p in parts), 2)
                self.assertTrue(first.arbitration_alerts)


if __name__ == '__main__': unittest.main()
