"""
发票邮件处理流水线（后厨）：无 Streamlit 依赖，由 app_demo 等入口调用 run_pipeline。

【文件里大致顺序 — 从上到下读即可】
1. 配置与输出路径（RunConfig、桌面文件夹名清洗、是否发网易 IMAP 兼容指令）
2. 通用小工具（重名避让、金额/日期格式化、邮件头解码、附件文件名清洗）
3. AI 失败时的本机正则兜底（从 PDF 片段里硬抽金额/票号/日期）
4. ZIP 解压出 PDF、往报销 Excel 追加行
5. 本机 PDF 关键词分类 + 与 AI 返回的 file_classifications 比对（人工确认列表）
6. 调百炼 API：要 JSON + 按本机类型重命名 PDF
7. run_pipeline：连邮箱 → 扫未读 → 主题含「发票/报销/报销凭证/电子发票/开票」才处理 → 写 Excel → 写 task_debug.json
"""
from __future__ import annotations

from datetime import datetime
import email
import hashlib
import imaplib
import io
import json
import os
import re
import shutil
import time
import traceback
import zipfile
from dataclasses import dataclass
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# (SUMMARY_COLUMNS / TRANSPORT_RECEIPT_KEYWORDS 已拆分至 config.py，由上方 import 承接)

import dashscope
import pdfplumber
from dashscope import Generation
from openpyxl import Workbook, load_workbook

from config import (
    RunConfig,
    SUMMARY_COLUMNS,
    TRANSPORT_RECEIPT_KEYWORDS,
    _imap_error_hint,
    ensure_output_dirs,
    safe_desktop_subfolder,
    should_send_netease_id,
)

from utils import (
    _normalize_date_text,
    build_non_conflicting_path,
    clean_filename,
    clean_receipt_type,
    decode_str,
    format_amount_text,
    format_invoice_date_cn,
    format_travel_date_cn,
    safe_parse_mail_date,
    sanitize_excel_date_display,
    zip_directory_to_bytes,
)

LogFn = Callable[[str], None]


def _noop_log(_: str) -> None:
    """无网页时的空日志，避免每处都判断 log 是否为 None。"""
    pass


def _log_exc(log: LogFn, prefix: str = "异常") -> None:
    """把当前正在处理的异常的完整 traceback 写到 log。

    用法：在 except 块里直接调用 `_log_exc(log, "场景名")`。
    必须在 except 块内调用，否则 traceback.format_exc() 拿不到当前异常。
    """
    log(f"❌ {prefix}：\n{traceback.format_exc()}")


# (配置与输出路径：safe_desktop_subfolder / RunConfig / _imap_error_hint /
#  should_send_netease_id / ensure_output_dirs 已拆分至 config.py，由上方 import 承接)

# (通用小工具：build_non_conflicting_path / format_amount_text / clean_receipt_type /
#  format_travel_date_cn / format_invoice_date_cn / _normalize_date_text
#  已拆分至 utils.py，由上方 import 承接)


# ---------------------------------------------------------------------------
# 三、AI 不可用或 JSON 坏了时：用正则从已抽取的 PDF 文本片段里硬凑一张「假审计结果」
# ---------------------------------------------------------------------------

