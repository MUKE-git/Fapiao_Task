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
from openpyxl import Workbook


from attachment_processor import collect_mail_files
from config import (RunConfig, SUMMARY_COLUMNS, TRANSPORT_RECEIPT_KEYWORDS,
                    ensure_output_dirs, should_send_netease_id)
from data_model import (
    InvoiceFields, InvoiceRecord, ItineraryRecord, KIND_A, KIND_B, MailRecord, NOT_FOUND,
    PRE_READ_INVOICE, PRE_READ_ITINERARY, STATUS_COMPLETE, STATUS_MANUAL_REVIEW,
    STATUS_MISSING_VOUCHER, SourceMeta,
    amount_requires_review, mail_to_dict, normalize_amount,
)
from excel_writer import append_to_summary_excel
from delivery_exporter import prepare_delivery, build_delivery_zip
from mail_link_downloader import download_links
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
    failed_files: list[dict] = field(default_factory=list)
    converted_pdfs: dict[str, str] = field(default_factory=dict)


def _mail_date(value: str | None) -> str:
    try:
        return parsedate_to_datetime(value or "").strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _travel_time_to_minute(value: object) -> str:
    """仅把有效的秒级行程时间降至已约定的分钟精度；无效值留给 T2-6 报错。"""
    raw = str(value or NOT_FOUND)
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", raw):
        try:
            return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return raw


def _alert(mail: MailRecord, reason: str, file_seq: str = "", filename: str = "",
           date_header: str = "", code: str = "", level: str = "warning") -> dict:
    alert = {"mail_id": mail.mail_id, "mail_uid": mail.mail_uid,
            "mail_subject": mail.subject, "mail_date": mail.mail_date,
            "mail_date_header": date_header, "file_seq": file_seq,
            "filename": filename, "flags": [reason]}
    if code:
        alert["code"] = code
        alert["level"] = level
    return alert


def _invoice_record(mail: MailRecord, result: DocumentResult) -> InvoiceRecord:
    """把单文件处理结果映射到冻结数据模型，不跨文件借字段。"""
    data = result.fields
    invoice_type = data.get("invoice_type")
    receipt_type = data.get("receipt_type")
    seller = data.get("seller")
    fields = InvoiceFields(
        invoice_date=str(data.get("invoice_date") or NOT_FOUND),
        invoice_number=str(data.get("invoice_number") or NOT_FOUND),
        total_amount=normalize_amount(data.get("total_amount")),
        invoice_type="" if invoice_type in (None, "", NOT_FOUND) else invoice_type,
        receipt_type=(clean_receipt_type(receipt_type) if isinstance(receipt_type, str)
                      else NOT_FOUND if receipt_type is None else receipt_type),
        seller=NOT_FOUND if seller is None or seller == "" else seller,
    )
    receipt_missing = (not isinstance(fields.receipt_type, str)
                       or fields.receipt_type in ("", NOT_FOUND))
    kind = ("" if receipt_missing else
            KIND_A if any(key in fields.receipt_type for key in TRANSPORT_RECEIPT_KEYWORDS)
            else KIND_B)
    sources = [result.file_seq] + [seq for seq in result.source_file_seq
                                   if seq != result.file_seq]
    record = InvoiceRecord(
        record_id=f"{mail.mail_id}-{result.file_seq}", kind=kind,
        fields=fields, source=SourceMeta(sources,
            field_evidence=result.field_evidence, conflicts=result.conflicts,
            delivery_blocked=any(item.get("reason") in {
                "unresolved_field_conflict", "xml_conflict", "ambiguous_numberless_match",
                "multiple_numberless_sources"} for item in result.conflicts)),
        status=STATUS_COMPLETE,
    )
    if (result.error_reason or receipt_missing or amount_requires_review(fields.total_amount)
            or result.conflicts or not isinstance(fields.invoice_type, str)
            or not isinstance(fields.seller, str)):
        record.status = STATUS_MANUAL_REVIEW
    if (record.status == STATUS_COMPLETE and
            all(next((f.file_type for f in mail.files if f.file_seq == seq), "") == "XML"
                for seq in sources)):
        record.status = STATUS_MISSING_VOUCHER
    return record


