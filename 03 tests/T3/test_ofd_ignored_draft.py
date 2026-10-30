"""测试工程师制作、待用户审核：OFD 忽略规则受控整链测试。

本稿由集成负责人代拟和执行，尚未经独立测试工程师核验。
"""
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from attachment_processor import collect_mail_files
from config import RunConfig, ensure_output_dirs
from data_model import MailRecord
from mail_link_downloader import DownloadedFile
from pipeline import run_pipeline
from stage_ports import load_stage_ports
from test_real_local_chain_draft import FakeImap, XML, PDF, OFD_A, NUMBER_A, mail, controlled_pdf_ai

def ofd_bytes():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("OFD.xml", "<OFD/>")
    return out.getvalue()

def container_bytes(name, content):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(name, content)
    return out.getvalue()

class IgnoreOfdDraft(unittest.TestCase):
    def run_case(self, msg, links=()):
        with tempfile.TemporaryDirectory() as root:
            cfg = RunConfig("imap.test", "user", "password", output_root=root,
                            dashscope_api_key="fixed-no-network")
            imap = FakeImap(msg)
            with patch("pipeline.imaplib.IMAP4_SSL", return_value=imap), \
                 patch("pipeline.download_links", return_value=(list(links), [])), \
                 patch("pdf_processor.call_ai_audit_and_rename", side_effect=controlled_pdf_ai), \
                 patch("ofd_processor.process_ofd") as ofd:
                _, stats = run_pipeline(cfg)
            ofd.assert_not_called()
            self.assertIsNone(stats["run_error"])
            self.assertIsNone(stats["imap_error"])
            self.assertEqual(stats["delivery_status"], "ready")
            self.assertNotIn("OFD", load_stage_ports(cfg).document_handlers)
            with zipfile.ZipFile(io.BytesIO(stats["delivery_zip_bytes"])) as archive:
                self.assertIsNone(archive.testzip())
                result = json.loads(archive.read("Invoice_Task/task_debug.json"))
                names = archive.namelist()
            self.assertFalse(any(n.lower().endswith(".ofd") for n in names))
            return stats, result, names

    @unittest.skipUnless(XML.is_file(), "缺用户 XML A 原件")
    def test_ofd_ignored_for_attachment_and_zip_with_real_xml(self):
        for where in ("attachment", "zip"):
            with self.subTest(where=where):
                msg = mail(XML)
                links = []
                if where == "attachment":
                    msg.add_attachment(ofd_bytes(), maintype="application", subtype="ofd",
                                       filename="测试工程师制作.ofd")
                elif where == "zip":
                    msg.add_attachment(container_bytes("伪装.zip", ofd_bytes()),
                                       maintype="application", subtype="zip",
                                       filename="测试工程师制作.zip")
                stats, mails, names = self.run_case(msg, links)
                self.assertEqual(len(mails[0]["files"]), 1)
                self.assertEqual(mails[0]["files"][0]["file_type"], "XML")
                self.assertEqual(mails[0]["records"][0]["fields"]["invoice_number"], NUMBER_A)
                self.assertEqual(mails[0]["records"][0]["source"]["source_file_seq"], ["01"])
                self.assertEqual(stats["summary_row_count"], 1)
                self.assertNotIn("OFD_CONVERSION", mails[0]["error_reason"])
                self.assertFalse(any(n.lower().endswith(".pdf") for n in names))

    @unittest.skipUnless(XML.is_file() and PDF.is_file() and OFD_A.is_file(),
                         "缺用户 XML、PDF 或 OFD 原件")
    def test_real_xml_pdf_and_ofd_keep_only_xml_and_original_pdf(self):
        stats, mails, names = self.run_case(mail(XML, PDF, OFD_A))
        self.assertEqual([f["file_type"] for f in mails[0]["files"]], ["XML", "PDF"])
        self.assertEqual(len(mails[0]["records"]), 1)
        record = mails[0]["records"][0]
        self.assertEqual(record["source"]["source_file_seq"], ["01", "02"])
        self.assertEqual(record["fields"]["invoice_number"], NUMBER_A)
        self.assertEqual(stats["summary_row_count"], 1)
        pdfs = [n for n in names if n.lower().endswith(".pdf")]
        self.assertEqual(len(pdfs), 1)
        with zipfile.ZipFile(io.BytesIO(stats["delivery_zip_bytes"])) as archive:
            self.assertEqual(archive.read(pdfs[0]), PDF.read_bytes())
        self.assertNotIn("OFD_CONVERSION", mails[0]["error_reason"])

    @unittest.skipUnless(XML.is_file() and PDF.is_file(), "缺用户 XML 或 PDF 原件")
    def test_real_link_discovery_skips_explicit_ofd_and_keeps_xml_pdf(self):
        from mail_link_downloader import download_links, _Response
        msg = mail(body="测试工程师制作、待用户审核")
        msg.add_alternative(
            '<a href="https://e.example/get?jflx=ofd">OFD</a>'
            '<a href="https://e.example/get?jflx=xml">XML</a>'
            '<a href="https://e.example/get?jflx=pdf">PDF</a>',
            subtype="html",
        )
        calls = []
        def resolver(host, port, **kwargs):
            return [(0, 0, 0, "", ("8.8.8.8", port))]
        def requester(url, *_args):
            calls.append(url)
            if "jflx=xml" in url:
                return _Response(200, {"content-type": "application/xml"}, XML.read_bytes())
            if "jflx=pdf" in url:
                return _Response(200, {"content-type": "application/pdf"}, PDF.read_bytes())
            raise AssertionError("明确 OFD 链接不得访问")
        def fixed_http(msg, **kwargs):
            return download_links(msg, resolver=resolver, requester=requester, **kwargs)

        with tempfile.TemporaryDirectory() as root:
            cfg = RunConfig("imap.test", "user", "password", output_root=root,
                            dashscope_api_key="fixed-no-network")
            with patch("pipeline.imaplib.IMAP4_SSL", return_value=FakeImap(msg)), \
                 patch("pipeline.download_links", side_effect=fixed_http), \
                 patch("pdf_processor.call_ai_audit_and_rename", side_effect=controlled_pdf_ai), \
                 patch("ofd_processor.process_ofd") as ofd:
                _, stats = run_pipeline(cfg)
            ofd.assert_not_called()
            self.assertEqual(len(calls), 2)
            self.assertFalse(any("jflx=ofd" in url for url in calls))
            self.assertEqual(stats["delivery_status"], "ready")
            with zipfile.ZipFile(io.BytesIO(stats["delivery_zip_bytes"])) as archive:
                mails = json.loads(archive.read("Invoice_Task/task_debug.json"))
                self.assertEqual([f["file_type"] for f in mails[0]["files"]], ["XML", "PDF"])
                self.assertEqual(mails[0]["records"][0]["fields"]["invoice_number"], NUMBER_A)
                pdfs = [n for n in archive.namelist() if n.lower().endswith(".pdf")]
                self.assertEqual(len(pdfs), 1)
                self.assertEqual(archive.read(pdfs[0]), PDF.read_bytes())

    @unittest.skipUnless(XML.is_file() and PDF.is_file(), "缺用户 XML 或 PDF 原件")
    def test_existing_attachment_prevents_body_link_fetch(self):
        msg = mail(XML, body="发票下载 https://e.example/extra.pdf")
        link = DownloadedFile("extra.pdf", PDF.read_bytes(), "正文链接 example.test（PDF）", "PDF")
        with tempfile.TemporaryDirectory() as root:
            cfg = RunConfig("imap.test", "user", "password", output_root=root,
                            dashscope_api_key="fixed-no-network")
            with patch("pipeline.imaplib.IMAP4_SSL", return_value=FakeImap(msg)), \
                 patch("pipeline.download_links", return_value=([link], [])) as fetch:
                _, stats = run_pipeline(cfg)
            fetch.assert_not_called()
            self.assertEqual(stats["summary_row_count"], 1)
            with zipfile.ZipFile(io.BytesIO(stats["delivery_zip_bytes"])) as archive:
                mails = json.loads(archive.read("Invoice_Task/task_debug.json"))
                self.assertEqual([f["file_type"] for f in mails[0]["files"]], ["XML"])
                self.assertFalse(any(n.lower().endswith(".pdf") for n in archive.namelist()))

    @unittest.skipUnless(XML.is_file(), "缺用户 XML 原件")
    def test_only_ofd_attachment_does_not_block_body_link(self):
        msg = mail(body="测试链接")
        msg.add_attachment(ofd_bytes(), maintype="application", subtype="ofd",
                           filename="测试工程师制作.ofd")
        link = DownloadedFile("发票.xml", XML.read_bytes(), "正文链接 example.test（XML）", "XML")
        stats, mails, _ = self.run_case(msg, [link])
        self.assertEqual([f["file_type"] for f in mails[0]["files"]], ["XML"])
        self.assertEqual(mails[0]["files"][0]["acquisition"], "正文链接")
        self.assertEqual(mails[0]["records"][0]["fields"]["invoice_number"], NUMBER_A)
        self.assertEqual(stats["summary_row_count"], 1)

    def test_ofd_marker_after_first_thousand_archive_entries_is_ignored(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            for i in range(1001):
                archive.writestr(f"f{i}.txt", "")
            archive.writestr("OFD.xml", "<OFD/>")
        msg = mail()
        msg.add_attachment(payload.getvalue(), maintype="application", subtype="zip",
                           filename="测试工程师制作.zip")
        stats, mails, _ = self.run_case(msg)
        self.assertEqual(mails[0]["files"], [])
        self.assertEqual(mails[0]["records"], [])
        self.assertEqual(stats["summary_row_count"], 0)

    def test_generic_link_returning_ofd_is_downloaded_then_ignored(self):
        from mail_link_downloader import download_links, _Response
        msg = mail(body="测试链接")
        msg.add_alternative('<a href="https://e.example/get">下载发票</a>', subtype="html")
        calls = []
        def resolver(host, port, **kwargs):
            return [(0, 0, 0, "", ("8.8.8.8", port))]
        def requester(url, *_args):
            calls.append(url)
            return _Response(200, {"content-type": "application/ofd"}, ofd_bytes())
        def fixed_http(msg, **kwargs):
            return download_links(msg, resolver=resolver, requester=requester, **kwargs)
        with tempfile.TemporaryDirectory() as root:
            cfg = RunConfig("imap.test", "user", "password", output_root=root,
                            dashscope_api_key="fixed-no-network")
            with patch("pipeline.imaplib.IMAP4_SSL", return_value=FakeImap(msg)), \
                 patch("pipeline.download_links", side_effect=fixed_http) as fetch, \
                 patch("ofd_processor.process_ofd") as ofd:
                _, stats = run_pipeline(cfg)
            fetch.assert_called_once()
            ofd.assert_not_called()
            self.assertEqual(len(calls), 1)
            self.assertEqual(stats["summary_row_count"], 0)
            with zipfile.ZipFile(io.BytesIO(stats["delivery_zip_bytes"])) as archive:
                mails = json.loads(archive.read("Invoice_Task/task_debug.json"))
                self.assertEqual(mails[0]["files"], [])
                self.assertEqual(mails[0]["records"], [])

    @unittest.skipUnless(XML.is_file(), "缺用户 XML 原件")
    def test_zip_attachment_containing_only_ofd_still_uses_attachment_branch(self):
        msg = mail(body="发票下载 https://e.example/invoice.xml")
        msg.add_attachment(container_bytes("ignored.ofd", ofd_bytes()),
                           maintype="application", subtype="zip", filename="发票.zip")
        link = DownloadedFile("发票.xml", XML.read_bytes(), "正文链接 example.test（XML）", "XML")
        with tempfile.TemporaryDirectory() as root:
            cfg = RunConfig("imap.test", "user", "password", output_root=root,
                            dashscope_api_key="fixed-no-network")
            with patch("pipeline.imaplib.IMAP4_SSL", return_value=FakeImap(msg)), \
                 patch("pipeline.download_links", return_value=([link], [])) as fetch:
                _, stats = run_pipeline(cfg)
            fetch.assert_not_called()
            self.assertEqual(stats["summary_row_count"], 0)
            with zipfile.ZipFile(io.BytesIO(stats["delivery_zip_bytes"])) as archive:
                mails = json.loads(archive.read("Invoice_Task/task_debug.json"))
                self.assertEqual(mails[0]["files"], [])

    def test_only_ofd_has_no_record_and_reports_no_valid_file(self):
        msg = mail()
        msg.add_attachment(ofd_bytes(), maintype="application", subtype="ofd",
                           filename="测试工程师制作.ofd")
        stats, mails, names = self.run_case(msg)
        self.assertEqual(mails[0]["files"], [])
        self.assertEqual(mails[0]["records"], [])
        self.assertEqual(stats["summary_row_count"], 0)
        flags = [f for a in stats["not_found_alerts"] for f in a.get("flags", [])]
        self.assertTrue(any("无有效附件" in f for f in flags))
        self.assertFalse(any(n.lower().endswith(".pdf") for n in names))

    def test_real_signature_wins_over_misleading_extension(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = RunConfig("test", "test", "test", output_root=root)
            ensure_output_dirs(cfg)
            msg = mail()
            msg.add_attachment(b"%PDF-test", maintype="application", subtype="pdf",
                               filename="测试工程师制作.ofd")
            msg.add_attachment(ofd_bytes(), maintype="application", subtype="pdf",
                               filename="测试工程师制作.pdf")
            record = MailRecord(mail_id="Msg1", mail_uid="test")
            collected = collect_mail_files(msg, record, cfg)
            self.assertEqual(len(collected), 1)
            self.assertEqual(collected[0].meta.file_type, "PDF")
            self.assertEqual(collected[0].meta.original_name, "测试工程师制作.ofd")
            self.assertEqual(collected[0].meta.file_seq, "01")

if __name__ == "__main__":
    unittest.main()