def build_fallback_audit_result(files):
    """把本机已标成 Invoice/Itinerary 的 text_snapshot 拼起来，用正则抠金额、票号、日期、首段上车时间。"""
    invoice_text = ""
    itinerary_text = ""
    for f in files:
        t = f.get("identified_type", "")
        txt = str(f.get("text_snapshot", ""))
        if t == "Invoice":
            invoice_text += "\n" + txt
        elif t == "Itinerary":
            itinerary_text += "\n" + txt

    amount = "Not Found"
    invoice_no = "Not Found"
    invoice_date = "Not Found"
    travel_time = "Not Found"
    invoice_type = "Not Found"
    receipt_type = "Not Found"
    seller = "Not Found"
    origin_dest = "Not Found"

    m_amt = re.search(r"价税合计[^\n]{0,40}?[（\(]小写[）\)]\s*[¥￥]?\s*([0-9]+(?:\.[0-9]+)?)", invoice_text)
    if not m_amt:
        m_amt = re.search(r"合\s*计[^\n]{0,30}?[¥￥]\s*([0-9]+(?:\.[0-9]+)?)", invoice_text)
    if m_amt:
        amount = m_amt.group(1)

    m_no = re.search(r"发票号码[:：]?\s*([0-9]{8,})", invoice_text)
    if m_no:
        invoice_no = m_no.group(1)

    m_date = re.search(r"开票日期[:：]?\s*([0-9]{4}[年\-/][0-9]{1,2}[月\-/][0-9]{1,2})", invoice_text)
    if m_date:
        invoice_date = _normalize_date_text(m_date.group(1))

    # 发票类型：匹配标题行如 "电子发票（普通发票）"、"增值税电子普通发票" 等
    m_inv_type = re.search(r"(?:发票类型|发票名称)[:：]?\s*(.{2,30})", invoice_text)
    if not m_inv_type:
        m_inv_type = re.search(r"(增值税)?电子发票[（(]([^)）]+)[)）]", invoice_text)
        if m_inv_type:
            invoice_type = f"电子发票（{m_inv_type.group(2)}）"
        else:
            m_inv_type = re.search(r"(增值税\w*发票|电子发票|通用机打发票|公路内河货运发票)", invoice_text)
    if m_inv_type and invoice_type == "Not Found":
        invoice_type = m_inv_type.group(1) if m_inv_type.lastindex is None or m_inv_type.lastindex == 0 else m_inv_type.group(0)

    # 票面类型：匹配 *服务名*项目名 格式，如 "*运输服务*客运服务费"
    m_rec_type = re.search(r"(?:服务名称|货物名称)[:：]?\s*\*?([^*\n]{2,20})\*?", invoice_text)
    if not m_rec_type:
        m_rec_type = re.search(r"\*([^*]+)\*([^*\n]{2,20})", invoice_text)
        if m_rec_type:
            receipt_type = m_rec_type.group(2).strip()
    if m_rec_type and receipt_type == "Not Found":
        receipt_type = m_rec_type.group(1).strip() if receipt_type == "Not Found" else receipt_type

    # 销售方：匹配各种格式，如 "销 名称：xxx"、"销售方名称：xxx"、"销售方：xxx"
    m_seller = re.search(r"(?:销售方名称|销售方)[:：]?\s*(.{4,60})", invoice_text)
    if not m_seller:
        m_seller = re.search(r"销\s*售?\s*方?\s*名称[:：]?\s*(.{4,60})", invoice_text)
    if not m_seller:
        m_seller = re.search(r"销\s+名称[:：]\s*(.{4,60})", invoice_text)
    if m_seller:
        seller = m_seller.group(1).strip()

    # 出行时间：从行程单首行提取日期+时间（支持跨行格式）
    m_time = re.search(
        r"([0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2})[\s\S]*?([0-2]?[0-9]:[0-5][0-9](?::[0-5][0-9])?)",
        itinerary_text
    )
    if m_time:
        travel_time = f"{_normalize_date_text(m_time.group(1))} {m_time.group(2)}"

    # 出发地/目的地：从行程单表格中提取起点和终点
    m_od = re.search(r"([\u4e00-\u9fa5]{2,}(?:站|机场|中心)?)\s*[-—→至到]\s*([\u4e00-\u9fa5]{2,}(?:站|机场|中心)?)", itinerary_text)
    if not m_od:
        # 尝试匹配表格格式：城市 起点 终点（如 "广州 春兰花园西南侧 智光综合能源产业"）
        m_od = re.search(r"[\u4e00-\u9fa5]{2,}\s+([\u4e00-\u9fa5]{2,}(?:[-\u4e00-\u9fa5]*)?)\s+([\u4e00-\u9fa5]{2,}(?:[-\u4e00-\u9fa5]*)?)\s+[¥￥]", itinerary_text)
    if m_od:
        origin_dest = f"{m_od.group(1)} → {m_od.group(2)}"

    return {
        "total_amount": amount,
        "invoice_number": invoice_no,
        "invoice_date": invoice_date,
        "travel_time": travel_time,
        "invoice_type": invoice_type,
        "receipt_type": receipt_type,
        "seller": seller,
        "origin_dest": origin_dest,
        "data_source": "Fallback_Local_Regex",
    }


# ---------------------------------------------------------------------------
# 四、ZIP 附件：只解压根目录单层 PDF → 落到 extract_dir，供后续 local_inspect / AI 使用
# ---------------------------------------------------------------------------

def extract_pdfs_from_zip(zip_path: str, mail_id: str, zip_display_name: str, extract_dir: str):
    """无密码 zip；跳过子目录里的文件，只处理压缩包根下的 .pdf。返回 (落盘路径, zip 内原始文件名) 列表。"""
    pdf_paths: List[Tuple[str, str]] = []
    zip_base = os.path.splitext(os.path.basename(zip_display_name))[0]
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            name = info.filename
            if name.endswith("/") or "/" in name or "\\" in name:
                continue
            if not name.lower().endswith(".pdf"):
                continue
            inner_base = os.path.basename(name)
            safe_pdf_name = clean_filename(inner_base, 1)
            target_name = f"Msg{mail_id}_{zip_base}_{safe_pdf_name}"
            target_path = build_non_conflicting_path(os.path.join(extract_dir, target_name))
            with zf.open(info, "r") as src, open(target_path, "wb") as dst:
                dst.write(src.read())
            pdf_paths.append((target_path, inner_base))
    return pdf_paths


