"""T2-4：本轮发票号码重复检查与 PDF 来源冲突的 AI 二次仲裁。

入口仍为 stage_ports 预留的 arbitrate_records(mails) -> mails。可选 cfg 由
load_stage_ports 在实际运行时绑定；不读取历史 Excel，不修改原始发票字段。
"""
from __future__ import annotations

from prompt_loader import load_prompt

import json
import os
import re
import time
from collections import OrderedDict
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Callable

import pdfplumber
from dashscope import Generation

from attachment_processor import CollectedFile
from config import RunConfig
from data_model import (
    FileRecord, InvoiceRecord, MailRecord, NOT_FOUND, STATUS_MANUAL_REVIEW, normalize_amount,
)


REQUIRED_FIELDS = ("invoice_date", "invoice_number", "total_amount", "receipt_type", "seller")
FIELD_NAMES = {
    "invoice_date": "开票日期", "invoice_number": "发票号码",
    "total_amount": "报销金额", "receipt_type": "票面类型", "seller": "销售方",
}


def _number(record: InvoiceRecord) -> str:
    value = str(record.fields.invoice_number or "").strip()
    return "" if value.lower() == NOT_FOUND.lower() else value


def _value(record: InvoiceRecord, field: str):
    value = getattr(record.fields, field)
    if field == "total_amount":
        amount = normalize_amount(value)
        return amount if isinstance(amount, Decimal) else str(amount).strip()
    return str(value or "").strip()


def _signature(record: InvoiceRecord) -> tuple:
    return tuple(_value(record, name) for name in REQUIRED_FIELDS)


def _reliable_xml_values(mail: MailRecord, record: InvoiceRecord) -> dict[str, str]:
    """Only values backed by a located XML field may control T3 arbitration."""
    xml_seqs = {item.file_seq for item in mail.files if item.file_type == "XML"}
    values: dict[str, str] = {}
    for field in REQUIRED_FIELDS:
        evidence = (record.source.field_evidence or {}).get(field, [])
        supported = {str(item.get("normalized")) for item in evidence
                     if item.get("file_seq") in xml_seqs and item.get("location")
                     and str(item.get("normalized")) == str(_value(record, field))}
        if len(supported) == 1 and not any(
                conflict.get("field") == field and conflict.get("reason") == "xml_conflict"
                for conflict in record.source.conflicts):
            values[field] = next(iter(supported))
    return values


def _choose_with_xml(entries: list[tuple[MailRecord, InvoiceRecord]],
                     differences: list[str]) -> str | None:
    xml = [(record, _reliable_xml_values(mail, record)) for mail, record in entries]
    xml = [(record, values) for record, values in xml if values]
    if not xml or any(field not in values for _, values in xml for field in differences):
        return None
    if any(len({values[field] for _, values in xml}) != 1 for field in differences):
        return None
    winners = [record.record_id for record, values in xml
               if all(str(_value(record, field)) == values[field] for field in differences)]
    return winners[0] if len(winners) == 1 else None


def _alert(mail: MailRecord, record: InvoiceRecord, reason: str) -> None:
    seq = record.source.source_file_seq[0] if record.source.source_file_seq else ""
    file = next((item for item in mail.files if item.file_seq == seq), None)
    mail.arbitration_alerts.append({
        "mail_subject": mail.subject, "mail_date": mail.mail_date,
        "mail_date_header": "", "file_seq": seq,
        "filename": file.original_name if file else "", "flags": [reason],
    })
    mail.error_reason = (mail.error_reason + ";" if mail.error_reason else "") + reason


def _source_pdf(file: FileRecord, cfg: RunConfig) -> CollectedFile | None:
    if file.file_type != "PDF" or not file.internal_name:
        return None
    # internal_name 是已登记的文件名，仍检查不能逃出本轮输出目录。
    if os.path.basename(file.internal_name) != file.internal_name:
        return None
    root = Path(cfg.extract_dir).resolve()
    path = (root / file.internal_name).resolve()
    if path.parent != root or not path.is_file():
        return None
    # 复读阶段使用文件元数据副本，避免改动首次提取的来源快照。
    return CollectedFile(meta=replace(file), path=str(path))


def _pdf_text_file(file: FileRecord, cfg: RunConfig) -> str:
    source = _source_pdf(file, cfg)
    if source is None:
        return ""
    try:
        with pdfplumber.open(source.path) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception:
        return ""


