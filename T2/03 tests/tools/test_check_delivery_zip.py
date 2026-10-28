"""Synthetic tests for the read-only delivery checker."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_delivery_zip import HEADERS, check_bundle


ROOT = Path(__file__).resolve().parents[3]


def _mail() -> dict:
    files = []
    for seq, name in (("01", "虚构服务+2031-01-02+17.00元.pdf"),
                      ("02", "行程单+2031-01-02+17.00元.pdf")):
        files.append({"file_seq": seq, "file_type": "PDF", "original_name": f"synthetic{seq}.pdf",
                      "pre_read_type": "Invoice" if seq == "01" else "Itinerary",
                      "pre_read_invoice_no": "Not Found", "pre_read_amount": "Not Found",
                      "internal_name": f"Msg1_{seq}_synthetic.pdf", "exported_name": name})
    return {"mail_id": "Msg1", "mail_uid": "9001", "subject": "合成测试",
            "mail_date": "2031-02-03 04:05", "files": files,
            "itineraries": [{"record_id": "Msg1-02", "match_amount": "99.00",
                             "travel_time": "2031-01-02 03:04", "origin_dest": "甲地 → 乙地"}],
            "records": [{"record_id": "Msg1-01", "kind": "a类",
                         "fields": {"invoice_date": "2031-02-03", "invoice_number": "9" * 20,
                                    "total_amount": "17.00", "invoice_type": "",
                                    "receipt_type": "虚构服务", "seller": "合成公司",
                                    "travel_time": "2031-01-02 03:04", "origin_dest": "甲地 → 乙地"},
                         "source": {"source_file_seq": ["01"]},
                         "matched_itinerary": {"file_seq": "02", "match_amount": "99.00"},
                         "status": "complete"}], "dedup_skipped": [], "error_reason": ""}


class DeliveryCheckerTests(unittest.TestCase):
    def _check(self, mail: dict):
        with tempfile.TemporaryDirectory(dir=ROOT) as root:
            path = Path(root) / "synthetic.zip"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(HEADERS)
            sheet.append(("2031-02-03", "9" * 20, 17.0, None, "虚构服务",
                          "2031-01-02 03:04", "合成公司", "甲地 → 乙地"))
            excel = BytesIO()
            workbook.save(excel)
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("Invoice_Task/task_debug.json", json.dumps([mail]))
                archive.writestr("Invoice_Task/发票信息汇总表.xlsx", excel.getvalue())
                for file in mail["files"]:
                    archive.writestr("Invoice_Task/用户票据/" + file["exported_name"],
                                     b"%PDF-1.4\nsynthetic")
            return check_bundle(path)

    def test_consistent_bundle(self):
        errors, warnings, counts = self._check(_mail())
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(counts["excel_rows_compared"], 1)

    def test_broken_match_is_reported(self):
        mail = _mail()
        mail["records"][0]["matched_itinerary"]["file_seq"] = "03"
        errors, _warnings, _counts = self._check(mail)
        self.assertTrue(any("行程单缺失" in item for item in errors), errors)


if __name__ == "__main__":
    unittest.main()
