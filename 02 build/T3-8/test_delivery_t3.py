"""T3-8 研发自测固定输入；不属于用户已审核的 18 条用例或独立验收。"""
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
from delivery_exporter import build_delivery_zip, prepare_delivery


PDF = b"%PDF-1.4\nfixed development fixture"


class DeliveryT3Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = RunConfig("example", "u", "p", output_root=self.temp.name)
        Path(self.cfg.extract_dir).mkdir(parents=True)
        Path(self.cfg.summary_excel_file).write_bytes(b"fixed workbook fixture")

    def pdf(self, name):
        path = Path(self.cfg.extract_dir) / name
        path.write_bytes(PDF)
        return str(path)

    def mail(self, files, sources, blocked=False):
        return [{"mail_id": "Msg1", "mail_uid": "101", "files": files,
                 "records": [{"record_id": "Msg1-01", "status": "人工待核" if blocked else "complete",
                              "fields": {"invoice_number": "12345678901234567890",
                                         "invoice_date": "2031-01-02", "total_amount": "12.34",
                                         "receipt_type": "服务费", "seller": "合成销售方"},
                              "source": {"source_file_seq": sources,
                                         "delivery_blocked": blocked}}],
                 "itineraries": []}]

    def test_original_pdf_wins_over_ofd_and_image_copy(self):
        original = self.pdf("Msg1_02_source.pdf")
        ofd_copy = self.pdf("Msg1_01_ofd_copy.pdf")
        image_copy = self.pdf("Msg1_03_image_copy.pdf")
        files = [
            {"file_seq": "01", "file_type": "OFD", "internal_name": "Msg1_01.ofd"},
            {"file_seq": "02", "file_type": "PDF", "internal_name": Path(original).name},
            {"file_seq": "03", "file_type": "图片", "internal_name": "Msg1_03.jpg"},
        ]
        source = self.mail(files, ["01", "02", "03"])
        plan = prepare_delivery(self.cfg, source, {("Msg1", "01"): ofd_copy,
                                                   ("Msg1", "03"): image_copy})
        self.assertEqual([entry.file_seq for entry in plan.entries], ["02"])
        self.assertEqual([item["exported_name"] for item in plan.mails[0]["files"]],
                         ["", "服务费+2031-01-02+12.34元.pdf", ""])
        self.assertNotIn("exported_name", source[0]["files"][0])
        Path(self.cfg.debug_file).write_text(json.dumps(plan.mails), encoding="utf-8")
        with zipfile.ZipFile(io.BytesIO(build_delivery_zip(self.cfg, plan))) as archive:
            self.assertEqual(set(archive.namelist()), {
                "Invoice_Task/发票信息汇总表.xlsx", "Invoice_Task/task_debug.json",
                "Invoice_Task/用户票据/服务费+2031-01-02+12.34元.pdf",
            })

    def test_ofd_copy_is_ignored_even_if_an_old_copy_is_supplied(self):
        ofd = self.pdf("ofd_copy.pdf")
        image = self.pdf("image_copy.pdf")
        files = [{"file_seq": "01", "file_type": "OFD", "internal_name": "a.ofd"},
                 {"file_seq": "02", "file_type": "图片", "internal_name": "b.jpg"}]
        source = self.mail(files, ["01", "02"])
        plan = prepare_delivery(self.cfg, source, {("Msg1", "01"): ofd,
                                                   ("Msg1", "02"): image})
        self.assertEqual(plan.entries[0].file_seq, "02")
        self.assertEqual(plan.mails[0]["files"][0]["exported_name"], "")
        ofd_only = prepare_delivery(self.cfg, self.mail(files[:1], ["01"]),
                                    {("Msg1", "01"): ofd})
        self.assertFalse(ofd_only.entries)

    def test_xml_only_and_blocked_conflict_do_not_export(self):
        files = [{"file_seq": "01", "file_type": "XML", "internal_name": "a.xml"}]
        self.assertFalse(prepare_delivery(self.cfg, self.mail(files, ["01"])).entries)
        pdf = self.pdf("invoice.pdf")
        files = [{"file_seq": "01", "file_type": "PDF", "internal_name": Path(pdf).name}]
        plan = prepare_delivery(self.cfg, self.mail(files, ["01"], blocked=True))
        self.assertFalse(plan.entries)
        self.assertEqual(plan.mails[0]["files"][0]["exported_name"], "")

    def test_json_must_match_selection_and_path_cannot_escape(self):
        pdf = self.pdf("invoice.pdf")
        files = [{"file_seq": "01", "file_type": "PDF", "internal_name": Path(pdf).name}]
        plan = prepare_delivery(self.cfg, self.mail(files, ["01"]))
        Path(self.cfg.debug_file).write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "最终 JSON"):
            build_delivery_zip(self.cfg, plan)
        files = [{"file_seq": "01", "file_type": "图片", "internal_name": "a.png"}]
        plan = prepare_delivery(self.cfg, self.mail(files, ["01"]),
                                {("Msg1", "01"): str(Path(self.temp.name) / "outside.pdf")})
        self.assertFalse(plan.entries)
        self.assertTrue(plan.issues)

    def test_unresolved_cross_mail_number_conflict_suppresses_only_that_pdf(self):
        self.pdf("Msg1_01.pdf")
        self.pdf("Msg2_01.pdf")
        self.pdf("Msg2_02.pdf")
        first = self.mail([{"file_seq": "01", "file_type": "PDF",
                            "internal_name": "Msg1_01.pdf"}], ["01"])[0]
        second = self.mail([{"file_seq": "01", "file_type": "PDF",
                             "internal_name": "Msg2_01.pdf"},
                            {"file_seq": "02", "file_type": "PDF",
                             "internal_name": "Msg2_02.pdf"}], ["01"])[0]
        second["mail_id"] = "Msg2"
        second["records"][0]["record_id"] = "Msg2-01"
        second["records"][0]["fields"]["total_amount"] = "99.99"
        extra = self.mail([], ["02"])[0]["records"][0]
        extra["record_id"] = "Msg2-02"
        extra["source"]["source_file_seq"] = ["02"]
        extra["fields"]["invoice_number"] = "22222222222222222222"
        second["records"].append(extra)
        plan = prepare_delivery(self.cfg, [first, second])
        self.assertEqual([(e.mail_id, e.file_seq) for e in plan.entries], [("Msg2", "02")])
        self.assertEqual(sum("同号业务值冲突" in issue for issue in plan.issues), 2)


if __name__ == "__main__":
    unittest.main()
