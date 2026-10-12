"""T2-3 研发自测：业务关系、Decimal 边界、名称与竞争处理。"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, localcontext
import unittest

from data_model import (
    FileRecord, InvoiceFields, InvoiceRecord, ItineraryRecord, MailRecord,
    SourceMeta, STATUS_COMPLETE, STATUS_MANUAL_REVIEW, NOT_FOUND, mail_to_dict,
)
from itinerary_matcher import match_itineraries, _amount_level
from processor_contracts import DocumentResult


def fixture(invoices, itineraries, mail_id="Msg1"):
    mail = MailRecord(mail_id=mail_id, mail_uid="123", subject="电子发票",
                      mail_date="2026-10-04 10:00")
    evidence = []
    for name, amount, kind in invoices:
        seq = f"{len(mail.files) + 1:02}"
        mail.files.append(FileRecord(seq, "PDF", name, internal_name=f"{mail_id}_{seq}_内部.pdf"))
        mail.records.append(InvoiceRecord(
            record_id=f"{mail_id}-{seq}", kind=kind,
            fields=InvoiceFields(invoice_date="2026-10-04", invoice_number=seq * 10,
                                 total_amount=amount, receipt_type="客运服务" if kind == "a类" else "办公用品",
                                 seller="发票销售方"),
            source=SourceMeta([seq]), status=STATUS_COMPLETE,
        ))
    for spec in itineraries:
        name, amount = spec[:2]
        time = spec[2] if len(spec) > 2 else "2026-10-03 09:00"
        origin = spec[3] if len(spec) > 3 else "杭州→上海"
        seq = f"{len(mail.files) + 1:02}"
        mail.files.append(FileRecord(seq, "PDF", name))
        mail.itineraries.append(ItineraryRecord(f"{mail_id}-{seq}", amount, time, origin))
        evidence.append(DocumentResult(seq, "Itinerary", {
            "match_amount": amount, "travel_time": time, "origin_dest": origin,
        }))
    return mail, evidence


def matched(mail):
    return {record.record_id: record.matched_itinerary.file_seq
            for record in mail.records if record.matched_itinerary}


class ItineraryMatcherTests(unittest.TestCase):
    def test_single_pair_ignores_amount_and_suffix(self):
        for left, right in (("100.00", "900.00"), (NOT_FOUND, "1.00"),
                            ("1.00", NOT_FOUND), (NOT_FOUND, NOT_FOUND),
                            ("bad", "0"), ("1.00001", "1.00")):
            with self.subTest(left=left, right=right):
                mail, evidence = fixture([("发票C.pdf", left, "a类")], [("行程单A.pdf", right)])
                match_itineraries(mail, evidence)
                self.assertEqual(matched(mail), {"Msg1-01": "02"})
                self.assertEqual(mail.records[0].matched_itinerary.match_amount, right)
                self.assertEqual(mail.records[0].fields.travel_time, "2026-10-03 09:00")

    def test_single_b_and_unknown_do_not_receive_fields(self):
        for kind in ("b类", ""):
            with self.subTest(kind=kind):
                mail, evidence = fixture([("发票A.pdf", "10.00", kind)], [("行程单A.pdf", "10.00")])
                alerts = match_itineraries(mail, evidence)
                self.assertEqual(matched(mail), {})
                self.assertEqual(mail.records[0].status, STATUS_COMPLETE)
                serialized = mail_to_dict(mail)["records"][0]
                self.assertNotIn("travel_time", serialized["fields"])
                self.assertNotIn("matched_itinerary", serialized)
                self.assertFalse(any("缺少 出行" in str(alert) for alert in alerts))

    def test_mixed_invoice_count_does_not_become_single_pair(self):
        for other_kind in ("a类", "b类", ""):
            with self.subTest(kind=other_kind):
                mail, evidence = fixture([("发票C.pdf", "900.00", "a类"),
                                          ("其他发票B.pdf", "800.00", other_kind)],
                                         [("行程单A.pdf", "10.00")])
                match_itineraries(mail, evidence)
                self.assertEqual(matched(mail), {})

    def test_half_up_precision_and_tolerance_are_separate_layers(self):
        cases = [("100.00", "100.0049", 1), ("100.00", "100.0050", 2),
                 ("100.00", "100.49", 2), ("100.00", "100.50", 2),
                 ("100.00", "100.51", 0), ("100.00", "100.5049", 0),
                 ("100.50", "100.00", 2), ("100", "100.49", 1),
                 ("100", "100.51", 0), ("53.93", "53.9300", 1)]
        with localcontext() as context:
            context.prec = 3
            for left, right, expected in cases:
                with self.subTest(left=left, right=right):
                    self.assertEqual(_amount_level(left, right), expected)

    def test_amount_boundaries_affect_actual_complex_matching(self):
        for amount, expected in (("100.49", True), ("100.50", True),
                                 ("100.51", False), ("100.5049", False)):
            with self.subTest(amount=amount):
                mail, evidence = fixture([("发票C.pdf", "100.00", "a类"),
                                          ("发票Z.pdf", "900.00", "b类")],
                                         [("行程单A.pdf", amount)])
                match_itineraries(mail, evidence)
                self.assertEqual(bool(matched(mail)), expected)

    def test_equal_layer_wins_over_tolerance_and_filename(self):
        mail, evidence = fixture([("发票B.pdf", "100.00", "a类")],
                                 [("行程单A.pdf", "100.0000"), ("行程单B.pdf", "100.40")])
        match_itineraries(mail, evidence)
        self.assertEqual(matched(mail), {"Msg1-01": "02"})

    def test_equal_candidates_use_suffix(self):
        mail, evidence = fixture([("发票B.pdf", "100.00", "a类")],
                                 [("行程单A.pdf", "100.0049"), ("行程单B.pdf", "100.0000")])
        match_itineraries(mail, evidence)
        self.assertEqual(matched(mail), {"Msg1-01": "03"})

    def test_tolerance_candidates_use_suffix_not_nearest_amount(self):
        mail, evidence = fixture([("发票B.pdf", "100.00", "a类")],
                                 [("行程单A.pdf", "100.01"), ("行程单B.pdf", "100.50")])
        match_itineraries(mail, evidence)
        self.assertEqual(matched(mail), {"Msg1-01": "03"})

    def test_ambiguous_equal_layer_does_not_search_lower_layer(self):
        mail, evidence = fixture([("发票C.pdf", "100.00", "a类")],
                                 [("行程单A.pdf", "100.0000"), ("行程单B.pdf", "100.0049"),
                                  ("行程单C.pdf", "100.50")])
        match_itineraries(mail, evidence)
        self.assertEqual(matched(mail), {})
        self.assertEqual(mail.records[0].status, STATUS_MANUAL_REVIEW)

    def test_suffix_fallback_accepts_missing_and_distant_amounts(self):
        for value in (NOT_FOUND, "100.00", "bad", "0", "-1", "1.00001"):
            with self.subTest(amount=value):
                mail, evidence = fixture([("平台电子发票A.pdf", value, "a类"),
                                          ("发票B.pdf", "200.00", "a类")],
                                         [("平台行程单A.pdf", "500.00"), ("行程单B.pdf", "900.00")])
                match_itineraries(mail, evidence)
                self.assertEqual(matched(mail), {"Msg1-01": "03", "Msg1-02": "04"})

    def test_suffix_mismatch_and_empty_suffix_are_not_evidence(self):
        for inv_name, it_name in (("发票C.pdf", "行程单A.pdf"),
                                  ("发票.pdf", "行程单.pdf"),
                                  ("发票A.pdf", "行程单AB.pdf"),
                                  ("同平台A.pdf", "同平台A.pdf")):
            with self.subTest(invoice=inv_name, itinerary=it_name):
                mail, evidence = fixture([(inv_name, NOT_FOUND, "a类"),
                                          ("发票Z.pdf", "2000", "b类")], [(it_name, "500")])
                match_itineraries(mail, evidence)
                self.assertEqual(matched(mail), {})

    def test_suffix_normalization_and_full_original_name(self):
        long_prefix = "长原名" * 100
        mail, evidence = fixture([(long_prefix + "电子发票_Ａ.PDF", NOT_FOUND, "a类"),
                                  ("发票Z.pdf", "5000", "b类")],
                                 [(long_prefix + "行程单-a.pdf", "500")])
        mail.files[0].internal_name = "Msg1_01_发票Z.pdf"
        match_itineraries(mail, evidence)
        self.assertEqual(matched(mail), {"Msg1-01": "03"})

    def test_identical_suffix_on_multiple_itineraries_is_ambiguous(self):
        mail, evidence = fixture([("发票A.pdf", NOT_FOUND, "a类")],
                                 [("甲行程单A.pdf", "500"), ("乙行程单A.pdf", "900")])
        match_itineraries(mail, evidence)
        self.assertEqual(matched(mail), {})

    def test_boundary_separators_preserve_internal_suffix_characters(self):
        for invoice_name, itinerary_name, expected in (
            ("发票（Ａ）.PDF", "行程单_a.pdf", True),
            ("发票—A—.pdf", "行程单+[a].pdf", True),
            ("发票A-1.pdf", "行程单a-1.pdf", True),
            ("发票A-1.pdf", "行程单A1.pdf", False),
        ):
            with self.subTest(invoice=invoice_name, itinerary=itinerary_name):
                mail, evidence = fixture([(invoice_name, NOT_FOUND, "a类"),
                                          ("发票Z.pdf", "2000", "b类")],
                                         [(itinerary_name, "500")])
                match_itineraries(mail, evidence)
                self.assertEqual(bool(matched(mail)), expected)

    def test_conflict_marks_all_claimants_and_preserves_independent_pair(self):
        mail, evidence = fixture([("发票A.pdf", "100.00", "a类"),
                                  ("发票B.pdf", "100.00", "a类"),
                                  ("发票C.pdf", "200.00", "a类")],
                                 [("行程单A.pdf", "100.00"), ("行程单C.pdf", "200.00")])
        match_itineraries(mail, evidence)
        self.assertEqual(matched(mail), {"Msg1-03": "05"})
        self.assertEqual([r.status for r in mail.records],
                         [STATUS_MANUAL_REVIEW, STATUS_MANUAL_REVIEW, STATUS_COMPLETE])
        self.assertIn("Msg1-01、Msg1-02", mail.error_reason)

    def test_reordering_records_files_and_itineraries_is_stable(self):
        mail, evidence = fixture([("发票A.pdf", "100.00", "a类"),
                                  ("发票B.pdf", "100.00", "a类"),
                                  ("发票C.pdf", "200.00", "a类")],
                                 [("行程单A.pdf", "100.00"), ("行程单C.pdf", "200.00")])
        reversed_mail = deepcopy(mail)
        reversed_mail.records.reverse()
        reversed_mail.itineraries.reverse()
        reversed_mail.files.reverse()
        alerts = match_itineraries(mail, evidence)
        reversed_alerts = match_itineraries(reversed_mail, list(reversed(evidence)))
        self.assertEqual(matched(mail), matched(reversed_mail))
        self.assertEqual(alerts, reversed_alerts)

    def test_sales_invoice_numbers_and_prior_travel_are_not_anchors(self):
        mail, evidence = fixture([("发票C.pdf", "10.00", "a类"),
                                  ("发票D.pdf", "20.00", "a类")],
                                 [("行程单A.pdf", "100"), ("行程单B.pdf", "200")])
        for record in mail.records:
            record.fields.seller = "同一销售方"
            record.fields.travel_time = "伪旅行时间"
            record.fields.origin_dest = "伪起终点"
        for item in evidence:
            item.fields.update(seller="同一销售方", invoice_number=mail.records[0].fields.invoice_number)
        match_itineraries(mail, evidence)
        self.assertEqual(matched(mail), {})
        self.assertEqual(mail.records[0].fields.travel_time, NOT_FOUND)

    def test_preserves_existing_review_status_and_all_candidate_facts(self):
        mail, evidence = fixture([("发票A.pdf", Decimal("53.93"), "a类")],
                                 [("行程单A.pdf", Decimal("53.9300")), ("行程单B.pdf", Decimal("999.0000"))])
        mail.records[0].status = STATUS_MANUAL_REVIEW
        mail.error_reason = "已有AI异常"
        before = deepcopy(mail_to_dict(mail))
        match_itineraries(mail, evidence)
        after = mail_to_dict(mail)
        self.assertEqual(mail.records[0].status, STATUS_MANUAL_REVIEW)
        self.assertEqual(before["itineraries"], after["itineraries"])
        self.assertEqual(before["files"], after["files"])
        self.assertEqual(after["records"][0]["source"], {"source_file_seq": ["01"]})
        self.assertEqual(after["records"][0]["matched_itinerary"]["match_amount"], "53.9300")
        self.assertTrue(mail.error_reason.startswith("已有AI异常"))

    def test_missing_travel_fields_preserves_relation_without_borrowing(self):
        mail, evidence = fixture([("发票A.pdf", "100.00", "a类")],
                                 [("行程单A.pdf", "100.00", NOT_FOUND, ""),
                                  ("行程单B.pdf", "999.00")])
        match_itineraries(mail, evidence)
        self.assertEqual(matched(mail), {"Msg1-01": "02"})
        self.assertEqual(mail.records[0].fields.travel_time, NOT_FOUND)
        self.assertEqual(mail.records[0].fields.origin_dest, "")
        self.assertEqual(mail.records[0].status, STATUS_MANUAL_REVIEW)
        self.assertIn("行程单缺少", mail.error_reason)

    def test_repeated_call_does_not_duplicate_json_reasons(self):
        mail, evidence = fixture([("发票C.pdf", "100.00", "a类")],
                                 [("行程单A.pdf", "100.00"), ("行程单B.pdf", "100.00")])
        first = match_itineraries(mail, evidence)
        saved = mail_to_dict(mail)
        second = match_itineraries(mail, evidence)
        self.assertEqual(first, second)
        self.assertEqual(saved, mail_to_dict(mail))

    def test_bad_or_duplicate_sources_do_not_create_relations(self):
        for mutation in ("missing_invoice", "missing_itinerary", "duplicate_file",
                         "duplicate_invoice", "duplicate_itinerary", "foreign_itinerary"):
            with self.subTest(mutation=mutation):
                mail, evidence = fixture([("发票A.pdf", "100.00", "a类")], [("行程单A.pdf", "100.00")])
                if mutation == "missing_invoice": mail.files.pop(0)
                if mutation == "missing_itinerary": mail.files.pop()
                if mutation == "duplicate_file": mail.files.append(deepcopy(mail.files[-1]))
                if mutation == "duplicate_invoice": mail.records.append(deepcopy(mail.records[0]))
                if mutation == "duplicate_itinerary": mail.itineraries.append(deepcopy(mail.itineraries[0]))
                if mutation == "foreign_itinerary": mail.itineraries[0].record_id = "Msg2-02"
                alerts = match_itineraries(mail, evidence)
                self.assertEqual(matched(mail), {})
                self.assertTrue(alerts)

    def test_conflicting_evidence_is_not_silently_substituted(self):
        for mutation in ("amount", "travel", "role", "duplicate"):
            with self.subTest(mutation=mutation):
                mail, evidence = fixture([("发票A.pdf", "100.00", "a类")], [("行程单A.pdf", "100.00")])
                if mutation == "amount": evidence[0].fields["match_amount"] = "101.00"
                if mutation == "travel": evidence[0].fields["travel_time"] = "不同时间"
                if mutation == "role": evidence[0].role = "Invoice"
                if mutation == "duplicate": evidence.append(deepcopy(evidence[0]))
                match_itineraries(mail, evidence)
                self.assertEqual(matched(mail), {})
                self.assertIn("处理结果", mail.error_reason)

    def test_persisted_mail_can_be_matched_without_raw_processor_results(self):
        mail, _ = fixture([("发票A.pdf", "100.00", "a类")], [("行程单A.pdf", "100.00")])
        match_itineraries(mail, [])
        self.assertEqual(matched(mail), {"Msg1-01": "02"})

    def test_no_invoices_or_itineraries(self):
        for invs, itins in (([], []), ([], [("行程单A.pdf", "100")]),
                            ([("发票A.pdf", "100", "a类")], []),
                            ([("发票A.pdf", "100", "b类")], [])):
            with self.subTest(invoices=invs, itineraries=itins):
                mail, evidence = fixture(invs, itins)
                match_itineraries(mail, evidence)
                self.assertEqual(matched(mail), {})


if __name__ == "__main__":
    unittest.main()
