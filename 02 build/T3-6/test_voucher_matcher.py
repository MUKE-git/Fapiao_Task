"""研发固定结果测试；样本与预期由研发制作，不是用户验收用例。"""
from __future__ import annotations

from decimal import Decimal
import unittest

from data_model import FileRecord, MailRecord
from processor_contracts import DocumentResult
from voucher_matcher import combine_vouchers


NO = "26447000000342589935"
OTHER = "26522000000868668241"


def result(seq: str, **fields) -> DocumentResult:
    return DocumentResult(seq, "Invoice", fields, [seq])


def xml(seq: str, **fields) -> DocumentResult:
    item = result(seq, **fields)
    item.field_evidence = {
        key: [{"file_seq": seq, "raw": str(value), "normalized": str(value),
               "location": f"/EInvoice/{key}"}]
        for key, value in fields.items()
    }
    return item


def mail(*types: str) -> MailRecord:
    return MailRecord(mail_id="Msg1", files=[
        FileRecord(file_seq=f"{i:02d}", file_type=kind)
        for i, kind in enumerate(types, 1)
    ])


class VoucherMatcherTest(unittest.TestCase):
    def test_xml_pdf_ofd_same_invoice_and_sources(self):
        source = [
            xml("01", invoice_number=NO, invoice_date="2026-04-27",
                total_amount="121.74", seller="甲公司"),
            result("02", invoice_number=NO, invoice_date="2026-04-27",
                   total_amount=Decimal("121.74"), seller="甲公司"),
            result("03", invoice_number=NO, invoice_date="2026-04-27",
                   total_amount="121.74", seller="甲公司"),
        ]
        out = combine_vouchers(mail("XML", "PDF", "OFD"), source)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].file_seq, "01")
        self.assertEqual(out[0].source_file_seq, ["01", "02", "03"])
        self.assertEqual(out[0].fields["total_amount"], "121.74")
        self.assertFalse(out[0].conflicts)
        self.assertEqual(len(source[0].source_file_seq), 1)  # no input mutation

    def test_different_number_returns_two_candidates(self):
        out = combine_vouchers(mail("XML", "图片"), [
            xml("01", invoice_number=NO, invoice_date="2026-04-27"),
            result("02", invoice_number=OTHER, invoice_date="2026-04-27")])
        self.assertEqual([x.file_seq for x in out], ["01", "02"])

    def test_xml_field_wins_without_pdf_field_comparison(self):
        out = combine_vouchers(mail("XML", "PDF"), [
            xml("01", invoice_number=NO, total_amount="121.74"),
            result("02", invoice_number=NO, total_amount="131.74")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].fields["total_amount"], "121.74")
        self.assertFalse(out[0].conflicts)
        self.assertNotIn("人工核对", out[0].error_reason)

    def test_no_xml_conflict_retains_distinct_candidates(self):
        out = combine_vouchers(mail("PDF", "OFD"), [
            result("01", invoice_number=NO, total_amount="121.74"),
            result("02", invoice_number=NO, total_amount="131.74")])
        self.assertEqual(len(out), 2)
        self.assertEqual([x.source_file_seq for x in out], [["01"], ["02"]])
        self.assertTrue(all(x.conflicts for x in out))

    def test_xml_without_field_evidence_cannot_settle_conflict(self):
        out = combine_vouchers(mail("XML", "PDF"), [
            result("01", invoice_number=NO, total_amount="121.74"),
            result("02", invoice_number=NO, total_amount="131.74")])
        self.assertEqual(len(out), 2)

    def test_numberless_requires_unique_three_anchors(self):
        out = combine_vouchers(mail("PDF", "OFD"), [
            result("01", invoice_number=NO, invoice_date="2026-04-27",
                   total_amount="121.74", seller="甲公司"),
            result("02", invoice_date="2026-04-27", total_amount="121.74",
                   seller="甲公司")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].source_file_seq, ["01", "02"])
        self.assertEqual(out[0].fields["invoice_number"], NO)

    def test_numberless_ambiguous_preserved(self):
        out = combine_vouchers(mail("PDF", "OFD", "图片"), [
            result("01", invoice_number=NO, invoice_date="2026-04-27",
                   total_amount="121.74", seller="甲公司"),
            result("02", invoice_number=OTHER, invoice_date="2026-04-27",
                   total_amount="121.74", seller="甲公司"),
            result("03", invoice_date="2026-04-27", total_amount="121.74",
                   seller="甲公司")])
        self.assertEqual(len(out), 3)
        self.assertIn("多张发票", out[2].error_reason)

    def test_orphans_and_unknown_return(self):
        unknown = DocumentResult("03", "Unknown", source_file_seq=["03"])
        out = combine_vouchers(mail("XML", "PDF", "其他"), [
            xml("01", invoice_number=NO), result("02", invoice_number=OTHER), unknown])
        self.assertEqual([x.role for x in out], ["Invoice", "Invoice", "Unknown"])
        self.assertEqual(out[0].source_file_seq, ["01"])

    def test_multi_xml_conflict_unresolved(self):
        out = combine_vouchers(mail("XML", "XML"), [
            xml("01", invoice_number=NO, total_amount="121.74"),
            xml("02", invoice_number=NO, total_amount="131.74")])
        self.assertEqual(len(out), 2)
        self.assertTrue(all("多个 XML" in x.error_reason for x in out))

    def test_two_numberless_sources_cannot_both_claim_one_voucher(self):
        shared = {"invoice_date": "2026-04-27", "total_amount": "121.74",
                  "seller": "甲公司"}
        out = combine_vouchers(mail("PDF", "OFD", "图片"), [
            result("01", invoice_number=NO, **shared),
            result("02", **shared), result("03", **shared)])
        self.assertEqual(len(out), 3)
        self.assertTrue(all("争同一凭证" in x.error_reason for x in out[1:]))

    def test_conflicting_xml_without_number_evidence_stays_unresolved(self):
        out = combine_vouchers(mail("XML", "XML"), [
            result("01", invoice_number=NO, total_amount="121.74"),
            result("02", invoice_number=NO, total_amount="131.74")])
        self.assertEqual(len(out), 2)
        self.assertTrue(all("多个 XML" in x.error_reason for x in out))

    def test_itinerary_identity_merge_or_keep_uncertain(self):
        def itinerary(seq, number=""):
            item = DocumentResult(seq, "Itinerary", {
                "itinerary_number": number, "match_amount": "121.74",
                "travel_time": "2026-04-27 12:00", "origin_dest": "A→B"}, [seq])
            if number:
                item.field_evidence["itinerary_number"] = [{
                    "file_seq": seq, "raw": number, "normalized": number,
                    "location": "page1/business-id"}]
            return item
        out = combine_vouchers(mail("PDF", "OFD", "图片", "PDF"), [
            itinerary("01", "TRIP-A"), itinerary("02", "TRIP-A"),
            itinerary("03"), itinerary("04")])
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0].source_file_seq, ["01", "02"])
        self.assertTrue(all("无可靠身份" in x.error_reason for x in out[1:]))

    def test_itinerary_number_without_evidence_does_not_merge(self):
        items = [DocumentResult(seq, "Itinerary", {
            "itinerary_number": "TRIP-A", "match_amount": "121.74",
            "travel_time": "2026-04-27 12:00", "origin_dest": "A→B"}, [seq])
            for seq in ("01", "02")]
        out = combine_vouchers(mail("PDF", "OFD"), items)
        self.assertEqual(len(out), 2)
        self.assertTrue(all("无可靠身份" in x.error_reason for x in out))


if __name__ == "__main__":
    unittest.main()
