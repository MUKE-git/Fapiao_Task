"""Focused checks for the user ZIP; these inputs are synthetic, not mailbox results."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config import RunConfig
from delivery_exporter import build_delivery_zip
from pdf_business_namer import BusinessPdfNamer


class DeliveryExporterTests(unittest.TestCase):
    def test_unmatched_invoice_uses_invoice_date_and_missing_trip_date_does_not_fall_back(self):
        fields = {"receipt_type": "虚构车费", "invoice_date": "2031-06-07",
                  "total_amount": "21.10"}
        namer = BusinessPdfNamer()
        self.assertEqual(namer.invoice_name(fields, "unmatched"),
                         "虚构车费+2031-06-07+21.10元.pdf")
        self.assertEqual(namer.invoice_name(fields, "matched", {"travel_time": "Not Found"}),
                         "虚构车费+日期缺失+21.10元.pdf")

    def test_only_business_named_pdfs_and_business_outputs_are_delivered(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = RunConfig("imap.example", "user", "password", output_root=root)
            Path(cfg.extract_dir).mkdir(parents=True)
            Path(cfg.summary_excel_file).write_bytes(b"synthetic excel bytes")
            for name in ("Msg1_01_invoice.pdf", "Msg1_02_trip.pdf"):
                (Path(cfg.extract_dir) / name).write_bytes(b"%PDF-1.4\nsynthetic")
            (Path(cfg.extract_dir) / "Msg1_03_invoice.xml").write_text("<test/>")
            (Path(cfg.extract_dir) / "Msg1_04_invoice.ofd").write_bytes(b"OFD")
            mails = [{
                "mail_id": "Msg1", "mail_uid": "101", "subject": "synthetic",
                "files": [
                    {"file_seq": "01", "file_type": "PDF", "internal_name": "Msg1_01_invoice.pdf"},
                    {"file_seq": "02", "file_type": "PDF", "internal_name": "Msg1_02_trip.pdf"},
                    {"file_seq": "03", "file_type": "XML", "internal_name": "Msg1_03_invoice.xml"},
                    {"file_seq": "04", "file_type": "OFD", "internal_name": "Msg1_04_invoice.ofd"},
                ],
                "records": [{"record_id": "Msg1-01", "source": {"source_file_seq": ["01"]},
                             "matched_itinerary": {"file_seq": "02", "match_amount": "88.7700"},
                             "fields": {"invoice_number": "99999999999999999999", "receipt_type": "*虚构*合成服务费",
                                        "invoice_date": "2031-02-03", "total_amount": "17.4567",
                                        "seller": "合成测试有限公司"}}],
                "itineraries": [{"record_id": "Msg1-02", "travel_time": "2031-01-02 04:05:06",
                                 "match_amount": "88.7700"}],
            }]
            Path(cfg.debug_file).write_text(json.dumps(mails), encoding="utf-8")

            result = build_delivery_zip(cfg)
            with zipfile.ZipFile(io.BytesIO(result)) as archive:
                names = set(archive.namelist())
                self.assertEqual(names, {
                    "Invoice_Task/发票信息汇总表.xlsx", "Invoice_Task/task_debug.json",
                    "Invoice_Task/用户票据/合成服务费+2031-01-02+17.4567元.pdf",
                    "Invoice_Task/用户票据/行程单+2031-01-02+17.4567元.pdf",
                })
                delivered = json.loads(archive.read("Invoice_Task/task_debug.json"))
            files = delivered[0]["files"]
            self.assertEqual(delivered[0]["itineraries"][0]["match_amount"], "88.7700")
            self.assertEqual(delivered[0]["records"][0]["matched_itinerary"]["match_amount"], "88.7700")
            self.assertEqual([item["exported_name"] for item in files], [
                "合成服务费+2031-01-02+17.4567元.pdf",
                "行程单+2031-01-02+17.4567元.pdf", "", "",
            ])
            self.assertTrue((Path(cfg.extract_dir) / "Msg1_01_invoice.pdf").exists())

    def test_two_mails_with_same_itinerary_amount_follow_their_own_invoices(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = RunConfig("imap.example", "user", "password", output_root=root)
            Path(cfg.extract_dir).mkdir(parents=True)
            Path(cfg.summary_excel_file).write_bytes(b"synthetic excel bytes")
            mails = []
            for index, invoice_amount in ((1, "21.10"), (2, "34.20")):
                mail_id = f"Msg{index}"
                invoice_file = f"{mail_id}_01_invoice.pdf"
                itinerary_file = f"{mail_id}_02_trip.pdf"
                (Path(cfg.extract_dir) / invoice_file).write_bytes(b"%PDF-1.4\nsynthetic invoice")
                (Path(cfg.extract_dir) / itinerary_file).write_bytes(b"%PDF-1.4\nsynthetic shared trip")
                mails.append({
                    "mail_id": mail_id, "mail_uid": str(9000 + index),
                    "files": [
                        {"file_seq": "01", "file_type": "PDF", "internal_name": invoice_file},
                        {"file_seq": "02", "file_type": "PDF", "internal_name": itinerary_file},
                    ],
                    "records": [{"record_id": f"{mail_id}-01",
                                 "source": {"source_file_seq": ["01"]},
                                 "matched_itinerary": {"file_seq": "02", "match_amount": "55.30"},
                                 "fields": {"invoice_number": str(9000 + index),
                                            "receipt_type": "虚构车费", "invoice_date": "2031-06-07",
                                            "total_amount": invoice_amount, "seller": "合成公司"}}],
                    "itineraries": [{"record_id": f"{mail_id}-02",
                                     "travel_time": "2031-05-06 07:08",
                                     "match_amount": "55.30"}],
                })
            Path(cfg.debug_file).write_text(json.dumps(mails), encoding="utf-8")
            with zipfile.ZipFile(io.BytesIO(build_delivery_zip(cfg))) as archive:
                names = set(archive.namelist())
                self.assertIn("Invoice_Task/用户票据/虚构车费+2031-05-06+21.10元.pdf", names)
                self.assertIn("Invoice_Task/用户票据/虚构车费+2031-05-06+34.20元.pdf", names)
                self.assertIn("Invoice_Task/用户票据/行程单+2031-05-06+21.10元.pdf", names)
                self.assertIn("Invoice_Task/用户票据/行程单+2031-05-06+34.20元.pdf", names)
                exported = json.loads(archive.read("Invoice_Task/task_debug.json"))
            self.assertEqual([mail["itineraries"][0]["match_amount"] for mail in exported],
                             ["55.30", "55.30"])

    def test_unmatched_itinerary_uses_its_own_amount(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = RunConfig("imap.example", "user", "password", output_root=root)
            Path(cfg.extract_dir).mkdir(parents=True)
            Path(cfg.summary_excel_file).write_bytes(b"synthetic excel bytes")
            (Path(cfg.extract_dir) / "Msg1_01_trip.pdf").write_bytes(b"%PDF-1.4\nsynthetic")
            mails = [{"mail_id": "Msg1", "mail_uid": "9001",
                      "files": [{"file_seq": "01", "file_type": "PDF",
                                 "internal_name": "Msg1_01_trip.pdf"}],
                      "records": [],
                      "itineraries": [{"record_id": "Msg1-01", "travel_time": "2031-05-06 07:08",
                                       "match_amount": "55.30"}]}]
            Path(cfg.debug_file).write_text(json.dumps(mails), encoding="utf-8")
            with zipfile.ZipFile(io.BytesIO(build_delivery_zip(cfg))) as archive:
                self.assertIn("Invoice_Task/用户票据/行程单+2031-05-06+55.30元.pdf",
                              archive.namelist())


if __name__ == "__main__":
    unittest.main()