# ---------------------------------------------------------------------------
# 四（续）、报销总表：仅 total2.xlsx，按 SUMMARY_COLUMNS 列顺序追加行并带表头
# ---------------------------------------------------------------------------

def append_to_summary_excel(
    rows: List[Dict[str, Any]],
    summary_path: str,
) -> Tuple[Optional[str], Optional[str]]:
    """rows 来自本轮所有「处理成功」邮件的 AI 结果；无行则跳过。返回 (错误信息, 实际写入的文件路径)。"""
    if not rows:
        return None, None
    try:
        if os.path.exists(summary_path):
            wb = load_workbook(summary_path)
        else:
            wb = Workbook()
        ws = wb.active
        if ws.cell(row=1, column=1).value in (None, ""):
            for j, (header, _) in enumerate(SUMMARY_COLUMNS, start=1):
                ws.cell(row=1, column=j, value=header)
        row_idx = 2
        while ws.cell(row=row_idx, column=1).value not in (None, ""):
            row_idx += 1
        for item in rows:
            for j, (_, key) in enumerate(SUMMARY_COLUMNS, start=1):
                val = item.get(key, "Not Found")
                if key == "invoice_date":
                    val = sanitize_excel_date_display(val)
                ws.cell(row=row_idx, column=j, value=val)
            row_idx += 1
        wb.save(summary_path)
        return None, summary_path
    except PermissionError:
        return "EXCEL_PERMISSION_DENIED: 请关闭 发票信息汇总表.xlsx 后重试", None
    except Exception as e:
        return f"EXCEL_WRITE_ERROR: {str(e)}", None


# (通用小工具：zip_directory_to_bytes / clean_filename / decode_str / safe_parse_mail_date /
#  sanitize_excel_date_display 已拆分至 utils.py，由上方 import 承接)


# ---------------------------------------------------------------------------
# 五、文档类型：本机关键词 vs AI 的 file_classifications（不一致则交给网页提示人工确认）
# ---------------------------------------------------------------------------

def classify_type_from_attachment_name(name: str) -> Optional[str]:
    """
    仅从附件原名推断类型；能确定时返回 Invoice / Itinerary，否则 None（由正文与 AI 继续判断）。
    优先级：行程单类关键词先于发票类（避免「通行费电子行程单」等被误判为发票）。
    """
    if not name or not str(name).strip():
        return None
    n = str(name)
    s = n.lower()
    if "行程单" in n or "行程明细" in n or "itinerary" in s:
        return "Itinerary"
    if "发票" in n or "invoice" in s:
        return "Invoice"
    return None


def merge_type_from_name_and_local(attachment_name: str, local_pdf_type: str) -> str:
    """附件原名优先，否则采用 pdfplumber 关键词分类。"""
    hint = classify_type_from_attachment_name(attachment_name)
    if hint:
        return hint
    return local_pdf_type


def resolve_effective_type_for_rename(f: dict, ai_role: Optional[str]) -> str:
    """重命名用类型：附件原名 > AI > 合并后的 identified_type（原名+正文）。"""
    nh = classify_type_from_attachment_name((f.get("attachment_original_name") or "").strip())
    if nh:
        return nh
    if ai_role:
        ai_n = normalize_doc_role(ai_role)
        if ai_n != "Unknown":
            return ai_n
    return normalize_doc_role(f.get("identified_type"))


def triple_type_disagreement(f: dict, ai_role: Optional[str]) -> bool:
    """
    三种依据：① 附件原名关键词 ② PDF 正文 local_pdf_type ③ AI 的 file_classifications。
    规范化后若参与比较的取值超过一种（Invoice/Itinerary/Unknown），则视为不相符，文件名需加「（待人工核查）」。
    原名无法识别（无关键词）时不作为独立一票，仅比较正文与 AI；无 AI 时仅比较原名与正文（若原名也无提示则无法构成「多方」分歧，不加后缀）。
    """
    name_hint = classify_type_from_attachment_name((f.get("attachment_original_name") or "").strip())
    name_n = normalize_doc_role(name_hint) if name_hint else None
    local_n = normalize_doc_role(f.get("local_pdf_type"))
    ai_n = normalize_doc_role(ai_role) if (ai_role and str(ai_role).strip()) else None

    vals: List[str] = []
    if name_n is not None:
        vals.append(name_n)
    vals.append(local_n)
    if ai_n is not None:
        vals.append(ai_n)
    return len(set(vals)) > 1


