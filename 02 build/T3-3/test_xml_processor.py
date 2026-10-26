"""T3-3 fixed synthetic XML tests; inputs are developer-made, not approved cases."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from attachment_processor import CollectedFile
from config import RunConfig
from data_model import FileRecord, NOT_FOUND
from xml_processor import process_xml


def sample(*, number="26447000000342589935", header_number=None,
           date="2026-04-27", amount="121.74", items=None) -> bytes:
    header_number = number if header_number is None else header_number
    items = ["*运输服务*客运服务费"] if items is None else items
    item_nodes = "".join(f"<IssuItemInformation><ItemName>{x}</ItemName></IssuItemInformation>"
                         for x in items)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<EInvoice><Header><EIid>{header_number}</EIid><InherentLabel>
<EInvoiceType><LabelName>电子发票</LabelName></EInvoiceType>
<GeneralOrSpecialVAT><LabelName>普通发票</LabelName></GeneralOrSpecialVAT>
</InherentLabel></Header><EInvoiceData><SellerInformation>
<SellerName>示例销售方</SellerName></SellerInformation>
<BasicInformation><TotalTax-includedAmount>{amount}</TotalTax-includedAmount>
</BasicInformation>{item_nodes}</EInvoiceData><TaxSupervisionInfo>
<InvoiceNumber>{number}</InvoiceNumber><IssueTime>{date}</IssueTime>
</TaxSupervisionInfo></EInvoice>""".encode("utf-8")


class XmlProcessorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = RunConfig("", "", "", output_root=self.tmp.name)
        self.path = Path(self.tmp.name) / "a.xml"

    def process(self, content: bytes):
        self.path.write_bytes(content)
        item = CollectedFile(FileRecord(file_seq="01", file_type="XML",
                                        original_name="a.xml", internal_name="a.xml"),
                             str(self.path))
        return process_xml(item, self.cfg, "示例主题", "2026-04-27 10:00")

    def test_fields_and_xpath_evidence(self):
        result = self.process(sample())
        self.assertEqual(result.role, "Invoice")
        self.assertEqual(result.fields["invoice_number"], "26447000000342589935")
        self.assertEqual(result.fields["total_amount"], "121.74")
        self.assertEqual(result.fields["invoice_type"], "电子发票（普通发票）")
        self.assertEqual(result.fields["receipt_type"], "客运服务费")
        self.assertEqual(result.source_file_seq, ["01"])
        self.assertEqual(result.field_evidence["invoice_date"][0]["location"],
                         "/EInvoice/TaxSupervisionInfo/IssueTime")
        self.assertFalse(result.error_reason)

    def test_conflicting_number_is_not_selected(self):
        result = self.process(sample(header_number="26327000000518184747"))
        self.assertEqual(result.fields["invoice_number"], NOT_FOUND)
        self.assertIn("XML_CONFLICT_INVOICE_NUMBER", result.error_reason)
        self.assertEqual(len(result.field_evidence["invoice_number"]), 2)

    def test_multiple_different_items_are_not_arbitrarily_chosen(self):
        result = self.process(sample(items=["*运输服务*客运服务费", "*美容*护理费"]))
        self.assertEqual(result.fields["receipt_type"], NOT_FOUND)
        self.assertIn("XML_CONFLICT_RECEIPT_TYPE", result.error_reason)

    def test_invalid_date_and_four_decimal_amount(self):
        result = self.process(sample(date="2026-02-30", amount="12.3456"))
        self.assertEqual(result.fields["invoice_date"], "2026-02-30")
        self.assertIn("XML_INVALID_INVOICE_DATE", result.error_reason)
        self.assertEqual(result.fields["total_amount"], "12.3456")

    def test_dtd_and_utf16_dtd_rejected(self):
        content = b'<!DOCTYPE x [<!ENTITY x "boom">]><EInvoice>&x;</EInvoice>'
        for payload in (content, content.decode().encode("utf-16")):
            self.assertEqual(self.process(payload).error_reason,
                             "XML_DTD_OR_ENTITY_FORBIDDEN")

    def test_wrong_template_and_broken_xml(self):
        self.assertEqual(self.process(b"<Other/>").error_reason,
                         "XML_UNSUPPORTED_TEMPLATE")
        self.assertEqual(self.process(b"<EInvoice>").error_reason, "XML_PARSE_ERROR")

    def test_size_limit(self):
        self.assertEqual(self.process(b" " * (2 * 1024 * 1024 + 1)).error_reason,
                         "XML_SIZE_LIMIT")


if __name__ == "__main__":
    unittest.main()