def _itinerary_record(mail: MailRecord, result: DocumentResult) -> ItineraryRecord:
    """保存单文件行程单显式字段；匹配算法由 T2-3 端口负责。"""
    data = result.fields
    # 兼容 T2-2 已确认的同文件旧结果；每个 DocumentResult 仅来自当前行程单。
    amount = data.get("match_amount", data.get("total_amount", NOT_FOUND))
    return ItineraryRecord(
        record_id=f"{mail.mail_id}-{result.file_seq}",
        match_amount=normalize_amount(amount),
        travel_time=_travel_time_to_minute(data.get("travel_time")),
        origin_dest=str(data.get("origin_dest") or NOT_FOUND),
        source_file_seq=([result.file_seq] + [seq for seq in result.source_file_seq
                                               if seq != result.file_seq]),
        conflicts=result.conflicts,
        delivery_blocked=any(item.get("reason") in {
            "itinerary_identity_unconfirmed", "itinerary_conflict"}
            for item in result.conflicts),
    )


def process_message(msg: email.message.Message, mail_id: str, mail_uid: str,
                    cfg: RunConfig, run_id: str = "",
                    ports: StagePorts | None = None,
                    existing_mail: MailRecord | None = None) -> MessageOutcome:
    """单封邮件的分流与结果组装；可用合成邮件离线验证。"""
    mail = existing_mail if existing_mail is not None else MailRecord(
        mail_id=mail_id, mail_uid=mail_uid,
        subject=decode_str(msg.get("Subject")), mail_date=_mail_date(msg.get("Date")))
    outcome = MessageOutcome(mail)
    header = (msg.get("Date") or "").strip()
    ports = ports or load_stage_ports()
    try:
        linked_files, link_failures = download_links(msg)
    except Exception as exc:
        linked_files, link_failures = [], []
        outcome.alerts.append(_alert(mail, f"正文链接检查失败：{type(exc).__name__}",
                                     date_header=header))
    for failure in link_failures:
        outcome.alerts.append(_alert(mail, f"{failure.source_label} 下载失败：{failure.reason}",
                                     date_header=header))
    files = collect_mail_files(msg, mail, cfg, linked_files)
    files_by_seq = {item.meta.file_seq: item for item in files}
    results: list[DocumentResult] = []
    for item in files:
        try:
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
            if not isinstance(result, DocumentResult):
                raise TypeError("处理器未返回文件级 DocumentResult")
        except Exception as exc:
            reason = f"FILE_PROCESSING_EXCEPTION:{item.meta.file_seq}:{type(exc).__name__}"
            mail.error_reason = (mail.error_reason + ";" + reason).strip(";")
            outcome.alerts.append(_alert(mail, reason, item.meta.file_seq,
                                         item.meta.original_name, header))
            outcome.failed_files.append({"file_seq": item.meta.file_seq,
                                         "filename": item.meta.original_name, "reason": reason})
            continue
        results.append(result)
        if result.pdf_path:
            outcome.converted_pdfs[result.file_seq] = result.pdf_path
        for mismatch in result.mismatches:
            mismatch.setdefault("mail_id", mail.mail_id)
            mismatch.setdefault("mail_uid", mail.mail_uid)
        outcome.mismatches.extend(result.mismatches)
        if result.mismatches:
            outcome.alerts.append(_alert(mail, "附件预读类型与识别类型不一致，需人工确认",
                                         result.file_seq, item.meta.original_name,
                                         header, code="P10"))
        if result.error_reason:
            mail.error_reason = (mail.error_reason + ";" + result.error_reason).strip(";")
            outcome.alerts.append(_alert(mail, result.error_reason, result.file_seq,
                                         item.meta.original_name, header))
        if result.manual_unknown and not result.mismatches:
            outcome.alerts.append(_alert(mail, "附件类型依据不一致，需人工确认",
                                         result.file_seq, item.meta.original_name,
                                         header, code="P10"))
    if ports.combine_vouchers is not None:
        results = ports.combine_vouchers(mail, results)
    for result in results:
        item = files_by_seq.get(result.file_seq)
        filename = item.meta.original_name if item else ""
        if result.role == PRE_READ_INVOICE:
            record = _invoice_record(mail, result)
            mail.records.append(record)
            if (record.status == STATUS_MISSING_VOUCHER or
                    all(next((f.file_type for f in mail.files if f.file_seq == seq), "") == "XML"
                        for seq in record.source.source_file_seq)):
                outcome.alerts.append(_alert(mail, f"{record.record_id} 缺凭证，需回原邮件核对",
                                             result.file_seq, filename, header))
        elif result.role == PRE_READ_ITINERARY:
            mail.itineraries.append(_itinerary_record(mail, result))
            outcome.itineraries.append(result)
        else:
            outcome.alerts.append(_alert(mail, f"{item.meta.file_type if item else '文件'} 类型无法确定，需人工核对",
                                         result.file_seq, filename, header))
        if result.error_reason and result.error_reason not in mail.error_reason:
            mail.error_reason = (mail.error_reason + ";" + result.error_reason).strip(";")
            outcome.alerts.append(_alert(mail, result.error_reason, result.file_seq,
                                         filename, header))

    if not files:
        outcome.alerts.append(_alert(
            mail, "正文链接未取得有效文件，需回原邮件核对" if link_failures else "无有效附件",
            date_header=header))
    if mail.error_reason:
        outcome.alerts.append(_alert(mail, mail.error_reason, date_header=header))
    for record in mail.records:
        if not record.kind:
            source_seq = record.source.source_file_seq[0] if record.source.source_file_seq else ""
            source_file = files_by_seq.get(source_seq)
            receipt_reason = ("票面类型缺失" if record.fields.receipt_type in ("", NOT_FOUND)
                              else "票面类型无效")
            outcome.alerts.append(_alert(
                mail, f"{record.record_id} {receipt_reason}，行程类型待人工核对",
                source_seq, source_file.meta.original_name if source_file else "", header,
            ))
        if amount_requires_review(record.fields.total_amount):
            source_seq = record.source.source_file_seq[0] if record.source.source_file_seq else ""
            source_file = files_by_seq.get(source_seq)
            outcome.alerts.append(_alert(
                mail, f"{record.record_id} 金额格式或精度需人工核对",
                source_seq, source_file.meta.original_name if source_file else "", header,
            ))
    for itinerary in mail.itineraries:
        if amount_requires_review(itinerary.match_amount):
            source_seq = itinerary.record_id[len(mail.mail_id) + 1:]
            source_file = files_by_seq.get(source_seq)
            outcome.alerts.append(_alert(
                mail, f"{itinerary.record_id} 行程单金额格式或精度需人工核对",
                source_seq, source_file.meta.original_name if source_file else "", header,
            ))
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
        if any(any(conflict.get("reason") == "itinerary_identity_unconfirmed"
                       for conflict in item.conflicts)
               for item in outcome.itineraries):
            for record in transport_records:
                record.status = STATUS_MANUAL_REVIEW
            outcome.alerts.append(_alert(mail, "行程单重复关系无可靠身份，发票与行程单关系需人工核对"))
            continue
        if ports.match_itineraries is not None:
            outcome.alerts.extend(ports.match_itineraries(mail, outcome.itineraries))
            continue
        if transport_records:
            for record in transport_records:
                record.status = STATUS_MANUAL_REVIEW
            outcome.alerts.append(_alert(mail, "行程单匹配尚待 T2-3 接入，需人工核对"))