def normalize_doc_role(label: Optional[str]) -> str:
    """将本机或 AI 的标签统一为 Invoice / Itinerary / Unknown，便于比较。"""
    if label is None or (isinstance(label, str) and not str(label).strip()):
        return "Unknown"
    raw = str(label).strip()
    s = raw.lower()
    if s in ("invoice", "发票", "增值税发票", "电子发票"):
        return "Invoice"
    if s in ("itinerary", "行程单", "行程明细", "行程"):
        return "Itinerary"
    if s in ("unknown", "未知", "其他", "票据", "pending", "other"):
        return "Unknown"
    if "行程" in raw:
        return "Itinerary"
    if "发票" in raw:
        return "Invoice"
    return "Unknown"


def _resolve_ai_role_for_file(
    files: List[dict],
    classifications: List[dict],
    index: int,
) -> Optional[str]:
    """按 file 字段匹配 basename，否则按与 files 相同的下标对齐。"""
    if not classifications:
        return None
    bn = os.path.basename(files[index]["local_path"])
    for c in classifications:
        fn = (c.get("file") or c.get("filename") or "").strip()
        if fn and (fn == bn or fn in bn or bn.endswith(fn) or fn in bn):
            return c.get("role") or c.get("type")
    if index < len(classifications):
        c = classifications[index]
        return c.get("role") or c.get("type")
    return None


def collect_classification_mismatches(
    task_info: dict,
    ai_data: Optional[dict],
    mail_subject: str,
) -> List[Dict[str, Any]]:
    """
    比较「原名+正文合并后的 identified_type」与 AI file_classifications。
    仅当 AI 给出了该文件的 role 且与合并结果不一致时记入列表。
    """
    if not ai_data or not task_info.get("files"):
        return []
    raw_list = ai_data.get("file_classifications")
    if not isinstance(raw_list, list):
        return []
    mismatches: List[Dict[str, Any]] = []
    files = task_info["files"]
    for i, f in enumerate(files):
        local_raw = f.get("identified_type", "") or ""
        local_n = normalize_doc_role(local_raw)
        ai_raw = _resolve_ai_role_for_file(files, raw_list, i)
        if ai_raw is None:
            continue
        ai_n = normalize_doc_role(ai_raw)
        if local_n != ai_n:
            mismatches.append({
                "filename": os.path.basename(f["local_path"]),
                "local_path": f["local_path"],
                "local_type": local_raw,
                "local_normalized": local_n,
                "ai_type": str(ai_raw).strip(),
                "ai_normalized": ai_n,
                "mail_subject": mail_subject,
            })
    return mismatches


def local_inspect_pdf(file_path: str):
    """用 pdfplumber 抽全文，再靠少量关键词粗分 Invoice / Itinerary / Unknown（决定重命名用哪套模板）。"""
    text_content = ""
    file_type = "Unknown"
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text_content += page.extract_text() or ""
        if any(kw in text_content for kw in ["行程单", "上车时间", "用车时间", "行程日期", "ITINERARY"]):
            file_type = "Itinerary"
        elif any(kw in text_content for kw in ["发票", "税务局", "Invoice", "价税合计"]):
            file_type = "Invoice"
    except Exception as e:
        text_content = f"读取失败: {str(e)}"
    return file_type, text_content


# ---------------------------------------------------------------------------
# 六、调用百炼 qwen-plus：拉结构化 JSON + 按本机/AI 规则批量重命名 PDF
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 七、主流程 run_pipeline：入口函数，串起「邮箱 → 邮件 → 附件 → AI → Excel → 调试 JSON」
# ---------------------------------------------------------------------------

