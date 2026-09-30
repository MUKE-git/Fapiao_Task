"""T2-1 主循环：收件、调度 T2 处理器、组装记录与输出。

T1/ 是只读旧版快照，本模块不调用其中的代码。附件和 PDF 实质处理
分别由新版 attachment_processor.py、pdf_processor.py 完成；T2-3/4/6
通过 stage_ports.py 接入。T3 文件形态目前只登记并明确提示待核。
"""
from __future__ import annotations

import email
import imaplib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Optional

import dashscope

from attachment_processor import collect_mail_files
from config import RunConfig, TRANSPORT_RECEIPT_KEYWORDS, ensure_output_dirs, should_send_netease_id
from data_model import (
    InvoiceFields, InvoiceRecord, KIND_A, KIND_B, MailRecord, NOT_FOUND,
    PRE_READ_INVOICE, PRE_READ_ITINERARY, STATUS_MANUAL_REVIEW, SourceMeta,
    mail_to_dict,
)
from excel_writer import append_to_summary_excel
from pdf_processor import process_pdf
from processor_contracts import DocumentResult
from stage_ports import StagePorts, load_stage_ports
from utils import clean_receipt_type, decode_str

LogFn = Callable[[str], None]
KEYWORDS = ("发票", "报销", "报销凭证", "电子发票", "开票")


@dataclass
class MessageOutcome:
    mail: MailRecord
    itineraries: list[DocumentResult] = field(default_factory=list)
    mismatches: list[dict] = field(default_factory=list)
    alerts: list[dict] = field(default_factory=list)


def _mail_date(value: str | None) -> str:
    try:
        return parsedate_to_datetime(value or "").strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _number(value: Any) -> float | str:
    try:
        result = float(str(value).replace("¥", "").replace("￥", "").replace(",", ""))
        return round(result, 2) if result > 0 else NOT_FOUND
    except (TypeError, ValueError):
        return NOT_FOUND


def _alert(mail: MailRecord, reason: str, file_seq: str = "", filename: str = "",
           date_header: str = "") -> dict:
    return {"mail_subject": mail.subject, "mail_date": mail.mail_date,
            "mail_date_header": date_header, "file_seq": file_seq,
            "filename": filename, "flags": [reason]}


def _invoice_record(mail: MailRecord, result: DocumentResult) -> InvoiceRecord:
    """把单文件处理结果映射到冻结数据模型，不跨文件借字段。"""
    data = result.fields
    fields = InvoiceFields(
        invoice_date=str(data.get("invoice_date") or NOT_FOUND),
        invoice_number=str(data.get("invoice_number") or NOT_FOUND),
        total_amount=_number(data.get("total_amount")),
        invoice_type=str(data.get("invoice_type") or NOT_FOUND),
        receipt_type=clean_receipt_type(str(data.get("receipt_type") or NOT_FOUND)),
        seller=str(data.get("seller") or NOT_FOUND),
    )
    kind = (KIND_A if any(key in fields.receipt_type for key in TRANSPORT_RECEIPT_KEYWORDS)
            else KIND_B)
    record = InvoiceRecord(
        record_id=f"{mail.mail_id}-{result.file_seq}", kind=kind,
        fields=fields, source=SourceMeta(result.source_file_seq or [result.file_seq]),
    )
    if result.error_reason:
        record.status = STATUS_MANUAL_REVIEW
    return record


def process_message(msg: email.message.Message, mail_id: str, mail_uid: str,
                    cfg: RunConfig, run_id: str = "",
                    ports: StagePorts | None = None) -> MessageOutcome:
    """单封邮件的分流与结果组装；可用合成邮件离线验证。"""
    mail = MailRecord(mail_id=mail_id, mail_uid=mail_uid,
                      subject=decode_str(msg.get("Subject")), mail_date=_mail_date(msg.get("Date")))
    outcome = MessageOutcome(mail)
    header = (msg.get("Date") or "").strip()
    ports = ports or load_stage_ports()
    files = collect_mail_files(msg, mail, cfg)
    files_by_seq = {item.meta.file_seq: item for item in files}
    results: list[DocumentResult] = []
    for item in files:
        if item.meta.file_type == "PDF":
            result = process_pdf(item, cfg, mail.subject, mail.mail_date, run_id)
        else:
            handler = (ports.document_handlers or {}).get(item.meta.file_type)
            if handler is None:
                outcome.alerts.append(_alert(
                    mail, f"{item.meta.file_type} 文件已登记，本阶段尚未提取，请人工核对",
                    item.meta.file_seq, item.meta.original_name, header,
                ))
                continue
            result = handler(item, cfg, mail.subject, mail.mail_date, run_id)
        results.append(result)
        outcome.mismatches.extend(result.mismatches)
        if result.error_reason:
            mail.error_reason = (mail.error_reason + ";" + result.error_reason).strip(";")
            outcome.alerts.append(_alert(mail, result.error_reason, result.file_seq,
                                         item.meta.original_name, header))
        if result.manual_unknown:
            outcome.alerts.append(_alert(mail, "附件类型依据不一致，需人工确认",
                                         result.file_seq, item.meta.original_name, header))
    if ports.combine_vouchers is not None:
        results = ports.combine_vouchers(mail, results)
    for result in results:
        item = files_by_seq.get(result.file_seq)
        filename = item.meta.original_name if item else ""
        if result.role == PRE_READ_INVOICE:
            mail.records.append(_invoice_record(mail, result))
        elif result.role == PRE_READ_ITINERARY:
            outcome.itineraries.append(result)
        else:
            outcome.alerts.append(_alert(mail, "PDF 类型无法确定，需人工核对",
                                         result.file_seq, filename, header))

    if not files:
        outcome.alerts.append(_alert(mail, "无有效附件", date_header=header))
    if mail.error_reason:
        outcome.alerts.append(_alert(mail, mail.error_reason, date_header=header))
    return outcome


