"""T2-5 研发受控测试：附件实写、JSON 来源与实际文件正反查。"""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
import zipfile
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path

from attachment_processor import _safe_storage_path, collect_mail_files
from config import RunConfig, ensure_output_dirs
from data_model import (
    InvoiceRecord, ItineraryRecord, MailRecord, MatchedItinerary, SourceMeta,
    mail_to_dict,
)
from pipeline import _check_source_chain


def _message(*attachments: tuple[str, bytes, str]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = "电子发票"
    msg.set_content("合成测试")
    for name, content, subtype in attachments:
        msg.add_attachment(content, maintype="application", subtype=subtype, filename=name)
    return msg


def _zip(*entries: tuple[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return buffer.getvalue()


class SourceTraceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = RunConfig("imap.example.com", "test@example.com", "unused",
                             output_folder_name="run", output_root=self.temp.name)
        ensure_output_dirs(self.cfg)

    def _collect(self, mail_id: str, uid: str, msg: EmailMessage) -> MailRecord:
        mail = MailRecord(mail_id=mail_id, mail_uid=uid, subject="电子发票")
        collect_mail_files(msg, mail, self.cfg)
        return mail

    def test_direct_zip_dedup_and_bidirectional_record_lookup(self) -> None:
        invoice = b"synthetic invoice"
        itinerary = b"synthetic itinerary"
        archive = _zip(("目录/行程单.pdf", itinerary), ("重复.pdf", invoice),
                       ("保留.ofd", b"synthetic ofd"))
        msg = _message(("发票.pdf", invoice, "pdf"), ("票据.zip", archive, "zip"))
        mail = self._collect("Msg1", "701", msg)
        self.assertEqual([f.file_seq for f in mail.files], ["01", "02", "03"])
        self.assertEqual([f.original_name for f in mail.files],
                         ["发票.pdf", "目录/行程单.pdf", "保留.ofd"])
        self.assertEqual([f.file_type for f in mail.files], ["PDF", "PDF", "OFD"])
        self.assertEqual([d.filename for d in mail.dedup_skipped], ["重复.pdf"])
        mail.records.append(InvoiceRecord(record_id="Msg1-01",
                                          source=SourceMeta(["01"]),
                                          matched_itinerary=MatchedItinerary("02", "10")))
        mail.itineraries.append(ItineraryRecord(record_id="Msg1-02", match_amount="10"))
        other = self._collect("Msg2", "702", _message(("另一张.pdf", b"another", "pdf")))
        _check_source_chain([mail, other], self.cfg)
        self.assertEqual(other.files[0].file_seq, "01")
        saved = [mail_to_dict(item) for item in (mail, other)]
        self.assertEqual(saved[0]["mail_uid"], "701")
        self.assertEqual(saved[0]["records"][0]["source"]["source_file_seq"], ["01"])
        self.assertEqual(saved[0]["records"][0]["matched_itinerary"]["file_seq"], "02")
        # JSON 文件名 -> 实际字节；实际文件名 -> 唯一邮件 UID 和相关记录。
        expected_bytes = {"发票.pdf": invoice, "目录/行程单.pdf": itinerary,
                          "保留.ofd": b"synthetic ofd", "另一张.pdf": b"another"}
        inverse = {}
        for entry in saved:
            for item in entry["files"]:
                path = os.path.join(self.cfg.extract_dir, item["internal_name"])
                self.assertEqual(Path(path).read_bytes(), expected_bytes[item["original_name"]])
                self.assertNotIn(item["internal_name"], inverse)
                inverse[item["internal_name"]] = (entry["mail_uid"], item["file_seq"])
        self.assertEqual(inverse[saved[0]["files"][0]["internal_name"]], ("701", "01"))
        self.assertEqual(inverse[saved[1]["files"][0]["internal_name"]], ("702", "01"))

    def test_long_names_collision_and_zip_container_stay_within_120(self) -> None:
        long_name = "长" * 155 + ".pdf"
        zip_name = "压缩包" * 55 + ".zip"
        archive = _zip(("补充.xml", b"xml"))
        mail = self._collect("Msg3", "703", _message(
            (long_name, b"first", "pdf"), (long_name, b"second", "pdf"),
            (zip_name, archive, "zip")))
        self.assertEqual([f.file_seq for f in mail.files], ["01", "02", "03"])
        self.assertEqual(mail.files[0].original_name, long_name)
        self.assertEqual(mail.files[1].original_name, long_name)
        self.assertEqual(mail.files[2].original_name, "补充.xml")
        names = [f.internal_name for f in mail.files]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(len(name) <= 120 for name in names))
        self.assertTrue(all(name.endswith(ext) for name, ext in zip(names, (".pdf", ".pdf", ".xml"))))
        self.assertIn(hashlib.sha256(long_name.encode()).hexdigest()[:8], names[0])
        self.assertEqual(len(names[0]), 120)
        self.assertEqual(len(names[1]), 120)
        # 同一编号的目标名若已存在，重名后缀也必须计入总长。
        collision = _safe_storage_path(self.cfg.extract_dir, "Msg3_01_", long_name, 1)
        self.assertNotEqual(collision, os.path.join(self.cfg.extract_dir, names[0]))
        self.assertTrue(os.path.basename(collision).endswith("(1).pdf"))
        self.assertEqual(len(os.path.basename(collision)), 120)
        self.assertTrue(all(len(name) <= 120 for name in os.listdir(self.cfg.zips_dir)))
        _check_source_chain([mail], self.cfg)

    def test_blank_invalid_and_encoded_names_preserve_original_and_extension(self) -> None:
        encoded = "=?utf-8?b?5Y+R56WoLnBkZg==?="  # 发票.pdf
        mail = self._collect("Msg4", "704", _message(
            ("", b"blank pdf", "pdf"), ("CON.ofd", b"ofd", "ofd"),
            ("a<bad>?.xml", b"xml", "xml"), (encoded, b"named pdf", "pdf")))
        self.assertEqual([f.original_name for f in mail.files],
                         ["", "CON.ofd", "a<bad>?.xml", "发票.pdf"])
        self.assertEqual([f.file_type for f in mail.files], ["PDF", "OFD", "XML", "PDF"])
        self.assertTrue(mail.files[0].internal_name.endswith("Attachment_1.pdf"))
        self.assertTrue(mail.files[1].internal_name.endswith("Attachment_2.ofd"))
        self.assertNotIn("<", mail.files[2].internal_name)
        self.assertTrue(mail.files[3].internal_name.endswith("发票.pdf"))
        self.assertTrue(all(item.exported_name == "" for item in mail.files))
        _check_source_chain([mail], self.cfg)

    def test_exact_120_character_boundary_and_one_character_over(self) -> None:
        prefix_length = len("Msg9_01_")
        stem_length = 120 - prefix_length - len(".pdf")
        exact = "甲" * stem_length + ".pdf"
        over = "乙" * (stem_length + 1) + ".pdf"
        mail = self._collect("Msg9", "709", _message(
            (exact, b"exact", "pdf"), (over, b"over", "pdf")))
        self.assertEqual(mail.files[0].internal_name, "Msg9_01_" + exact)
        self.assertEqual(len(mail.files[0].internal_name), 120)
        self.assertEqual(len(mail.files[1].internal_name), 120)
        self.assertIn(hashlib.sha256(over.encode()).hexdigest()[:8],
                      mail.files[1].internal_name)
        self.assertEqual(mail.files[1].original_name, over)
        _check_source_chain([mail], self.cfg)

    def test_source_checker_rejects_broken_file_and_record_references(self) -> None:
        mail = self._collect("Msg5", "705", _message(("发票.pdf", b"pdf", "pdf")))
        mail.records.append(InvoiceRecord(record_id="Msg5-01", source=SourceMeta(["01"])))
        _check_source_chain([mail], self.cfg)
        mail.records.append(InvoiceRecord(record_id="Msg5-01", source=SourceMeta(["01"])))
        with self.assertRaisesRegex(ValueError, "记录编号重复"):
            _check_source_chain([mail], self.cfg)
        mail.records.pop()
        mail.records[0].source.source_file_seq = ["02"]
        with self.assertRaisesRegex(ValueError, "来源链断裂"):
            _check_source_chain([mail], self.cfg)
        mail.records[0].source.source_file_seq = ["01"]
        os.remove(os.path.join(self.cfg.extract_dir, mail.files[0].internal_name))
        with self.assertRaisesRegex(ValueError, "内部文件不存在"):
            _check_source_chain([mail], self.cfg)

    def test_missing_itinerary_and_duplicate_uid_are_rejected(self) -> None:
        first = self._collect("Msg6", "706", _message(("发票.pdf", b"one", "pdf")))
        first.records.append(InvoiceRecord(record_id="Msg6-01", source=SourceMeta(["01"]),
                                           matched_itinerary=MatchedItinerary("01", "10")))
        with self.assertRaisesRegex(ValueError, "匹配的行程单未保存"):
            _check_source_chain([first], self.cfg)
        first.records[0].matched_itinerary = None
        second = self._collect("Msg7", "706", _message(("票据.pdf", b"two", "pdf")))
        with self.assertRaisesRegex(ValueError, "IMAP UID"):
            _check_source_chain([first, second], self.cfg)

    def test_direct_attachment_limit_keeps_01_to_99(self) -> None:
        msg = _message(*[(f"文件{i}.pdf", bytes([i]), "pdf") for i in range(100)])
        mail = self._collect("Msg8", "708", msg)
        self.assertEqual(len(mail.files), 99)
        self.assertEqual(mail.files[0].file_seq, "01")
        self.assertEqual(mail.files[-1].file_seq, "99")
        self.assertIn("无法分配两位文件序号", mail.error_reason)
        _check_source_chain([mail], self.cfg)


if __name__ == "__main__":
    unittest.main()
