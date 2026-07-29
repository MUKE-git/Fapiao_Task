"""
发票邮件处理流水线（后厨）：无 Streamlit 依赖，由 app_demo 等入口调用 run_pipeline。

【文件里大致顺序 — 从上到下读即可】
1. 配置与输出路径（RunConfig、桌面文件夹名清洗、是否发网易 IMAP 兼容指令）
2. 通用小工具（重名避让、金额/日期格式化、邮件头解码、附件文件名清洗）
3. AI 失败时的本机正则兜底（从 PDF 片段里硬抽金额/票号/日期）
4. ZIP 解压出 PDF、往报销 Excel 追加行
5. 本机 PDF 关键词分类 + 与 AI 返回的 file_classifications 比对（人工确认列表）
6. 调百炼 API：要 JSON + 按本机类型重命名 PDF
7. run_pipeline：连邮箱 → 扫未读 → 主题含「发票」才处理 → 写 Excel → 写 task_debug.json
"""
from __future__ import annotations

import email
import imaplib
import io
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# 汇总表列顺序：表头与 rows 字典键对应；扩展时在列表末尾追加 (表头, 键名)。
SUMMARY_COLUMNS: List[Tuple[str, str]] = [
    ("发票日期", "invoice_date"),
    ("发票号码", "invoice_number"),
    ("报销金额", "total_amount"),
    ("发票类型", "invoice_type"),
    ("票面类型", "receipt_type"),
    ("出行时间", "travel_time"),
    ("销售方", "seller"),
    ("邮件号码", "task_id"),
    ("行程类型", "trip_type"),
    ("出发地/目的地", "origin_dest"),
]

import dashscope
import pdfplumber
from dashscope import Generation
from openpyxl import Workbook, load_workbook

LogFn = Callable[[str], None]


def _noop_log(_: str) -> None:
    """无网页时的空日志，避免每处都判断 log 是否为 None。"""
    pass


# ---------------------------------------------------------------------------
# 一、配置与输出路径（一次任务用一份 RunConfig，决定文件落在桌面哪个文件夹）
# ---------------------------------------------------------------------------

def safe_desktop_subfolder(name: str) -> str:
    """把用户在网页里填的文件夹名洗干净，去掉非法字符，防止路径跑出桌面。"""
    name = (name or "").strip() or "Invoice_Task"
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip(".")
    if not name or name in (".", ".."):
        return "Invoice_Task"
    return name[:120]


@dataclass
class RunConfig:
    """单次运行所需配置：邮箱登录信息 + 输出子文件夹名 + 网易开关 + AI Key。

    output_root 为 None 时，结果落在本机桌面下的子文件夹；否则落在该根目录下的子文件夹（如云上临时目录）。
    """
    imap_host: str
    imap_user: str
    imap_password: str
    output_folder_name: str = "Invoice_Task"
    use_netease_id: bool = False
    dashscope_api_key: str = ""
    dashscope_model: str = "qwen-plus"
    output_root: Optional[str] = None

    @property
    def base_path(self) -> str:
        """任务根目录：默认桌面子文件夹；若设置了 output_root 则为 根目录/子文件夹。"""
        sub = safe_desktop_subfolder(self.output_folder_name)
        if self.output_root:
            return os.path.join(os.path.abspath(self.output_root), sub)
        return str(Path.home() / "Desktop" / sub)

    @property
    def zips_dir(self) -> str:
        """邮件里 zip 附件先存这里。"""
        return os.path.join(self.base_path, "1_Downloaded_Zips")

    @property
    def extract_dir(self) -> str:
        """单页 PDF 与 zip 解出的 PDF 最终都放在这里。"""
        return os.path.join(self.base_path, "2_Extracted_PDFs")

    @property
    def debug_file(self) -> str:
        """整轮任务的结构化快照，方便排查 AI/附件问题。"""
        return os.path.join(self.base_path, "task_debug.json")

    @property
    def summary_excel_file(self) -> str:
        """报销汇总表（按 SUMMARY_COLUMNS 顺序写入）。"""
        return os.path.join(self.base_path, "发票信息汇总表.xlsx")


def _imap_error_hint(err_text: str) -> str:
    """将 IMAP 常见英文错误转为一行中文提示（不含敏感信息）。"""
    t = err_text.lower()
    if "authentication" in t or "login" in t or "password" in t or "credentials" in t or "auth" in t:
        return "提示：登录失败，多为账号/授权码错误；163 等需使用「客户端授权码」且 IMAP 已开启。"
    if "getaddrinfo" in t or "name or service not known" in t or "nodename" in t:
        return "提示：无法解析 IMAP 服务器地址，请检查「IMAP 服务器」是否拼写正确。"
    if "certificate" in t or ("ssl" in t and "wrong" in t):
        return "提示：SSL 证书或加密方式异常，请确认使用官方 IMAP 主机（如 imap.163.com）。"
    if "timed out" in t or "timeout" in t or "connection refused" in t:
        return "提示：连接超时或被拒绝，请检查网络与防火墙。"
    return ""


