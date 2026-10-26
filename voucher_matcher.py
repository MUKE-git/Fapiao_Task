"""T3-6: decide same-mail voucher relationships from file-level results.

This module does not call a model, match an itinerary to an invoice, arbitrate
across mail, write outputs, or select delivery PDFs. It preserves undecided
file-level candidates for the later T2 stages.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import re

from data_model import MailRecord, NOT_FOUND
from processor_contracts import DocumentResult


_INVOICE_FIELDS = (
    "invoice_number", "invoice_date", "total_amount", "invoice_type",
    "receipt_type", "seller",
)
_ANCHORS = ("invoice_date", "total_amount", "seller")
_NUMBER = re.compile(r"^[0-9]{20}$")


def _missing(value: object) -> bool:
    return value is None or isinstance(value, str) and value.strip() in ("", NOT_FOUND)


def _normal(field: str, value: object) -> str | None:
    if _missing(value):
        return None
    raw = str(value).strip()
    if field == "invoice_number":
        return raw if _NUMBER.fullmatch(raw) else None
    if field in {"total_amount", "match_amount"}:
        try:
            number = Decimal(raw.replace("￥", "").replace("¥", ""))
        except InvalidOperation:
            return None
        return format(number, "f") if number.is_finite() and number > 0 else None
    if field == "invoice_date":
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            return None
    return raw


def _format(mail: MailRecord, result: DocumentResult) -> str:
    return next((f.file_type for f in mail.files if f.file_seq == result.file_seq), "其他")


def _order(mail: MailRecord) -> dict[str, int]:
    return {item.file_seq: index for index, item in enumerate(mail.files)}


def _field_evidence(result: DocumentResult, field: str) -> list[dict]:
    """Retain upstream evidence; a flat legacy PDF value is not fabricated evidence."""
    return [dict(item) for item in result.field_evidence.get(field, [])]


def _xml_reliable(result: DocumentResult, field: str) -> bool:
    if _normal(field, result.fields.get(field)) is None:
        return False
    if any(item.get("field") == field for item in result.conflicts):
        return False
    return any(
        item.get("file_seq") == result.file_seq
        and item.get("location")
        and _normal(field, item.get("normalized"))
        == _normal(field, result.fields.get(field))
        for item in _field_evidence(result, field)
    )


def _issue(result: DocumentResult, reason: str, details: dict | None = None) -> None:
    if reason not in result.error_reason:
        result.error_reason = ";".join(filter(None, (result.error_reason, reason)))
    if details is not None and details not in result.conflicts:
        result.conflicts.append(details)


def _values_conflict(a: DocumentResult, b: DocumentResult) -> list[str]:
    return [field for field in _INVOICE_FIELDS if field != "invoice_number"
            and _normal(field, a.fields.get(field)) is not None
            and _normal(field, b.fields.get(field)) is not None
            and _normal(field, a.fields.get(field)) != _normal(field, b.fields.get(field))]


def _can_attach_numberless(a: DocumentResult, b: DocumentResult) -> bool:
    return all(_normal(field, a.fields.get(field)) is not None
               and _normal(field, a.fields.get(field)) == _normal(field, b.fields.get(field))
               for field in _ANCHORS)


def _merge(mail: MailRecord, group: list[DocumentResult], order: dict[str, int]) -> DocumentResult:
    xml = [r for r in group if _format(mail, r) == "XML"
           and _xml_reliable(r, "invoice_number")]
    anchor = min(xml, key=lambda r: order.get(r.file_seq, 10**6)) if xml else min(
        group, key=lambda r: (0 if _format(mail, r) == "PDF" else 1,
                              order.get(r.file_seq, 10**6)))
    merged = deepcopy(anchor)
    merged.source_file_seq = sorted(
        {seq for r in group for seq in (r.source_file_seq or [r.file_seq])},
        key=lambda seq: order.get(seq, 10**6),
    )
    merged.field_evidence = {}
    merged.conflicts = [deepcopy(item) for r in group for item in r.conflicts]
    for field in _INVOICE_FIELDS:
        valid = [(r, _normal(field, r.fields.get(field))) for r in group]
        valid = [(r, value) for r, value in valid if value is not None]
        merged.field_evidence[field] = [item for r in group
                                        for item in _field_evidence(r, field)]
        if not valid:
            continue
        xml_valid = [(r, value) for r, value in valid
                     if _format(mail, r) == "XML" and _xml_reliable(r, field)]
        xml_values = {value for _, value in xml_valid}
        if len(xml_values) > 1:
            _issue(merged, "XML 来源字段冲突，需人工核对", {
                "field": field, "reason": "xml_conflict",
                "candidates": [{"file_seq": r.file_seq, "value": value}
                               for r, value in xml_valid],
            })
            continue
        chosen = xml_valid[0] if xml_valid else valid[0]
        merged.fields[field] = chosen[0].fields[field]
        different = [(r, value) for r, value in valid if value != chosen[1]]
        if different:
            _issue(merged, "跨格式字段冲突，需人工核对", {
                "field": field, "reason": "source_disagreement",
                "chosen_file_seq": chosen[0].file_seq,
                "candidates": [{"file_seq": r.file_seq, "value": value}
                               for r, value in valid],
            })
    return merged


def _combine_invoices(mail: MailRecord, invoices: list[DocumentResult],
                      order: dict[str, int]) -> list[DocumentResult]:
    numbered: dict[str, list[DocumentResult]] = {}
    numberless: list[DocumentResult] = []
    for result in invoices:
        number = _normal("invoice_number", result.fields.get("invoice_number"))
        if number:
            numbered.setdefault(number, []).append(result)
        else:
            numberless.append(result)

    # A missing number is attached only when all three independent anchors
    # agree with exactly one numbered invoice candidate.
    numberless_matches: dict[str, list[DocumentResult]] = {}
    for result in numberless:
        matches = [number for number, group in numbered.items()
                   if all(_can_attach_numberless(result, item) for item in group)]
        if len(matches) == 1:
            numberless_matches.setdefault(matches[0], []).append(result)
        elif matches:
            _issue(result, "缺票号来源对应多张发票，需人工核对", {
                "reason": "ambiguous_numberless_match", "candidates": matches})
        else:
            _issue(result, "缺票号，跨格式同票关系待核")
    for number, candidates in numberless_matches.items():
        if len(candidates) == 1:
            numbered[number].append(candidates[0])
            numberless.remove(candidates[0])
        else:
            for result in candidates:
                _issue(result, "多份缺号来源争同一凭证，需人工核对", {
                    "reason": "multiple_numberless_sources", "number": number})

    out: list[DocumentResult] = []
    for group in numbered.values():
        if len(group) == 1:
            out.extend(group)
            continue
        all_xml = [r for r in group if _format(mail, r) == "XML"]
        xml = [r for r in group if _format(mail, r) == "XML"
               and _xml_reliable(r, "invoice_number")]
        xml_conflicts = any(_values_conflict(a, b)
                            for i, a in enumerate(all_xml)
                            for b in all_xml[i + 1:])
        if xml_conflicts:
            for result in group:
                _issue(result, "多个 XML 来源相互冲突，需人工核对", {
                    "reason": "xml_conflict", "file_seq": [r.file_seq for r in xml]})
            out.extend(group)
            continue
        conflicting_fields = {
            field for i, a in enumerate(group) for b in group[i + 1:]
            for field in _values_conflict(a, b)
        }
        xml_can_decide = all(len({_normal(field, r.fields.get(field))
                                   for r in xml if _xml_reliable(r, field)}) == 1
                             for field in conflicting_fields)
        if conflicting_fields and (not xml or not xml_can_decide):
            for result in group:
                _issue(result, "同号来源字段冲突且无可靠 XML 值，待本轮仲裁", {
                    "reason": "unresolved_field_conflict", "fields": sorted(conflicting_fields),
                    "file_seq": [r.file_seq for r in group]})
            out.extend(group)
            continue
        out.append(_merge(mail, group, order))
    out.extend(numberless)
    return out


def _combine_itineraries(itineraries: list[DocumentResult],
                         order: dict[str, int]) -> list[DocumentResult]:
    out: list[DocumentResult] = []
    by_identity: dict[str, list[DocumentResult]] = {}
    anonymous: list[DocumentResult] = []
    for result in itineraries:
        identity = str(result.fields.get("itinerary_number") or "").strip()
        has_identity_evidence = any(
            item.get("file_seq") == result.file_seq
            and item.get("location")
            and str(item.get("normalized") or "").strip() == identity
            for item in _field_evidence(result, "itinerary_number")
        )
        if identity and identity != NOT_FOUND and has_identity_evidence:
            by_identity.setdefault(identity, []).append(result)
        else:
            anonymous.append(result)
    for identity, group in by_identity.items():
        if len(group) == 1:
            out.extend(group)
            continue
        key_fields = ("match_amount", "travel_time", "origin_dest")
        first = group[0]
        if any(any(_normal(field, first.fields.get(field)) !=
                   _normal(field, other.fields.get(field)) for field in key_fields)
               for other in group[1:]):
            for result in group:
                _issue(result, "同编号行程单业务字段冲突，需人工核对", {
                    "reason": "itinerary_conflict", "identity": identity})
            out.extend(group)
            continue
        merged = deepcopy(min(group, key=lambda r: order.get(r.file_seq, 10**6)))
        merged.source_file_seq = sorted(
            {seq for r in group for seq in (r.source_file_seq or [r.file_seq])},
            key=lambda seq: order.get(seq, 10**6))
        merged.field_evidence = {
            field: [item for r in group for item in _field_evidence(r, field)]
            for field in key_fields + ("itinerary_number",)
        }
        out.append(merged)
    # Similar amount/time/name alone is not an identity. Mark likely duplicates
    # so the T3-9 gate can keep them out of the single-pair shortcut.
    for i, a in enumerate(anonymous):
        for b in anonymous[i + 1:]:
            if all(_normal(field, a.fields.get(field)) is not None
                   and _normal(field, a.fields.get(field)) ==
                   _normal(field, b.fields.get(field))
                   for field in ("match_amount", "travel_time", "origin_dest")):
                for result in (a, b):
                    _issue(result, "行程单重复关系无可靠身份，需人工核对", {
                        "reason": "itinerary_identity_unconfirmed",
                        "file_seq": [a.file_seq, b.file_seq]})
    out.extend(anonymous)
    return out


def combine_vouchers(mail: MailRecord,
                     results: list[DocumentResult]) -> list[DocumentResult]:
    """Return same-mail candidates with evidence; never mutate inputs."""
    copied = deepcopy(results)
    order = _order(mail)
    invoices = [r for r in copied if r.role == "Invoice"]
    itineraries = [r for r in copied if r.role == "Itinerary"]
    unknown = [r for r in copied if r.role not in ("Invoice", "Itinerary")]
    combined = (_combine_invoices(mail, invoices, order)
                + _combine_itineraries(itineraries, order) + unknown)
    return sorted(combined, key=lambda r: order.get(r.file_seq, 10**6))
