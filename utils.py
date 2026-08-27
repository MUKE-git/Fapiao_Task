"""
通用小工具模块（由 invoice_pipeline.py 拆分而来，契约 = A 类：仅换 import，逻辑不变）。

涵盖：重名路径避让、金额/日期中文格式化、票面类型清洗、邮件头与附件名解码、
Date 头安全解析、Excel 日期显示清洗、目录 zip 打包。
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Optional


def build_non_conflicting_path(path: str) -> str:
    """目标路径已存在时自动加 (1)、(2)…，避免覆盖。"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while True:
        candidate = f"{base}({i}){ext}"
        if not os.path.exists(candidate):
            return candidate
        i += 1


def format_amount_text(amount) -> str:
    """把金额转成用于文件名的短字符串（去多余小数点）。"""
    try:
        num = float(amount)
        if num.is_integer():
            return str(int(num))
        return f"{num:.2f}".rstrip("0").rstrip(".")
    except Exception:
        return str(amount).strip() if str(amount).strip() else "0"


def clean_receipt_type(raw: str) -> str:
    """清洗票面类型：*A*B 格式取二级类目 B，同时去掉 Windows 非法字符。"""
    if not raw:
        return raw
    # 匹配 *一级类目*二级类目 格式，取二级类目
    m = re.match(r'\*[^*]+\*(.+)', raw)
    if m:
        cleaned = m.group(1).strip()
    else:
        cleaned = raw.strip()
    return re.sub(r'[\\/:*?"<>|]', '', cleaned)


def format_travel_date_cn(travel_time) -> str:
    """从出行时间里抠出年月日，变成「2026年03月09日」（行程单等）。"""
    text = str(travel_time or "").strip()
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if not m:
        return "未知日期"
    y, mo, d = m.groups()
    return f"{y}年{int(mo):02d}月{int(d):02d}日"


def format_invoice_date_cn(invoice_date) -> str:
    """开票日期 YYYY-MM-DD →「2026年03月09日」，用于发票 PDF 重命名（勿与 travel_time 混用）。"""
    s = str(invoice_date or "").strip()
    if not s or s.lower() == "not found":
        return "未知日期"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        return f"{m.group(1)}年{int(m.group(2)):02d}月{int(m.group(3)):02d}日"
    m2 = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m2:
        y, mo, d = m2.groups()
        return f"{y}年{int(mo):02d}月{int(d):02d}日"
    return "未知日期"


def _normalize_date_text(date_text) -> str:
    """各种中文/斜杠日期统一成 YYYY-MM-DD，供兜底解析与 Excel。"""
    s = str(date_text or "").strip()
    m = re.search(r"(\d{4})[年\-/](\d{1,2})[月\-/](\d{1,2})", s)
    if not m:
        return "Not Found"
    y, mo, d = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def zip_directory_to_bytes(root_dir: str, arcname_root: str) -> bytes:
    """将 root_dir 下所有文件递归打入 zip；无法读取的文件跳过。arcname_root 为压缩包内顶层文件夹名。"""
    buf = io.BytesIO()
    root_dir = os.path.abspath(root_dir)
    arcname_root = arcname_root.strip("/\\") or "output"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if not os.path.isdir(root_dir):
            return b""
        for folder, _, files in os.walk(root_dir):
            for fn in files:
                path = os.path.join(folder, fn)
                if not os.path.isfile(path):
                    continue
                try:
                    rel = os.path.relpath(path, root_dir)
                    arc = os.path.join(arcname_root, rel).replace("\\", "/")
                    zf.write(path, arcname=arc)
                except OSError:
                    pass
    buf.seek(0)
    return buf.read()


def clean_filename(filename: str, idx: int) -> str:
    """去掉邮件附件名里 Windows 非法字符；乱码或过短时改成 Attachment_{idx}.pdf。"""
    filename = re.sub(r'[\\/:*?"<>|]', '_', filename)
    if "?" in filename or "\ufffd" in filename or len(filename.strip()) < 5:
        return f"Attachment_{idx}.pdf"
    return filename


def decode_str(s) -> str:
    """解码邮件 Subject/附件名里的 MIME 编码（=?utf-8?b?...?= 等）。"""
    if not s:
        return ""
    try:
        decoded_list = decode_header(s)
        combined_text = ""
        for content, charset in decoded_list:
            if isinstance(content, bytes):
                for enc in [charset, 'utf-8', 'gbk', 'gb18030']:
                    if not enc:
                        continue
                    try:
                        combined_text += content.decode(enc)
                        break
                    except Exception:
                        continue
                else:
                    combined_text += content.decode('utf-8', errors='replace')
            else:
                combined_text += str(content)
        return combined_text
    except Exception:
        return "decoded_error"


def safe_parse_mail_date(date_header: Optional[str]) -> str:
    """
    解析邮件 Date 头为 YYYY-MM-DD；异常格式（如 Go 调试串含 m=+）不抛错，尽量抠出日期或返回「未知日期」。
    """
    if not date_header or not str(date_header).strip():
        return "未知日期"
    raw = str(date_header).strip()
    try:
        return parsedate_to_datetime(raw).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        pass
    if " m=+" in raw:
        raw = raw.split(" m=+", 1)[0].strip()
    m = re.search(r"\d{4}-\d{2}-\d{2}", raw)
    if m:
        return m.group(0)
    return "未知日期"


_ISO_DATE_FULL = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def sanitize_excel_date_display(value) -> str:
    """
    开票日期写入汇总表「发票日期」列前清洗：只保留合法 YYYY-MM-DD，避免 Go 调试串等触发 Invalid date value or format。
    """
    if value is None:
        return "Not Found"
    s = str(value).strip()
    if not s or s.lower() == "not found":
        return "Not Found"
    if _ISO_DATE_FULL.match(s):
        return s
    if " m=+" in s:
        s = s.split(" m=+", 1)[0].strip()
    m = re.search(r"\d{4}-\d{2}-\d{2}", s)
    if m:
        return m.group(0)
    return "Not Found"