def should_send_netease_id(host: str, use_netease_checkbox: bool) -> bool:
    """网易邮箱服务器往往需要额外的 IMAP ID 指令；QQ 等不要发，否则可能登不上。"""
    if use_netease_checkbox:
        return True
    h = (host or "").lower()
    return any(x in h for x in ("163.com", "126.com", "yeah.net", "netease"))


def ensure_output_dirs(cfg: RunConfig) -> None:
    """每次跑任务前确保「下载 zip」和「解压 PDF」两个目录存在。"""
    os.makedirs(cfg.zips_dir, exist_ok=True)
    os.makedirs(cfg.extract_dir, exist_ok=True)


# ---------------------------------------------------------------------------
# 二、通用小工具（多处复用：起名、展示、解码）
# ---------------------------------------------------------------------------

def build_non_conflicting_path(path: str) -> str:
    """目标路径已存在时自动加 _dup1、_dup2…，避免覆盖。"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while True:
        candidate = f"{base}_dup{i}{ext}"
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
    trip_type = "Not Found"
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

    m_time = re.search(
        r"([0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2})[\s\n]*([0-2]?[0-9]:[0-5][0-9](?::[0-5][0-9])?)",
        itinerary_text
    )
    if m_time:
        travel_time = f"{_normalize_date_text(m_time.group(1))} {m_time.group(2)}"

    m_inv_type = re.search(r"(?:发票类型|发票名称)[:：]?\s*(.{2,30})", invoice_text)
    if m_inv_type:
        invoice_type = m_inv_type.group(1).strip()

    m_rec_type = re.search(r"(?:服务名称|货物名称)[:：]?\s*\*?([^*\n]{2,20})\*?", invoice_text)
    if m_rec_type:
        receipt_type = m_rec_type.group(1).strip()

    m_seller = re.search(r"(?:销售方名称|销售方)[:：]?\s*(.{4,60})", invoice_text)
    if m_seller:
        seller = m_seller.group(1).strip()

    m_trip_type = re.search(r"(机票|高铁|火车|滴滴|网约车|出租车|地铁|公交|大巴|飞机)", itinerary_text)
    if m_trip_type:
        trip_type = m_trip_type.group(1)

    m_od = re.search(r"([\u4e00-\u9fa5]{2,}(?:站|机场|中心)?)\s*[-—→至到]\s*([\u4e00-\u9fa5]{2,}(?:站|机场|中心)?)", itinerary_text)
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
        "trip_type": trip_type,
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
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按「原名>AI>正文」解析类型后重命名；仅当三种类型依据不一致时加「（待人工核查）」。"""
    amount_text = format_amount_text(data.get("total_amount", "0"))
    inv_date_raw = str(data.get("invoice_date", "")).strip()
    if not inv_date_raw or inv_date_raw == "Not Found":
        inv_date_raw = "未知日期"
    classifications = data.get("file_classifications") or []
    files = task_info.get("files") or []
    manual_unknown: List[Dict[str, Any]] = []

    # #region agent log
    def _dbg(loc: str, msg: str, data_d: Dict[str, Any], hid: str) -> None:
        try:
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug-70cc39.log")
            with open(p, "a", encoding="utf-8") as df:
                df.write(
                    json.dumps(
                        {
                            "sessionId": "70cc39",
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
            inv_type = str(data.get("receipt_type") or data.get("invoice_type") or "发票").strip()
            base = f"{inv_type}+{inv_date_raw}+{amount_text}元"
        elif eff_n == "Itinerary":
            base = f"行程单+{inv_date_raw}+{amount_text}元"
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
3. **信息提取**：从判定后的文件中提取七个核心字段。
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
   - 重点寻找“上车时间”或“用车时间” 。
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
8. **行程类型 (trip_type)**：
   - 必须从【行程单】文本中提取。
   - 判断出行方式，如“机票”、“高铁”、“火车”、“滴滴”、“网约车”、“出租车”、“地铁”、“公交”等。
   - 若行程单中无明确出行方式，填写 "Not Found"。
9. **出发地/目的地 (origin_dest)**：
   - 必须从【行程单】文本中提取。
   - 格式为“出发地 → 目的地”，如“北京 → 上海”、“杭州东站 → 南京南站”。
   - 若行程单包含多段行程，只提取第一段的起终点。
   - 若无法提取，填写 "Not Found"。

# Constraints
- 如果信息缺失，请填写 "Not Found"。
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
  "trip_type": "网约车",
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
            response = Generation.call(model=model, prompt=f"{system_prompt}\n内容：{combined_content}")
            if response.status_code == 200:
                break
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < len(retry_intervals):
                    time.sleep(retry_intervals[attempt])
                    continue
            break

        if response.status_code == 200:
            res_text = response.output.text.replace("```json", "").replace("```", "").strip()
            try:
                data = json.loads(res_text)
            except Exception:
                # 步骤 B-失败：JSON 解析不了 → 用本机正则兜底一版「伪 AI 结果」
                data = build_fallback_audit_result(task_info.get("files", []))
                error_reason = "JSON_PARSE_ERROR;USE_FALLBACK"
            if any(data.get(k) in (None, "", "Not Found") for k in ["invoice_date", "invoice_number", "total_amount"]):
                error_reason = (error_reason + ";FIELD_NOT_FOUND").strip(";")
            mm, manual_u = _rename_pdfs_with_audit(task_info, data, mail_subject, mail_date_hint)
            return data, error_reason, mm, manual_u
        # 步骤 B-HTTP 非 200：同样走兜底 + 重命名 + 空差异列表（AI 没给出分类）
        code = str(getattr(response, "code", "") or "")
        error_reason = f"AI_API_{code}" if code else f"AI_API_STATUS_{response.status_code}"
        data = build_fallback_audit_result(task_info.get("files", []))
        if any(data.get(k) in (None, "", "Not Found") for k in ["invoice_date", "invoice_number", "total_amount"]):
            error_reason = (error_reason + ";FIELD_NOT_FOUND").strip(";")
        mm, manual_u = _rename_pdfs_with_audit(task_info, data, mail_subject, mail_date_hint)
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
                if has_it and audit.get("trip_type") in (None, "", "Not Found"):
                    flags.append("缺少或无效: trip_type（行程单）")
                if has_it and audit.get("origin_dest") in (None, "", "Not Found"):
                    flags.append("缺少或无效: origin_dest（行程单）")
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
                logp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug-70cc39.log")
                with open(logp, "a", encoding="utf-8") as wf:
                    wf.write(
                        json.dumps(
                            {
                                "sessionId": "70cc39",
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

        search_typ, data = mail.search(None, "UNSEEN")
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

        invoice_kw_count = 0
        # 7.4 逐封处理：主题不含「发票」的整封跳过
        for m_id in mail_ids:
            msg_id_str = m_id.decode()
            _, h_data = mail.fetch(m_id, "(BODY[HEADER.FIELDS (SUBJECT DATE)])")
            msg_h = email.message_from_bytes(h_data[0][1])
            subject = decode_str(msg_h["Subject"])
            friendly_date = safe_parse_mail_date(msg_h.get("Date"))

            if "发票" in subject:
                invoice_kw_count += 1
                # 7.5 拉整封 MIME，遍历部件：落盘 pdf / zip，并对每个 pdf 做 local_inspect
                _, f_data = mail.fetch(m_id, "(RFC822)")
                msg = email.message_from_bytes(f_data[0][1])
                pdf_count, idx = 0, 0
                task_summary[msg_id_str] = {
                    "subject": subject,
                    "mail_date": friendly_date,
                    "mail_date_header": (msg_h.get("Date") or "").strip(),
                    "files": [],
                    "error_reason": "",
                }

                for part in msg.walk():
                    if part.get_content_maintype() == 'multipart':
                        continue
                    fname = part.get_filename()
                    if not fname:
                        continue
                    fname = decode_str(fname)
                    if fname.lower().endswith(".pdf"):
                        pdf_count += 1
                        idx += 1
                        fname_orig = fname
                        fname = clean_filename(fname, idx)
                        save_p = os.path.join(cfg.extract_dir, f"Msg{msg_id_str}_{fname}")
                        with open(save_p, "wb") as f:
                            f.write(part.get_payload(decode=True))
                        f_type, f_text = local_inspect_pdf(save_p)
                        merged = merge_type_from_name_and_local(fname_orig, f_type)
                        task_summary[msg_id_str]["files"].append({
                            "local_path": save_p,
                            "attachment_original_name": fname_orig,
                            "identified_type": merged,
                            "local_pdf_type": f_type,
                            "text_snapshot": f_text[:800],
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
                                pdf_count += 1
                                f_type, f_text = local_inspect_pdf(p)
                                merged = merge_type_from_name_and_local(inner_orig, f_type)
                                task_summary[msg_id_str]["files"].append({
                                    "local_path": p,
                                    "attachment_original_name": inner_orig,
                                    "identified_type": merged,
                                    "local_pdf_type": f_type,
                                    "text_snapshot": f_text[:800],
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
                        task_summary[msg_id_str], model=cfg.dashscope_model, mail_subject=subject, mail_date_hint=friendly_date
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
                                "receipt_type": audit_res.get("receipt_type", "Not Found"),
                                "travel_time": audit_res.get("travel_time", "Not Found"),
                                "seller": audit_res.get("seller", "Not Found"),
                                "task_id": msg_id_str,
                                "trip_type": audit_res.get("trip_type", "Not Found"),
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
