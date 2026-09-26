r"""
T2-2 结构验证脚本：只把旧 task_debug.json 映射为 v2 结构。

边界：
- 只读取旧 JSON 中已经存在的显式字段；
- 不读取原 PDF，不解析 text_snapshot，不调用 AI/兜底正则；
- 旧 JSON 没有的字段保留结构并写 Not Found，Excel 展示为空；
- 每个 Invoice 文件必须且只能生成一条 record；
- 多发票邮件只有一组旧 audit_result 时，第一条承接已有信息，其余记录留空并标人工待核。

使用指南：
在终端输入：python .\convert_task_debug_v2.py
默认读取：第60行的目录下文件，据实更改
只转换旧 JSON 已有数据，不读取原始 PDF、不解析 text_snapshot、不调用 AI 或正则补数据
默认输出到：fapiao3.0的_v2_output
输出：- task_debug_v2.json
- 内部校验表.xlsx
- 发票信息汇总表.xlsx
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from email.utils import parsedate_to_datetime
from typing import Dict, Iterable, List

from openpyxl import Workbook

from data_model import (
    DedupSkipped,
    FILE_TYPE_IMAGE,
    FILE_TYPE_OFD,
    FILE_TYPE_OTHER,
    FILE_TYPE_PDF,
    FILE_TYPE_XML,
    InvoiceFields,
    KIND_A,
    KIND_B,
    MatchedItinerary,
    NOT_FOUND,
    PRE_READ_INVOICE,
    PRE_READ_ITINERARY,
    STATUS_COMPLETE,
    STATUS_MANUAL_REVIEW,
    FileRecord,
    InvoiceRecord,
    MailRecord,
    SourceMeta,
    mail_to_dict,
)
from config import TRANSPORT_RECEIPT_KEYWORDS
from excel_writer import append_to_summary_excel
from type_classifier import normalize_doc_role
from utils import clean_receipt_type, sanitize_excel_date_display


# 默认读取文件：需要更换输入数据时，只修改下面这一行。
OLD_DEFAULT = r"C:\Users\lenovo\Desktop\T1-3\task_debug.json"

class FileSeqAssigner:
    """转换期间按旧 JSON 文件顺序分配 01-99。"""

    def __init__(self, mail_id: str):
        self.mail_id = mail_id
        self._counter = 0

    def assign(self) -> str:
        self._counter += 1
        if self._counter > 99:
            raise ValueError(f"邮件 {self.mail_id} 文件数超过 99 上限")
        return f"{self._counter:02d}"

    def record_id(self, file_seq: str) -> str:
        return f"{self.mail_id}-{file_seq}"


def _detect_file_type(filename: str) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return FILE_TYPE_PDF
    if name.endswith(".xml"):
        return FILE_TYPE_XML
    if name.endswith(".ofd"):
        return FILE_TYPE_OFD
    if name.endswith((".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")):
        return FILE_TYPE_IMAGE
    return FILE_TYPE_OTHER


def _parse_mail_date_minute(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "未知日期 00:00"
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed is not None:
            return parsed.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError, OSError):
        pass
    raw = raw.split(" m=+", 1)[0].strip()
    match = re.search(r"\d{4}-\d{2}-\d{2}", raw)
    return f"{match.group(0)} 00:00" if match else "未知日期 00:00"


def _normalize_string(value: object) -> str:
    text = str(value or "").strip()
    return NOT_FOUND if text in ("", "Not Found", "not found", "None", "null") else text


def _is_missing(value: object) -> bool:
    return value is None or (
        isinstance(value, str)
        and value.strip() in ("", "Not Found", "not found", "None", "null")
    )


def _build_invoice_record(
    record_id: str, file_seq: str, audit: Dict[str, object]
) -> InvoiceRecord:
    receipt_type = clean_receipt_type(_normalize_string(audit.get("receipt_type")))
    fields = InvoiceFields(
        invoice_date=sanitize_excel_date_display(audit.get("invoice_date")),
        invoice_number=_normalize_string(audit.get("invoice_number")),
        total_amount=audit.get("total_amount", NOT_FOUND),
        invoice_type=_normalize_string(audit.get("invoice_type")),
        receipt_type=receipt_type,
        seller=str(audit.get("seller") or "").strip().replace(",", "").replace("，", ""),
    )
    status = STATUS_MANUAL_REVIEW if any(
        _is_missing(value)
        for value in (fields.invoice_date, fields.invoice_number, fields.total_amount)
    ) else STATUS_COMPLETE
    kind = KIND_A if any(
        keyword in str(receipt_type or "") for keyword in TRANSPORT_RECEIPT_KEYWORDS
    ) else KIND_B
    return InvoiceRecord(
        record_id=record_id,
        kind=kind,
        fields=fields,
        source=SourceMeta(source_file_seq=[file_seq]),
        matched_itinerary=None,
        status=status,
    )


def _normalize_travel_time(value: object) -> str:
    text = _normalize_string(value)
    if text == NOT_FOUND:
        return NOT_FOUND
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})[ T](\d{1,2}):(\d{2})", text)
    if match:
        year, month, day, hour, minute = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d} {int(hour):02d}:{minute}"
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d} 00:00"
    return NOT_FOUND


def _match_single_itinerary(
    invoice: InvoiceRecord,
    itinerary_file: FileRecord,
    audit: Dict[str, object] | None,
) -> None:
    if invoice.kind != KIND_A:
        return
    audit = audit or {}
    invoice.fields.travel_time = _normalize_travel_time(audit.get("travel_time"))
    origin_dest = _normalize_string(audit.get("origin_dest"))
    invoice.fields.origin_dest = (
        re.sub(r"\s*→\s*", "→", origin_dest).strip()
        if origin_dest != NOT_FOUND else NOT_FOUND
    )
    invoice.matched_itinerary = MatchedItinerary(
        file_seq=itinerary_file.file_seq,
        match_amount=itinerary_file.pre_read_amount,
    )


_INTERNAL_COLUMNS = [
    ("邮件", "mail_id", "邮件序号"), ("邮件", "mail_uid", "IMAP UID"),
    ("邮件", "subject", "邮件主题"), ("邮件", "mail_date", "邮件日期时间"),
    ("文件", "file_seq", "文件序号"), ("文件", "file_type", "文件类型"),
    ("文件", "original_name", "原文件名"), ("文件", "pre_read_type", "预读类型"),
    ("文件", "pre_read_invoice_no", "预读票号"), ("文件", "pre_read_amount", "预读金额"),
    ("发票", "record_id", "文件编号"), ("发票", "kind", "行程类型ab"),
    ("发票", "invoice_date", "发票日期"), ("发票", "invoice_number", "发票号码"),
    ("发票", "total_amount", "报销金额"), ("发票", "invoice_type", "发票类型"),
    ("发票", "receipt_type", "票面类型"), ("发票", "seller", "销售方"),
    ("发票", "status", "状态"), ("行程单", "itinerary_record_id", "文件编号"),
    ("行程单", "match_amount", "匹配金额"), ("行程单", "carrier", "承运方"),
    ("行程单", "travel_time", "出行时间"), ("行程单", "origin_dest", "出发地目的地"),
]


def _write_internal_audit_excel(mail_records: List[MailRecord], path: str) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "内部校验表"
    for column, (entity, _, header) in enumerate(_INTERNAL_COLUMNS, start=1):
        sheet.cell(1, column, entity)
        sheet.cell(2, column, header)
    start = 1
    while start <= len(_INTERNAL_COLUMNS):
        entity = _INTERNAL_COLUMNS[start - 1][0]
        end = start
        while end < len(_INTERNAL_COLUMNS) and _INTERNAL_COLUMNS[end][0] == entity:
            end += 1
        if end > start:
            sheet.merge_cells(start_row=1, start_column=start, end_row=1, end_column=end)
        start = end + 1
    output_row = 3
    for mail in mail_records:
        files = {item.file_seq: item for item in mail.files}
        for record in mail.records:
            source_seq = record.source.source_file_seq[0] if record.source.source_file_seq else ""
            source = files.get(source_seq)
            matched = record.matched_itinerary
            values = {
                "mail_id": mail.mail_id, "mail_uid": mail.mail_uid,
                "subject": mail.subject, "mail_date": mail.mail_date,
                "file_seq": source.file_seq if source else "",
                "file_type": source.file_type if source else "",
                "original_name": source.original_name if source else "",
                "pre_read_type": source.pre_read_type if source else "",
                "pre_read_invoice_no": source.pre_read_invoice_no if source else "",
                "pre_read_amount": source.pre_read_amount if source else "",
                "record_id": record.record_id, "kind": record.kind,
                "invoice_date": record.fields.invoice_date,
                "invoice_number": record.fields.invoice_number,
                "total_amount": record.fields.total_amount,
                "invoice_type": record.fields.invoice_type,
                "receipt_type": record.fields.receipt_type,
                "seller": record.fields.seller, "status": record.status,
                "itinerary_record_id": f"{mail.mail_id}-{matched.file_seq}" if matched else "",
                "match_amount": matched.match_amount if matched else "", "carrier": "",
                "travel_time": record.fields.travel_time if matched else "",
                "origin_dest": record.fields.origin_dest if matched else "",
            }
            for column, (_, key, _) in enumerate(_INTERNAL_COLUMNS, start=1):
                value = values.get(key, "")
                sheet.cell(output_row, column, "" if value == NOT_FOUND else value)
            output_row += 1
    workbook.save(path)


def _summary_row(record: InvoiceRecord) -> Dict[str, object]:
    return {
        "invoice_date": record.fields.invoice_date,
        "invoice_number": record.fields.invoice_number,
        "total_amount": record.fields.total_amount,
        "invoice_type": record.fields.invoice_type,
        "receipt_type": record.fields.receipt_type,
        "seller": record.fields.seller,
        "travel_time": "" if record.fields.travel_time == NOT_FOUND else record.fields.travel_time,
        "origin_dest": "" if record.fields.origin_dest == NOT_FOUND else record.fields.origin_dest,
    }


def _legacy_role(file_info: dict) -> str:
    """映射旧 JSON 已有分类，不从文本重新判断。"""
    return normalize_doc_role(
        file_info.get("pre_read_type")
        or file_info.get("identified_type")
        or file_info.get("local_pdf_type")
    )


def _legacy_pre_read(file_info: dict) -> dict:
    """只搬运旧 JSON 显式存在的预读字段；不存在就留空。"""
    return {
        "pre_read_type": _legacy_role(file_info),
        "pre_read_invoice_no": file_info.get("pre_read_invoice_no", NOT_FOUND),
        "pre_read_amount": file_info.get("pre_read_amount", NOT_FOUND),
    }


def _append_reason(existing: object, reason: str) -> str:
    parts = [part for part in (str(existing or "").strip(";"), reason) if part]
    return ";".join(parts)


def _blank_invoice_record(
    assigner: FileSeqAssigner,
    file_seq: str,
    inferred_kind: str,
) -> InvoiceRecord:
    record = _build_invoice_record(assigner.record_id(file_seq), file_seq, {})
    record.kind = inferred_kind
    record.status = STATUS_MANUAL_REVIEW
    return record


def _validate_mail_relationships(mail: MailRecord) -> List[str]:
    issues: List[str] = []
    files_by_seq = {file.file_seq: file for file in mail.files}
    if len(files_by_seq) != len(mail.files):
        issues.append("file_seq 在邮件内不唯一")

    invoice_seqs = [
        file.file_seq for file in mail.files if file.pre_read_type == PRE_READ_INVOICE
    ]
    itinerary_seqs = {
        file.file_seq
        for file in mail.files
        if file.pre_read_type == PRE_READ_ITINERARY
    }
    source_seqs: List[str] = []
    for record in mail.records:
        if len(record.source.source_file_seq) != 1:
            issues.append(f"{record.record_id} 必须且只能有一个发票来源文件")
            continue
        source_seq = record.source.source_file_seq[0]
        source_seqs.append(source_seq)
        source_file = files_by_seq.get(source_seq)
        if source_file is None:
            issues.append(f"{record.record_id} 来源文件 {source_seq} 不存在")
        elif source_file.pre_read_type != PRE_READ_INVOICE:
            issues.append(f"{record.record_id} 来源文件 {source_seq} 不是 Invoice")
        if record.record_id != f"{mail.mail_id}-{source_seq}":
            issues.append(f"{record.record_id} 与来源文件 {source_seq} 不一致")
        if (
            record.matched_itinerary is not None
            and record.matched_itinerary.file_seq not in itinerary_seqs
        ):
            issues.append(
                f"{record.record_id} 匹配文件 {record.matched_itinerary.file_seq} 不是 Itinerary"
            )

    if Counter(source_seqs) != Counter(invoice_seqs):
        issues.append(
            f"Invoice 文件与 records 未一一对应: invoices={invoice_seqs}, sources={source_seqs}"
        )
    return issues


def validate_relationships(mail_records: Iterable[MailRecord]) -> None:
    issues: List[str] = []
    for mail in mail_records:
        issues.extend(
            f"{mail.mail_id}: {issue}"
            for issue in _validate_mail_relationships(mail)
        )
    if issues:
        raise ValueError("T2-2 数据关系校验失败:\n" + "\n".join(issues))


def convert_legacy_data(old: Dict[str, dict]) -> List[MailRecord]:
    """把旧 JSON 的现有字段映射到 v2，不生成任何新业务数据。"""
    mail_records: List[MailRecord] = []
    for index, (uid, raw_item) in enumerate(old.items(), start=1):
        item = raw_item if isinstance(raw_item, dict) else {}
        if index > 150:
            raise ValueError("邮件序号超过 150 上限")
        mail_id = f"Msg{index}"
        assigner = FileSeqAssigner(mail_id)
        subject = str(item.get("subject") or "")
        mail_date = _parse_mail_date_minute(
            item.get("mail_date_header") or item.get("mail_date")
        )

        file_records: List[FileRecord] = []
        invoice_files: List[FileRecord] = []
        itinerary_files: List[FileRecord] = []
        for raw_file in item.get("files") or []:
            if not isinstance(raw_file, dict):
                continue
            original_name = str(
                raw_file.get("attachment_original_name")
                or raw_file.get("original_name")
                or ""
            )
            pre_read = _legacy_pre_read(raw_file)
            file_record = FileRecord(
                file_seq=assigner.assign(),
                file_type=_detect_file_type(original_name),
                original_name=original_name,
                pre_read_type=pre_read["pre_read_type"],
                pre_read_invoice_no=pre_read["pre_read_invoice_no"],
                pre_read_amount=pre_read["pre_read_amount"],
            )
            file_records.append(file_record)
            if file_record.pre_read_type == PRE_READ_INVOICE:
                invoice_files.append(file_record)
            elif file_record.pre_read_type == PRE_READ_ITINERARY:
                itinerary_files.append(file_record)

        audit = (
            item.get("audit_result")
            if isinstance(item.get("audit_result"), dict)
            else None
        )
        inferred_kind = (
            _build_invoice_record("", "", audit).kind if audit else KIND_B
        )

        records: List[InvoiceRecord] = []
        for position, invoice_file in enumerate(invoice_files):
            if position == 0 and audit:
                record = _build_invoice_record(
                    assigner.record_id(invoice_file.file_seq),
                    invoice_file.file_seq,
                    audit,
                )
            else:
                record = _blank_invoice_record(
                    assigner, invoice_file.file_seq, inferred_kind
                )
            if len(invoice_files) > 1:
                record.status = STATUS_MANUAL_REVIEW
            records.append(record)

        # 仅“单发票 + 单行程单”按冻结规则直接建立关系。
        # match_amount 不在旧 JSON 中时必须留空，不能复制发票金额或重新提取。
        if len(records) == 1 and len(itinerary_files) == 1:
            record = records[0]
            if record.kind == "a类":
                _match_single_itinerary(record, itinerary_files[0], audit)
        elif records:
            for record in records:
                if record.kind == "a类":
                    record.status = STATUS_MANUAL_REVIEW

        error_reason = str(item.get("error_reason") or "")
        if not invoice_files:
            error_reason = _append_reason(error_reason, "NO_INVOICE_CANDIDATE")
        elif len(invoice_files) > 1:
            error_reason = _append_reason(
                error_reason, "MULTI_INVOICE_FIELDS_UNRESOLVED"
            )
        elif not audit:
            error_reason = _append_reason(error_reason, "INVOICE_FIELDS_UNRESOLVED")

        dedup_skipped = [
            DedupSkipped(
                filename=str(entry.get("filename", "")),
                reason=str(entry.get("reason", "")),
            )
            for entry in (item.get("dedup_skipped") or [])
            if isinstance(entry, dict)
        ]
        mail_records.append(MailRecord(
            mail_id=mail_id, mail_uid=str(uid), subject=subject,
            mail_date=mail_date, files=file_records, records=records,
            dedup_skipped=dedup_skipped, error_reason=error_reason,
        ))

    validate_relationships(mail_records)
    return mail_records


def _write_outputs(mail_records: List[MailRecord], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(
        os.path.join(out_dir, "task_debug_v2.json"), "w", encoding="utf-8"
    ) as handle:
        json.dump(
            [mail_to_dict(mail) for mail in mail_records],
            handle,
            ensure_ascii=False,
            indent=2,
        )

    _write_internal_audit_excel(mail_records, os.path.join(out_dir, "内部校验表.xlsx"))

    summary_rows = [
        row
        for mail in mail_records
        for record in mail.records
        for row in [_summary_row(record)]
    ]
    summary_path = os.path.join(out_dir, "发票信息汇总表.xlsx")
    if os.path.exists(summary_path):
        os.remove(summary_path)
    append_to_summary_excel(summary_rows, summary_path)


def convert(old_path: str, out_dir: str) -> List[MailRecord]:
    with open(old_path, encoding="utf-8") as handle:
        old = json.load(handle)
    if not isinstance(old, dict):
        raise ValueError("旧 task_debug.json 顶层必须是 {mail_uid: mail_data} 对象")
    mail_records = convert_legacy_data(old)
    _write_outputs(mail_records, out_dir)

    invoice_files = sum(
        1
        for mail in mail_records
        for file in mail.files
        if file.pre_read_type == PRE_READ_INVOICE
    )
    records = [record for mail in mail_records for record in mail.records]
    manual = sum(record.status == STATUS_MANUAL_REVIEW for record in records)
    print(
        f"转换 {len(mail_records)} 封邮件，Invoice 文件 {invoice_files} 个，records {len(records)} 条"
    )
    print(f"  人工待核={manual}")
    print("  关系校验=通过（每个 Invoice 文件恰好对应一条 record）")
    print("  数据来源=仅旧 JSON 显式字段；未调用原文件、正则兜底或 AI")
    print(f"输出目录: {out_dir}")
    return mail_records


if __name__ == "__main__":
    old_path = sys.argv[1] if len(sys.argv) > 1 else OLD_DEFAULT
    output_dir = (
        sys.argv[2]
        if len(sys.argv) > 2
        else os.path.join(os.path.dirname(os.path.abspath(__file__)), "_v2_output")
    )
    convert(old_path, output_dir)
