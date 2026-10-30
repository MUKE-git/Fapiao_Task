"""T2 单文件 PDF 处理器：预读、分类、旧文本 AI/正则能力适配。

只处理传入的一份 PDF，返回其文件级结果；不收邮件、不写 Excel、
不决定其他发票或行程单的配对关系。T1/ 只是只读回归基线。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from decimal import Decimal

from ai_client import call_ai_audit_and_rename
from attachment_processor import CollectedFile, local_inspect_pdf
from config import RunConfig
from data_model import NOT_FOUND, PRE_READ_UNKNOWN, normalize_amount
from fallback_extractor import build_fallback_audit_result
from processor_contracts import DocumentResult
from type_classifier import merge_type_from_name_and_local, normalize_doc_role


def _preserve_invoice_amount_scale(data: dict, pre_read_amount: object, role: str) -> dict:
    """Keep PDF text's trailing zeroes only when the AI chose the same value."""
    if role != "Invoice":
        return data
    local = normalize_amount(pre_read_amount)
    chosen = normalize_amount(data.get("total_amount"))
    if not (isinstance(local, Decimal) and isinstance(chosen, Decimal)
            and local.is_finite() and chosen.is_finite() and local > 0
            and local == chosen and -4 <= local.as_tuple().exponent < chosen.as_tuple().exponent):
        return data
    return {**data, "total_amount": format(local, "f")}


def process_pdf(file: CollectedFile, cfg: RunConfig, mail_subject: str,
                mail_date: str, run_id: str = "") -> DocumentResult:
    """返回与 file_seq 绑定的单文件结果；AI 失败时沿用本地兜底。"""
    local_type, text = local_inspect_pdf(file.path)
    pre_type = normalize_doc_role(
        merge_type_from_name_and_local(file.meta.original_name, local_type)
    )
    file.meta.pre_read_type = pre_type
    fallback_input = [{"identified_type": pre_type, "text_snapshot": text[:3000]}]
    pre_read = build_fallback_audit_result(fallback_input)
    file.meta.pre_read_invoice_no = str(pre_read.get("invoice_number") or NOT_FOUND)
    file.meta.pre_read_amount = normalize_amount(
        pre_read.get("match_amount") if pre_type == "Itinerary" else pre_read.get("total_amount")
    )

    # 旧接口同时重命名传入 PDF。隔离副本，保证正式存盘名持续携带 file_seq。
    with tempfile.TemporaryDirectory(prefix="fapiao_pdf_") as temp_dir:
        temp_path = os.path.join(temp_dir, os.path.basename(file.path))
        shutil.copyfile(file.path, temp_path)
        task_info = {"files": [{
            "local_path": temp_path,
            "attachment_original_name": file.meta.original_name,
            "identified_type": pre_type,
            "local_pdf_type": local_type,
            "text_snapshot": text[:3000],
        }]}
        file.meta.model_usage = {"model_kind": "text", "configured_model": cfg.dashscope_model,
                                 "config_source": getattr(cfg, "dashscope_model_source", "programmatic")}
        task_info["model_usage"] = file.meta.model_usage
        audit, error, mismatches, manual = call_ai_audit_and_rename(
            task_info, model=cfg.dashscope_model, mail_subject=mail_subject,
            mail_date_hint=mail_date, run_id=run_id,
            api_key=cfg.dashscope_api_key,
        )
        expected_name = os.path.basename(temp_path)

    for mismatch in mismatches:
        mismatch["local_path"] = file.path
        mismatch["filename"] = os.path.basename(file.path)
    for item in manual:
        item["filename"] = os.path.basename(file.path)

    data = audit if isinstance(audit, dict) else pre_read
    if not isinstance(audit, dict):
        file.meta.model_usage["extraction_source"] = "local_regex"
    file.meta.model_usage["result"] = error or "response_parsed"
    ai_roles = data.get("file_classifications") or []
    valid_classification = (isinstance(ai_roles, list) and len(ai_roles) == 1
                            and isinstance(ai_roles[0], dict)
                            and ai_roles[0].get("file") == expected_name
                            and ai_roles[0].get("role") in ("Invoice", "Itinerary", "Unknown"))
    if isinstance(audit, dict) and not valid_classification:
        error = (error + ";" if error else "") + "PDF 单文件分类结果缺失、数量错误或文件名不对应"
        manual.append({"filename": os.path.basename(file.path),
                       "reason": "PDF 单文件分类契约异常"})
    ai_role = (normalize_doc_role(ai_roles[0].get("role"))
               if valid_classification else PRE_READ_UNKNOWN)
    role = pre_type if pre_type != PRE_READ_UNKNOWN else ai_role
    if isinstance(audit, dict):
        data = _preserve_invoice_amount_scale(data, file.meta.pre_read_amount, role)
    return DocumentResult(file_seq=file.meta.file_seq, role=role, fields=data,
                          source_file_seq=[file.meta.file_seq], error_reason=error,
                          mismatches=mismatches, manual_unknown=manual)
