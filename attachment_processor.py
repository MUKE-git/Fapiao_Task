"""
附件处理模块（由 invoice_pipeline.py 拆分而来，契约 = A 类：仅换 import，逻辑不变）。

- extract_pdfs_from_zip：从无密码 ZIP（仅根目录层 .pdf）解出 PDF 落到 extract_dir，返回 (落盘路径, 原始文件名)。
- local_inspect_pdf：用 pdfplumber 抽全文 + 少量关键词粗分 Invoice / Itinerary / Unknown。
"""

from __future__ import annotations

import email
import hashlib
import os
import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import List, Tuple

import pdfplumber

from utils import (
    build_non_conflicting_path,
    clean_filename,
    decode_str,
)
from config import RunConfig
from data_model import DedupSkipped, FileRecord, MailRecord


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


# T2 公共附件入口。上面的 T1 函数保留兼容；新主循环只调用此入口。
_FILE_TYPES = {".pdf": "PDF", ".xml": "XML", ".ofd": "OFD",
               ".png": "图片", ".jpg": "图片", ".jpeg": "图片",
               ".bmp": "图片", ".webp": "图片"}
_MAX_ZIP_FILES = 50
_MAX_ZIP_BYTES = 15 * 1024 * 1024


@dataclass(frozen=True)
class CollectedFile:
    """已登记文件；业务字段由后续形态处理器提取。"""
    meta: FileRecord
    path: str


def _safe_attachment_name(name: str, seq: int) -> str:
    ext = os.path.splitext(name)[1].lower()
    cleaned = clean_filename(os.path.basename(name.replace("\\", "/")), seq)
    if cleaned.startswith("Attachment_") and ext:
        cleaned = f"Attachment_{seq}{ext}"
    elif cleaned.startswith("Attachment_"):
        cleaned = f"Attachment_{seq}"
    return cleaned[:180]


def collect_mail_files(msg: email.message.Message, mail: MailRecord,
                       cfg: RunConfig) -> list[CollectedFile]:
    """下载附件、解 ZIP、去重、安全存盘并编号；不提取凭证业务字段。

    返回的 meta 与 mail.files 中对象相同，供 PDF 处理器补预读字段。
    ZIP 是容器；其中保留的文件才占 file_seq。未知格式记为“其他”。
    """
    collected: list[CollectedFile] = []
    seen_hashes: set[str] = set()
    zip_file_count = 0
    zip_total_bytes = 0

    def add_file(name: str, content: bytes, source: str) -> None:
        nonlocal zip_file_count, zip_total_bytes
        if source.startswith("ZIP:"):
            zip_file_count += 1
            zip_total_bytes += len(content)
            if zip_file_count > _MAX_ZIP_FILES or zip_total_bytes > _MAX_ZIP_BYTES:
                raise ValueError("ZIP 解出数量或总大小超过限制（50个文件、15MB）")
        digest = hashlib.md5(content).hexdigest()
        if digest in seen_hashes:
            mail.dedup_skipped.append(DedupSkipped(name, f"同邮件内容重复（{source}）"))
            return
        seen_hashes.add(digest)
        seq = len(mail.files) + 1
        if seq > 99:
            raise ValueError("单邮件文件数超过 99，无法分配两位文件序号")
        seq_text = f"{seq:02d}"
        suffix = os.path.splitext(name)[1].lower()
        safe_name = _safe_attachment_name(name, seq)
        path = build_non_conflicting_path(
            os.path.join(cfg.extract_dir, f"{mail.mail_id}_{seq_text}_{safe_name}")
        )
        with open(path, "wb") as out:
            out.write(content)
        meta = FileRecord(file_seq=seq_text,
                          file_type=_FILE_TYPES.get(suffix, "其他"), original_name=name)
        mail.files.append(meta)
        collected.append(CollectedFile(meta, path))

    def unpack_zip(name: str, content: bytes, depth: int) -> None:
        if depth > 2:
            mail.error_reason = (mail.error_reason + ";ZIP_DEPTH_LIMIT").strip(";")
            return
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                for entry in archive.infolist():
                    if entry.is_dir():
                        continue
                    inner = entry.filename.replace("\\", "/")
                    if inner.startswith("/") or ".." in inner.split("/"):
                        mail.error_reason = (mail.error_reason + ";ZIP_UNSAFE_PATH").strip(";")
                        continue
                    if entry.file_size > _MAX_ZIP_BYTES or entry.flag_bits & 1:
                        mail.error_reason = (mail.error_reason + ";ZIP_UNSUPPORTED_ENTRY").strip(";")
                        continue
                    payload = archive.read(entry)
                    if inner.lower().endswith(".zip"):
                        unpack_zip(inner, payload, depth + 1)
                    else:
                        add_file(inner, payload, f"ZIP:{name}")
        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
            mail.error_reason = (mail.error_reason + f";ZIP_EXTRACT_ERROR:{exc}").strip(";")

    for part in msg.walk():
        if part.is_multipart():
            continue
        raw_name = part.get_filename()
        if not raw_name:
            continue
        name = decode_str(raw_name)
        content = part.get_payload(decode=True)
        if content is None:
            continue
        if name.lower().endswith(".zip"):
            path = build_non_conflicting_path(
                os.path.join(cfg.zips_dir, f"{mail.mail_id}_{_safe_attachment_name(name, 1)}")
            )
            with open(path, "wb") as out:
                out.write(content)
            unpack_zip(name, content, 1)
        else:
            try:
                add_file(name, content, "直接附件")
            except ValueError as exc:
                mail.error_reason = (mail.error_reason + f";ATTACHMENT_LIMIT:{exc}").strip(";")
                break
    return collected
