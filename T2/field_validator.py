"""T2-6 差异化字段校验。只校验已提取值，不从其他凭证借字段。"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from config import TRANSPORT_RECEIPT_KEYWORDS
from data_model import (
    KIND_A, KIND_B, MailRecord, NOT_FOUND, STATUS_COMPLETE,
    STATUS_MANUAL_REVIEW, normalize_amount,
)
from utils import clean_receipt_type


_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}\Z")
_NUMBER = re.compile(r"[0-9]{20}\Z")


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and
                             value.strip().casefold() in ("", NOT_FOUND.casefold()))


def _date_issue(value: object, label: str, *, with_time: bool = False) -> str:
    if _missing(value):
        return f"{label}缺失"
    raw = str(value)
    pattern = _TIME if with_time else _DATE
    if not pattern.fullmatch(raw):
        return f"{label}格式无效，应为{'YYYY-MM-DD HH:mm' if with_time else 'YYYY-MM-DD'}"
    try:
        if with_time:
            datetime.strptime(raw, "%Y-%m-%d %H:%M")
        else:
            date.fromisoformat(raw)
    except ValueError:
        return f"{label}不是有效{'日期时间' if with_time else '日期'}"
    return ""


def _amount_issue(value: object, label: str) -> str:
    if _missing(value):
        return f"{label}缺失"
    amount = normalize_amount(value)
    if not isinstance(amount, Decimal) or not amount.is_finite():
        return f"{label}不是有效十进制金额"
    if amount <= 0:
        return f"{label}须大于 0"
    if amount.as_tuple().exponent < -4:
        return f"{label}小数超过 4 位"
    if amount.adjusted() + 1 > 14:
        return f"{label}超出 decimal(18,4) 范围"
    return ""


def _text_issue(value: object, label: str, maximum: int, *, required: bool = True) -> str:
    if _missing(value):
        return f"{label}缺失" if required else ""
    if not isinstance(value, str):
        return f"{label}类型无效"
    if len(value) > maximum:
        return f"{label}超过 {maximum} 字符"
    return ""


def _route_issue(value: object) -> str:
    issue = _text_issue(value, "出发地/目的地", 100)
    if issue:
        return issue
    parts = str(value).split("→")
    if len(parts) != 2 or not all(part.strip() for part in parts):
        return "出发地/目的地格式无效，应为 A→B"
    return ""


def _report(mail: MailRecord, record_id: str, file_seq: str,
            issues: list[str]) -> dict:
    file = next((item for item in mail.files if item.file_seq == file_seq), None)
    flags = [f"T2-6 {record_id} {issue}" for issue in issues]
    existing = set(mail.error_reason.split(";"))
    for flag in flags:
        if flag not in existing:
            mail.error_reason = (mail.error_reason + ";" + flag).strip(";")
            existing.add(flag)
    return {"mail_subject": mail.subject, "mail_date": mail.mail_date,
            "mail_date_header": "", "file_seq": file_seq,
            "filename": file.original_name if file else "", "flags": flags}


def _receipt_issue(value: object) -> str:
    """按已确认契约检查票面类型的文字类型、缺失和长度。"""
    return _text_issue(value, "票面类型", 50)


def validate_records(mail: MailRecord) -> list[dict]:
    """原位标记待核，返回可按邮件/文件/记录定位的提示。"""
    alerts: list[dict] = []
    itineraries = {item.record_id: item for item in mail.itineraries}
    for itinerary in mail.itineraries:
        issues = [issue for issue in (
            _amount_issue(itinerary.match_amount, "行程单匹配金额"),
            _date_issue(itinerary.travel_time, "出行时间", with_time=True),
            _route_issue(itinerary.origin_dest),
        ) if issue]
        if issues:
            seq = itinerary.record_id[len(mail.mail_id) + 1:] if itinerary.record_id.startswith(
                f"{mail.mail_id}-") else ""
            alerts.append(_report(mail, itinerary.record_id, seq, issues))

    for record in mail.records:
        fields = record.fields
        issues: list[str] = []
        for issue in (
            _date_issue(fields.invoice_date, "开票日期"),
            "发票号码缺失" if _missing(fields.invoice_number) else
            "发票号码须为 20 位数字" if not _NUMBER.fullmatch(str(fields.invoice_number)) else "",
            _amount_issue(fields.total_amount, "报销金额"),
        ):
            if issue:
                issues.append(issue)

        fields.invoice_type = ("" if _missing(fields.invoice_type) else
                               fields.invoice_type.strip() if isinstance(fields.invoice_type, str)
                               else fields.invoice_type)
        optional_issue = _text_issue(fields.invoice_type, "发票类型", 20, required=False)
        if optional_issue:
            issues.append(optional_issue)
        if not isinstance(fields.invoice_type, str):
            fields.invoice_type = str(fields.invoice_type)

        if isinstance(fields.receipt_type, str) and not _missing(fields.receipt_type):
            fields.receipt_type = clean_receipt_type(fields.receipt_type)
        receipt_issue = _receipt_issue(fields.receipt_type)
        if receipt_issue:
            issues.append(receipt_issue)
            record.kind = ""
            record.matched_itinerary = None
            fields.travel_time = ""
            fields.origin_dest = ""
        else:
            expected_kind = (KIND_A if any(keyword in fields.receipt_type
                                           for keyword in TRANSPORT_RECEIPT_KEYWORDS)
                             else KIND_B)
            if record.kind != expected_kind:
                issues.append("行程类型与票面类型判定不一致")
                record.kind = expected_kind
        if not isinstance(fields.receipt_type, str):
            fields.receipt_type = str(fields.receipt_type)

        if isinstance(fields.seller, str) and not _missing(fields.seller):
            fields.seller = fields.seller.strip().replace(",", "").replace("，", "")
        seller_issue = _text_issue(fields.seller, "销售方", 50)
        if seller_issue:
            issues.append(seller_issue)
        if not isinstance(fields.seller, str):
            fields.seller = str(fields.seller)

        if record.kind == KIND_A:
            matched = record.matched_itinerary
            if matched is None:
                issues.append("a 类缺少已匹配行程单")
            else:
                itinerary = itineraries.get(f"{mail.mail_id}-{matched.file_seq}")
                if itinerary is None:
                    issues.append("匹配的行程单记录不存在")
                else:
                    match_amount_issue = _amount_issue(itinerary.match_amount, "行程单匹配金额")
                    if match_amount_issue:
                        issues.append(match_amount_issue)
                    if fields.travel_time != itinerary.travel_time:
                        issues.append("出行时间与已匹配行程单不一致")
                    if fields.origin_dest != itinerary.origin_dest:
                        issues.append("出发地/目的地与已匹配行程单不一致")
            for issue in (_date_issue(fields.travel_time, "出行时间", with_time=True),
                          _route_issue(fields.origin_dest)):
                if issue:
                    issues.append(issue)
        elif record.kind == KIND_B:
            # 旅行字段不是 b 类发票字段；不报告其缺失或格式错误。
            if record.matched_itinerary is not None:
                issues.append("b 类不应关联行程单")
            record.matched_itinerary = None
            fields.travel_time = ""
            fields.origin_dest = ""

        if issues:
            if record.status == STATUS_COMPLETE:
                record.status = STATUS_MANUAL_REVIEW
            seq = record.source.source_file_seq[0] if record.source.source_file_seq else ""
            alerts.append(_report(mail, record.record_id, seq, issues))
    return alerts
