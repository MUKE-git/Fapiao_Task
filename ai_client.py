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


def _t3_default_transport(*, model: str, api_key: str, messages: list, vision: bool):
    if vision:
        from dashscope import MultiModalConversation
        return MultiModalConversation.call(api_key=api_key, model=model,
                                           messages=messages,
                                           response_format={"type": "json_object"})
    if Generation is None:
        raise RuntimeError("DASHSCOPE_SDK_UNAVAILABLE")
    return Generation.call(api_key=api_key, model=model, messages=messages,
                           result_format="message", response_format={"type": "json_object"})


def extract_single_file(request: ModelFileInput, cfg: object,
                        transport: Callable[..., object] | None = None,
                        sleep: Callable[[float], None] = time.sleep) -> ModelFileOutput:
    """Return one file's candidate only; never rename files or pair documents.

    ``transport`` accepts the same keyword arguments as _t3_default_transport,
    so fixed-response tests replace only the external service boundary.
    """
    if not request.file_seq or not request.internal_name or not request.file_type:
        return ModelFileOutput(error_reason="MODEL_INPUT_IDENTITY_MISSING")
    if len(request.text) > 12000 or len(request.images) > 8 or sum(len(p[0]) for p in request.images) > 15_000_000:
        return ModelFileOutput(error_reason="MODEL_INPUT_LIMIT_EXCEEDED")
    vision = bool(request.images)
    key = str(getattr(cfg, "dashscope_api_key", "") or os.getenv("DASHSCOPE_API_KEY", ""))
    if transport is None and not key:
        return ModelFileOutput(error_reason="MODEL_API_KEY_MISSING")
    model = str((getattr(cfg, "dashscope_vision_model", None) or os.getenv("DASHSCOPE_VISION_MODEL")
                 or "qwen-vl-plus") if vision else (getattr(cfg, "dashscope_model", None) or "qwen-plus"))
    system = ("从当前单个票据文件提取 JSON。只返回一项 file_classifications，file 必须等于输入 internal_name。"
              "role 只能是 Invoice、Itinerary、Unknown。发票字段为 invoice_number、invoice_date、"
              "total_amount、invoice_type、receipt_type、seller；行程单字段为 match_amount、"
              "travel_time、origin_dest。字段值必须为字符串；开票日期严格使用 YYYY-MM-DD，"
              "金额只用数字和小数点，保留原小数位，不带货币符号或单位；缺失用 Not Found。"
              "每个非空字段在 field_evidence 中给出 raw 和 location；不臆造不可见的值。"
              "若同一画面有多张独立票据，设置 multiple_documents=true。"
              f"提示词版本 {T3_EXTRACTION_PROMPT_VERSION}。")
    identity = (f"mail_id={request.mail_id}; file_seq={request.file_seq}; internal_name={request.internal_name}; "
                f"original_name={request.original_name}; format={request.file_type}; "
                f"pre_read_role={request.pre_read_role}")
    if vision:
        system += (
            "图片发票的 receipt_type 必须从票面“项目名称”一栏提取，"
            "若项目名称为 *一级*二级，则只返回二级名称；"
            "例如 *运输服务*客运服务费 应返回 客运服务费。"
            "不要将票面标题、二维码旁的文字或文件名当作项目名称。"
            "项目名称无法辨认或多个项目无法唯一确定时，返回 Not Found，不要猜测。"
            "receipt_type 的 field_evidence 中 raw 应保留项目名称原文，"
            "location 应指明项目名称在图片中的位置。"
            "图片票面类型规则版本 t3-image-receipt-v2。"
        )
        content = [{"image": "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")}
                   for data, mime in request.images]
        content.append({"text": identity})
        messages = [{"role": "system", "content": [{"text": system}]},
                    {"role": "user", "content": content}]
    else:
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": identity + "\n" + request.text}]
    send = transport or _t3_default_transport
    response = None
    for attempt in range(3):
        try:
            response = send(model=model, api_key=key, messages=messages, vision=vision)
            status = int(getattr(response, "status_code", 0) or 0)
        except Exception as exc:
            if isinstance(exc, (TimeoutError, ConnectionError)) or any(
                    word in type(exc).__name__.lower() for word in ("timeout", "connection")):
                status = 503
                response = None
            else:
                return ModelFileOutput(error_reason="MODEL_TRANSPORT_" + type(exc).__name__)
        if status == 200:
            break
        if (status == 429 or status >= 500) and attempt < 2:
            sleep((1, 3)[attempt])
            continue
        return ModelFileOutput(error_reason=f"MODEL_API_STATUS_{status}")
    raw = _t3_response_text(response).strip()
    if raw.startswith("```json"):
        raw = raw[7:].removesuffix("```").strip()
    try:
        parsed = json.loads(raw, parse_float=Decimal)
    except (ValueError, TypeError):
        return ModelFileOutput(error_reason="MODEL_JSON_INVALID")
    if not isinstance(parsed, dict):
        return ModelFileOutput(error_reason="MODEL_SHAPE_INVALID")
    classifications = parsed.get("file_classifications")
    if not (isinstance(classifications, list) and len(classifications) == 1
            and isinstance(classifications[0], dict)
            and classifications[0].get("file") == request.internal_name
            and classifications[0].get("role") in _T3_ROLES):
        return ModelFileOutput(error_reason="MODEL_CLASSIFICATION_INVALID")
    role = classifications[0]["role"]
    if parsed.get("multiple_documents") is True:
        return ModelFileOutput(role="Unknown", error_reason="MULTIPLE_DOCUMENTS_IN_IMAGE")
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
    return ModelFileOutput(role=role, fields=values, field_evidence=evidence,
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


def call_ai_audit_and_rename(
    task_info: dict,
    model: str = "qwen-plus",
    mail_subject: str = "",
    mail_date_hint: str = "",
    run_id: str = "",
    api_key: str = "",
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

    system_prompt = """# Role
你是一个专业的财务报销审计专家，擅长从复杂的出行票据中提取结构化信息。

# Context 我会为你提供一组文件的文本内容。每个文件含「AttachmentOriginalName」（邮件内附件原名）与「Pre-Type（原名优先合并后）」：若原名含「行程单」「发票」等，以该合并结果为准；否则再结合正文判断。 

# Task 
1. **角色判定**：结合附件原名与正文，判断每个文件是真正的【发票】、还是【行程单/行程明细】、或其它（Unknown）。
2. **数据核实**：若附件原名已能判断类型，file_classifications 的 role 应与「Pre-Type（原名优先合并后）」一致；若正文与原名明显矛盾，role 可为 Unknown。 
3. **信息提取**：从判定后的文件中提取以下所有字段。
4. **必须输出 file_classifications**：与上述文件一一对应；每个对象的 file 字段必须与输入中的文件名（含 Msg 前缀的 pdf 名）一致；role 只能是 Invoice、Itinerary、Unknown 三者之一（英文）。

# Extraction Rules
1. **报销金额 (total_amount)**：
   - 必须从【发票】文本中提取。
   - 寻找“价税合计（小写）”或“合 计”字样后的数值 。
   - 如果发票包含多个金额（如单价、税额、抵扣额），请务必只提取最终的【价税合计】。
   - 按票面原有小数位输出十进制字符串，包括末尾的 0；例如票面 `8.00` 应输出 `"8.00"`，不得缩写为 `8.0` 或 `8`。
   - 行程单没有发票价税合计时填写 "Not Found"，不得用行程单金额填充此字段。
2. **行程单匹配金额 (match_amount)**：
   - 必须从【当前行程单】自身的票面总金额提取，保留原有小数位；可见标签可能是“报销金额”“费用合计”“总金额”“实付金额”等。
   - 多段行程时取明确标注的整单金额，不取某一段费用；没有可确认总额时填写 "Not Found"。
   - 发票文件填写 "Not Found"，不得复制发票价税合计或另一文件的金额。
3. **发票号码 (invoice_number)**：
   - 从【发票】文本中提取。
   - 通常位于“发票号码”关键词之后，是一串长数字 。
   - 通常位于文件右上部分 。
4. **开票日期 (invoice_date)**：
   - 从【发票】文本中提取。
   - 通常位于“开票日期”关键词之后 。
   - 通常位于文件右上部分，发票号码下方 。
   - 格式统一转化为 YYYY-MM-DD（例如 2026-03-09） 。
5. **出行时间 (travel_time)**：
   - 必须从【行程单】文本中提取。
   - 重点寻找“上车时间”、“用车时间”或“入口时间” 。
   - **核心逻辑**：如果行程单包含多段行程（多行数据），请务必只提取【第一笔行程】的起始时间 。
   - 忽略“申请日期”或“打印日期” 。
   - 输出统一为 YYYY-MM-DD HH:mm；票面含秒时保留到分钟，不把秒写入字段。
6. **发票类型 (invoice_type)**：
   - 从【发票】文本中提取。
   - 如“增值税电子普通发票”、“增值税专用发票”、“增值税普通发票”等。
   - 通常位于票面顶部标题区域。
7. **票面类型 (receipt_type)**：
   - 从【发票】文本中提取。
   - 即发票的服务/货物名称，如“客运服务费”、“餐饮服务费”、“住宿服务费”等。
   - 通常位于“货物或应税劳务、服务名称”或“*服务名称*”之后。
8. **销售方 (seller)**：
   - 从【发票】文本中提取。
   - 即开票方企业名称，通常位于“销售方名称”或“销售方”之后。
   - 提取完整的公司全称。
9. **出发地/目的地 (origin_dest)**：
   - 必须从【行程单】文本中提取。
   - 格式为“出发地 → 目的地”，如“北京 → 上海”、“杭州东站 → 南京南站”。
   - 若行程单包含多段行程，只提取第一段的起终点。
   - 若无法提取，填写 "Not Found"。

# Constraints
- 如果信息缺失，请填写 "Not Found"。
- **每个字段都必须出现在 JSON 输出中**，即使值为 "Not Found" 也不能省略该字段。
- 不要包含任何解释性文字，只输出 JSON 格式。
- 金额只保留数字，不带货币符号。
- file_classifications 数组长度必须与附件文件数量相同，顺序与输入 File 段落顺序一致。

# Output Format (Example JSON)
{
  "total_amount": "14.76",
  "match_amount": "Not Found",
  "invoice_number": "26327000000491302024",
  "invoice_date": "2026-03-09",
  "travel_time": "Not Found",
  "invoice_type": "增值税电子普通发票",
  "receipt_type": "客运服务费",
  "seller": "某某出行科技有限公司",
  "origin_dest": "Not Found",
  "data_source": "T3_Chuxing",
  "file_classifications": [
    {"file": "Msg123_发票.pdf", "role": "Invoice"}
  ]
}"""

    error_reason = ""
    try:
        # 步骤 B：HTTP 调 DashScope，带重试（429/5xx 最多重试2次，间隔1s/3s）
        response = None
        retry_intervals = [1, 3]
        for attempt in range(1 + len(retry_intervals)):
            if Generation is None:
                raise RuntimeError("DASHSCOPE_SDK_UNAVAILABLE")
            response = Generation.call(
                api_key=api_key or os.getenv("DASHSCOPE_API_KEY", ""),
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": combined_content},
                ],
            )
            if response.status_code == 200:
                break
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < len(retry_intervals):
                    time.sleep(retry_intervals[attempt])
                    continue
            break

        if response.status_code == 200:
            raw = response.output.text
            if not raw and response.output and getattr(response.output, "choices", None):
                raw = response.output.choices[0].get("message", {}).get("content", "")
            res_text = (raw or "").replace("```json", "").replace("```", "").strip()
            try:
                data = json.loads(res_text, parse_float=Decimal)
            except Exception:
                # 步骤 B-失败：JSON 解析不了 → 用本机正则兜底一版「伪 AI 结果」
                data = build_fallback_audit_result(task_info.get("files", []))
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
        if core_missing(data):
            error_reason = (error_reason + ";FIELD_NOT_FOUND").strip(";")
        mm, manual_u = _rename_pdfs_with_audit(task_info, data, mail_subject, mail_date_hint, run_id)
        return data, error_reason, mm, manual_u
    except Exception as e:
        return None, f"PROCESSING_EXCEPTION:{type(e).__name__}", [], []
