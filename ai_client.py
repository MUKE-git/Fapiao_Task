"""
AI 审计与重命名模块（由 invoice_pipeline.py 拆分而来，契约 = A 类：仅换 import，逻辑不变）。

职责：
- call_ai_audit_and_rename：把一封邮件下的多个 PDF 文本拼给百炼 qwen，带重试（429/5xx 最多2次，
  间隔1s/3s），JSON 解析失败用本机正则兜底，字段缺失补默认值并记录 error_reason。
- _rename_pdfs_with_audit：按「原名>AI>正文」解析类型后批量重命名，三层不一致加「（待人工核查）」，
  内嵌 agent debug 日志（写 debug-{run_id}.log）。

依赖：build_fallback_audit_result 来自 fallback_extractor.py（无循环依赖，可直接 import）。
"""

from __future__ import annotations

from prompt_loader import load_prompt

import json
import base64
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from dashscope import Generation
except ImportError:  # Fixed-response tests run without the optional service SDK.
    Generation = None

from fallback_extractor import build_fallback_audit_result

from utils import (
    build_non_conflicting_path,
    clean_receipt_type,
    format_amount_text,
)

from type_classifier import (
    _resolve_ai_role_for_file,
    classify_type_from_attachment_name,
    collect_classification_mismatches,
    normalize_doc_role,
    resolve_effective_type_for_rename,
    triple_type_disagreement,
)


# The old PDF audit entry point below remains for T2 regression. T3 processors
# use this side-effect-free, single-file adapter instead.
T3_EXTRACTION_PROMPT_VERSION = "t3-file-v1"
_T3_ROLES = {"Invoice", "Itinerary", "Unknown"}
_T3_INVOICE_FIELDS = ("invoice_number", "invoice_date", "total_amount",
                      "invoice_type", "receipt_type", "seller")
_T3_ITINERARY_FIELDS = ("match_amount", "travel_time", "origin_dest")


@dataclass(frozen=True)
class ModelFileInput:
    file_seq: str
    internal_name: str
    original_name: str
    file_type: str
    pre_read_role: str = "Unknown"
    text: str = ""
    # Already decoded, complete image frames in (bytes, MIME type) order.
    images: tuple[tuple[bytes, str], ...] = ()
    mail_id: str = ""


@dataclass
class ModelFileOutput:
    role: str = "Unknown"
    fields: dict[str, str] = field(default_factory=dict)
    field_evidence: dict[str, list[dict]] = field(default_factory=dict)
    error_reason: str = ""


def _t3_response_text(response: object) -> str:
    output = getattr(response, "output", None)
    raw = getattr(output, "text", None)
    if raw:
        return str(raw)
    choices = getattr(output, "choices", None) or []
    if choices:
        choice = choices[0]
        message = choice.get("message", {}) if isinstance(choice, dict) else getattr(choice, "message", None)
        content = message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
        if isinstance(content, list):
            return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        return str(content or "")
    return ""


def _t3_default_transport(*, model: str, api_key: str, messages: list, vision: bool, source="programmatic", base_url=""):
    from model_gateway import model_call
    return model_call(api_key=api_key, model=model, messages=messages, vision=vision, source=source, base_url=base_url,
                     response_format={"type": "json_object"},
                     **({} if vision else {"result_format": "message"}))


