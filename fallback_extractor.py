"""
本机正则兜底模块（由 invoice_pipeline.py 拆分而来，契约 = A 类：仅换 import，逻辑不变）。

当 AI 不可用或返回的 JSON 解析失败时，从已抽取的 PDF 文本片段里硬凑一张「伪审计结果」，
保证后续 Excel 写入与 PDF 重命名流程不中断。字段缺失一律填 "Not Found"。
"""

from __future__ import annotations

import re

from utils import _normalize_date_text


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
    match_amount = "Not Found"
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

    # 行程单金额是独立字段；只接受行程单自身明确标注的总金额，不能借发票价税合计。
    m_match = re.search(
        r"(?:报销金额|匹配金额|费用合计|合计金额|总金额|实付金额|订单金额|总费用)"
        r"[ \t]*[:：]?[ \t]*[¥￥]?[ \t]*([0-9]+(?:\.[0-9]+)?)",
        itinerary_text,
    )
    if m_match:
        match_amount = m_match.group(1)

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

    # 项目名可能与单价、数量、金额等列挤在同一行。只在明确的
    # 列间空白或数字/货币列开始处截断，保留名称内部的数字。
    m_rec_type = re.search(r"\*([^*\n]+)\*([^*\n]{1,80})", invoice_text)
    if m_rec_type:
        item_name = m_rec_type.group(2)
    else:
        labeled = re.search(r"(?:服务名称|货物名称)[:：]?[ \t]*([^\n]{1,80})", invoice_text)
        item_name = labeled.group(1) if labeled else ""
    item_name = re.split(r"[ \t]{2,}|[ \t]+(?=[¥￥]?[0-9]+(?:\.[0-9]+)?(?:[ \t%]|$))|[ \t]+(?=[¥￥])", item_name, maxsplit=1)[0].strip()
    if item_name:
        receipt_type = item_name

    # 销售方：匹配各种格式，如 "销 名称：xxx"、"销售方名称：xxx"、"销售方：xxx"
    m_seller = re.search(r"(?:销售方名称|销售方)[:：]?\s*(.{4,60})", invoice_text)
    if not m_seller:
        m_seller = re.search(r"销\s*售?\s*方?\s*名称[:：]?\s*(.{4,60})", invoice_text)
    if not m_seller:
        m_seller = re.search(r"销\s+名称[:：]\s*(.{4,60})", invoice_text)
    if m_seller:
        seller = m_seller.group(1).strip()

    # 日期和时间必须相邻，避免跨段落把申请日期和另一笔行程的时间拼接。
    for m_time in re.finditer(
        r"(?<![0-9])([0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2})"
        r"(?:[ \t]+|[ \t]*\r?\n[ \t]*)([01]?[0-9]|2[0-3]):([0-5][0-9])(?::[0-5][0-9])?",
        itinerary_text,
    ):
        line_start = itinerary_text.rfind("\n", 0, m_time.start()) + 1
        prefix = itinerary_text[line_start:m_time.start()]
        if (any(label in prefix for label in ("申请日期", "申请时间", "开票日期", "制作时间", "打印时间"))
                or re.search(r"[0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2}", prefix)):
            continue
        travel_time = f"{_normalize_date_text(m_time.group(1))} {int(m_time.group(2)):02d}:{m_time.group(3)}"
        break

    # 出发地/目的地：从行程单表格中提取起点和终点
    m_od = re.search(r"([\u4e00-\u9fa5]{2,}(?:站|机场|中心)?)\s*[-—→至到]\s*([\u4e00-\u9fa5]{2,}(?:站|机场|中心)?)", itinerary_text)
    if not m_od:
        # 尝试匹配表格格式：城市 起点 终点（如 "广州 春兰花园西南侧 智光综合能源产业"）
        m_od = re.search(r"[\u4e00-\u9fa5]{2,}\s+([\u4e00-\u9fa5]{2,}(?:[-\u4e00-\u9fa5]*)?)\s+([\u4e00-\u9fa5]{2,}(?:[-\u4e00-\u9fa5]*)?)\s+[¥￥]", itinerary_text)
    if m_od:
        origin_dest = f"{m_od.group(1)} → {m_od.group(2)}"

    return {
        "total_amount": amount,
        "match_amount": match_amount,
        "invoice_number": invoice_no,
        "invoice_date": invoice_date,
        "travel_time": travel_time,
        "invoice_type": invoice_type,
        "receipt_type": receipt_type,
        "seller": seller,
        "origin_dest": origin_dest,
        "data_source": "Fallback_Local_Regex",
    }
