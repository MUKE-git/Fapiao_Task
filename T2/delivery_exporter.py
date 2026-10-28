"""Build the user delivery from a completed T2 run without renaming source files.

The T1 business-name rule is applied to PDF copies in the ZIP. Internal Msg names
remain available for source tracing; only delivered files receive exported_name.
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

from config import RunConfig, safe_desktop_subfolder
from pdf_business_namer import BusinessPdfNamer


def _pdf_bytes(cfg: RunConfig, file: dict) -> bytes:
    if file.get("file_type") != "PDF":
        raise ValueError("交付来源不是 PDF")
    name = str(file.get("internal_name") or "")
    if not name or Path(name).name != name or Path(name).suffix.lower() != ".pdf":
        raise ValueError("内部 PDF 文件名无效")
    root = Path(cfg.extract_dir).resolve()
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        raise ValueError(f"交付 PDF 不存在或不在本轮目录：{name}")
    content = path.read_bytes()
    if b"%PDF-" not in content[:1024]:
        raise ValueError(f"交付来源不是可识别 PDF：{name}")
    return content


def build_delivery_zip(cfg: RunConfig) -> bytes:
    """Return a checked ZIP containing Excel, final JSON and business-named PDFs."""
    if not os.path.isfile(cfg.summary_excel_file):
        raise ValueError("汇总表未生成")
    with open(cfg.debug_file, encoding="utf-8") as source:
        mails = json.load(source)
    if not isinstance(mails, list):
        raise ValueError("调试 JSON 顶层不是邮件列表")

    root_name = safe_desktop_subfolder(cfg.output_folder_name)
    prefix = f"{root_name}/"
    selected: list[tuple[str, bytes]] = []
    namer = BusinessPdfNamer()
    seen_invoice_numbers: dict[str, tuple] = {}

    for mail in mails:
        if not isinstance(mail, dict):
            raise ValueError("调试 JSON 邮件条目无效")
        files = mail.get("files") or []
        by_seq = {str(item.get("file_seq")): item for item in files}
        for item in files:
            item["exported_name"] = ""

        mail_id = str(mail.get("mail_id") or "")
        itineraries_by_seq: dict[str, dict] = {}
        for itinerary in mail.get("itineraries") or []:
            record_id = str(itinerary.get("record_id") or "")
            if not record_id.startswith(mail_id + "-"):
                continue
            seq = record_id[len(mail_id) + 1:]
            if seq in itineraries_by_seq:
                raise ValueError(f"同一邮件行程单序号重复，无法确定交付文件名：{seq}")
            itineraries_by_seq[seq] = itinerary

        # The name of a matched itinerary follows the linked invoice amount.
        # Keep the itinerary's own match_amount unchanged in the JSON.
        matched_amount_by_seq: dict[str, object] = {}
        for record in mail.get("records") or []:
            matched = record.get("matched_itinerary") or {}
            if not matched:
                continue
            seq = str(matched.get("file_seq") or "")
            if not seq:
                raise ValueError("已匹配行程单缺少文件序号")
            if seq in matched_amount_by_seq:
                raise ValueError(f"同一邮件多张发票关联同一行程单，无法确定交付文件名：{seq}")
            matched_amount_by_seq[seq] = (record.get("fields") or {}).get("total_amount")

        for record in mail.get("records") or []:
            fields = record.get("fields") or {}
            source_seqs = (record.get("source") or {}).get("source_file_seq") or []
            candidates = [by_seq.get(str(seq)) for seq in source_seqs]
            chosen = next((item for item in candidates if item and item.get("file_type") == "PDF"), None)
            if chosen is None:
                continue
            number = str(fields.get("invoice_number") or "").strip()
            signature = (str(fields.get("invoice_date")), str(fields.get("total_amount")),
                         str(fields.get("receipt_type")), str(fields.get("seller")))
            if number and number != "Not Found":
                previous = seen_invoice_numbers.get(number)
                if previous is not None:
                    if previous != signature:
                        raise ValueError(f"同票号记录内容冲突，无法选择交付 PDF：{number}")
                    continue
                seen_invoice_numbers[number] = signature
            matched = record.get("matched_itinerary") or {}
            matched_itinerary = None
            if matched:
                matched_seq = str(matched.get("file_seq") or "")
                matched_itinerary = itineraries_by_seq.get(matched_seq)
                if matched_itinerary is None:
                    raise ValueError(f"发票关联的行程单不存在，无法确定交付文件名：{matched_seq}")
            name = namer.invoice_name(fields, f"{mail.get('mail_uid')}:{chosen.get('file_seq')}",
                                      matched_itinerary)
            selected.append((name, _pdf_bytes(cfg, chosen)))
            chosen["exported_name"] = name

        for itinerary in mail.get("itineraries") or []:
            record_id = str(itinerary.get("record_id") or "")
            seq = record_id[len(mail_id) + 1:] if record_id.startswith(mail_id + "-") else ""
            chosen = by_seq.get(seq)
            if chosen is None or chosen.get("file_type") != "PDF":
                continue
            if chosen.get("exported_name"):
                continue
            amount_value = (matched_amount_by_seq[seq] if seq in matched_amount_by_seq
                            else itinerary.get("match_amount"))
            name = namer.itinerary_name(itinerary, f"{mail.get('mail_uid')}:{seq}", amount_value)
            selected.append((name, _pdf_bytes(cfg, chosen)))
            chosen["exported_name"] = name

    json_bytes = (json.dumps(mails, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    excel_bytes = Path(cfg.summary_excel_file).read_bytes()
    expected = {prefix + "发票信息汇总表.xlsx", prefix + "task_debug.json"}
    expected.update(prefix + "用户票据/" + name for name, _ in selected)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(prefix + "发票信息汇总表.xlsx", excel_bytes)
        archive.writestr(prefix + "task_debug.json", json_bytes)
        for name, content in selected:
            archive.writestr(prefix + "用户票据/" + name, content)
    zip_bytes = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        if set(archive.namelist()) != expected or archive.testzip() is not None:
            raise ValueError("交付 ZIP 清单或文件内容校验失败")
    Path(cfg.debug_file).write_bytes(json_bytes)
    return zip_bytes