def _check_source_chain(mails: list[MailRecord], cfg: RunConfig | None = None) -> None:
    """输出前检查编号、记录引用及实际落盘文件组成可双向追查的来源链。"""
    mail_ids: set[str] = set()
    mail_uids: set[str] = set()
    record_ids: set[str] = set()
    internal_names: set[str] = set()
    extract_dir = os.path.realpath(cfg.extract_dir) if cfg is not None else ""
    for mail in mails:
        if not re.fullmatch(r"Msg(?:[1-9]|[1-9]\d|1[0-4]\d|150)", mail.mail_id):
            raise ValueError(f"{mail.mail_id} 邮件编号不符合本轮格式")
        if mail.mail_id in mail_ids:
            raise ValueError(f"{mail.mail_id} 邮件编号重复")
        mail_ids.add(mail.mail_id)
        if cfg is not None and (not mail.mail_uid.isdigit() or mail.mail_uid in mail_uids):
            raise ValueError(f"{mail.mail_id} IMAP UID 缺失、格式错误或重复")
        mail_uids.add(mail.mail_uid)
        known = {f.file_seq for f in mail.files}
        if len(known) != len(mail.files):
            raise ValueError(f"{mail.mail_id} 文件序号重复")
        for index, file in enumerate(mail.files, 1):
            if index > 99 or file.file_seq != f"{index:02d}":
                raise ValueError(f"{mail.mail_id} 文件序号不连续或超出 01–99")
            if cfg is not None:
                name = file.internal_name
                if (not name or len(name) > 120 or os.path.basename(name) != name
                        or not name.startswith(f"{mail.mail_id}_{file.file_seq}_")):
                    raise ValueError(f"{mail.mail_id}-{file.file_seq} 内部文件名无效")
                if name in internal_names:
                    raise ValueError(f"{name} 内部文件名重复")
                internal_names.add(name)
                path = os.path.realpath(os.path.join(extract_dir, name))
                if os.path.dirname(path) != extract_dir or not os.path.isfile(path):
                    raise ValueError(f"{mail.mail_id}-{file.file_seq} 内部文件不存在或不在本轮目录")
        itinerary_ids = [item.record_id for item in mail.itineraries]
        if len(itinerary_ids) != len(set(itinerary_ids)):
            raise ValueError(f"{mail.mail_id} 行程单编号重复")
        for itinerary in mail.itineraries:
            if not itinerary.record_id.startswith(f"{mail.mail_id}-") or itinerary.record_id[len(mail.mail_id) + 1:] not in known:
                raise ValueError(f"{itinerary.record_id} 行程单来源链断裂")
            if itinerary.source_file_seq and (
                    itinerary.record_id != f"{mail.mail_id}-{itinerary.source_file_seq[0]}"
                    or not set(itinerary.source_file_seq) <= known):
                raise ValueError(f"{itinerary.record_id} 行程单聚合来源链断裂")
            if itinerary.record_id in record_ids:
                raise ValueError(f"{itinerary.record_id} 记录编号重复")
            record_ids.add(itinerary.record_id)
        itineraries_by_id = {item.record_id: item for item in mail.itineraries}
        for record in mail.records:
            expected = f"{mail.mail_id}-{record.source.source_file_seq[0]}" if record.source.source_file_seq else ""
            if (record.record_id != expected or not set(record.source.source_file_seq) <= known
                    or len(record.source.source_file_seq) != len(set(record.source.source_file_seq))):
                raise ValueError(f"{record.record_id} 来源链断裂")
            if record.record_id in record_ids:
                raise ValueError(f"{record.record_id} 记录编号重复")
            record_ids.add(record.record_id)
            if record.matched_itinerary and record.matched_itinerary.file_seq not in known:
                raise ValueError(f"{record.record_id} 行程单来源链断裂")
            if record.matched_itinerary:
                matched_id = f"{mail.mail_id}-{record.matched_itinerary.file_seq}"
                itinerary = itineraries_by_id.get(matched_id)
                if itinerary is None:
                    raise ValueError(f"{record.record_id} 匹配的行程单未保存")
                matched_amount = normalize_amount(record.matched_itinerary.match_amount)
                itinerary_amount = normalize_amount(itinerary.match_amount)
                if (matched_amount != NOT_FOUND and itinerary_amount != NOT_FOUND
                        and matched_amount != itinerary_amount):
                    raise ValueError(f"{record.record_id} 匹配金额与行程单条目不一致")