def _apply_matching(outcomes: list[MessageOutcome], ports: StagePorts) -> None:
    for outcome in outcomes:
        mail = outcome.mail
        transport_records = [record for record in mail.records if record.kind == KIND_A]
        if transport_records and not outcome.itineraries:
            for record in transport_records:
                record.status = STATUS_MANUAL_REVIEW
            outcome.alerts.append(_alert(mail, "缺行程单，需人工核对"))
            continue
        if ports.match_itineraries is not None:
            outcome.alerts.extend(ports.match_itineraries(mail, outcome.itineraries))
            continue
        if transport_records:
            for record in transport_records:
                record.status = STATUS_MANUAL_REVIEW
            outcome.alerts.append(_alert(mail, "行程单匹配尚待 T2-3 接入，需人工核对"))


def _check_source_chain(mails: list[MailRecord]) -> None:
    """输出前检查行级来源确实指向同邮件已登记文件。"""
    for mail in mails:
        known = {f.file_seq for f in mail.files}
        if len(known) != len(mail.files):
            raise ValueError(f"{mail.mail_id} 文件序号重复")
        for record in mail.records:
            expected = f"{mail.mail_id}-{record.source.source_file_seq[0]}" if record.source.source_file_seq else ""
            if record.record_id != expected or not set(record.source.source_file_seq) <= known:
                raise ValueError(f"{record.record_id} 来源链断裂")
            if record.matched_itinerary and record.matched_itinerary.file_seq not in known:
                raise ValueError(f"{record.record_id} 行程单来源链断裂")


def _excel_rows(mails: list[MailRecord]) -> list[dict]:
    rows: list[dict] = []
    for mail in mails:
        for record in mail.records:
            fields = record.fields
            rows.append({
                "invoice_date": fields.invoice_date,
                "invoice_number": fields.invoice_number,
                "total_amount": fields.total_amount,
                "invoice_type": fields.invoice_type,
                "receipt_type": fields.receipt_type,
                "travel_time": (fields.travel_time if record.kind == KIND_A
                                and record.matched_itinerary is not None
                                and fields.travel_time != NOT_FOUND else ""),
                "seller": fields.seller,
                "origin_dest": (fields.origin_dest if record.kind == KIND_A
                                and record.matched_itinerary is not None
                                and fields.origin_dest != NOT_FOUND else ""),
            })
    return rows


def _write_debug(cfg: RunConfig, mails: list[MailRecord]) -> None:
    with open(cfg.debug_file, "w", encoding="utf-8") as out:
        json.dump([mail_to_dict(mail) for mail in mails], out, ensure_ascii=False, indent=2)


