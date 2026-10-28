"""Synthetic checks for PDF pre-read to model amount handoff."""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# The model call is mocked below; keep this focused test runnable offline.
try:
    import dashscope  # noqa: F401
except ModuleNotFoundError:
    stub = types.ModuleType("dashscope")
    stub.Generation = object
    sys.modules["dashscope"] = stub

from attachment_processor import CollectedFile
from config import RunConfig
from data_model import FileRecord
from pdf_processor import process_pdf


class AmountPrecisionHandoffTests(unittest.TestCase):
    def _process(self, local_amount: str, model_amount: object):
        with tempfile.TemporaryDirectory(dir=ROOT) as root:
            path = Path(root) / "Msg1_01_synthetic.pdf"
            path.write_bytes(b"%PDF-1.4\nsynthetic")
            cfg = RunConfig("imap.example", "user", "unused", output_root=root)
            item = CollectedFile(FileRecord("01", original_name="虚构发票.pdf"), str(path))
            local = {"invoice_number": "9" * 20, "total_amount": local_amount}
            model = {"invoice_number": "9" * 20, "total_amount": model_amount,
                     "file_classifications": [{"file": path.name, "role": "Invoice"}]}
            with patch("pdf_processor.local_inspect_pdf", return_value=("Invoice", "合计 ￥8.00")), \
                 patch("pdf_processor.build_fallback_audit_result", return_value=local), \
                 patch("pdf_processor.call_ai_audit_and_rename", return_value=(model, "", [], [])):
                result = process_pdf(item, cfg, "合成测试", "2031-01-01")
            return item.meta.pre_read_amount, result.fields["total_amount"]

    def test_same_value_keeps_trailing_zeroes_from_pdf_text(self):
        for model_amount in ("8.0", Decimal("8.0")):
            with self.subTest(model_amount=model_amount):
                pre_read, final = self._process("8.00", model_amount)
                self.assertEqual(pre_read.as_tuple().exponent, -2)
                self.assertEqual(final, "8.00")

    def test_different_value_is_not_overwritten_by_pre_read(self):
        pre_read, final = self._process("8.00", "8.01")
        self.assertEqual(pre_read, Decimal("8.00"))
        self.assertEqual(final, "8.01")

    def test_invalid_local_precision_is_not_used(self):
        _pre_read, final = self._process("8.00000", "8.0")
        self.assertEqual(final, "8.0")


if __name__ == "__main__":
    unittest.main()
