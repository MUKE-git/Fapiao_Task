"""B 编号 T2-1 业务验收测试；每个方法对应测试设计中的一条技术用例。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import imaplib

from openpyxl import load_workbook

from attachment_processor import collect_mail_files
from config import RunConfig, SUMMARY_COLUMNS, ensure_output_dirs
from data_model import MailRecord, MatchedItinerary
from pipeline import _excel_rows, process_message, run_pipeline
from processor_contracts import DocumentResult
from stage_ports import StagePorts


OBSERVED: dict[str, dict] = {}


def mail(*attachments, subject="电子发票", date="Tue, 29 Sep 2026 09:30:00 +0800"):
    message = EmailMessage()
    message["Subject"] = subject
    message["Date"] = date
    message.set_content("测试邮件")
    for name, payload in attachments:
        message.add_attachment(payload, maintype="application", subtype="octet-stream", filename=name)
    return message


def zipped(entries):
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return stream.getvalue()


def mark_first_entry_encrypted(archive_bytes):
    """Set ZIP encryption flag in local and central headers for a deterministic rejection fixture."""
    content = bytearray(archive_bytes)
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        index = content.find(signature)
        if index < 0:
            raise ValueError("missing ZIP header")
        flag = int.from_bytes(content[index + flag_offset:index + flag_offset + 2], "little")
        content[index + flag_offset:index + flag_offset + 2] = (flag | 1).to_bytes(2, "little")
    return bytes(content)


def result(file, *, role="Invoice", number=None, amount=126.4, receipt="办公用品",
           seller="测试销售方有限公司", error="", travel=None, origin=None):
    fields = {"invoice_date": "2026-09-28", "invoice_number": number or "1" * 20,
              "total_amount": amount, "invoice_type": "电子发票（普通发票）",
              "receipt_type": receipt, "seller": seller}
    if travel is not None:
        fields["travel_time"] = travel
    if origin is not None:
        fields["origin_dest"] = origin
    return DocumentResult(file_seq=file.meta.file_seq, role=role, fields=fields,
                          source_file_seq=[file.meta.file_seq], error_reason=error)


class FakeImap:
    def __init__(self, messages, *, fail_uid=None, fail_always=False):
        self.messages = {str(uid): item for uid, item in messages.items()}
        self.fail_uid = str(fail_uid) if fail_uid is not None else None
        self.fail_always = fail_always
        self.failed = False
        self.events = []
        self.connections = 0

    def connect(self, *_args):
        self.connections += 1
        self.events.append(["connect", self.connections])
        return self

    def login(self, *_args):
        return "OK", []

    def select(self, *_args):
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command, *args):
        if command == "SEARCH":
            self.events.append(["search", "UNSEEN"])
            return "OK", [" ".join(self.messages).encode()]
        uid = args[0].decode() if isinstance(args[0], bytes) else str(args[0])
        query = args[1]
        self.events.append(["fetch", uid, query])
        if uid == self.fail_uid and query == "(BODY.PEEK[])" and (self.fail_always or not self.failed):
            self.failed = True
            self.events.append(["abort", uid])
            raise imaplib.IMAP4.abort("simulated disconnect")
        item = self.messages[uid]
        if "HEADER.FIELDS" in query:
            header = EmailMessage()
            header["Subject"] = item["Subject"]
            header["Date"] = item["Date"]
            return "OK", [(b"header", header.as_bytes())]
        return "OK", [(b"body", item.as_bytes())]

    def status(self, *_args):
        n = len(self.messages)
        return "OK", [f"INBOX (MESSAGES {n} UNSEEN {n})".encode()]

    def logout(self):
        self.events.append(["logout"])
        return "BYE", []


class T21Acceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = RunConfig("imap.test", "tester", "password", output_root=self.tmp.name,
                             dashscope_api_key="test-only")
        ensure_output_dirs(self.cfg)
        self.trace = []
        self.observed = {}

    def tearDown(self):
        key = self._testMethodName.replace("test_", "").replace("_", "-", 1)
        OBSERVED[key] = {"trace": self.trace, "observed": self.observed}

    def pdf(self, file, *_args):
        self.trace.append(["process_pdf", file.meta.file_seq, file.meta.original_name])
        name = file.meta.original_name
        if "行程单" in name:
            return result(file, role="Itinerary", amount=53.93,
                          travel="2026-09-25 10:00", origin="杭州→上海")
        if "甲" in name:
            return result(file, number="1" * 20, amount=53.93, seller="甲公司")
        if "乙" in name:
            return result(file, number="2" * 20, seller="乙公司")
        return result(file)

    def process(self, message, *, ports=None, pdf=None, uid="1001", mail_id="Msg1"):
        with patch("pipeline.process_pdf", side_effect=pdf or self.pdf):
            outcome = process_message(message, mail_id, uid, self.cfg,
                                      ports=ports if ports is not None else StagePorts())
        self.observed = {"files": [f.file_type for f in outcome.mail.files],
                         "records": [r.record_id for r in outcome.mail.records],
                         "alerts": [str(a.get("flags")) for a in outcome.alerts]}
        return outcome

    def run_mailbox(self, messages, *, fake=None, pdf=None, ports=None):
        fake = fake or FakeImap(messages)
        with patch("pipeline.imaplib.IMAP4_SSL", side_effect=fake.connect), \
             patch("pipeline.process_pdf", side_effect=pdf or self.pdf), \
             patch("pipeline.load_stage_ports", return_value=ports or StagePorts()), \
             patch("pipeline.time.sleep", return_value=None):
            _, stats = run_pipeline(self.cfg)
        self.trace.extend(fake.events)
        saved = json.loads(Path(self.cfg.debug_file).read_text(encoding="utf-8"))
        self.observed = {"stats": stats, "mails": [m["mail_uid"] for m in saved],
                         "records": [[r["record_id"] for r in m["records"]] for m in saved]}
        return stats, saved, fake

    def excel(self):
        path = Path(self.cfg.summary_excel_file)
        if not path.exists():
            return []
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            return list(book.active.values)
        finally:
            book.close()

    def test_B00_01(self):
        stats, saved, fake = self.run_mailbox({})
        self.assertEqual((stats["unseen_total"], stats["invoice_keyword_mails"]), (0, 0))
        self.assertEqual(saved, [])
        self.assertFalse(self.excel())
        self.assertFalse(any(x[0] == "fetch" for x in fake.events))

    def test_B01_01(self):
        stats, saved, _ = self.run_mailbox({"1001": mail(("办公用品发票.pdf", b"pdf-A"))})
        self.assertIsNone(stats["imap_error"])
        self.assertEqual(self.observed["records"], [["Msg1-01"]])
        record = saved[0]["records"][0]
        self.assertEqual(record["source"]["source_file_seq"], ["01"])
        self.assertEqual(record["kind"], "b类")
        rows = self.excel()
        self.assertEqual(rows[0], tuple(name for name, _ in SUMMARY_COLUMNS))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], "1" * 20)
        self.assertEqual(sum(t[0] == "process_pdf" for t in self.trace), 1)

    def test_B02_01(self):
        outcome = self.process(mail(("甲发票.pdf", b"one"), ("乙发票.pdf", b"two")))
        records = outcome.mail.records
        self.assertEqual([(r.record_id, r.fields.invoice_number, r.source.source_file_seq)
                          for r in records], [("Msg1-01", "1" * 20, ["01"]),
                                              ("Msg1-02", "2" * 20, ["02"])])
        self.assertEqual(len(_excel_rows([outcome.mail])), 2)
        self.assertEqual(len(self.trace), 2)

    def test_B02_02(self):
        def pdf(file, *_args):
            return result(file, number="1" * 20 if file.meta.file_seq == "01" else "Not Found")
        outcome = self.process(mail(("甲发票.pdf", b"one"), ("乙发票.pdf", b"two")), pdf=pdf)
        self.assertEqual([r.fields.invoice_number for r in outcome.mail.records],
                         ["1" * 20, "Not Found"])
        self.assertEqual(len(outcome.mail.records), 2)

    def test_B03_01(self):
        outcome = self.process(mail(("客运发票.pdf", b"invoice"), ("行程单.pdf", b"route")),
                               pdf=lambda file, *_: result(file, role="Itinerary", amount=53.93,
                                   travel="2026-09-25 10:00", origin="杭州→上海")
                               if "行程单" in file.meta.original_name else
                               result(file, number="3" * 20, amount=53.93, receipt="客运服务"))
        self.assertEqual(len(outcome.mail.records), 1)
        self.assertEqual(len(outcome.itineraries), 1)
        self.assertEqual(len(_excel_rows([outcome.mail])), 1)
        from pipeline import _apply_matching
        _apply_matching([outcome], StagePorts())
        self.observed["matched_itinerary"] = (
            outcome.mail.records[0].matched_itinerary.file_seq
            if outcome.mail.records[0].matched_itinerary else None)
        self.observed["matching_alerts"] = [a["flags"] for a in outcome.alerts]
        # 文档要求 T2-1 单对单直接成组；当前实现若缺席，作为真实失败呈现。
        self.assertIsNotNone(outcome.mail.records[0].matched_itinerary)
        self.assertEqual(outcome.mail.records[0].matched_itinerary.file_seq, "02")
        self.assertEqual(outcome.mail.records[0].fields.travel_time, "2026-09-25 10:00")

    def test_B04_01(self):
        outcome = self.process(mail(("办公用品发票.pdf", b"pdf")),
                               pdf=lambda file, *_: result(file, travel="伪行程", origin="伪路线"))
        record = outcome.mail.records[0]
        self.assertEqual(record.kind, "b类")
        self.assertEqual((_excel_rows([outcome.mail])[0]["travel_time"],
                          _excel_rows([outcome.mail])[0]["origin_dest"]), ("", ""))
        from data_model import mail_to_dict
        fields = mail_to_dict(outcome.mail)["records"][0]["fields"]
        self.assertNotIn("travel_time", fields)
        self.assertNotIn("origin_dest", fields)

    def _transport(self, receipt):
        outcome = self.process(mail(("客运发票.pdf", b"pdf")),
                               pdf=lambda file, *_: result(file, receipt=receipt))
        record = outcome.mail.records[0]
        self.assertEqual(record.kind, "a类")
        self.assertEqual(len(_excel_rows([outcome.mail])), 1)
        self.assertFalse(record.matched_itinerary)
        # 当前主循环的待核提示仅在匹配阶段产生，不能只看 record.status。
        from pipeline import _apply_matching
        _apply_matching([outcome], StagePorts())
        self.assertTrue(any("缺行程单" in str(a["flags"]) for a in outcome.alerts))
        self.assertEqual(record.status, "人工待核")
        self.observed["transport"] = {"kind": record.kind, "status": record.status,
                                      "alerts": [a["flags"] for a in outcome.alerts],
                                      "excel_row": _excel_rows([outcome.mail])[0]}
        self.assertEqual(_excel_rows([outcome.mail])[0]["travel_time"], "")
        self.assertEqual(_excel_rows([outcome.mail])[0]["origin_dest"], "")
        _, saved, _ = self.run_mailbox({"501": mail(("客运发票.pdf", b"pdf"))},
                                      pdf=lambda file, *_: result(file, receipt=receipt))
        workbook_rows = self.excel()
        self.assertEqual(len(saved[0]["records"]), 1)
        self.assertEqual(len(workbook_rows), 2)
        self.assertIn(workbook_rows[1][5], (None, ""))
        self.assertIn(workbook_rows[1][7], (None, ""))
        self.observed["transport_workbook_row"] = list(workbook_rows[1])

    def test_B14_01(self): self._transport("客运服务")
    def test_B14_02(self): self._transport("客运服务费")

    def test_B05_01(self):
        outcome = self.process(mail(("发票.pdf", b"pdf")),
                               pdf=lambda file, *_: result(file, number="Not Found"))
        self.assertEqual(len(outcome.mail.records), 1)
        self.assertEqual(outcome.mail.records[0].fields.invoice_number, "Not Found")
        self.assertEqual(outcome.mail.records[0].source.source_file_seq, ["01"])

    def test_B05_02(self):
        def pdf(file, *_):
            item = result(file)
            if file.meta.file_seq == "01": item.fields["total_amount"] = "Not Found"
            else: item.fields["invoice_date"] = "Not Found"
            return item
        outcome = self.process(mail(("甲发票.pdf", b"a"), ("乙发票.pdf", b"b")), pdf=pdf)
        self.assertEqual(len(outcome.mail.records), 2)
        self.assertEqual(outcome.mail.records[0].fields.total_amount, "Not Found")
        self.assertEqual(outcome.mail.records[1].fields.invoice_date, "Not Found")

    def test_B06_01(self):
        outcome = self.process(mail(("未知.pdf", b"pdf")),
                               pdf=lambda file, *_: DocumentResult(file.meta.file_seq, "Unknown"))
        self.assertEqual(len(outcome.mail.files), 1)
        self.assertEqual(outcome.mail.records, [])
        self.assertTrue(any("类型无法确定" in str(a["flags"]) for a in outcome.alerts))

    def test_B06_02(self):
        outcome = self.process(mail(("发票.pdf", b"pdf")),
                               pdf=lambda file, *_: result(file, error="AI_PARSE_ERROR"))
        self.assertEqual(len(outcome.mail.records), 1)
        self.assertEqual(outcome.mail.records[0].status, "人工待核")
        self.assertIn("AI_PARSE_ERROR", outcome.mail.error_reason)
        self.assertTrue(any("AI_PARSE_ERROR" in str(a["flags"]) for a in outcome.alerts))

    def _unsupported(self, name, kind):
        outcome = self.process(mail((name, b"payload")))
        self.assertEqual(outcome.mail.files[0].file_type, kind)
        self.assertEqual(outcome.mail.records, [])
        self.assertTrue(outcome.alerts)
        self.assertEqual(_excel_rows([outcome.mail]), [])

    def test_B07_01(self): self._unsupported("数据.xml", "XML")
    def test_B07_02(self): self._unsupported("票据.ofd", "OFD")
    def test_B07_03(self): self._unsupported("照片.png", "图片")
    def test_B07_04(self): self._unsupported("附件.txt", "其他")

    def test_B07_05(self):
        called = []
        def handler(kind):
            def fn(file, *_):
                called.append([kind, file.meta.file_type, file.meta.file_seq])
                return DocumentResult(file.meta.file_seq, "Unknown")
            return fn
        ports = StagePorts(document_handlers={k: handler(k) for k in ("XML", "OFD", "图片")})
        self.process(mail(("数据.xml", b"xml"), ("票据.ofd", b"ofd"),
                          ("照片.png", b"image")), ports=ports)
        self.assertEqual(called, [["XML", "XML", "01"], ["OFD", "OFD", "02"],
                                  ["图片", "图片", "03"]])
        self.observed["handler_calls"] = called

    def test_B08_01(self):
        outcome = self.process(mail(("发票.pdf", b"pdf"), ("数据.xml", b"xml")))
        self.assertEqual([f.file_type for f in outcome.mail.files], ["PDF", "XML"])
        self.assertEqual(len(outcome.mail.records), 1)
        self.assertEqual(outcome.mail.records[0].source.source_file_seq, ["01"])
        self.assertTrue(any("XML" in str(a["flags"]) for a in outcome.alerts))
        self.assertEqual(len(_excel_rows([outcome.mail])), 1)

    def test_B09_01(self):
        outcome = self.process(mail(subject="电子发票通知"))
        self.assertEqual((len(outcome.mail.files), len(outcome.mail.records)), (0, 0))
        self.assertTrue(any("无有效附件" in str(a["flags"]) for a in outcome.alerts))

    def test_B10_01(self):
        archive = zipped([("重复发票.pdf", b"same"), ("乙发票.pdf", b"other")])
        outcome = self.process(mail(("甲发票.pdf", b"same"), ("附件.zip", archive)))
        self.assertEqual([f.file_seq for f in outcome.mail.files], ["01", "02"])
        self.assertEqual(len(outcome.mail.dedup_skipped), 1)
        self.assertEqual(outcome.mail.dedup_skipped[0].filename, "重复发票.pdf")
        self.assertEqual(len(outcome.mail.records), 2)
        self.assertEqual(len(self.trace), 2)

    def test_B11_01(self):
        outcome = self.process(mail(("发票.pdf", b"pdf"), ("坏包.zip", b"not-a-zip")))
        self.assertEqual(len(outcome.mail.records), 1)
        self.assertIn("ZIP_EXTRACT_ERROR", outcome.mail.error_reason)
        self.assertTrue(outcome.alerts)

    def test_B11_02(self):
        archive = zipped([("../逃逸.pdf", b"unsafe"), ("正常.pdf", b"safe")])
        outcome = self.process(mail(("附件.zip", archive)))
        self.assertIn("ZIP_UNSAFE_PATH", outcome.mail.error_reason)
        self.assertEqual([f.original_name for f in outcome.mail.files], ["正常.pdf"])
        self.assertFalse(Path(self.tmp.name, "逃逸.pdf").exists())
        encrypted = mark_first_entry_encrypted(zipped([("加密.pdf", b"secret")]))
        second = self.process(mail(("加密附件.zip", encrypted)), mail_id="Msg2", uid="102")
        self.assertIn("ZIP_UNSUPPORTED_ENTRY", second.mail.error_reason)
        self.assertEqual(second.mail.files, [])

    def test_B11_03(self):
        depth3 = zipped([("里.pdf", b"deep")])
        depth2 = zipped([("第三层.zip", depth3)])
        outer = zipped([("第二层.zip", depth2), ("正常.pdf", b"safe")])
        outcome = self.process(mail(("附件.zip", outer)))
        self.assertIn("ZIP_DEPTH_LIMIT", outcome.mail.error_reason)
        self.assertEqual([f.original_name for f in outcome.mail.files], ["正常.pdf"])

    def test_B11_04(self):
        # 独立子例，避免文件编号及累计字节互相影响。
        for count in (50, 51):
            with self.subTest(count=count):
                archive = zipped([(f"{i:02d}.txt", f"payload-{i}".encode())
                                  for i in range(count)])
                item = MailRecord(mail_id="Msg1", mail_uid="101")
                files = collect_mail_files(mail(("附件.zip", archive)), item, self.cfg)
                self.assertEqual(len(files), min(count, 50))
                self.assertEqual(bool(item.error_reason), count == 51)
                if count == 51:
                    self.assertIn("ZIP_EXTRACT_ERROR", item.error_reason)
        limit = 15 * 1024 * 1024
        for size in (limit, limit + 1):
            with self.subTest(size=size):
                archive = zipped([("大文件.bin", b"x" * size)])
                item = MailRecord(mail_id="Msg2", mail_uid="102")
                files = collect_mail_files(mail(("附件.zip", archive)), item, self.cfg)
                self.assertEqual(len(files), 1 if size == limit else 0)
                self.assertEqual(bool(item.error_reason), size > limit)
                if size > limit:
                    self.assertIn("ZIP_UNSUPPORTED_ENTRY", item.error_reason)
        self.observed["boundaries"] = {"counts": [50, 51], "bytes": [limit, limit + 1]}

    def test_B12_01(self):
        messages = {"101": mail(subject="会议通知"), "102": mail(subject="项目周报")}
        stats, saved, fake = self.run_mailbox(messages)
        self.assertEqual((stats["unseen_total"], stats["skipped_no_invoice_keyword"],
                          stats["invoice_keyword_mails"]), (2, 2, 0))
        self.assertEqual(saved, [])
        self.assertFalse(any(x[0] == "fetch" and x[2] == "(BODY.PEEK[])" for x in fake.events))
        self.assertFalse(self.excel())

    def test_B12_02(self):
        messages = {"101": mail(("不相关.pdf", b"x"), subject="会议通知"),
                    "102": mail(("发票.pdf", b"y"), subject="电子发票")}
        stats, saved, fake = self.run_mailbox(messages)
        self.assertEqual((stats["unseen_total"], stats["skipped_no_invoice_keyword"],
                          stats["invoice_keyword_mails"]), (2, 1, 1))
        self.assertEqual([m["mail_uid"] for m in saved], ["102"])
        self.assertEqual(len(saved[0]["records"]), 1)
        self.assertFalse(any(x[0] == "fetch" and x[1] == "101" and x[2] == "(BODY.PEEK[])"
                             for x in fake.events))

    def _three_messages(self):
        return {"201": mail(("201发票.pdf", b"one")),
                "202": mail(("202发票.pdf", b"two")),
                "203": mail(("203发票.pdf", b"three"))}

    def _three_pdf(self, file, *_):
        uid = file.meta.original_name[:3]
        self.trace.append(["process_pdf", uid, file.meta.file_seq])
        return result(file, number=(uid * 6 + uid[:2]))

    def test_B13_01(self):
        messages = self._three_messages()
        fake = FakeImap(messages, fail_uid="202")
        stats, saved, fake = self.run_mailbox(messages, fake=fake, pdf=self._three_pdf)
        self.assertIsNone(stats["imap_error"])
        self.assertEqual((stats["unseen_total"], stats["invoice_keyword_mails"],
                          stats["skipped_no_invoice_keyword"]), (3, 3, 0))
        self.assertEqual([(m["mail_uid"], m["mail_id"], m["records"][0]["record_id"])
                          for m in saved], [("201", "Msg1", "Msg1-01"),
                                           ("202", "Msg2", "Msg2-01"),
                                           ("203", "Msg3", "Msg3-01")])
        self.assertEqual([r[1] for r in self.trace if r[0] == "process_pdf"],
                         ["201", "202", "203"])
        self.assertEqual(len([x for x in fake.events if x[0] == "fetch" and x[1] == "202"
                              and x[2] == "(BODY.PEEK[])"]), 2)
        self.assertEqual(len(self.excel()), 4)

    def test_B13_02(self):
        messages = {"301": mail(("发票.pdf", b"pdf"))}
        logs = []
        with patch("pipeline.imaplib.IMAP4_SSL", side_effect=FakeImap(messages).connect), \
             patch("pipeline.process_pdf", side_effect=self.pdf), \
             patch("pipeline.load_stage_ports", return_value=StagePorts()), \
             patch("pipeline.append_to_summary_excel", return_value=("EXCEL_PERMISSION_DENIED", None)):
            _, stats = run_pipeline(self.cfg, log=logs.append)
        saved = json.loads(Path(self.cfg.debug_file).read_text(encoding="utf-8"))
        self.assertIsNone(stats["imap_error"])
        self.assertIn("EXCEL_PERMISSION_DENIED", saved[0]["error_reason"])
        self.assertFalse(any("Excel 已写入" in line for line in logs))
        self.assertFalse(self.excel())
        self.observed = {"logs": logs, "error_reason": saved[0]["error_reason"]}

    def test_B13_03(self):
        messages = self._three_messages()
        fake = FakeImap(messages, fail_uid="202", fail_always=True)
        stats, saved, fake = self.run_mailbox(messages, fake=fake, pdf=self._three_pdf)
        self.assertTrue(stats["imap_error"])
        self.assertEqual((stats["unseen_total"], stats["invoice_keyword_mails"],
                          stats["skipped_no_invoice_keyword"]), (3, 2, 0))
        self.assertEqual([m["mail_uid"] for m in saved], ["201"])
        self.assertEqual(len(saved[0]["records"]), 1)
        self.assertFalse(self.excel())
        self.assertFalse(any(x[0] == "fetch" and x[1] == "203" for x in fake.events))


if __name__ == "__main__":
    unittest.main()
