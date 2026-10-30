"""Select confirmed voucher PDFs, name copies, then verify the user ZIP.

``prepare_delivery`` runs before final JSON serialization. The one-argument
``build_delivery_zip`` remains available to existing callers.
"""
from __future__ import annotations

import io
import json
import zipfile
import copy
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from config import RunConfig, safe_desktop_subfolder
from pdf_business_namer import BusinessPdfNamer


@dataclass(frozen=True)
class DeliveryEntry:
    name: str
    path: Path
    mail_id: str
    file_seq: str


@dataclass
class DeliveryPlan:
    mails: list[dict]
    entries: list[DeliveryEntry] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


def _read_checked_pdf(cfg: RunConfig, path: Path) -> bytes:
    root = Path(cfg.base_path).resolve()
    resolved = path.resolve()
    if resolved.suffix.lower() != ".pdf" or not resolved.is_relative_to(root):
        raise ValueError("交付 PDF 路径不在本轮工作目录")
    if not resolved.is_file():
        raise ValueError(f"交付 PDF 不存在：{path.name}")
    content = resolved.read_bytes()
    if not content.startswith(b"%PDF-"):
        raise ValueError(f"交付来源不是可识别 PDF：{path.name}")
    return content


def _pdf_for_source(cfg: RunConfig, item: dict, mail_id: str,
                    converted_pdfs: Mapping[tuple[str, str], str]) -> Path:
    kind = str(item.get("file_type") or "")
    seq = str(item.get("file_seq") or "")
    if kind == "PDF":
        name = str(item.get("internal_name") or "")
        if not name or Path(name).name != name or Path(name).suffix.lower() != ".pdf":
            raise ValueError("内部 PDF 文件名无效")
        return Path(cfg.extract_dir) / name
    if kind == "图片" and (mail_id, seq) in converted_pdfs:
        return Path(converted_pdfs[(mail_id, seq)])
    raise ValueError("来源没有可交付 PDF")


def _choose_pdf(cfg: RunConfig, mail_id: str, files: list[dict], seqs: list[str],
                converted_pdfs: Mapping[tuple[str, str], str], issues: list[str]
                ) -> tuple[dict, Path] | None:
    priority = {"PDF": 0, "图片": 1}
    order = {str(item.get("file_seq") or ""): index for index, item in enumerate(files)}
    by_seq = {str(item.get("file_seq") or ""): item for item in files}
    candidates = [by_seq[str(seq)] for seq in dict.fromkeys(seqs) if str(seq) in by_seq]
    candidates.sort(key=lambda item: (priority.get(str(item.get("file_type") or ""), 99),
                                      order[str(item.get("file_seq") or "")]))
    for item in candidates:
        if item.get("file_type") not in priority:
            continue
        try:
            path = _pdf_for_source(cfg, item, mail_id, converted_pdfs)
            _read_checked_pdf(cfg, path)
            return item, path
        except (OSError, ValueError) as exc:
            issues.append(f"{mail_id}-{item.get('file_seq')}: {exc}")
    return None


def _trip_seq(mail_id: str, trip: dict) -> str:
    record_id = str(trip.get("record_id") or "")
    return record_id[len(mail_id) + 1:] if record_id.startswith(mail_id + "-") else ""


