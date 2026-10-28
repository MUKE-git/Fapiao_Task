"""Generate T1-style business names for delivered PDF copies.

This module only decides names. Source selection, file I/O and packaging belong
to the delivery exporter.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from decimal import Decimal

from data_model import normalize_amount
from utils import clean_receipt_type


_INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_TIME = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}) [0-9]{2}:[0-9]{2}(?::[0-9]{2})?\Z")


def _date_text(value: object) -> str:
    raw = str(value or "").strip()
    if _DATE.fullmatch(raw):
        try:
            date.fromisoformat(raw)
            return raw
        except ValueError:
            pass
    return "日期缺失"


def _travel_date(value: object) -> str:
    raw = str(value or "").strip()
    match = _TIME.fullmatch(raw)
    if match:
        try:
            datetime.strptime(raw, "%Y-%m-%d %H:%M:%S" if len(raw) == 19 else "%Y-%m-%d %H:%M")
            return match.group(1)
        except ValueError:
            pass
    return "日期缺失"


def _amount_text(value: object) -> str:
    amount = normalize_amount(value)
    if (not isinstance(amount, Decimal) or not amount.is_finite() or amount <= 0
            or amount.as_tuple().exponent < -4 or amount.adjusted() + 1 > 14):
        return "金额缺失"
    return format(amount, "f")


def _safe_component(value: str, fallback: str) -> str:
    cleaned = _INVALID.sub("", value).strip(" .")
    return cleaned or fallback


class BusinessPdfNamer:
    """Apply the business name rule and avoid collisions within one delivery."""

    def __init__(self) -> None:
        self._used: set[str] = set()

    def _unique(self, stem: str, identity: str) -> str:
        stem = _safe_component(stem, "票据")
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
        suffix = ""
        index = 0
        while True:
            budget = 120 - len(suffix) - len(".pdf")
            body = stem if len(stem) <= budget else stem[:budget - 9].rstrip(" .") + "_" + digest
            candidate = body + suffix + ".pdf"
            if candidate.casefold() not in self._used:
                self._used.add(candidate.casefold())
                return candidate
            index += 1
            suffix = f"({index})"

    def invoice_name(self, fields: dict, identity: str,
                     matched_itinerary: dict | None = None) -> str:
        amount = _amount_text(fields.get("total_amount"))
        raw_receipt = str(fields.get("receipt_type") or "").strip()
        receipt = _safe_component(
            clean_receipt_type(raw_receipt) if raw_receipt.casefold() != "not found" else "",
            "票面类型缺失")
        date_text = (_travel_date(matched_itinerary.get("travel_time"))
                     if matched_itinerary is not None
                     else _date_text(fields.get("invoice_date")))
        stem = f"{receipt}+{date_text}+{amount}"
        return self._unique(stem + ("元" if amount != "金额缺失" else ""), identity)

    def itinerary_name(self, itinerary: dict, identity: str, amount_value: object) -> str:
        """Name a PDF with its selected export amount; keep itinerary facts intact."""
        amount = _amount_text(amount_value)
        stem = f"行程单+{_travel_date(itinerary.get('travel_time'))}+{amount}"
        return self._unique(stem + ("元" if amount != "金额缺失" else ""), identity)