def _pdf_text(mail: MailRecord, record: InvoiceRecord, cfg: RunConfig) -> str:
    seq = record.source.source_file_seq[0] if record.source.source_file_seq else ""
    file = next((item for item in mail.files if item.file_seq == seq), None)
    return _pdf_text_file(file, cfg) if file is not None else ""


def _response_json(response) -> dict | None:
    if isinstance(response, dict) and "selected_record_id" in response:
        return response
    if getattr(response, "status_code", None) != 200:
        return None
    output = getattr(response, "output", None)
    raw = getattr(output, "text", "") or ""
    if not raw and getattr(output, "choices", None):
        raw = output.choices[0].get("message", {}).get("content", "")
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if raw.startswith("```json"):
        raw = raw[7:]
    elif raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    try:
        data = json.loads(raw.strip())
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _quote_supports(field: str, value: object, quote: str) -> bool:
    if not quote or not isinstance(quote, str):
        return False
    target = str(value).strip()
    if not target or target.lower() == NOT_FOUND.lower():
        return False
    if field == "invoice_date":
        digits = "".join(re.findall(r"\d", quote))
        return re.sub(r"\D", "", target) in digits
    if field == "total_amount":
        try:
            expected = Decimal(target)
            return any(Decimal(match.replace(",", "")) == expected for match in
                       re.findall(r"\d[\d,]*(?:\.\d+)?", quote))
        except Exception:
            return False
    clean_quote = re.sub(r"\s+", "", quote)
    clean_target = re.sub(r"\s+", "", target)
    return clean_target in clean_quote


def _confirmed_selection(data: dict | None, entries: list[tuple[MailRecord, InvoiceRecord]],
                         texts: dict[str, str], differences: list[str]) -> tuple[str, list[dict]] | None:
    if not isinstance(data, dict):
        return None
    selected = data.get("selected_record_id")
    by_id = {record.record_id: record for _, record in entries}
    if not isinstance(selected, str) or selected not in by_id:
        return None
    evidence = data.get("evidence")
    if not isinstance(evidence, list):
        return None
    source = texts.get(selected, "")
    checked: list[dict] = []
    for field in differences:
        proofs = [item for item in evidence if isinstance(item, dict) and item.get("field") == field]
        proof = next((item for item in proofs if isinstance(item.get("quote"), str)
                      and re.sub(r"\s+", "", item["quote"]) in re.sub(r"\s+", "", source)
                      and _quote_supports(field, _value(by_id[selected], field), item["quote"])), None)
        if proof is None:
            return None
        checked.append({"field": field, "quote": proof["quote"]})
    # 若另一候选的所有差异值也能从它自己的原文找到，模型无法仅凭这些证据
    # 证明哪张应被采信；保留各候选交人工复核。
    for record_id, rival in by_id.items():
        if record_id != selected and all(_quote_supports(
                field, _value(rival, field), texts.get(record_id, "")) for field in differences):
            return None
    return selected, checked