def run_pipeline(
    cfg: RunConfig, log: Optional[LogFn] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """执行完整一轮；返回 (分类不一致列表, 统计信息)。统计含 unseen_total、invoice_keyword_mails、manual_unknown_alerts 等。"""
    run_id = f"{datetime.now().month}-{datetime.now().day}-{datetime.now().hour:02d}{datetime.now().minute:02d}"
    log = log or _noop_log
    all_mismatches: List[Dict[str, Any]] = []
    empty_stats: Dict[str, Any] = {
        "unseen_total": 0,
        "skipped_no_invoice_keyword": 0,
        "invoice_keyword_mails": 0,
        "manual_unknown_alerts": [],
        "not_found_alerts": [],
        "imap_mailbox_status": None,
        "imap_error": None,
    }
    # 7.0 前置检查：没有 Key 无法调模型，直接结束
    if not (cfg.dashscope_api_key or "").strip():
        log("错误：未配置 DashScope API Key。请在 .streamlit/secrets.toml 中设置 DASHSCOPE_API_KEY，或设置环境变量 DASHSCOPE_API_KEY。")
        return [], {**empty_stats, "error": "no_api_key"}

    # 7.1 注入全局 Key，并创建输出子目录
    task_summary: Dict[str, Any] = {}
    excel_rows: List[Dict[str, Any]] = []
    all_manual_unknown: List[Dict[str, Any]] = []
    mail = None
    dashscope.api_key = cfg.dashscope_api_key.strip()
    ensure_output_dirs(cfg)

    def _collect_not_found_alerts(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
        """汇总需人工复查的邮件：关键字段 Not Found、FIELD_NOT_FOUND、AI Key 异常等。"""
        out: List[Dict[str, Any]] = []
        for _mid, item in summary.items():
            if not isinstance(item, dict):
                continue
            files = item.get("files") or []
            if not files:
                continue
            er = str(item.get("error_reason", "") or "")
            audit = item.get("audit_result")
            has_it = any(
                (f.get("local_pdf_type") == "Itinerary" or f.get("identified_type") == "Itinerary")
                for f in files
            )
            flags: List[str] = []
            if "InvalidApiKey" in er:
                flags.append("AI 调用返回 InvalidApiKey（本次未走通云端模型，已用本地兜底）")
            if "FIELD_NOT_FOUND" in er:
                flags.append("存在 FIELD_NOT_FOUND（票号/日期等未从兜底正则抽出）")
            if audit is None:
                flags.append("无 audit_result")
            else:
                for k in ("invoice_number", "invoice_date", "total_amount", "invoice_type", "receipt_type", "seller"):
                    if audit.get(k) in (None, "", "Not Found"):
                        flags.append(f"缺少或无效: {k}")
                if has_it and audit.get("travel_time") in (None, "", "Not Found"):
                    flags.append("缺少或无效: travel_time（行程单）")
                if has_it and audit.get("origin_dest") in (None, "", "Not Found"):
                    flags.append("缺少或无效: origin_dest（行程单）")
                # 交通类发票强制要求行程单：票面类型含交通关键词但无行程单附件
                if not has_it and audit is not None:
                    receipt_type = clean_receipt_type(str(audit.get("receipt_type", "") or ""))
                    if receipt_type and receipt_type != "Not Found":
                        if any(kw in receipt_type for kw in TRANSPORT_RECEIPT_KEYWORDS):
                            flags.append(f"缺少行程单: 票面类型为「{receipt_type}」（交通类），但未找到行程单附件")
            if flags:
                out.append({
                    "mail_subject": item.get("subject", ""),
                    "mail_date": item.get("mail_date", ""),
                    "mail_date_header": item.get("mail_date_header", ""),
                    "flags": flags,
                })
        return out

    imap_mbox_status: Optional[Dict[str, Any]] = None

    try:
        # 7.2 连接邮箱：SSL + 登录；必要时发网易专用 ID
        host = (cfg.imap_host or "").strip() or "imap.163.com"
        mail = imaplib.IMAP4_SSL(host)
        mail.login(cfg.imap_user.strip(), cfg.imap_password)

        if should_send_netease_id(host, cfg.use_netease_id):
            imaplib.Commands['ID'] = ('AUTH', '(("name" "com.netease.mail") ("version" "1.0.0") ("vendor" "netease"))')
            mail._simple_command('ID', '("name" "com.netease.mail" "version" "1.0.0" "vendor" "netease")')

        # #region agent log
        def _imap_ingest(hid: str, msg: str, d: Dict[str, Any]) -> None:
            try:
                logp = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"debug-{run_id}.log")
                with open(logp, "a", encoding="utf-8") as wf:
                    wf.write(
                        json.dumps(
                            {
                                "sessionId": run_id,
                                "timestamp": int(time.time() * 1000),
                                "hypothesisId": hid,
                                "location": "invoice_pipeline.py:run_pipeline:imap",
                                "message": msg,
                                "data": d,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except OSError:
                pass

        # #endregion

        # 7.3 进收件箱，只拉「未读」列表
        sel_typ, sel_dat = mail.select("INBOX")
        try:
            _, list_raw = mail.list()
            inbox_lines = [
                x.decode(errors="replace")[:200]
                for x in (list_raw or [])
                if x and (b"INBOX" in x.upper() or "\u6536\u4ef6\u7bb1".encode("utf-8") in x)
            ][:8]
        except Exception as list_err:
            inbox_lines = [f"LIST_ERROR:{list_err!s}"]
        _imap_ingest(
            "H_select",
            "select_INBOX",
            {
                "host": host,
                "select_typ": str(sel_typ),
                "select_dat_preview": str(sel_dat)[:300],
                "inbox_related_list_entries": inbox_lines,
            },
        )

        # 用 UID 搜索（而非序列号）：UID 在会话间稳定，断线重连后可精确续跑当前邮件（契约§5）
        search_typ, data = mail.uid("SEARCH", "UNSEEN")
        if data and len(data) > 0 and data[0] is not None:
            raw_chunk = data[0]
        else:
            raw_chunk = b""
        mail_ids = raw_chunk.split() if raw_chunk else []
        _imap_ingest(
            "H_search",
            "search_UNSEEN",
            {
                "search_typ": str(search_typ),
                "unseen_count": len(mail_ids),
                "raw_bytes_len": len(raw_chunk) if isinstance(raw_chunk, (bytes, bytearray)) else -1,
                "data_is_none": data is None,
                "chunk0_is_none": (data[0] is None) if (data and len(data) > 0) else True,
            },
        )

        all_typ, all_dat = mail.search(None, "ALL")
        if all_dat and len(all_dat) > 0 and all_dat[0] is not None:
            all_raw = all_dat[0]
        else:
            all_raw = b""
        all_search_count = len(all_raw.split()) if all_raw else 0
        exists_hint: Optional[int] = None
        if sel_dat and sel_dat[0] is not None:
            try:
                ev = sel_dat[0]
                exists_hint = int(ev.decode("ascii", errors="ignore") if isinstance(ev, bytes) else ev)
            except (ValueError, TypeError):
                exists_hint = None
        _imap_ingest(
            "H_all",
            "search_ALL",
            {
                "search_typ": str(all_typ),
                "all_search_count": all_search_count,
                "exists_from_select": exists_hint,
                "exists_matches_all": (
                    exists_hint is not None and all_search_count == exists_hint
                ),
            },
        )

        try:
            st_st, st_d = mail.status("INBOX", "(MESSAGES UNSEEN)")
            sraw = (st_d[0].decode(errors="replace") if st_d and st_d[0] else "")
            _msgs: Optional[int] = None
            _uns: Optional[int] = None
            m1 = re.search(r"MESSAGES\s+(\d+)", sraw, re.I)
            if m1:
                _msgs = int(m1.group(1))
            m2 = re.search(r"UNSEEN\s+(\d+)", sraw, re.I)
            if m2:
                _uns = int(m2.group(1))
            imap_mbox_status = {
                "messages": _msgs,
                "unseen": _uns,
                "status_typ": str(st_st),
                "raw": sraw[:400],
                "exists_from_select": exists_hint,
                "all_search_count": all_search_count,
            }
            _imap_ingest("H_status", "status_INBOX_MESSAGES_UNSEEN", imap_mbox_status)
        except Exception as st_ex:
            imap_mbox_status = {
                "error": str(st_ex),
                "exists_from_select": exists_hint,
                "all_search_count": all_search_count,
            }
            _imap_ingest("H_status", "status_INBOX_fail", imap_mbox_status)

        log(f"--- 任务开始：检测到 {len(mail_ids)} 封未读邮件 ---")

        # 断线重连：163 会在 AI 审计等长空闲后掐断 IMAP 连接（如 Errno 10054）。
        # 策略：宁可重做断点邮件，不可遗漏；已处理完的邮件不再触碰。
        def _reconnect_imap() -> bool:
            nonlocal mail
            try:
                try:
                    mail.logout()
                except Exception:
                    pass
                mail = imaplib.IMAP4_SSL(host)
                mail.login(cfg.imap_user.strip(), cfg.imap_password)
                if should_send_netease_id(host, cfg.use_netease_id):
                    imaplib.Commands['ID'] = ('AUTH', '(("name" "com.netease.mail") ("version" "1.0.0") ("vendor" "netease"))')
                    mail._simple_command('ID', '("name" "com.netease.mail" "version" "1.0.0" "vendor" "netease")')
                typ, _dat = mail.select("INBOX")
                return typ == "OK"
            except Exception as rc_err:
                _log_exc(log, "IMAP 重连失败")
                log(f"错误: {rc_err}")
                return False

        def _fetch_uid_with_reconnect(m_id: bytes, query: str):
            """UID FETCH + 断线重连：重连后对断点邮件整体重做（已读状态不影响 fetch）。

            最多 2 次重连，间隔 1s/3s；仍失败则抛出，由外层 IMAP 异常分支统一兜底。
            """
            nonlocal mail
            delays = (1, 3)
            last_err: Optional[Exception] = None
            for attempt in range(len(delays) + 1):
                try:
                    return mail.uid("FETCH", m_id, query)
                except (imaplib.IMAP4.abort, OSError) as e:
                    last_err = e
                    if attempt >= len(delays):
                        raise
                    wait = delays[attempt]
                    log(f"⚠️ IMAP 连接中断（{type(e).__name__}: {e}），{wait}s 后重连并重做当前邮件（第 {attempt + 1} 次重试）...")
                    _imap_ingest(
                        "H_reconnect",
                        "fetch_abort_reconnect",
                        {
                            "mail_uid": m_id.decode(errors="replace"),
                            "attempt": attempt + 1,
                            "error": f"{type(e).__name__}: {e}",
                            "query": query,
                        },
                    )
                    time.sleep(wait)
                    if not _reconnect_imap():
                        raise last_err
            raise last_err  # 理论不可达

        invoice_kw_count = 0
        # 7.4 逐封处理：主题不含发票/报销相关关键词的整封跳过
        for m_id in mail_ids:
            msg_id_str = m_id.decode()
            _, h_data = _fetch_uid_with_reconnect(m_id, "(BODY[HEADER.FIELDS (SUBJECT DATE)])")
            msg_h = email.message_from_bytes(h_data[0][1])
            subject = decode_str(msg_h["Subject"])
            friendly_date = safe_parse_mail_date(msg_h.get("Date"))

            if any(kw in subject for kw in ["发票", "报销", "报销凭证", "电子发票", "开票"]):
                invoice_kw_count += 1
                # 7.5 拉整封 MIME，遍历部件：落盘 pdf / zip，并对每个 pdf 做 local_inspect
                _, f_data = _fetch_uid_with_reconnect(m_id, "(RFC822)")
                msg = email.message_from_bytes(f_data[0][1])
                pdf_count, idx = 0, 0
                task_summary[msg_id_str] = {
                    "subject": subject,
                    "mail_date": friendly_date,
                    "mail_date_header": (msg_h.get("Date") or "").strip(),
                    "files": [],
                    "error_reason": "",
                }

                seen_hashes: set[str] = set()  # 同封邮件内 MD5 去重（直接附件 vs ZIP 内含相同 PDF）
                for part in msg.walk():
                    if part.get_content_maintype() == 'multipart':
                        continue
                    fname = part.get_filename()
                    if not fname:
                        continue
                    fname = decode_str(fname)
                    if fname.lower().endswith(".pdf"):
                        payload = part.get_payload(decode=True)
                        content_hash = hashlib.md5(payload).hexdigest()
                        if content_hash in seen_hashes:
                            task_summary[msg_id_str].setdefault("dedup_skipped", []).append({
                                "filename": fname,
                                "md5": content_hash,
                                "reason": "直接附件内重复",
                            })
                            continue
                        seen_hashes.add(content_hash)
                        pdf_count += 1
                        idx += 1
                        fname_orig = fname
                        fname = clean_filename(fname, idx)
                        save_p = os.path.join(cfg.extract_dir, f"Msg{msg_id_str}_{fname}")
                        with open(save_p, "wb") as f:
                            f.write(payload)
                        f_type, f_text = local_inspect_pdf(save_p)
                        merged = merge_type_from_name_and_local(fname_orig, f_type)
                        task_summary[msg_id_str]["files"].append({
                            "local_path": save_p,
                            "attachment_original_name": fname_orig,
                            "identified_type": merged,
                            "local_pdf_type": f_type,
                            "text_snapshot": f_text[:3000],
                        })
                    elif fname.lower().endswith(".zip"):
                        idx += 1
                        zip_name = clean_filename(fname, idx)
                        if not zip_name.lower().endswith(".zip"):
                            zip_name = os.path.splitext(zip_name)[0] + ".zip"
                        zip_path = build_non_conflicting_path(
                            os.path.join(cfg.zips_dir, f"Msg{msg_id_str}_{zip_name}")
                        )
                        try:
                            with open(zip_path, "wb") as f:
                                f.write(part.get_payload(decode=True))
                            extracted = extract_pdfs_from_zip(zip_path, msg_id_str, zip_name, cfg.extract_dir)
                            for p, inner_orig in extracted:
                                with open(p, "rb") as _f:
                                    content_hash = hashlib.md5(_f.read()).hexdigest()
                                if content_hash in seen_hashes:
                                    task_summary[msg_id_str].setdefault("dedup_skipped", []).append({
                                        "filename": inner_orig,
                                        "md5": content_hash,
                                        "reason": "ZIP内与直接附件重复",
                                    })
                                    os.remove(p)  # 物理删除重复文件，避免被打包进用户下载的压缩包
                                    continue
                                seen_hashes.add(content_hash)
                                pdf_count += 1
                                f_type, f_text = local_inspect_pdf(p)
                                merged = merge_type_from_name_and_local(inner_orig, f_type)
                                task_summary[msg_id_str]["files"].append({
                                    "local_path": p,
                                    "attachment_original_name": inner_orig,
                                    "identified_type": merged,
                                    "local_pdf_type": f_type,
                                    "text_snapshot": f_text[:3000],
                                })
                        except Exception as e:
                            old_reason = task_summary[msg_id_str].get("error_reason", "")
                            task_summary[msg_id_str]["error_reason"] = (
                                old_reason + f";ZIP_EXTRACT_ERROR: {str(e)}"
                            ).strip(";")

                # 7.6 有 PDF 才调 AI；否则只打日志
                if pdf_count == 0:
                    log(f"⚠️ [人工预警] 日期:{friendly_date} 标题:{subject} 无PDF附件")
                else:
                    log(f"正在调用 AI 审计: {subject}...")
                    audit_res, error_reason, mm, manual_u = call_ai_audit_and_rename(
                        task_summary[msg_id_str], model=cfg.dashscope_model, mail_subject=subject, mail_date_hint=friendly_date, run_id=run_id
                    )
                    all_manual_unknown.extend(manual_u)
                    if audit_res:
                        inv = sanitize_excel_date_display(audit_res.get("invoice_date", "Not Found"))
                        audit_res = {**audit_res, "invoice_date": inv}
                    task_summary[msg_id_str]["audit_result"] = audit_res
                    task_summary[msg_id_str]["classification_mismatches"] = mm
                    all_mismatches.extend(mm)
                    if mm:
                        log(f"⚠️ 本封邮件有 {len(mm)} 个附件「本机分类」与「AI 分类」不一致，请在前端查看详情并人工确认。")
                    pre_reason = task_summary[msg_id_str].get("error_reason", "")
                    task_summary[msg_id_str]["error_reason"] = (pre_reason + ";" + error_reason).strip(";")
                    if audit_res:
                        has_not_found = any(
                            str(audit_res.get(k, "")).strip() in ("", "Not Found")
                            for k in ("invoice_date", "invoice_number", "total_amount")
                        )
                        if has_not_found:
                            log(f"⚠️ 关键字段缺失，未写入 Excel: {subject}")
                        else:
                            excel_rows.append({
                                "invoice_date": audit_res.get("invoice_date", "Not Found"),
                                "invoice_number": audit_res.get("invoice_number", "Not Found"),
                                "total_amount": audit_res.get("total_amount", "Not Found"),
                                "invoice_type": audit_res.get("invoice_type", "Not Found"),
                                "receipt_type": clean_receipt_type(audit_res.get("receipt_type", "Not Found")),
                                "travel_time": audit_res.get("travel_time", "Not Found"),
                                "seller": audit_res.get("seller", "Not Found"),
                                "origin_dest": audit_res.get("origin_dest", "Not Found"),
                            })

        # 7.7 所有邮件处理完后：把本轮汇总行写入 Excel，并写 task_debug.json
        excel_error, excel_target_file = append_to_summary_excel(
            excel_rows,
            cfg.summary_excel_file,
        )
        if excel_error:
            log(f"⚠️ Excel回填失败: {excel_error}")
            for _, item in task_summary.items():
                old_reason = item.get("error_reason", "")
                item["error_reason"] = (old_reason + ";" + excel_error).strip(";")
        elif excel_target_file:
            log(f"√ Excel回填完成: {excel_target_file}")
        # task_summary 里含每封邮件的 files、audit_result、classification_mismatches 等
        with open(cfg.debug_file, 'w', encoding='utf-8') as f:
            json.dump(task_summary, f, ensure_ascii=False, indent=4)
        if mail:
            mail.logout()
        log("\n--- 任务完成！文件已按「附件原名>AI>正文」重命名；仅当三种类型依据不一致时文件名含「（待人工核查）」---")
        log(f"输出目录: {cfg.base_path}")
        stats_out = {
            "unseen_total": len(mail_ids),
            "skipped_no_invoice_keyword": len(mail_ids) - invoice_kw_count,
            "invoice_keyword_mails": invoice_kw_count,
            "manual_unknown_alerts": all_manual_unknown,
            "not_found_alerts": _collect_not_found_alerts(task_summary),
            "imap_mailbox_status": imap_mbox_status,
            "imap_error": None,
        }
        return all_mismatches, stats_out
    except imaplib.IMAP4.error as e:
        err = str(e)
        _log_exc(log, "IMAP 协议错误")
        log(f"错误: {err}")
        hint = _imap_error_hint(err)
        if hint:
            log(hint)
        if mail:
            try:
                mail.logout()
            except Exception:
                pass
        try:
            with open(cfg.debug_file, "w", encoding="utf-8") as f:
                json.dump(task_summary, f, ensure_ascii=False, indent=4)
        except Exception:
            pass
        return all_mismatches, {
            **empty_stats,
            "imap_error": err,
        }
    except Exception as e:
        _log_exc(log, "主流程未捕获异常")
        log(f"错误: {e}")
        if mail:
            try:
                mail.logout()
            except Exception:
                pass
        try:
            with open(cfg.debug_file, "w", encoding="utf-8") as f:
                json.dump(task_summary, f, ensure_ascii=False, indent=4)
        except Exception:
            pass
        return all_mismatches, {**empty_stats, "imap_error": str(e)}
