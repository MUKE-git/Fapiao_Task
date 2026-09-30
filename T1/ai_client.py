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
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from dashscope import Generation

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


def _rename_pdfs_with_audit(
    task_info: dict,
    data: dict,
    mail_subject: str,
    mail_date_hint: str,
    run_id: str = "",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按「原名>AI>正文」解析类型后重命名；仅当三种类型依据不一致时加「（待人工核查）」。"""
    amount_text = format_amount_text(data.get("total_amount", "0"))
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
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"debug-{run_id}.log")
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
            base = f"{inv_type}+{inv_date_raw}+{amount_text}元"
        elif eff_n == "Itinerary":
            base = f"行程单+{travel_date_raw}+{amount_text}元"
        else:
            base = f"票据+{inv_date_raw}+{amount_text}元"
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
) -> Tuple[Optional[dict], str, List[Dict[str, Any]], List[Dict[str, Any]]]:
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
2. **发票号码 (invoice_number)**：
   - 从【发票】文本中提取。
   - 通常位于“发票号码”关键词之后，是一串长数字 。
   - 通常位于文件右上部分 。
3. **开票日期 (invoice_date)**：
   - 从【发票】文本中提取。
   - 通常位于“开票日期”关键词之后 。
   - 通常位于文件右上部分，发票号码下方 。
   - 格式统一转化为 YYYY-MM-DD（例如 2026-03-09） 。
4. **出行时间 (travel_time)**：
   - 必须从【行程单】文本中提取。
   - 重点寻找“上车时间”、“用车时间”或“入口时间” 。
   - **核心逻辑**：如果行程单包含多段行程（多行数据），请务必只提取【第一笔行程】的起始时间 。
   - 忽略“申请日期”或“打印日期” 。
5. **发票类型 (invoice_type)**：
   - 从【发票】文本中提取。
   - 如“增值税电子普通发票”、“增值税专用发票”、“增值税普通发票”等。
   - 通常位于票面顶部标题区域。
6. **票面类型 (receipt_type)**：
   - 从【发票】文本中提取。
   - 即发票的服务/货物名称，如“客运服务费”、“餐饮服务费”、“住宿服务费”等。
   - 通常位于“货物或应税劳务、服务名称”或“*服务名称*”之后。
7. **销售方 (seller)**：
   - 从【发票】文本中提取。
   - 即开票方企业名称，通常位于“销售方名称”或“销售方”之后。
   - 提取完整的公司全称。
8. **出发地/目的地 (origin_dest)**：
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
  "total_amount": 14.76,
  "invoice_number": "26327000000491302024",
  "invoice_date": "2026-03-09",
  "travel_time": "2026-02-27 09:18",
  "invoice_type": "增值税电子普通发票",
  "receipt_type": "客运服务费",
  "seller": "某某出行科技有限公司",
  "origin_dest": "杭州东站 → 萧山国际机场",
  "data_source": "T3_Chuxing",
  "file_classifications": [
    {"file": "Msg123_发票.pdf", "role": "Invoice"},
    {"file": "Msg123_行程单.pdf", "role": "Itinerary"}
  ]
}"""

    error_reason = ""
    try:
        # 步骤 B：HTTP 调 DashScope，带重试（429/5xx 最多重试2次，间隔1s/3s）
        response = None
        retry_intervals = [1, 3]
        for attempt in range(1 + len(retry_intervals)):
            response = Generation.call(
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
                data = json.loads(res_text)
            except Exception:
                # 步骤 B-失败：JSON 解析不了 → 用本机正则兜底一版「伪 AI 结果」
                data = build_fallback_audit_result(task_info.get("files", []))
                error_reason = "JSON_PARSE_ERROR;USE_FALLBACK"
            # 确保所有期望字段存在，缺失的补默认值并记录
            expected_fields = [
                "total_amount", "invoice_number", "invoice_date", "travel_time",
                "invoice_type", "receipt_type", "seller",
                "origin_dest", "file_classifications",
            ]
            missing = [f for f in expected_fields if f not in data]
            if missing:
                for f in missing:
                    data[f] = "Not Found" if f != "file_classifications" else []
                error_reason = (error_reason + ";AI_MISSING_FIELDS:" + ",".join(missing)).strip(";")
            if any(data.get(k) in (None, "", "Not Found") for k in ["invoice_date", "invoice_number", "total_amount"]):
                error_reason = (error_reason + ";FIELD_NOT_FOUND").strip(";")
            mm, manual_u = _rename_pdfs_with_audit(task_info, data, mail_subject, mail_date_hint, run_id)
            return data, error_reason, mm, manual_u
        # 步骤 B-HTTP 非 200：同样走兜底 + 重命名 + 空差异列表（AI 没给出分类）
        code = str(getattr(response, "code", "") or "")
        message = str(getattr(response, "message", "") or "")
        error_reason = f"AI_API_{code}" if code else f"AI_API_STATUS_{response.status_code}"
        if message:
            error_reason += f"({message})"
        data = build_fallback_audit_result(task_info.get("files", []))
        if any(data.get(k) in (None, "", "Not Found") for k in ["invoice_date", "invoice_number", "total_amount"]):
            error_reason = (error_reason + ";FIELD_NOT_FOUND").strip(";")
        mm, manual_u = _rename_pdfs_with_audit(task_info, data, mail_subject, mail_date_hint, run_id)
        return data, error_reason, mm, manual_u
    except Exception as e:
        return None, f"PROCESSING_EXCEPTION: {str(e)}", [], []