def run_pipeline(cfg: RunConfig, log: Optional[LogFn] = None) -> tuple[list[dict], dict[str, Any]]:
    """冻结入口：IMAP → 附件/PDF处理器 → T2阶段端口 → Excel/JSON。"""
    log = log or (lambda _message: None)
    stats: dict[str, Any] = {
        "unseen_total": 0, "skipped_no_invoice_keyword": 0,
        "invoice_keyword_mails": 0, "manual_unknown_alerts": [],
        "not_found_alerts": [], "imap_mailbox_status": None, "imap_error": None,
    }
    if not (cfg.dashscope_api_key or "").strip():
        return [], {**stats, "error": "no_api_key"}
    ensure_output_dirs(cfg)
    dashscope.api_key = cfg.dashscope_api_key.strip()
    ports = load_stage_ports()
    outcomes: list[MessageOutcome] = []
    client = None
    run_id = datetime.now().strftime("%Y%m%d%H%M%S")
    try:
        host = (cfg.imap_host or "").strip() or "imap.163.com"

        def connect():
            conn = imaplib.IMAP4_SSL(host)
            conn.login(cfg.imap_user.strip(), cfg.imap_password)
            if should_send_netease_id(host, cfg.use_netease_id):
                imaplib.Commands["ID"] = ('AUTH', '(("name" "com.netease.mail") ("version" "1.0.0") ("vendor" "netease"))')
                conn._simple_command("ID", '("name" "com.netease.mail" "version" "1.0.0" "vendor" "netease")')
            conn.select("INBOX")
            return conn

        client = connect()

        def fetch(uid: bytes, query: str) -> bytes:
            nonlocal client
            for delay in (1, 3, None):
                try:
                    typ, data = client.uid("FETCH", uid, query)
                    if typ != "OK" or not data or not data[0] or not isinstance(data[0], tuple):
                        raise imaplib.IMAP4.error(f"FETCH failed for UID {uid!r}")
                    return data[0][1]
                except (imaplib.IMAP4.abort, OSError):
                    if delay is None:
                        raise
                    time.sleep(delay)
                    try:
                        client.logout()
                    except Exception:
                        pass
                    client = connect()
            raise RuntimeError("无法获取邮件")

        typ, data = client.uid("SEARCH", None, "UNSEEN")
        if typ != "OK":
            raise imaplib.IMAP4.error("UID SEARCH UNSEEN failed")
        uids = (data[0] or b"").split() if data else []
        stats["unseen_total"] = len(uids)
        if len(uids) > 150:
            raise ValueError("本轮未读邮件超过 150 封，超出 Msg 编号契约上限")
        try:
            _, status = client.status("INBOX", "(MESSAGES UNSEEN)")
            raw = status[0].decode(errors="replace") if status and status[0] else ""
            total = re.search(r"MESSAGES\s+(\d+)", raw, re.I)
            unseen = re.search(r"UNSEEN\s+(\d+)", raw, re.I)
            stats["imap_mailbox_status"] = {
                "messages": int(total.group(1)) if total else None,
                "unseen": int(unseen.group(1)) if unseen else None,
            }
        except Exception as exc:
            stats["imap_mailbox_status"] = {"error": str(exc)}

        for index, uid in enumerate(uids, 1):
            header = email.message_from_bytes(fetch(uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])"))
            subject = decode_str(header.get("Subject"))
            if not any(keyword in subject for keyword in KEYWORDS):
                stats["skipped_no_invoice_keyword"] += 1
                continue
            stats["invoice_keyword_mails"] += 1
            message = email.message_from_bytes(fetch(uid, "(BODY.PEEK[])"))
            outcome = process_message(message, f"Msg{index}", uid.decode("ascii"), cfg, run_id, ports)
            outcomes.append(outcome)
            log(f"已登记 {outcome.mail.mail_id}：{len(outcome.mail.files)} 个文件，"
                f"{len(outcome.mail.records)} 笔 PDF 发票")

        _apply_matching(outcomes, ports)
        mails = [outcome.mail for outcome in outcomes]
        if ports.arbitrate_records is not None:
            mails = ports.arbitrate_records(mails)
        elif any(mail.records for mail in mails):
            for mail in mails:
                for record in mail.records:
                    record.status = STATUS_MANUAL_REVIEW
            stats["not_found_alerts"].append({"mail_subject": "本轮结果",
                "flags": ["写表前去重仲裁尚待 T2-4 接入；当前记录均需人工核对"]})
        if ports.validate_records is not None:
            for mail in mails:
                stats["not_found_alerts"].extend(ports.validate_records(mail))
        elif any(mail.records for mail in mails):
            for mail in mails:
                for record in mail.records:
                    record.status = STATUS_MANUAL_REVIEW
            stats["not_found_alerts"].append({"mail_subject": "本轮结果",
                "flags": ["差异化字段校验尚待 T2-6 接入；当前记录均需人工核对"]})
        _check_source_chain(mails)

        excel_error, path = append_to_summary_excel(_excel_rows(mails), cfg.summary_excel_file)
        if excel_error:
            for mail in mails:
                mail.error_reason = (mail.error_reason + ";" + excel_error).strip(";")
            log(f"Excel 写入失败：{excel_error}")
        elif path:
            log(f"Excel 已写入：{path}")
        for outcome in outcomes:
            stats["not_found_alerts"].extend(outcome.alerts)
        _write_debug(cfg, mails)
    except (imaplib.IMAP4.error, OSError, ValueError) as exc:
        stats["imap_error"] = str(exc)
        log(f"处理失败：{exc}")
        _write_debug(cfg, [outcome.mail for outcome in outcomes])
    finally:
        if client:
            try:
                client.logout()
            except Exception:
                pass
    return [m for outcome in outcomes for m in outcome.mismatches], stats
