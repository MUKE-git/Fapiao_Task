"""T3-4 development tests. Synthetic materials are not approved business cases."""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from attachment_processor import CollectedFile
from config import RunConfig, ensure_output_dirs
from data_model import FileRecord
from ofd_processor import process_ofd


DOWNLOADS = Path(r"C:\Users\lenovo\Downloads")


def synthetic_ofd(*, extra: dict[str, str] | None = None) -> bytes:
    def text_object(object_id: str, y: int, value: str) -> str:
        return (f'<TextObject ID="{object_id}" Boundary="10 {y} 160 5" Size="3">'
                f'<TextCode X="0" Y="4">{value}</TextCode></TextObject>')
    pages = "".join((text_object("0", 10, "电子发票（普通发票）"),
                     text_object("1", 20, "26447000000342589935"),
                     text_object("2", 30, "2026年04月27日"),
                     text_object("3", 40, "南京领行科技股份有限公司广州分公司"),
                     text_object("4", 50, "121.74"),
                     text_object("5", 60, "*运输服务*客运服务费")))
    files = {
        "OFD.xml": '<OFD Version="1.1"><DocBody><DocRoot>/Doc_0/Document.xml</DocRoot></DocBody></OFD>',
        "Doc_0/Document.xml": ('<Document><Pages><Page ID="1" BaseLoc="Pages/Page_0/Content.xml"/>'
                                '</Pages><CustomTags>Tags/CustomTags.xml</CustomTags></Document>'),
        "Doc_0/Pages/Page_0/Content.xml": ('<Page><Area><PhysicalBox>0 0 210 100</PhysicalBox>'
                                             '</Area><Content><Layer>' + pages + '</Layer></Content></Page>'),
        "Doc_0/Tags/CustomTags.xml": '<CustomTags><CustomTag><FileLoc>CustomTag.xml</FileLoc></CustomTag></CustomTags>',
        "Doc_0/Tags/CustomTag.xml": ('<eInvoice><InvoiceNo><ObjectRef>1</ObjectRef></InvoiceNo>'
                                      '<IssueDate><ObjectRef>2</ObjectRef></IssueDate>'
                                      '<Seller><SellerName><ObjectRef>3</ObjectRef></SellerName></Seller>'
                                      '<TaxInclusiveTotalAmount><ObjectRef>4</ObjectRef></TaxInclusiveTotalAmount>'
                                      '<GoodsInfos><Item><ObjectRef>5</ObjectRef></Item></GoodsInfos></eInvoice>'),
    }
    files.update(extra or {})
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content.encode("utf-8"))
    return data.getvalue()


class OfdProcessorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cfg = RunConfig("", "", "", output_root=str(self.root))
        ensure_output_dirs(self.cfg)

    def run_ofd(self, data: bytes):
        path = self.root / "input.ofd"
        path.write_bytes(data)
        item = CollectedFile(FileRecord("01", file_type="OFD",
                                        original_name="input.ofd",
                                        internal_name="Msg1_01_input.ofd"), str(path))
        return process_ofd(item, self.cfg, "", "", "")

    def test_synthetic_invoice_fields_and_checked_pdf(self):
        result = self.run_ofd(synthetic_ofd())
        self.assertEqual(result.role, "Invoice")
        self.assertEqual(result.fields["total_amount"], "121.74")
        self.assertEqual(result.fields["receipt_type"], "客运服务费")
        self.assertTrue(result.pdf_path, result.error_reason)
        with pdfplumber.open(result.pdf_path) as pdf:
            self.assertEqual(len(pdf.pages), 1)
            self.assertIn("26447000000342589935", pdf.pages[0].extract_text())

    def test_unsafe_zip_entry_stays_unknown_without_pdf(self):
        result = self.run_ofd(synthetic_ofd(extra={"../escape.xml": "bad"}))
        self.assertEqual(result.role, "Unknown")
        self.assertIn("OFD_UNSAFE_PATH", result.error_reason)
        self.assertFalse(result.pdf_path)

    def test_dtd_is_rejected(self):
        content = synthetic_ofd(extra={"OFD.xml":
            "<!DOCTYPE OFD [<!ENTITY x SYSTEM 'file:///x'>]>"
            '<OFD Version="1.1"><DocBody><DocRoot>/Doc_0/Document.xml</DocRoot></DocBody></OFD>'})
        result = self.run_ofd(content)
        self.assertEqual(result.role, "Unknown")
        self.assertIn("OFD_FORBIDDEN_XML_DECLARATION", result.error_reason)

    def test_real_a_extracts_fields_but_missing_resource_blocks_delivery(self):
        path = DOWNLOADS / "T3出行电子发票(OFD)A.ofd"
        if not path.is_file():
            self.skipTest("user OFD A sample not available")
        result = self.run_ofd(path.read_bytes())
        self.assertEqual(result.fields["invoice_number"], "26447000000342589935")
        self.assertEqual(result.fields["total_amount"], "121.74")
        self.assertIn("OFD_MISSING_RESOURCE", result.error_reason)
        self.assertFalse(result.pdf_path)

    def test_real_complete_ofd_converts_to_checked_pdf(self):
        path = next((p for p in DOWNLOADS.glob("*.ofd") if "呼我出行" in p.name), None)
        if path is None:
            self.skipTest("complete local OFD sample not available")
        result = self.run_ofd(path.read_bytes())
        self.assertEqual(result.fields["invoice_number"], "26442000004589632231")
        self.assertEqual(result.fields["total_amount"], "48.31")
        self.assertTrue(result.pdf_path, result.error_reason)
        with pdfplumber.open(result.pdf_path) as pdf:
            self.assertEqual(len(pdf.pages), 1)
            self.assertGreaterEqual(len(pdf.pages[0].images), 1)
            self.assertIn("48.31", pdf.pages[0].extract_text())


if __name__ == "__main__":
    unittest.main()
