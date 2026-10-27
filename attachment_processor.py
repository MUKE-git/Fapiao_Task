"""
附件处理模块（由 invoice_pipeline.py 拆分而来，契约 = A 类：仅换 import，逻辑不变）。

- extract_pdfs_from_zip：从无密码 ZIP（仅根目录层 .pdf）解出 PDF 落到 extract_dir，返回 (落盘路径, 原始文件名)。
- local_inspect_pdf：用 pdfplumber 抽全文 + 少量关键词粗分 Invoice / Itinerary / Unknown。
"""

from __future__ import annotations

import email
import hashlib
import os
import re
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
_MAX_INTERNAL_NAME = 120
_MIME_EXTENSIONS = {
    "application/pdf": ".pdf", "application/xml": ".xml", "text/xml": ".xml",
    "application/ofd": ".ofd", "image/png": ".png", "image/jpeg": ".jpg",
    "image/bmp": ".bmp", "image/webp": ".webp", "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
}
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}


def _positive_format(content: bytes) -> tuple[str, str] | None:
    """Use a recognizable byte signature to override a misleading suffix."""
    if content.startswith(b"%PDF-"):
        return "PDF", ".pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "图片", ".png"
    if content.startswith(b"\xff\xd8\xff"):
        return "图片", ".jpg"
    if content.startswith(b"BM"):
        return "图片", ".bmp"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "图片", ".webp"
    if content.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                names = [entry.filename.lower().replace("\\", "/")
                         for entry in archive.infolist()[:1000]]
            return ("OFD", ".ofd") if any(name == "ofd.xml" for name in names) else ("ZIP", ".zip")
        except (OSError, zipfile.BadZipFile):
            return None
    for codec in ("utf-8-sig", "utf-16"):
        try:
            start = content[:256].decode(codec).lstrip()
        except UnicodeError:
            continue
        if start.startswith("<") and not start.lower().startswith(("<html", "<!doctype html")):
            return "XML", ".xml"
    return None


@dataclass(frozen=True)
class CollectedFile:
    """已登记文件；业务字段由后续形态处理器提取。"""
    meta: FileRecord
    path: str


def _safe_storage_path(directory: str, prefix: str, original_name: str,
                       seq: int, extension_hint: str = "") -> str:
    """按实际完整文件名的 120 字符上限选一个不会覆盖旧文件的路径。"""
    basename = original_name.replace("\\", "/").rsplit("/", 1)[-1]
    stem, extension = os.path.splitext(basename)
    if not extension and basename.lower() in {*_FILE_TYPES, ".zip"}:
        stem, extension = "", basename
    extension = extension or extension_hint
    # Windows 文件名规则只作用于存盘名，original_name 始终原样保留。
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip().rstrip(". ")
    extension = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", extension).rstrip(". ")
    if not stem or stem.upper() in _WINDOWS_RESERVED or "\ufffd" in stem:
        stem = f"Attachment_{seq}"
    digest = hashlib.sha256(original_name.encode("utf-8")).hexdigest()[:8]
    collision = 0
    while True:
        suffix = f"({collision})" if collision else ""
        budget = _MAX_INTERNAL_NAME - len(prefix) - len(extension) - len(suffix)
        if budget < 1:
            raise ValueError("附件扩展名或编号过长，无法满足内部文件名 120 字符上限")
        if len(stem) > budget:
            marker = f"_{digest}"
            if budget <= len(marker):
                raise ValueError("附件扩展名或编号过长，无法保留超长名摘要")
            short_stem = stem[:budget - len(marker)].rstrip(". ") + marker
        else:
            short_stem = stem
        candidate = os.path.join(directory, f"{prefix}{short_stem}{suffix}{extension}")
        if not os.path.lexists(candidate):
            return candidate
        collision += 1


def _write_unique_file(directory: str, prefix: str, original_name: str,
                       seq: int, content: bytes, extension_hint: str = "") -> str:
    """独占创建最终路径，避免并发创建同名文件时覆盖已有内容。"""
    while True:
        path = _safe_storage_path(directory, prefix, original_name, seq, extension_hint)
        try:
            with open(path, "xb") as out:
                out.write(content)
            return path
        except FileExistsError:
            continue