def prepare_delivery(cfg: RunConfig, mails: list[dict],
                     converted_pdfs: Mapping[tuple[str, str], str] | None = None
                     ) -> DeliveryPlan:
    """Choose PDFs and fill exported_name before the final JSON is written.

    Converted paths are checked PDF copies of image sources within this
    run's work directory. The input business result is copied, not changed.
    """
    if not Path(cfg.summary_excel_file).is_file():
        raise ValueError("汇总表未生成")
    if not isinstance(mails, list):
        raise ValueError("调试 JSON 顶层不是邮件列表")
    converted_pdfs = converted_pdfs or {}
    plan = DeliveryPlan(copy.deepcopy(mails))
    namer = BusinessPdfNamer()
    seen_numbers: dict[str, tuple] = {}
    seen_trip_content: set[tuple[str, str]] = set()
    by_mail = {str(m.get("mail_id") or ""): m for m in plan.mails}
    for m in plan.mails:
        for f in m.get("files") or []:
            f["exported_name"] = ""
    # A missed upstream arbitration flag must not let whichever mail is first
    # donate an arbitrary PDF. Suppress only this number, preserving other PDFs.
    number_signatures: dict[str, set[tuple]] = {}
    for mail in plan.mails:
        if not isinstance(mail, dict):
            raise ValueError("调试 JSON 邮件条目无效")
        for record in mail.get("records") or []:
            source = record.get("source") or {}
            if source.get("delivery_blocked") or record.get("delivery_blocked"):
                continue
            values = record.get("fields") or {}
            number = str(values.get("invoice_number") or "").strip()
            if not number or number == "Not Found":
                continue
            signature = (str(values.get("invoice_date")),
                         str(values.get("total_amount")),
                         str(values.get("receipt_type")), str(values.get("seller")))
            number_signatures.setdefault(number, set()).add(signature)
    ambiguous_numbers = {number for number, signatures in number_signatures.items()
                         if len(signatures) > 1}
    for mail in plan.mails:
        if not isinstance(mail, dict):
            raise ValueError("调试 JSON 邮件条目无效")
        mail_id = str(mail.get("mail_id") or "")
        files = mail.get("files") or []
        if not isinstance(files, list):
            raise ValueError("调试 JSON 文件列表无效")
        trips: dict[str, dict] = {}
        for trip in mail.get("itineraries") or []:
            seq = _trip_seq(mail_id, trip)
            if not seq or seq in trips:
                raise ValueError("行程单记录编号无效或重复，无法确定交付关系")
            trips[seq] = trip
        trip_invoice_amounts: dict[str, object] = {}
        for record in mail.get("records") or []:
            matched = record.get("matched_itinerary") or {}
            if not matched:
                continue
            seq = str(matched.get("file_seq") or "")
            if matched.get("mail_id") and matched["mail_id"] != mail_id:
                continue
            if not seq or seq not in trips:
                raise ValueError(f"发票关联的行程单不存在：{seq}")
            if seq in trip_invoice_amounts:
                raise ValueError(f"同一邮件多张发票关联同一行程单：{seq}")
            trip_invoice_amounts[seq] = (record.get("fields") or {}).get("total_amount")

        for record in mail.get("records") or []:
            source = record.get("source") or {}
            if source.get("delivery_blocked") or record.get("delivery_blocked"):
                if not source.get("same_invoice_duplicate"):
                    plan.issues.append(f"{record.get('record_id')}: 凭证归属未决，未自动交付 PDF")
                continue
            chosen = _choose_pdf(cfg, mail_id, files, list(source.get("source_file_seq") or []),
                                 converted_pdfs, [])
            chosen_mail = mail
            if chosen is None:
                for ref in source.get("same_invoice_records") or []:
                    other = by_mail.get(str(ref.get("mail_id") or ""))
                    if other is None:
                        continue
                    other_record = next((r for r in other.get("records") or [] if r.get("record_id") == ref.get("record_id")), None)
                    if not other_record:
                        continue
                    chosen = _choose_pdf(cfg, str(other.get("mail_id")), other.get("files") or [],
                                         list((other_record.get("source") or {}).get("source_file_seq") or []),
                                         converted_pdfs, [])
                    if chosen is not None:
                        chosen_mail = other
                        break
            if chosen is None:
                plan.issues.append(f"{record.get('record_id')}: 来源没有可交付 PDF")
                continue
            fields = record.get("fields") or {}
            number = str(fields.get("invoice_number") or "").strip()
            if number in ambiguous_numbers:
                plan.issues.append(f"{record.get('record_id')}: 同号业务值冲突，未自动交付 PDF")
                continue
            signature = (str(fields.get("invoice_date")), str(fields.get("total_amount")),
                         str(fields.get("receipt_type")), str(fields.get("seller")))
            if number and number != "Not Found":
                previous = seen_numbers.get(number)
                if previous is not None:
                    continue
                seen_numbers[number] = signature
            matched = record.get("matched_itinerary") or {}
            trip_mail = by_mail.get(str(matched.get("mail_id") or mail_id), mail)
            trip_seq = str(matched.get("file_seq") or "")
            linked_trip = next((t for t in trip_mail.get("itineraries") or []
                                if _trip_seq(str(trip_mail.get("mail_id") or ""), t) == trip_seq), None) if matched else None
            item, path = chosen
            seq = str(item.get("file_seq") or "")
            name = namer.invoice_name(fields, f"{mail.get('mail_uid')}:{seq}", linked_trip)
            item["exported_name"] = name
            plan.entries.append(DeliveryEntry(name, path, str(chosen_mail.get("mail_id") or mail_id), seq))

        for trip in mail.get("itineraries") or []:
            seq = _trip_seq(mail_id, trip)
            if trip.get("delivery_blocked"):
                plan.issues.append(f"{mail_id}-{seq}: 行程单来源未决，未自动交付 PDF")
                continue
            chosen = _choose_pdf(cfg, mail_id, files,
                                 list(trip.get("source_file_seq") or [seq]),
                                 converted_pdfs, plan.issues)
            if chosen is None:
                continue
            item, path = chosen
            if item.get("exported_name"):
                continue
            matched_numbers = {str((r.get("fields") or {}).get("invoice_number") or "")
                               for r in mail.get("records") or []
                               if str((r.get("matched_itinerary") or {}).get("file_seq") or "") == seq
                               and str((r.get("matched_itinerary") or {}).get("mail_id") or mail_id) == mail_id}
            valid_numbers = {n for n in matched_numbers if len(n) == 20 and n.isdigit()}
            if len(valid_numbers) == 1:
                key = (next(iter(valid_numbers)), hashlib.sha256(_read_checked_pdf(cfg, path)).hexdigest())
                if key in seen_trip_content:
                    continue
                seen_trip_content.add(key)
            amount = (trip_invoice_amounts[seq] if seq in trip_invoice_amounts
                      else trip.get("match_amount"))
            source_seq = str(item.get("file_seq") or "")
            name = namer.itinerary_name(trip, f"{mail.get('mail_uid')}:{source_seq}", amount)
            item["exported_name"] = name
            plan.entries.append(DeliveryEntry(name, path, mail_id, source_seq))
    return plan