def extract_single_file(request: ModelFileInput, cfg: object,
                        transport: Callable[..., object] | None = None,
                        sleep: Callable[[float], None] = time.sleep,
                        diagnostics: dict | None = None) -> ModelFileOutput:
    """Return one file's candidate only; never rename files or pair documents.

    ``transport`` accepts the same keyword arguments as _t3_default_transport,
    so fixed-response tests replace only the external service boundary.
    """
    trace = diagnostics if diagnostics is not None else {}
    trace.update(call_attempted=False, successful_response=False, extraction_source="none")
    def result(**kwargs):
        trace["result"] = kwargs.get("error_reason") or "response_parsed"
        if trace["result"].startswith(("MODEL_JSON_INVALID", "MODEL_SHAPE_INVALID", "MODEL_CLASSIFICATION_INVALID")):
            trace["extraction_source"] = "none"
        from model_gateway import record_processing
        record_processing(trace)
        return ModelFileOutput(**kwargs)

    if not request.file_seq or not request.internal_name or not request.file_type:
        return result(error_reason="MODEL_INPUT_IDENTITY_MISSING")
    if len(request.text) > 12000 or len(request.images) > 8 or sum(len(p[0]) for p in request.images) > 15_000_000:
        return result(error_reason="MODEL_INPUT_LIMIT_EXCEEDED")
    vision = bool(request.images)
    key = str(getattr(cfg, "dashscope_api_key", "") or os.getenv("DASHSCOPE_API_KEY", ""))
    if transport is None and not key:
        return result(error_reason="MODEL_API_KEY_MISSING")
    model = str((getattr(cfg, "dashscope_vision_model", None) or os.getenv("DASHSCOPE_VISION_MODEL")
                 or "qwen3-vl-plus") if vision else (getattr(cfg, "dashscope_model", None) or "qwen3.7-max"))
    system = load_prompt("single_file")
    identity = (f"mail_id={request.mail_id}; file_seq={request.file_seq}; internal_name={request.internal_name}; "
                f"original_name={request.original_name}; format={request.file_type}; "
                f"pre_read_role={request.pre_read_role}")
    if vision:
        system += load_prompt("image_extra")
        content = [{"image": "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")}
                   for data, mime in request.images]
        content.append({"text": identity + ("\n" + request.text if request.text else "")})
        messages = [{"role": "system", "content": [{"text": system}]},
                    {"role": "user", "content": content}]
    else:
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": identity + "\n" + request.text}]
    source = getattr(cfg, "dashscope_vision_model_source" if vision else "dashscope_model_source", "programmatic")
    trace.update(configured_model=model, config_source=source)
    send = transport or (lambda **kwargs: _t3_default_transport(source=source, base_url=getattr(cfg, "deepseek_base_url", ""), **kwargs))
    response = None
    for attempt in range(3):
        try:
            trace["call_attempted"] = True
            response = send(model=model, api_key=key, messages=messages, vision=vision)
            status = int(getattr(response, "status_code", 0) or 0)
        except Exception as exc:
            if isinstance(exc, (TimeoutError, ConnectionError)) or any(
                    word in type(exc).__name__.lower() for word in ("timeout", "connection")):
                status = 503
                response = None
            else:
                return result(error_reason="MODEL_TRANSPORT_" + type(exc).__name__)
        if status == 200:
            trace["successful_response"] = True
            trace["extraction_source"] = "model_response"
            break
        if (status == 429 or status >= 500) and attempt < 2:
            sleep((1, 3)[attempt])
            continue
        return result(error_reason=f"MODEL_API_STATUS_{status}")
    raw = _t3_response_text(response).strip()
    if raw.startswith("```json"):
        raw = raw[7:].removesuffix("```").strip()
    try:
        parsed = json.loads(raw, parse_float=Decimal)
    except (ValueError, TypeError):
        return result(error_reason="MODEL_JSON_INVALID")
    if not isinstance(parsed, dict):
        return result(error_reason="MODEL_SHAPE_INVALID")
    classifications = parsed.get("file_classifications")
    if not (isinstance(classifications, list) and len(classifications) == 1
            and isinstance(classifications[0], dict)
            and classifications[0].get("file") == request.internal_name
            and classifications[0].get("role") in _T3_ROLES):
        return result(error_reason="MODEL_CLASSIFICATION_INVALID")
    role = classifications[0]["role"]
    if parsed.get("multiple_documents") is True:
        return result(role="Unknown", error_reason="MULTIPLE_DOCUMENTS_IN_IMAGE")
    names = _T3_INVOICE_FIELDS if role == "Invoice" else _T3_ITINERARY_FIELDS if role == "Itinerary" else ()
    values: dict[str, str] = {}
    evidence: dict[str, list[dict]] = {}
    errors: list[str] = []
    classification = classifications[0]
    top_evidence = parsed.get("field_evidence")
    nested_evidence = classification.get("field_evidence")
    submitted_evidence = (
        top_evidence if isinstance(top_evidence, dict) and top_evidence
        else nested_evidence if isinstance(nested_evidence, dict)
        else {}
    )
    for name in names:
        raw_value = parsed.get(name)
        if raw_value is None:
            raw_value = classification.get(name)
        if isinstance(raw_value, dict) and "value" in raw_value:
            raw_value = raw_value["value"]
        if raw_value is None:
            errors.append("MODEL_FIELD_MISSING:" + name)
            values[name] = "" if name == "invoice_type" else "Not Found"
            continue
        if not isinstance(raw_value, str):
            errors.append("MODEL_FIELD_TYPE:" + name)
            values[name] = "" if name == "invoice_type" else "Not Found"
            continue
        value = raw_value.strip()
        values[name] = value if value not in ("", "Not Found") else ("" if name == "invoice_type" else "Not Found")
        if values[name] in ("", "Not Found"):
            continue
        source = submitted_evidence.get(name)
        source = source if isinstance(source, list) else [source] if isinstance(source, dict) else []
        valid = [dict(file_seq=request.file_seq, raw=str(item["raw"]),
                      normalized=values[name], location=str(item["location"]))
                 for item in source if isinstance(item, dict)
                 and item.get("raw") and item.get("location")]
        if valid:
            evidence[name] = valid
        else:
            errors.append("MODEL_EVIDENCE_MISSING:" + name)
    if role == "Invoice":
        if values.get("invoice_number") not in (None, "Not Found") and not re.fullmatch(r"\d{20}", values["invoice_number"]):
            errors.append("MODEL_INVOICE_NUMBER_INVALID")
        date = values.get("invoice_date")
        if date not in (None, "Not Found"):
            try:
                if datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d") != date:
                    raise ValueError
            except ValueError:
                errors.append("MODEL_INVOICE_DATE_INVALID")
        amount_name = "total_amount"
    else:
        amount_name = "match_amount"
    amount = values.get(amount_name)
    if amount not in (None, "Not Found"):
        try:
            decimal = Decimal(amount)
            if not decimal.is_finite() or decimal <= 0 or decimal.as_tuple().exponent < -4:
                raise ValueError
        except (ArithmeticError, ValueError):
            errors.append("MODEL_AMOUNT_INVALID")
    return result(role=role, fields=values, field_evidence=evidence,
                           error_reason=";".join(errors))