def collect_mail_files(msg: email.message.Message, mail: MailRecord,
                       cfg: RunConfig, downloaded_files: list | None = None) -> list[CollectedFile]:
    """下载附件、解 ZIP、去重、安全存盘并编号；不提取凭证业务字段。

    返回的 meta 与 mail.files 中对象相同，供 PDF 处理器补预读字段。
    ZIP 是容器；其中保留的文件才占 file_seq。未知格式记为“其他”。
    """
    collected: list[CollectedFile] = []
    seen_hashes: set[str] = set()
    zip_file_count = 0
    zip_total_bytes = 0

    def add_file(name: str, content: bytes, source: str,
                 extension_hint: str = "", declared_type: str = "") -> None:
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
        seq = len(mail.files) + 1
        if seq > 99:
            raise ValueError("单邮件文件数超过 99，无法分配两位文件序号")
        seq_text = f"{seq:02d}"
        actual = _positive_format(content)
        file_type = actual[0] if actual else declared_type
        storage_name = name
        if actual and os.path.splitext(name)[1].lower() != actual[1]:
            storage_name = os.path.splitext(name)[0] + actual[1]
        path = _write_unique_file(cfg.extract_dir, f"{mail.mail_id}_{seq_text}_",
                                  storage_name, seq, content, extension_hint)
        seen_hashes.add(digest)
        suffix = os.path.splitext(path)[1].lower()
        linked = "正文链接" in source
        meta = FileRecord(file_seq=seq_text,
                          file_type=file_type or _FILE_TYPES.get(suffix, "其他"),
                          original_name=name, internal_name=os.path.basename(path),
                          acquisition="正文链接" if linked else "",
                          source_ref=source.removeprefix("正文链接:") if linked else "")
        mail.files.append(meta)
        collected.append(CollectedFile(meta, path))

    def unpack_zip(name: str, content: bytes, depth: int,
                   link_label: str = "") -> None:
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
                        unpack_zip(inner, payload, depth + 1, link_label)
                    else:
                        source = f"ZIP:正文链接:{link_label}" if link_label else f"ZIP:{name}"
                        add_file(inner, payload, source)
        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
            mail.error_reason = (mail.error_reason + f";ZIP_EXTRACT_ERROR:{exc}").strip(";")

    for part in msg.walk():
        if part.is_multipart():
            continue
        raw_name = part.get_filename()
        if raw_name is None and part.get_content_disposition() != "attachment":
            continue
        name = decode_str(raw_name) if raw_name else ""
        content = part.get_payload(decode=True)
        if content is None:
            continue
        extension_hint = _MIME_EXTENSIONS.get(part.get_content_type().lower(), "")
        detected = _positive_format(content)
        if (detected and detected[0] == "ZIP") or (
                detected is None and
                (name.lower().endswith(".zip") or (not name and extension_hint == ".zip"))):
            path = _write_unique_file(cfg.zips_dir, f"{mail.mail_id}_",
                                      name, 1, content, extension_hint)
            unpack_zip(name, content, 1)
        else:
            try:
                add_file(name, content, "直接附件", extension_hint)
            except ValueError as exc:
                mail.error_reason = (mail.error_reason + f";ATTACHMENT_LIMIT:{exc}").strip(";")
                break
    for downloaded in downloaded_files or []:
        try:
            source = "正文链接:" + str(downloaded.source_label)
            if downloaded.file_type == "ZIP":
                unpack_zip(downloaded.name, downloaded.content, 1,
                           str(downloaded.source_label))
            else:
                add_file(downloaded.name, downloaded.content, source,
                         declared_type=downloaded.file_type)
        except ValueError as exc:
            mail.error_reason = (mail.error_reason + f";LINK_FILE_LIMIT:{type(exc).__name__}").strip(";")
    return collected
