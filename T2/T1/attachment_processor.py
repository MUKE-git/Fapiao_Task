"""
附件处理模块（由 invoice_pipeline.py 拆分而来，契约 = A 类：仅换 import，逻辑不变）。

- extract_pdfs_from_zip：从无密码 ZIP（仅根目录层 .pdf）解出 PDF 落到 extract_dir，返回 (落盘路径, 原始文件名)。
- local_inspect_pdf：用 pdfplumber 抽全文 + 少量关键词粗分 Invoice / Itinerary / Unknown。
"""

from __future__ import annotations

import os
import zipfile
from typing import List, Tuple

import pdfplumber

from utils import (
    build_non_conflicting_path,
    clean_filename,
)


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