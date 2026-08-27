"""
文档类型判定模块（由 invoice_pipeline.py 拆分而来，契约 = A 类：仅换 import，逻辑不变）。

负责「本机关键词 vs AI file_classifications」三类依据的归一化、合并与不一致检测：
- 附件原名关键词 → Invoice / Itinerary
- 与 PDF 正文合并 → 重命名用类型
- 三层（原名/正文/AI）不一致 → 由前端提示人工确认
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


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