def build_delivery_zip(cfg: RunConfig, plan: DeliveryPlan | None = None) -> bytes:
    """Build and byte-check the ZIP, retaining the one business root folder."""
    summary = Path(cfg.summary_excel_file)
    if not summary.is_file():
        raise ValueError("汇总表未生成")
    debug = Path(cfg.debug_file)
    if plan is None:
        with debug.open(encoding="utf-8") as source:
            plan = prepare_delivery(cfg, json.load(source))
        json_bytes = (json.dumps(plan.mails, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    else:
        json_bytes = debug.read_bytes()
        if json.loads(json_bytes) != plan.mails:
            raise ValueError("最终 JSON 与已选交付来源不一致")
    prefix = safe_desktop_subfolder(cfg.output_folder_name) + "/"
    payload = {prefix + "发票信息汇总表.xlsx": summary.read_bytes(),
               prefix + "task_debug.json": json_bytes}
    for entry in plan.entries:
        arcname = prefix + "用户票据/" + entry.name
        if arcname in payload:
            raise ValueError(f"交付 PDF 名称重复：{entry.name}")
        payload[arcname] = _read_checked_pdf(cfg, entry.path)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in payload.items():
            archive.writestr(name, content)
    result = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        names = archive.namelist()
        if len(names) != len(payload) or set(names) != set(payload) or archive.testzip():
            raise ValueError("交付 ZIP 清单或文件内容校验失败")
        for name, expected in payload.items():
            if archive.read(name) != expected:
                raise ValueError(f"交付 ZIP 内容与来源不一致：{name}")
    if not debug.exists() or debug.read_bytes() != json_bytes:
        debug.write_bytes(json_bytes)
    return result
