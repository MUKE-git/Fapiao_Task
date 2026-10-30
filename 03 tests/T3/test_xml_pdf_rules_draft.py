"""研发制作的固定样本测试，待用户审核；不是正式产品验收。"""
import unittest
from data_model import FileRecord, MailRecord, InvoiceRecord, InvoiceFields, SourceMeta
from processor_contracts import DocumentResult
from voucher_matcher import combine_vouchers
from fallback_extractor import build_fallback_audit_result
from dedup_arbitrator import arbitrate_records

NO = "26447000000342589935"
def item(seq, **fields):
    return DocumentResult(seq, "Invoice", fields, [seq])
def xml(seq, **fields):
    r = item(seq, **fields)
    r.field_evidence = {k: [{"file_seq": seq, "raw": str(v), "normalized": str(v), "location": "/xml/" + k}] for k, v in fields.items()}
    return r
def mail(*types):
    return MailRecord(mail_id="M", files=[FileRecord(file_seq=f"{i:02}", file_type=t) for i, t in enumerate(types, 1)])

class Rules(unittest.TestCase):
    def test_same_number_pdf_disagrees_but_xml_wins_without_conflict(self):
        out = combine_vouchers(mail("XML", "PDF"), [xml("01", invoice_number=NO, receipt_type="客运服务费", total_amount="100"), item("02", invoice_number=NO, receipt_type="客运服务费 94.126214 1 94", total_amount="200")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].fields["receipt_type"], "客运服务费")
        self.assertEqual(out[0].fields["total_amount"], "100")
        self.assertFalse(out[0].conflicts)
        self.assertEqual(out[0].source_file_seq, ["01", "02"])
    def test_missing_xml_field_not_filled_from_pdf(self):
        out = combine_vouchers(mail("XML", "PDF"), [xml("01", invoice_number=NO), item("02", invoice_number=NO, seller="PDF销售方")])
        self.assertEqual(len(out), 1)
        self.assertNotEqual(out[0].fields.get("seller"), "PDF销售方")
    def test_distinct_invoice_numbers_do_not_merge(self):
        out = combine_vouchers(mail("XML", "PDF"), [xml("01", invoice_number=NO), item("02", invoice_number="26522000000868668241")])
        self.assertEqual(len(out), 2)
    def test_xml_image_not_compared_or_used_to_fill_fields(self):
        out = combine_vouchers(mail("XML", "图片", "PDF"), [xml("01", invoice_number=NO, total_amount="293.52"), item("02", invoice_number=NO, total_amount="284.97", seller="图片销售方"), item("03", invoice_number=NO, total_amount="999")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].fields["total_amount"], "293.52")
        self.assertNotIn("seller", out[0].fields)
        self.assertFalse(out[0].conflicts)
        self.assertEqual(out[0].source_file_seq, ["01", "02", "03"])
    def test_cross_mail_xml_chosen_even_when_other_first(self):
        first = mail("图片")
        first.mail_id = "M1"
        first.records = [InvoiceRecord(record_id="M1-01", fields=InvoiceFields(invoice_number=NO, total_amount="284.97", seller="图片销售方"), source=SourceMeta(source_file_seq=["01"]))]
        second = mail("XML")
        second.mail_id = "M2"
        evidence = {"invoice_number": [{"file_seq": "01", "normalized": NO, "location": "/InvoiceNumber"}]}
        second.records = [InvoiceRecord(record_id="M2-01", fields=InvoiceFields(invoice_number=NO, total_amount="293.52"), source=SourceMeta(source_file_seq=["01"], field_evidence=evidence))]
        arbitrate_records([first, second])
        self.assertIn("M1-01", first.excel_skip_record_ids)
        self.assertNotIn("M2-01", second.excel_skip_record_ids)
        self.assertEqual(second.records[0].fields.total_amount, "293.52")
        self.assertNotEqual(second.records[0].fields.seller, "图片销售方")

    def test_regex(self):
        examples = [("*运输服务*客运服务费 94.126214 1 94.13 3% 2.82", "客运服务费"), ("*运输服务*客运服务费", "客运服务费"), ("*服务*3D 打印服务  100.00", "3D 打印服务"), ("服务名称：维修服务 12.30", "维修服务"), ("*运输服务*客运服务费 ￥12.30", "客运服务费")]
        for raw, expected in examples:
            with self.subTest(raw=raw):
                self.assertEqual(build_fallback_audit_result([{"identified_type": "Invoice", "text_snapshot": raw}])["receipt_type"], expected)

if __name__ == "__main__": unittest.main()
