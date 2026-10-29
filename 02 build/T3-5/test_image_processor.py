"""T3-2/T3-5 developer tests; fixed responses replace only the model service."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PIL import Image

from ai_client import ModelFileInput, ModelFileOutput, extract_single_file
from attachment_processor import CollectedFile
from config import RunConfig
from data_model import FileRecord
from image_processor import process_image


def response(data: dict | str, status: int = 200):
    raw = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else data
    return SimpleNamespace(status_code=status, output=SimpleNamespace(text=raw))


class ModelAdapterTest(unittest.TestCase):
    def setUp(self):
        self.cfg = SimpleNamespace(dashscope_api_key="test-key", dashscope_model="text-model",
                                   dashscope_vision_model="vision-model")
        self.request = ModelFileInput("01", "Msg1_01_a.jpg", "发票.jpg", "JPEG",
                                      images=((b"test-image", "image/jpeg"),))

    def test_fixed_invoice_response_and_vision_route(self):
        fixed = {
            "file_classifications": [{"file": self.request.internal_name, "role": "Invoice"}],
            "invoice_number": "26522000000868668241", "invoice_date": "2026-08-24",
            "total_amount": "21.60", "invoice_type": "增值税电子普通发票",
            "receipt_type": "客运服务费", "seller": "测试销售方",
            "field_evidence": {name: [{"raw": value, "location": "page=1"}]
                               for name, value in {
                                   "invoice_number": "26522000000868668241", "invoice_date": "2026-08-24",
                                   "total_amount": "21.60", "invoice_type": "增值税电子普通发票",
                                   "receipt_type": "客运服务费", "seller": "测试销售方"}.items()}
        }
        calls = []
        def fake(**kwargs):
            calls.append(kwargs)
            return response(fixed)
        result = extract_single_file(self.request, self.cfg, transport=fake)
        self.assertEqual(result.role, "Invoice")
        self.assertEqual(result.fields["total_amount"], "21.60")
        self.assertEqual(result.error_reason, "")
        self.assertEqual(result.field_evidence["seller"][0]["file_seq"], "01")
        self.assertTrue(calls[0]["vision"])
        self.assertEqual(calls[0]["model"], "vision-model")
        self.assertIn("data:image/jpeg;base64,", str(calls[0]["messages"]))

    def test_invoice_fields_inside_classification_are_read(self):
        fields = {
            "invoice_number": "26522000000868668241",
            "invoice_date": "2026-08-24",
            "total_amount": "21.60",
            "invoice_type": "增值税电子普通发票",
            "receipt_type": "客运服务费",
            "seller": "测试销售方",
        }
        classification = {
            "file": self.request.internal_name, "role": "Invoice",
            **fields,
            "field_evidence": {
                name: [{"raw": value, "location": "page=1"}]
                for name, value in fields.items()
            },
        }
        result = extract_single_file(
            self.request, self.cfg,
            transport=lambda **_: response({"file_classifications": [classification]}),
        )
        self.assertEqual(result.role, "Invoice")
        self.assertEqual(result.fields, fields)
        self.assertEqual(result.error_reason, "")
        self.assertEqual(result.field_evidence["seller"][0]["raw"], "测试销售方")

    def test_retry_only_transient_errors(self):
        calls = []
        sleeps = []
        def fake(**kwargs):
            calls.append(kwargs)
            return response("", 429 if len(calls) == 1 else 503 if len(calls) == 2 else 401)
        result = extract_single_file(self.request, self.cfg, transport=fake, sleep=sleeps.append)
        self.assertEqual(result.error_reason, "MODEL_API_STATUS_401")
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [1, 3])

    def test_invalid_classification_rejected(self):
        fixed = {"file_classifications": [{"file": "different.jpg", "role": "Invoice"}]}
        result = extract_single_file(self.request, self.cfg, transport=lambda **_: response(fixed))
        self.assertEqual(result.role, "Unknown")
        self.assertEqual(result.error_reason, "MODEL_CLASSIFICATION_INVALID")

    def test_invalid_json_and_multiple_documents_rejected(self):
        bad = extract_single_file(self.request, self.cfg, transport=lambda **_: response("{oops"))
        self.assertEqual(bad.error_reason, "MODEL_JSON_INVALID")
        many = extract_single_file(self.request, self.cfg, transport=lambda **_: response({
            "file_classifications": [{"file": self.request.internal_name, "role": "Invoice"}],
            "multiple_documents": True}))
        self.assertEqual(many.role, "Unknown")
        self.assertEqual(many.error_reason, "MULTIPLE_DOCUMENTS_IN_IMAGE")

    def test_text_route_is_one_file_without_images(self):
        request = ModelFileInput("02", "Msg1_02_a.pdf", "发票.pdf", "PDF",
                                 text="发票号码 26522000000868668241")
        fixed = {"file_classifications": [{"file": request.internal_name, "role": "Invoice"}],
                 "invoice_number": "26522000000868668241", "invoice_date": "Not Found",
                 "total_amount": "Not Found", "invoice_type": "Not Found",
                 "receipt_type": "Not Found", "seller": "Not Found",
                 "field_evidence": {"invoice_number": [{"raw": "26522000000868668241",
                                                        "location": "page=1"}]}}
        calls = []
        def fake(**kwargs):
            calls.append(kwargs)
            return response(fixed)
        result = extract_single_file(request, self.cfg, transport=fake)
        self.assertFalse(calls[0]["vision"])
        self.assertEqual(calls[0]["model"], "text-model")
        self.assertEqual(result.fields["invoice_number"], "26522000000868668241")


class ImageProcessorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="t3_image_test_")
        self.root = Path(self.tmp.name)
        self.cfg = RunConfig("imap.invalid", "a", "b", output_root=str(self.root))
        self.file_path = self.root / "Msg1_01_invoice.png"
        Image.new("RGB", (1080, 698), "white").save(self.file_path, format="JPEG")
        self.meta = FileRecord(file_seq="01", file_type="图片", original_name="发票文件1.png",
                               internal_name=self.file_path.name)
        self.file = CollectedFile(self.meta, str(self.file_path))

    def tearDown(self):
        self.tmp.cleanup()

    def test_jpeg_content_under_png_name_converts_complete_pdf(self):
        fixed = ModelFileOutput(role="Invoice", fields={
            "invoice_number": "26522000000868668241", "invoice_date": "2026-08-24",
            "total_amount": "21.60", "invoice_type": "增值税电子普通发票",
            "receipt_type": "*运输服务*客运服务费", "seller": "测试销售方"})
        with patch("image_processor.extract_single_file", return_value=fixed) as model:
            result = process_image(self.file, self.cfg, "发票", "2026-08-24 12:00")
        self.assertEqual(model.call_args.args[0].file_type, "JPEG")
        self.assertEqual(result.role, "Invoice")
        self.assertEqual(result.fields["total_amount"], "21.60")
        self.assertEqual(result.source_file_seq, ["01"])
        self.assertTrue(result.pdf_path)
        self.assertEqual(Path(result.pdf_path).read_bytes()[:5], b"%PDF-")
        self.assertEqual(self.meta.original_name, "发票文件1.png")

    def test_invalid_number_is_not_accepted(self):
        fixed = ModelFileOutput(role="Invoice", fields={
            "invoice_number": "123", "invoice_date": "2026-08-24",
            "total_amount": "21.60", "receipt_type": "客运服务费"})
        with patch("image_processor.extract_single_file", return_value=fixed):
            result = process_image(self.file, self.cfg, "发票", "2026-08-24 12:00")
        self.assertEqual(result.fields["invoice_number"], "Not Found")
        self.assertIn("IMAGE_INVOICE_NUMBER_INVALID", result.error_reason)

    def test_model_failure_keeps_unknown_source_without_pdf(self):
        with patch("image_processor.extract_single_file", return_value=ModelFileOutput(
                error_reason="MODEL_API_STATUS_503")):
            result = process_image(self.file, self.cfg, "发票", "2026-08-24 12:00")
        self.assertEqual(result.role, "Unknown")
        self.assertEqual(result.source_file_seq, ["01"])
        self.assertFalse(result.pdf_path)

    def test_user_sample_real_decoder_and_pdf_with_fixed_model(self):
        sample = Path(r"C:\Users\lenovo\Downloads\发票文件1.png")
        if not sample.is_file():
            self.skipTest("用户原始图片未在此环境提供")
        source = CollectedFile(FileRecord(file_seq="02", file_type="图片",
                                          original_name=sample.name,
                                          internal_name="Msg1_02_user.png"), str(sample))
        fixed = ModelFileOutput(role="Invoice", fields={
            "invoice_number": "26522000000868668241", "invoice_date": "2026-08-24",
            "total_amount": "21.60", "receipt_type": "客运服务费"})
        with patch("image_processor.extract_single_file", return_value=fixed) as model:
            result = process_image(source, self.cfg, "发票", "2026-08-24 12:00")
        self.assertEqual(model.call_args.args[0].file_type, "JPEG")
        self.assertTrue(Path(result.pdf_path).is_file())
        self.assertEqual(result.source_file_seq, ["02"])

    def test_corrupt_image_keeps_registered_unknown(self):
        self.file_path.write_bytes(b"not an image")
        with patch("image_processor.extract_single_file") as model:
            result = process_image(self.file, self.cfg, "发票", "2026-08-24 12:00")
        model.assert_not_called()
        self.assertEqual(result.role, "Unknown")
        self.assertEqual(result.source_file_seq, ["01"])
        self.assertIn("IMAGE_READ_", result.error_reason)

    def test_two_frames_convert_to_two_pdf_pages(self):
        first = Image.new("RGB", (400, 300), "white")
        second = Image.new("RGB", (400, 300), "gray")
        first.save(self.file_path, format="GIF", save_all=True, append_images=[second])
        fixed = ModelFileOutput(role="Itinerary", fields={
            "match_amount": "21.60", "travel_time": "2026-08-24 10:00",
            "origin_dest": "甲 → 乙"})
        with patch("image_processor.extract_single_file", return_value=fixed) as model:
            result = process_image(self.file, self.cfg, "行程单", "2026-08-24 12:00")
        self.assertEqual(len(model.call_args.args[0].images), 2)
        import pdfplumber
        with pdfplumber.open(result.pdf_path) as pdf:
            self.assertEqual(len(pdf.pages), 2)


if __name__ == "__main__":
    unittest.main()
