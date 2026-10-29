"""测试工程师制作、待用户审核：固定识别/下载结果的 T3 分支整链草案。"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from dataclasses import replace
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook
import pdfplumber

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ai_client import ModelFileOutput
from app_demo import mail_notice_cards
from config import RunConfig
from data_model import NOT_FOUND
from mail_link_downloader import LinkFailure
from pipeline import run_pipeline
from processor_contracts import DocumentResult
from stage_ports import load_stage_ports
from test_real_local_chain_draft import FakeImap, XML, PDF, OFD_A, IMAGE, NUMBER_A, NUMBER_B, mail


def invoice(seq: str, number: str = NUMBER_A, amount: str = "121.74") -> DocumentResult:
    return DocumentResult(seq, "Invoice", {
        "invoice_number": number, "invoice_date": "2026-04-27",
        "total_amount": amount, "invoice_type": "电子发票（普通发票）",
        "receipt_type": "客运服务费", "seller": "南京领行科技股份有限公司广州分公司",
    }, [seq])


class ControlledBranchChainDraft(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = RunConfig("imap.test", "user", "password",
                             output_root=self.temp.name, dashscope_api_key="fixed-no-network")

    def execute(self, msg: EmailMessage, *, pdf_result=None, ofd_result=None,
                image_result=None, image_model=None, link_failures=None):
        fake = FakeImap(msg)
        ports = load_stage_ports(self.cfg)
        handlers = dict(ports.document_handlers or {})
        if ofd_result is not None:
            handlers["OFD"] = lambda item, *_: ofd_result(item.meta.file_seq)
        if image_result is not None:
            handlers["图片"] = lambda item, *_: image_result(item.meta.file_seq)
        fixed_ports = replace(ports, document_handlers=handlers)
        with patch("pipeline.imaplib.IMAP4_SSL", return_value=fake), \
             patch("pipeline.load_stage_ports", return_value=fixed_ports), \
             patch("dedup_arbitrator.Generation.call") as external_arbitrator:
            from contextlib import ExitStack
            with ExitStack() as stack:
                if pdf_result is not None:
                    stack.enter_context(patch("pipeline.process_pdf",
                                              side_effect=lambda item, *_: pdf_result(item.meta.file_seq)))
                if image_model is not None:
                    stack.enter_context(patch("image_processor.extract_single_file",
                                              side_effect=image_model))
                if link_failures is not None:
                    stack.enter_context(patch("pipeline.download_links",
                                              return_value=([], link_failures)))
                _, stats = run_pipeline(self.cfg)
            external_arbitrator.assert_not_called()
        self.assertEqual(fake.fetch_queries,
                         ["(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])", "(BODY.PEEK[])"])
        self.assertIsNone(stats["imap_error"])
        self.assertIsNone(stats["run_error"])
        self.assertEqual(stats["delivery_status"], "ready", stats.get("delivery_error"))
        with zipfile.ZipFile(io.BytesIO(stats["delivery_zip_bytes"])) as archive:
            self.assertIsNone(archive.testzip())
            names = archive.namelist()
            self.assertTrue(all(name.startswith("Invoice_Task/") for name in names))
            self.assertTrue(all(name.endswith((".xlsx", ".json", ".pdf")) for name in names))
            self.assertEqual(archive.read("Invoice_Task/发票信息汇总表.xlsx"),
                             Path(self.cfg.summary_excel_file).read_bytes())
            mails = json.loads(archive.read("Invoice_Task/task_debug.json"))
            self.assertEqual(mails, json.loads(Path(self.cfg.debug_file).read_text(encoding="utf-8")))
        rows = list(load_workbook(self.cfg.summary_excel_file, data_only=True).active.values)
        return stats, mails, names, rows

    @unittest.skipUnless(XML.is_file() and IMAGE.is_file(), "缺用户 XML 与图片原件")
    def test_real_image_decode_fixed_itinerary_result_matches_xml_invoice(self):
        msg = mail(XML)
        msg.add_attachment(IMAGE.read_bytes(), maintype="image", subtype="jpeg",
                           filename="测试工程师制作-行程单图片.jpg")

        def fixed_itinerary(request, _cfg):
            self.assertEqual(request.file_type, "JPEG")
            self.assertEqual(request.file_seq, "02")
            fields = {"match_amount": "121.74", "travel_time": "2026-04-27 12:00",
                      "origin_dest": "广州南站→广州东站"}
            return ModelFileOutput(role="Itinerary", fields=fields,
                                   field_evidence={key: [{"file_seq": "02", "raw": value,
                                       "normalized": value, "location": "固定图片模型答复"}]
                                                   for key, value in fields.items()})

        stats, mails, names, rows = self.execute(msg, image_model=fixed_itinerary)
        self.assertEqual(stats["summary_row_count"], 1)
        self.assertEqual(len(mails[0]["records"]), 1)
        self.assertEqual(len(mails[0]["itineraries"]), 1)
        record = mails[0]["records"][0]
        self.assertEqual(record["matched_itinerary"]["file_seq"], "02")
        self.assertEqual(record["fields"]["travel_time"], "2026-04-27 12:00")
        self.assertEqual(record["fields"]["origin_dest"], "广州南站→广州东站")
        self.assertEqual(rows[1][5:8:2], ("2026-04-27 12:00", "广州南站→广州东站"))
        self.assertEqual(len([name for name in names if name.endswith(".pdf")]), 1)
        self.assertEqual(mails[0]["files"][1]["file_type"], "图片")
        with zipfile.ZipFile(io.BytesIO(stats["delivery_zip_bytes"])) as archive:
            pdf_name = next(name for name in names if name.endswith(".pdf"))
            with pdfplumber.open(io.BytesIO(archive.read(pdf_name))) as converted:
                self.assertEqual(len(converted.pages), 1)

    @unittest.skipUnless(PDF.is_file() and OFD_A.is_file(), "缺用户 PDF/OFD A 原件")
    def test_same_number_conflicting_pdf_ofd_keep_two_candidates(self):
        stats, mails, names, rows = self.execute(
            mail(PDF, OFD_A), pdf_result=lambda seq: invoice(seq),
            ofd_result=lambda seq: invoice(seq, amount="131.74"))
        records = mails[0]["records"]
        self.assertEqual(len(records), 2)
        self.assertEqual([r["record_id"] for r in records], ["Msg1-01", "Msg1-02"])
        self.assertEqual([r["source"]["source_file_seq"] for r in records], [["01"], ["02"]])
        self.assertEqual([r["fields"]["total_amount"] for r in records], ["121.74", "131.74"])
        self.assertTrue(all(any(c["reason"] == "unresolved_field_conflict"
                                for c in r["source"]["conflicts"]) for r in records))
        self.assertTrue(all(r["source"].get("delivery_blocked") for r in records))
        self.assertFalse(any(name.endswith(".pdf") for name in names))
        self.assertEqual(stats["summary_row_count"], 2)
        self.assertEqual(len(rows), 3)

    @unittest.skipUnless(XML.is_file() and PDF.is_file(), "缺用户 XML/PDF A 原件")
    def test_orphan_xml_and_pdf_return_as_separate_invoice_rows(self):
        stats, mails, names, rows = self.execute(
            mail(XML, PDF), pdf_result=lambda seq: invoice(seq, NUMBER_B))
        records = mails[0]["records"]
        self.assertEqual(len(records), 2)
        self.assertEqual({r["fields"]["invoice_number"] for r in records},
                         {NUMBER_A, NUMBER_B})
        self.assertEqual([r["source"]["source_file_seq"] for r in records], [["01"], ["02"]])
        self.assertEqual(records[0]["status"], "缺凭证")
        self.assertEqual(stats["summary_row_count"], 2)
        self.assertEqual({row[1] for row in rows[1:]}, {NUMBER_A, NUMBER_B})
        self.assertEqual(len([name for name in names if name.endswith(".pdf")]), 1)

    @unittest.skipUnless(PDF.is_file() and OFD_A.is_file() and IMAGE.is_file(),
                         "缺用户 PDF/OFD/图片原件")
    def test_numberless_candidate_ambiguous_between_two_numbered_invoices(self):
        anchors = {"invoice_date": "2026-04-27", "total_amount": "121.74",
                   "seller": "南京领行科技股份有限公司广州分公司"}
        numberless = DocumentResult("03", "Invoice", {
            **anchors, "invoice_type": "电子发票（普通发票）",
            "receipt_type": "客运服务费"}, ["03"])
        stats, mails, names, rows = self.execute(
            mail(PDF, OFD_A, IMAGE), pdf_result=lambda seq: invoice(seq),
            ofd_result=lambda seq: invoice(seq, NUMBER_B),
            image_result=lambda seq: replace(numberless, file_seq=seq, source_file_seq=[seq]))
        records = mails[0]["records"]
        self.assertEqual(len(records), 3)
        self.assertEqual([r["source"]["source_file_seq"] for r in records],
                         [["01"], ["02"], ["03"]])
        self.assertTrue(any(c["reason"] == "ambiguous_numberless_match"
                            for c in records[2]["source"]["conflicts"]))
        self.assertTrue(records[2]["source"].get("delivery_blocked"))
        self.assertEqual(records[2]["fields"]["invoice_number"], NOT_FOUND)
        self.assertEqual(stats["summary_row_count"], 3)
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum(name.endswith(".pdf") for name in names), 1)

    def test_failed_link_preserves_mail_identity_and_redacts_signed_url(self):
        msg = EmailMessage()
        msg["Subject"] = "电子发票：测试工程师制作，待用户审核"
        msg["Date"] = "Mon, 27 Apr 2026 09:00:00 +0800"
        msg.set_content("发票 PDF 下载：https://example.test/invoice.pdf?sign=SECRET_TEST_TOKEN")
        stats, mails, names, rows = self.execute(
            msg, link_failures=[LinkFailure("正文链接 example.test（PDF）", "HTTP 403 签名过期")])
        self.assertEqual(stats["summary_row_count"], 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(mails[0]["files"]), 0)
        self.assertEqual(len(mails[0]["records"]), 0)
        alerts = stats["not_found_alerts"]
        self.assertTrue(any("下载失败" in " ".join(item["flags"]) for item in alerts))
        self.assertTrue(any("正文链接未取得有效文件" in " ".join(item["flags"])
                            for item in alerts))
        self.assertTrue(all(item["mail_subject"] == msg["Subject"] for item in alerts))
        cards = mail_notice_cards(mails, alerts, [])
        self.assertEqual(len(cards), 1)
        self.assertIn("下载失败", json.dumps(cards, ensure_ascii=False))
        self.assertIn("2026-04-27", cards[0]["mail_date"])
        self.assertNotIn("SECRET_TEST_TOKEN", json.dumps({"mails": mails, "alerts": alerts,
                           "cards": cards, "names": names}, ensure_ascii=False))
        self.assertFalse(any(name.endswith(".pdf") for name in names))


if __name__ == "__main__":
    unittest.main()