def _excel_rows(mails: list[MailRecord]) -> list[dict]:
    rows: list[dict] = []
    for mail in mails:
        for record in mail.records:
            if record.record_id in mail.excel_skip_record_ids:
                continue
            fields = record.fields
            if len(_missing_excel_keys(record)) == 3:
                continue
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


def _missing_excel_keys(record: InvoiceRecord) -> list[str]:
    fields = record.fields
    values = {"开票日期": fields.invoice_date, "发票号码": fields.invoice_number,
              "金额": fields.total_amount}
    return [name for name, value in values.items()
            if value is None or (isinstance(value, str)
                                 and value.strip().lower() in ("", NOT_FOUND.lower()))]


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
        "failed_mails": [], "run_error": None, "excel_error": None,
        "delivery_status": "summary_failed", "delivery_zip_bytes": None,
        "delivery_error": "", "delivery_issues": [],
        "summary_row_count": 0,
    }
    if not (cfg.dashscope_api_key or "").strip():
        return [], {**stats, "error": "no_api_key"}
    ensure_output_dirs(cfg)
    ports = load_stage_ports(cfg)
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
            mail_id = f"Msg{index}"
            uid_text = uid.decode("ascii", errors="replace")
            subject = ""
            date_header = ""
            partial_mail = MailRecord(mail_id=mail_id, mail_uid=uid_text)
            try:
                header = email.message_from_bytes(fetch(uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])"))
                subject = decode_str(header.get("Subject"))
                date_header = (header.get("Date") or "").strip()
                if not any(keyword in subject for keyword in KEYWORDS):
                    stats["skipped_no_invoice_keyword"] += 1
                    continue
                stats["invoice_keyword_mails"] += 1
                partial_mail.subject = subject
                partial_mail.mail_date = _mail_date(date_header)
                message = email.message_from_bytes(fetch(uid, "(BODY.PEEK[])"))
                outcome = process_message(message, mail_id, uid_text, cfg, run_id, ports,
                                          existing_mail=partial_mail)
                if outcome.failed_files:
                    stats["failed_mails"].append({"mail_id": mail_id, "mail_uid": uid_text,
                                                   "failed_files": outcome.failed_files})
            except Exception as exc:
                reason = f"MAIL_PROCESSING_EXCEPTION:{type(exc).__name__}:{exc}"
                partial_mail.error_reason = (partial_mail.error_reason + ";" + reason).strip(";")
                for record in partial_mail.records:
                    record.status = STATUS_MANUAL_REVIEW
                outcome = MessageOutcome(partial_mail)
                outcome.alerts.append(_alert(partial_mail, reason, date_header=date_header))
                stats["failed_mails"].append({"mail_id": mail_id, "mail_uid": uid_text,
                                               "reason": reason})
                log(f"{mail_id} 处理失败，已保留已有记录并继续下一封：{exc}")
            outcomes.append(outcome)
            log(f"已登记 {outcome.mail.mail_id}：{len(outcome.mail.files)} 个文件，"
                f"{len(outcome.mail.records)} 笔 PDF 发票")

        _apply_matching(outcomes, ports)
        mails = [outcome.mail for outcome in outcomes]
        if ports.arbitrate_records is not None:
            mails = ports.arbitrate_records(mails)
            for mail in mails:
                for alert in mail.arbitration_alerts:
                    alert.setdefault("mail_id", mail.mail_id)
                    alert.setdefault("mail_uid", mail.mail_uid)
                stats["not_found_alerts"].extend(mail.arbitration_alerts)
        elif any(mail.records for mail in mails):
            for mail in mails:
                for record in mail.records:
                    record.status = STATUS_MANUAL_REVIEW
            stats["not_found_alerts"].append({"mail_subject": "本轮结果",
                "flags": ["写表前去重仲裁尚待 T2-4 接入；当前记录均需人工核对"]})
        if ports.validate_records is not None:
            for mail in mails:
                alerts = ports.validate_records(mail)
                for alert in alerts:
                    alert.setdefault("mail_id", mail.mail_id)
                    alert.setdefault("mail_uid", mail.mail_uid)
                stats["not_found_alerts"].extend(alerts)
        elif any(mail.records for mail in mails):
            for mail in mails:
                for record in mail.records:
                    record.status = STATUS_MANUAL_REVIEW
            stats["not_found_alerts"].append({"mail_subject": "本轮结果",
                "flags": ["差异化字段校验尚待 T2-6 接入；当前记录均需人工核对"]})
        _check_source_chain(mails, cfg)

        for mail in mails:
            files_by_seq = {item.file_seq: item for item in mail.files}
            for record in mail.records:
                missing = _missing_excel_keys(record)
                if not missing:
                    continue
                record.status = STATUS_MANUAL_REVIEW
                source_seq = record.source.source_file_seq[0] if record.source.source_file_seq else ""
                source = files_by_seq.get(source_seq)
                reason = (f"{record.record_id} 开票日期、发票号码、金额均缺失，未写入 Excel，需人工核对"
                          if len(missing) == 3 else
                          f"{record.record_id} 缺少{'、'.join(missing)}，已写入 Excel，需人工核对")
                stats["not_found_alerts"].append(_alert(
                    mail, reason, source_seq, source.original_name if source else ""))

        excel_rows = _excel_rows(mails)
        excel_error, path = append_to_summary_excel(excel_rows, cfg.summary_excel_file)
        if not excel_error and not path and not os.path.isfile(cfg.summary_excel_file):
            try:
                workbook = Workbook()
                for column, (header, _) in enumerate(SUMMARY_COLUMNS, 1):
                    workbook.active.cell(1, column, header)
                workbook.save(cfg.summary_excel_file)
                path = cfg.summary_excel_file
            except Exception as exc:
                excel_error = f"EXCEL_EMPTY_SUMMARY_ERROR:{type(exc).__name__}"
        if excel_error:
            stats["excel_error"] = excel_error
            for mail in mails:
                mail.error_reason = (mail.error_reason + ";" + excel_error).strip(";")
            log(f"Excel 写入失败：{excel_error}")
        elif path:
            stats["summary_row_count"] = len(excel_rows)
            log(f"Excel 已写入：{path}")
        for outcome in outcomes:
            stats["not_found_alerts"].extend(outcome.alerts)
        _write_debug(cfg, mails)
        if excel_error:
            stats["delivery_status"] = "summary_failed"
            stats["delivery_error"] = "汇总表生成失败"
        else:
            try:
                converted = {(outcome.mail.mail_id, seq): path
                             for outcome in outcomes
                             for seq, path in outcome.converted_pdfs.items()}
                plan = prepare_delivery(cfg, [mail_to_dict(mail) for mail in mails], converted)
                with open(cfg.debug_file, "w", encoding="utf-8") as out:
                    json.dump(plan.mails, out, ensure_ascii=False, indent=2)
                stats["delivery_zip_bytes"] = build_delivery_zip(cfg, plan)
                stats["delivery_issues"] = plan.issues
                stats["delivery_status"] = "ready"
                for issue in plan.issues:
                    match = re.match(r"^(Msg[0-9]+)-([0-9]{2}):", issue)
                    if not match:
                        continue
                    source_mail = next((mail for mail in mails if mail.mail_id == match[1]), None)
                    if source_mail is None:
                        continue
                    source_file = next((item for item in source_mail.files
                                        if item.file_seq == match[2]), None)
                    stats["not_found_alerts"].append(_alert(
                        source_mail, issue, match[2],
                        source_file.original_name if source_file else ""))
            except (OSError, ValueError, TypeError) as exc:
                stats["delivery_status"] = "package_failed"
                stats["delivery_error"] = f"交付包生成或核查失败：{type(exc).__name__}"
                log(f"交付包生成或核查失败：{type(exc).__name__}: {exc}")
    except (imaplib.IMAP4.error, OSError, ValueError) as exc:
        stats["imap_error"] = str(exc)
        log(f"处理失败：{exc}")
        for outcome in outcomes:
            for record in outcome.mail.records:
                record.status = STATUS_MANUAL_REVIEW
        _write_debug(cfg, [outcome.mail for outcome in outcomes])
    except Exception as exc:
        stats["run_error"] = f"{type(exc).__name__}: {exc}"
        log(f"本轮后处理失败，已保留部分记录：{exc}")
        for outcome in outcomes:
            for record in outcome.mail.records:
                record.status = STATUS_MANUAL_REVIEW
            stats["not_found_alerts"].extend(outcome.alerts)
        try:
            _write_debug(cfg, [outcome.mail for outcome in outcomes])
        except Exception as write_exc:
            stats["run_error"] += f";JSON_WRITE_ERROR:{write_exc}"
    finally:
        if client:
            try:
                client.logout()
            except Exception:
                pass
    return [m for outcome in outcomes for m in outcome.mismatches], stats