def _choose_with_ai(entries: list[tuple[MailRecord, InvoiceRecord]], cfg: RunConfig | None,
                    text_loader: Callable[[MailRecord, InvoiceRecord, RunConfig], str] | None,
                    ai_call: Callable | None,
                    ) -> tuple[str, list[dict]] | None:
    if cfg is None:
        return None
    prepared_files = {}
    visual_parts = []
    if text_loader is None:
        from pdf_input import prepare_pdf
        import base64
        try:
            ordered = [(mail, [item for item in mail.files if record.source.source_file_seq
                              and item.file_seq == record.source.source_file_seq[0]]) for mail, record in entries]
            ordered += [(mail, mail.files) for mail, _ in entries]
            for mail, files in ordered:
                for item in files:
                    key = (mail.mail_id, item.file_seq)
                    if item.file_type != 'PDF' or key in prepared_files:
                        continue
                    source = _source_pdf(item, cfg)
                    if source is None:
                        return None
                    prepared = prepare_pdf(source.path)
                    prepared_files[key] = prepared
                    for page, (data, mime) in zip(prepared.image_pages, prepared.images):
                        visual_parts.extend([{'text': f'mail_id={mail.mail_id}; file_seq={item.file_seq}; PDF page={page}'},
                                             {'image': 'data:' + mime + ';base64,' + base64.b64encode(data).decode('ascii')}])
            image_parts = [p for p in visual_parts if 'image' in p]
            if len(image_parts) > 8 or sum(len(p['image']) for p in image_parts) > 20_000_000:
                return None
        except Exception:
            return None
    def shared_text(mail, record, cfg):
        seq = record.source.source_file_seq[0] if record.source.source_file_seq else ''
        prepared = prepared_files.get((mail.mail_id, seq))
        return prepared.text if prepared else ''
    loader = text_loader or shared_text
    try:
        texts = {record.record_id: loader(mail, record, cfg) for mail, record in entries}
    except Exception:
        return None
    if any(not text.strip() for text in texts.values()):
        return None
    differences = [field for field in REQUIRED_FIELDS
                   if len({_value(record, field) for _, record in entries}) > 1]
    attachment_groups = []
    seen_mails = set()
    for mail, _ in entries:
        if mail.mail_id in seen_mails:
            continue
        seen_mails.add(mail.mail_id)
        attachments = []
        for item in mail.files:
            source_record = next((record for candidate_mail, record in entries
                                  if candidate_mail is mail and item.file_seq in record.source.source_file_seq), None)
            pdf_text = (texts[source_record.record_id] if source_record is not None
                        else prepared_files[(mail.mail_id, item.file_seq)].text
                        if (mail.mail_id, item.file_seq) in prepared_files
                        else _pdf_text_file(item, cfg) if item.file_type == "PDF" else "")
            attachments.append({"file_seq": item.file_seq, "file_type": item.file_type,
                                "original_name": item.original_name, "pdf_text": pdf_text})
        attachment_groups.append({"mail_uid": mail.mail_uid, "mail_subject": mail.subject,
                                  "mail_date": mail.mail_date, "attachments": attachments})
    if any(item["file_type"] == "PDF" and not item["pdf_text"].strip()
           for group in attachment_groups for item in group["attachments"]):
        return None
    payload = {
        "different_fields": differences,
        "attachment_groups": attachment_groups,
        "candidates": [{
            "record_id": record.record_id,
            "mail_subject": mail.subject, "mail_date": mail.mail_date,
            "mail_uid": mail.mail_uid,
            "source_files": [{"file_seq": item.file_seq, "original_name": item.original_name}
                             for item in mail.files if item.file_seq in record.source.source_file_seq],
            "fields": {field: str(_value(record, field)) for field in REQUIRED_FIELDS},
            "pdf_text": texts[record.record_id],
        } for mail, record in entries],
    }
    messages = [
        {"role": "system", "content": load_prompt("arbitration")},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    if len(json.dumps(payload, ensure_ascii=False)) > 36000:
        return None
    if visual_parts:
        messages = [{'role': 'system', 'content': [{'text': load_prompt('arbitration')}]},
                    {'role': 'user', 'content': [{'text': json.dumps(payload, ensure_ascii=False)}] + visual_parts}]
    call = ai_call or Generation.call
    data = None
    for attempt, delay in enumerate((1, 3, None)):
        try:
            if ai_call is not None:
                response = call(model=cfg.dashscope_model, messages=messages)
            else:
                from model_gateway import model_call
                response = model_call(api_key=cfg.dashscope_api_key,
                                     model=cfg.dashscope_vision_model if visual_parts else cfg.dashscope_model,
                                     messages=messages, vision=bool(visual_parts),
                                     source=cfg.dashscope_vision_model_source if visual_parts else cfg.dashscope_model_source,
                                     base_url=getattr(cfg, "deepseek_base_url", ""))
            data = _response_json(response)
            if data is not None:
                break
            status = getattr(response, "status_code", None)
            if status not in (429,) and not (isinstance(status, int) and status >= 500):
                break
        except Exception:
            pass
        if delay is None:
            break
        time.sleep(delay)
    decision = _confirmed_selection(data, entries, texts, differences)
    if visual_parts:
        # A page/region citation locates visual evidence, but cannot establish
        # its truth by string search. Never treat model OCR as independent proof.
        for mail, record in entries:
            _alert(mail, record, 'PDF 图片证据已提交视觉仲裁；缺少可独立核对的文字证据，需人工核对页面')
        decision = None
    if ai_call is None:
        from model_gateway import record_processing
        record_processing(dict(configured_model=cfg.dashscope_vision_model if visual_parts else cfg.dashscope_model,
                               config_source=cfg.dashscope_vision_model_source if visual_parts else cfg.dashscope_model_source,
                               model_kind="vision_arbitration" if visual_parts else "text_arbitration", call_attempted=True,
                               successful_response=getattr(locals().get("response"), "status_code", 0) == 200,
                               result="selection_confirmed" if decision else "manual_review_required"))
    return decision


def arbitrate_records(mails: list[MailRecord], cfg: RunConfig | None = None, *,
                      text_loader: Callable[[MailRecord, InvoiceRecord, RunConfig], str] | None = None,
                      ai_call: Callable | None = None,
                      ) -> list[MailRecord]:
    """原记录全部保留；仅决定哪些记录不再追加 Excel 行。"""
    for mail in mails:
        mail.excel_skip_record_ids.clear()
        mail.arbitration_alerts.clear()
    by_number: OrderedDict[str, list[tuple[MailRecord, InvoiceRecord]]] = OrderedDict()
    for mail in mails:
        for record in mail.records:
            number = _number(record)
            if number:
                by_number.setdefault(number, []).append((mail, record))

    for number, entries in by_number.items():
        if len(entries) < 2:
            continue
        # Same-number records with a verifiable XML invoice use that record
        # directly; other formats must not decide or fill its business fields.
        xml_entries = [(mail, record) for mail, record in entries
                       if "invoice_number" in _reliable_xml_values(mail, record)]
        if re.fullmatch(r"[0-9]{20}", number) and xml_entries and len({_signature(record) for _, record in xml_entries}) == 1:
            chosen = xml_entries[0][1]
            for mail, record in entries:
                if record is chosen:
                    continue
                mail.excel_skip_record_ids.add(record.record_id)
                record.source.delivery_blocked = True
                _alert(mail, record, f"{record.record_id} 与 {chosen.record_id} 同票，采用 XML 记录；原记录保留在 JSON")
            continue
        by_signature: OrderedDict[tuple, list[tuple[MailRecord, InvoiceRecord]]] = OrderedDict()
        for entry in entries:
            by_signature.setdefault(_signature(entry[1]), []).append(entry)
        representatives = [items[0] for items in by_signature.values()]
        for items in by_signature.values():
            kept = items[0][1]
            for mail, record in items[1:]:
                mail.excel_skip_record_ids.add(record.record_id)
                record.source.delivery_blocked = True
                _alert(mail, record, f"{record.record_id} 与 {kept.record_id} 的发票号及五个必填字段一致，已跳过重复 Excel 行；原记录保留在 JSON")
        if len(representatives) == 1:
            continue
        differences = [field for field in REQUIRED_FIELDS
                       if len({_value(record, field) for _, record in representatives}) > 1]
        has_xml = any(any(item.file_type == "XML" and
                              item.file_seq in record.source.source_file_seq
                              for item in mail.files)
                      for mail, record in representatives)
        xml_chosen = _choose_with_xml(representatives, differences) if has_xml else None
        decision = ((xml_chosen, []) if xml_chosen else
                    None if has_xml else
                    _choose_with_ai(representatives, cfg, text_loader, ai_call))
        if decision:
            chosen_id, evidence = decision
            channel = "XML 可靠字段优先" if xml_chosen else "AI 二次仲裁"
            evidence_text = "；".join(f"{FIELD_NAMES[item['field']]}：{item['quote'][:120]}"
                                     for item in evidence)
            for mail, record in representatives:
                if record.record_id == chosen_id:
                    _alert(mail, record, f"同票号 {number} 存在字段冲突；{channel}采信 {chosen_id}；原文证据 {evidence_text}")
                else:
                    mail.excel_skip_record_ids.add(record.record_id)
                    record.status = STATUS_MANUAL_REVIEW
                    record.source.delivery_blocked = True
                    _alert(mail, record, f"{record.record_id} 与 {chosen_id} 同票号但必填字段冲突；{channel}采信后者，本条未写入 Excel，原记录保留在 JSON")
        else:
            for mail, record in representatives:
                record.status = STATUS_MANUAL_REVIEW
                record.source.delivery_blocked = True
                _alert(mail, record, f"{record.record_id} 同票号 {number} 的必填字段冲突，无法确认其一；本条保留 Excel 并需人工核对")
    return mails