def _rename_pdfs_with_audit(
    task_info: dict,
    data: dict,
    mail_subject: str,
    mail_date_hint: str,
    run_id: str = "",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按「原名>AI>正文」解析类型后重命名；仅当三种类型依据不一致时加「（待人工核查）」。"""
    invoice_amount_text = format_amount_text(data.get("total_amount", "0"))
    itinerary_amount_text = format_amount_text(data.get("match_amount", "Not Found"))
    inv_date_raw = str(data.get("invoice_date", "")).strip()
    if not inv_date_raw or inv_date_raw == "Not Found":
        inv_date_raw = "未知日期"
    travel_time_raw = str(data.get("travel_time", "")).strip()
    travel_date_raw = travel_time_raw.split(" ")[0] if travel_time_raw and " " in travel_time_raw else travel_time_raw
    if not travel_date_raw or travel_date_raw.lower() == "not found":
        travel_date_raw = inv_date_raw
    classifications = data.get("file_classifications") or []
    files = task_info.get("files") or []
    manual_unknown: List[Dict[str, Any]] = []

    # #region agent log
    def _dbg(loc: str, msg: str, data_d: Dict[str, Any], hid: str) -> None:
        try:
            log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "04 logs")
            os.makedirs(log_dir, exist_ok=True)
            p = os.path.join(log_dir, f"debug-{run_id}.log")
            with open(p, "a", encoding="utf-8") as df:
                df.write(
                    json.dumps(
                        {
                            "sessionId": run_id,
                            "timestamp": int(time.time() * 1000),
                            "location": loc,
                            "message": msg,
                            "data": data_d,
                            "hypothesisId": hid,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError:
            pass

    # #endregion

    for i, f in enumerate(files):
        old = f["local_path"]
        ai_role = _resolve_ai_role_for_file(files, classifications, i)
        eff = resolve_effective_type_for_rename(f, ai_role)
        eff_n = normalize_doc_role(eff)
        disagree = triple_type_disagreement(f, ai_role)
        tag = "（待人工核查）" if disagree else ""

        # #region agent log
        _dbg(
            "invoice_pipeline.py:_rename_pdfs_with_audit",
            "triple_type_check",
            {
                "basename": os.path.basename(old),
                "name_hint": classify_type_from_attachment_name(
                    (f.get("attachment_original_name") or "").strip()
                ),
                "local_pdf_type": f.get("local_pdf_type"),
                "ai_role": ai_role,
                "eff_n": eff_n,
                "disagree": disagree,
                "suffix": bool(tag),
            },
            "H_triple",
        )
        # #endregion

        if eff_n == "Invoice":
            inv_type = clean_receipt_type(str(data.get("receipt_type") or data.get("invoice_type") or "发票"))
            base = f"{inv_type}+{inv_date_raw}+{invoice_amount_text}元"
        elif eff_n == "Itinerary":
            base = f"行程单+{travel_date_raw}+{itinerary_amount_text}元"
        else:
            base = f"票据+{inv_date_raw}+{invoice_amount_text}元"
        new_name = f"{base}{tag}.pdf"
        raw_new = os.path.join(os.path.dirname(old), new_name)
        new = build_non_conflicting_path(raw_new)
        os.rename(old, new)
        if tag:
            manual_unknown.append({
                "filename": os.path.basename(new),
                "mail_subject": mail_subject,
                "mail_date": mail_date_hint,
                "ai_role_used": str(ai_role).strip() if ai_role else "",
                "type_used": eff_n,
            })

    mm = collect_classification_mismatches(task_info, data, mail_subject)
    return mm, manual_unknown


def _connection_failure_category(exc: Exception) -> str:
    """Classify without saving exception messages, URLs, credentials or payloads."""
    pending = [exc]
    seen = set()
    signals = []
    while pending and len(seen) < 20:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, BaseException):
            signals.append(type(item).__name__.lower())
            signals.extend(str(arg).lower() for arg in item.args if isinstance(arg, str))
            pending.extend(arg for arg in item.args if isinstance(arg, BaseException))
            pending.extend(x for x in (item.__cause__, item.__context__) if x is not None)
    text = " ".join(signals)
    for category, words in (("proxy", ("proxyerror", "proxy error", "proxy connection")),
                            ("tls", ("sslerror", "certificate verify", "tls handshake")),
                            ("dns", ("name resolution", "nameresolution", "getaddrinfo", "nodename nor servname")),
                            ("timeout", ("timeout", "timed out")),
                            ("connection_reset", ("connection reset", "connectionreset")),
                            ("connection_refused", ("connection refused", "connectionrefused"))):
        if any(word in text for word in words):
            return category
    return "connection_other" if "connection" in text else "other"


def call_ai_audit_and_rename(
    task_info: dict,
    model: str = "qwen3.7-max",
    mail_subject: str = "",
    mail_date_hint: str = "",
    run_id: str = "",
    api_key: str = "",
    deepseek_base_url: str = "",
) -> Tuple[Optional[dict], str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    def document_role(data: dict) -> str:
        # 本次调用只有一个文件；原名/正文预读已确定时，字段归属与 PDF 处理器一致。
        files = task_info.get("files") or []
        role = normalize_doc_role(files[0].get("identified_type")) if files else "Unknown"
        if role == "Unknown":
            classifications = data.get("file_classifications") or []
            if isinstance(classifications, list) and len(classifications) == 1 and isinstance(classifications[0], dict):
                role = normalize_doc_role(classifications[0].get("role"))
        return role

    def missing_keys(data: dict) -> list[str]:
        role = document_role(data)
        if role == "Itinerary":
            expected = ("match_amount", "travel_time", "origin_dest", "file_classifications")
        elif role == "Invoice":
            expected = ("total_amount", "invoice_number", "invoice_date", "invoice_type",
                        "receipt_type", "seller", "file_classifications")
        else:
            expected = ("file_classifications",)
        return [field for field in expected if field not in data]

    def core_missing(data: dict) -> bool:
        role = document_role(data)
        if role == "Unknown":
            return False
        keys = (("match_amount", "travel_time", "origin_dest") if role == "Itinerary"
                else ("invoice_date", "invoice_number", "total_amount"))
        return any(data.get(key) in (None, "", "Not Found") for key in keys)

    # 步骤 A：把同一封邮件下的多个 PDF 片段拼成一段给模型的「内容」
    combined_content = ""
    for file in task_info["files"]:
        combined_content += f"\n--- File: {os.path.basename(file['local_path'])} ---\n"
        combined_content += f"AttachmentOriginalName: {file.get('attachment_original_name', '')}\n"
        combined_content += f"Pre-Type（原名优先合并后）: {file['identified_type']}\n"
        combined_content += f"Pre-Type（仅 PDF 正文）: {file.get('local_pdf_type', file.get('identified_type', ''))}\n"
        combined_content += f"Text: {file['text_snapshot']}\n"

    system_prompt = load_prompt("pdf_text")

    error_reason = ""
    trace = task_info.setdefault("model_usage", {})
    trace.update(call_attempted=False, successful_response=False, extraction_source="none",
                 attempt_count=0, attempt_errors=[], failure_categories=[])
    try:
        # 步骤 B：HTTP 调 DashScope，带重试（429/5xx 最多重试2次，间隔1s/3s）
        response = None
        retry_intervals = [1, 3]
        for attempt in range(1 + len(retry_intervals)):
            if Generation is None:
                raise RuntimeError("DASHSCOPE_SDK_UNAVAILABLE")
            trace["call_attempted"] = True
            trace["attempt_count"] += 1
            try:
                from model_gateway import model_call
                response = model_call(
                    sdk_call=Generation.call,
                    base_url=deepseek_base_url,
                    source=trace.get("config_source", "programmatic"),
                    api_key=api_key or os.getenv("DASHSCOPE_API_KEY", ""),
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": combined_content},
                    ],
                )
            except Exception as exc:
                kind = type(exc).__name__
                trace["attempt_errors"].append(kind)
                trace["failure_categories"].append(_connection_failure_category(exc))
                retryable = (isinstance(exc, (ConnectionError, TimeoutError))
                             or any(token in kind.lower() for token in ("connection", "timeout")))
                if retryable and attempt < len(retry_intervals):
                    time.sleep(retry_intervals[attempt])
                    continue
                raise
            if response.status_code == 200:
                break
            trace["attempt_errors"].append(f"HTTP_{response.status_code}")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < len(retry_intervals):
                    time.sleep(retry_intervals[attempt])
                    continue
            break

        if response.status_code == 200:
            trace["successful_response"] = True
            trace["extraction_source"] = "model_response"
            raw = response.output.text
            if not raw and response.output and getattr(response.output, "choices", None):
                raw = response.output.choices[0].get("message", {}).get("content", "")
            res_text = (raw or "").replace("```json", "").replace("```", "").strip()
            try:
                data = json.loads(res_text, parse_float=Decimal)
            except Exception:
                # 步骤 B-失败：JSON 解析不了 → 用本机正则兜底一版「伪 AI 结果」
                data = build_fallback_audit_result(task_info.get("files", []))
                trace["extraction_source"] = "local_regex"
                error_reason = "JSON_PARSE_ERROR;USE_FALLBACK"
            # 按当前文件角色检查必需字段；行程单不能因缺少发票字段而误报。
            missing = missing_keys(data)
            if missing:
                for f in missing:
                    data[f] = [] if f == "file_classifications" else "" if f == "invoice_type" else "Not Found"
                required_missing = [f for f in missing if f != "invoice_type"]
                if required_missing:
                    error_reason = (error_reason + ";AI_MISSING_FIELDS:" + ",".join(required_missing)).strip(";")
            if core_missing(data):
                error_reason = (error_reason + ";FIELD_NOT_FOUND").strip(";")
            mm, manual_u = _rename_pdfs_with_audit(task_info, data, mail_subject, mail_date_hint, run_id)
            return data, error_reason, mm, manual_u
        # 步骤 B-HTTP 非 200：同样走兜底 + 重命名 + 空差异列表（AI 没给出分类）
        code = str(getattr(response, "code", "") or "")
        error_reason = f"AI_API_{code}" if code else f"AI_API_STATUS_{response.status_code}"
        data = build_fallback_audit_result(task_info.get("files", []))
        trace["extraction_source"] = "local_regex"
        if core_missing(data):
            error_reason = (error_reason + ";FIELD_NOT_FOUND").strip(";")
        mm, manual_u = _rename_pdfs_with_audit(task_info, data, mail_subject, mail_date_hint, run_id)
        return data, error_reason, mm, manual_u
    except Exception as e:
        return None, f"PROCESSING_EXCEPTION:{type(e).__name__}", [], []
