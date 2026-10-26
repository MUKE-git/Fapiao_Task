"""T3-3: bounded parsing of the verified EInvoice XML template.

This module extracts one file-level candidate. It does not decide whether an
XML and a visual voucher are the same invoice, or make a printable PDF.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from attachment_processor import CollectedFile
from config import RunConfig
from data_model import NOT_FOUND
from processor_contracts import DocumentResult
from utils import clean_receipt_type


MAX_XML_BYTES = 2 * 1024 * 1024
MAX_XML_NODES = 20000
MAX_XML_DEPTH = 40
_NUMBER = re.compile(r"[0-9]{20}\Z")
_FORBIDDEN_DECLARATION = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.I)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _at(root: ET.Element, path: str) -> list[str]:
    """Read a fixed path, ignoring only an XML namespace prefix."""
    nodes = [root]
    for name in path.split("/"):
        nodes = [child for node in nodes for child in node
                 if _local(child.tag) == name]
    return [(node.text or "").strip() for node in nodes]


def _date(raw: str) -> str | None:
    if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})?)?",
            raw):
        return None
    candidate = raw[:10]
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return None


def _amount(raw: str) -> str | None:
    if not re.fullmatch(r"\d+(?:\.\d{1,4})?", raw):
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return raw if value.is_finite() and value > 0 else None


def _bounded_tree(content: bytes) -> ET.Element:
    if len(content) > MAX_XML_BYTES:
        raise ValueError("XML_SIZE_LIMIT")
    if _FORBIDDEN_DECLARATION.search(content.replace(b"\x00", b"")):
        raise ValueError("XML_DTD_OR_ENTITY_FORBIDDEN")
    try:
        root = ET.fromstring(content)
    except (ET.ParseError, UnicodeError, ValueError) as exc:
        raise ValueError("XML_PARSE_ERROR") from exc
    stack = [(root, 1)]
    nodes = 0
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if nodes > MAX_XML_NODES or depth > MAX_XML_DEPTH:
            raise ValueError("XML_COMPLEXITY_LIMIT")
        stack.extend((child, depth + 1) for child in node)
    return root


def process_xml(item: CollectedFile, cfg: RunConfig, subject: str,
                mail_date: str, run_id: str = "") -> DocumentResult:
    """Return one candidate with raw/normalized XPath evidence.

    Subject, mail date and run id belong to the calling mail record; XML
    content must not manufacture or override them.
    """
    seq = item.meta.file_seq
    unknown = lambda reason: DocumentResult(
        file_seq=seq, role="Unknown", source_file_seq=[seq], error_reason=reason)
    try:
        content = Path(item.path).read_bytes()
        root = _bounded_tree(content)
    except (OSError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else "XML_READ_ERROR"
        return unknown(reason)
    if _local(root.tag) != "EInvoice":
        return unknown("XML_UNSUPPORTED_TEMPLATE")

    fields: dict[str, str] = {}
    evidence: dict[str, list[dict]] = {}
    conflicts: list[dict] = []
    errors: list[str] = []

    def add(field: str, path: str, raw: str, normalized: str | None) -> None:
        evidence.setdefault(field, []).append({
            "file_seq": seq, "format": "XML", "location": "/EInvoice/" + path,
            "raw": raw, "normalized": normalized or "",
        })

    def choose(field: str, paths: tuple[str, ...], normalize) -> None:
        candidates: list[tuple[str, str, str | None]] = []
        for path in paths:
            for raw in _at(root, path):
                if not raw:
                    continue
                normalized = normalize(raw)
                candidates.append((path, raw, normalized))
                add(field, path, raw, normalized)
                if normalized is None:
                    errors.append(f"XML_INVALID_{field.upper()}")
        values = {norm for _, _, norm in candidates if norm is not None}
        if len(values) > 1:
            conflicts.append({"field": field, "reason": "XML_FIELD_CONFLICT",
                              "values": sorted(values), "file_seq": seq})
            errors.append(f"XML_CONFLICT_{field.upper()}")
            fields[field] = NOT_FOUND
        elif values:
            fields[field] = next(iter(values))
        elif candidates:
            # Keep a bad raw candidate visible to the downstream validator.
            fields[field] = candidates[0][1]
        else:
            fields[field] = NOT_FOUND
            errors.append(f"XML_MISSING_{field.upper()}")

    choose("invoice_number", (
        "TaxSupervisionInfo/InvoiceNumber", "Header/EIid"),
        lambda value: value if _NUMBER.fullmatch(value) else None)
    choose("invoice_date", ("TaxSupervisionInfo/IssueTime",), _date)
    choose("total_amount", (
        "EInvoiceData/BasicInformation/TotalTax-includedAmount",), _amount)
    choose("receipt_type", ("EInvoiceData/IssuItemInformation/ItemName",),
           lambda value: clean_receipt_type(value) or None)
    choose("seller", ("EInvoiceData/SellerInformation/SellerName",),
           lambda value: value if len(value) <= 50 and "," not in value else None)

    labels = []
    for path in ("Header/InherentLabel/EInvoiceType/LabelName",
                 "Header/InherentLabel/GeneralOrSpecialVAT/LabelName"):
        raw_values = [value for value in _at(root, path) if value]
        for value in raw_values:
            add("invoice_type", path, value, value)
        labels.append(raw_values)
    if any(len(set(values)) > 1 for values in labels):
        conflicts.append({"field": "invoice_type", "reason": "XML_FIELD_CONFLICT",
                          "file_seq": seq})
        errors.append("XML_CONFLICT_INVOICE_TYPE")
        fields["invoice_type"] = ""
    else:
        first = labels[0][0] if labels[0] else ""
        second = labels[1][0] if labels[1] else ""
        fields["invoice_type"] = f"{first}（{second}）" if first and second else first or second
        if fields["invoice_type"]:
            add("invoice_type", "Header/InherentLabel", " + ".join(
                value for value in (first, second) if value), fields["invoice_type"])

    return DocumentResult(
        file_seq=seq, role="Invoice", fields=fields, source_file_seq=[seq],
        error_reason=";".join(dict.fromkeys(errors)),
        field_evidence=evidence, conflicts=conflicts,
    )
