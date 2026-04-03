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
import json
import os
import re
import zipfile
from dataclasses import dataclass
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

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
    """单次运行所需配置：邮箱登录信息 + 桌面输出子文件夹名 + 网易开关 + AI Key。"""
    imap_host: str
    imap_user: str
    imap_password: str
    output_folder_name: str = "Invoice_Task"
    use_netease_id: bool = False
    dashscope_api_key: str = ""

    @property
    def base_path(self) -> str:
        """桌面上的任务根目录，例如 C:\\Users\\你\\Desktop\\Invoice_Task。"""
        return str(Path.home() / "Desktop" / safe_desktop_subfolder(self.output_folder_name))

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
    def total_excel_file(self) -> str:
        """优先使用的报销汇总表。"""
        return os.path.join(self.base_path, "total.xlsx")

    @property
    def fallback_total_excel_file(self) -> str:
        """total.xlsx 尚不存在时改用 total2（无扩展名，与旧逻辑一致）。"""
        return os.path.join(self.base_path, "total2")


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
    """从出行时间里抠出年月日，变成「2026年03月09日」用于重命名发票 PDF。"""
    text = str(travel_time or "").strip()
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if not m:
        return "未知日期"
    y, mo, d = m.groups()
    return f"{y}年{int(mo):02d}月{int(d):02d}日"


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

    return {
        "total_amount": amount,
        "invoice_number": invoice_no,
        "invoice_date": invoice_date,
        "travel_time": travel_time,
        "data_source": "Fallback_Local_Regex",
    }


# ---------------------------------------------------------------------------
# 四、ZIP 附件：只解压根目录单层 PDF → 落到 extract_dir，供后续 local_inspect / AI 使用
# ---------------------------------------------------------------------------

def extract_pdfs_from_zip(zip_path: str, mail_id: str, zip_display_name: str, extract_dir: str):
    """无密码 zip；跳过子目录里的文件，只处理压缩包根下的 .pdf。"""
    pdf_paths = []
    zip_base = os.path.splitext(os.path.basename(zip_display_name))[0]
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            name = info.filename
            if name.endswith("/") or "/" in name or "\\" in name:
                continue
            if not name.lower().endswith(".pdf"):
                continue
            safe_pdf_name = clean_filename(os.path.basename(name), 1)
            target_name = f"Msg{mail_id}_{zip_base}_{safe_pdf_name}"
            target_path = build_non_conflicting_path(os.path.join(extract_dir, target_name))
            with zf.open(info, "r") as src, open(target_path, "wb") as dst:
                dst.write(src.read())
            pdf_paths.append(target_path)
    return pdf_paths


# ---------------------------------------------------------------------------
# 四（续）、报销总表：在 D/E/G 列追加行（开票日期、发票号码、金额），兼容 total.xlsx 与 total2
# ---------------------------------------------------------------------------

def append_to_total_excel(
    rows,
    total_excel_file: str,
    fallback_total_excel_file: str,
) -> Tuple[Optional[str], Optional[str]]:
    """rows 来自本轮所有「处理成功」邮件的 AI 结果；无行则跳过。返回 (错误信息, 实际写入的文件路径)。"""
    if not rows:
        return None, None
    try:
        target_file = total_excel_file
        if os.path.exists(total_excel_file):
            wb = load_workbook(total_excel_file)
        else:
            target_file = fallback_total_excel_file
            if os.path.exists(target_file):
                wb = load_workbook(target_file)
            else:
                wb = Workbook()
                ws = wb.active
                ws["D1"] = "开票日期"
                ws["E1"] = "发票号码"
                ws["G1"] = "报销金额"
        ws = wb.active
        row_idx = 2
        while ws[f"D{row_idx}"].value not in (None, ""):
            row_idx += 1
        for item in rows:
            ws[f"D{row_idx}"] = sanitize_excel_date_display(item.get("invoice_date", "Not Found"))
            ws[f"E{row_idx}"] = item.get("invoice_number", "Not Found")
            ws[f"G{row_idx}"] = item.get("total_amount", "Not Found")
            row_idx += 1
        wb.save(target_file)
        return None, target_file
    except PermissionError:
        return "EXCEL_PERMISSION_DENIED: 请关闭 total.xlsx/total2 后重试", None
    except Exception as e:
        return f"EXCEL_WRITE_ERROR: {str(e)}", None


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
    开票日期写入 Excel D 列前清洗：只保留合法 YYYY-MM-DD，避免 Go 调试串等触发 Invalid date value or format。
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
    比较本机 identified_type 与 AI file_classifications。
    仅当 AI 给出了该文件的 role 且与本机规范化结果不一致时记入列表。
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
# 六、调用百炼 qwen-plus：拉结构化 JSON + 按「本机 identified_type」批量重命名磁盘上的 PDF
#    （注意：文件名模板仍信本机分类；AI 的 file_classifications 只参与「是否不一致」提示）
# ---------------------------------------------------------------------------

def call_ai_audit_and_rename(task_info: dict, mail_subject: str = "") -> Tuple[Optional[dict], str, List[Dict[str, Any]]]:
    # 步骤 A：把同一封邮件下的多个 PDF 片段拼成一段给模型的「内容」
    combined_content = ""
    for file in task_info["files"]:
        combined_content += f"\n--- File: {os.path.basename(file['local_path'])} ---\n"
        combined_content += f"Pre-Type: {file['identified_type']}\n"
        combined_content += f"Text: {file['text_snapshot']}\n"

    system_prompt = """# Role
你是一个专业的财务报销审计专家，擅长从复杂的出行票据中提取结构化信息。

# Context 我会为你提供一组文件的文本内容。每个文件可能带有预处理标签（如 "identified_type": "Invoice"），但这些标签仅供参考，可能存在错误或未定义（Pending）。 

# Task 
1. **角色判定**：自主分析每个文件的内容，判断每个文件是真正的【发票】、还是【行程单/行程明细】、或其它（Unknown）。
2. **数据核实**：如果预处理已提供分类，请核实其准确性；如果分类为 "Pending" 或错误，请根据内容重新定义。 
3. **信息提取**：从判定后的文件中提取四个核心字段。
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
   - 忽略“申请日期”或“打印日期” [cite: 15, 24]。

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
  "data_source": "T3_Chuxing",
  "file_classifications": [
    {"file": "Msg123_发票.pdf", "role": "Invoice"},
    {"file": "Msg123_行程单.pdf", "role": "Itinerary"}
  ]
}"""

    error_reason = ""
    try:
        # 步骤 B：HTTP 调 DashScope，期望返回一整段 JSON 字符串
        response = Generation.call(model='qwen-plus', prompt=f"{system_prompt}\n内容：{combined_content}")
        if response.status_code == 200:
            res_text = response.output.text.replace("```json", "").replace("```", "").strip()
            try:
                data = json.loads(res_text)
            except Exception:
                # 步骤 B-失败：JSON 解析不了 → 用本机正则兜底一版「伪 AI 结果」
                data = build_fallback_audit_result(task_info.get("files", []))
                error_reason = "JSON_PARSE_ERROR;USE_FALLBACK"
            # 步骤 C：用 AI（或兜底）里的金额、出行日期，生成重命名用的片段
            amount_text = format_amount_text(data.get("total_amount", "0"))
            travel_date_cn = format_travel_date_cn(data.get("travel_time", ""))
            # 步骤 D：逐个附件改名；模板只看本机 identified_type，不看 AI 的 role
            for f in task_info["files"]:
                old = f["local_path"]
                f_type = f.get("identified_type", "")
                if f_type == "Invoice":
                    new_name = f"{travel_date_cn}+客运服务费+{amount_text}元.pdf"
                elif f_type == "Itinerary":
                    new_name = f"行程单+{amount_text}元.pdf"
                else:
                    new_name = f"票据+{amount_text}元.pdf"
                raw_new = os.path.join(os.path.dirname(old), new_name)
                new = build_non_conflicting_path(raw_new)
                os.rename(old, new)
            if any(data.get(k) in (None, "", "Not Found") for k in ["invoice_date", "invoice_number", "total_amount"]):
                error_reason = (error_reason + ";FIELD_NOT_FOUND").strip(";")
            # 步骤 E：把 AI 的 file_classifications 和本机类型比对，供网页弹「人工确认」
            mm = collect_classification_mismatches(task_info, data, mail_subject)
            return data, error_reason, mm
        # 步骤 B-HTTP 非 200：同样走兜底 + 重命名 + 空差异列表（AI 没给出分类）
        code = str(getattr(response, "code", "") or "")
        error_reason = f"AI_API_{code}" if code else f"AI_API_STATUS_{response.status_code}"
        data = build_fallback_audit_result(task_info.get("files", []))
        amount_text = format_amount_text(data.get("total_amount", "0"))
        travel_date_cn = format_travel_date_cn(data.get("travel_time", ""))
        for f in task_info["files"]:
            old = f["local_path"]
            f_type = f.get("identified_type", "")
            if f_type == "Invoice":
                new_name = f"{travel_date_cn}+客运服务费+{amount_text}元.pdf"
            elif f_type == "Itinerary":
                new_name = f"行程单+{amount_text}元.pdf"
            else:
                new_name = f"票据+{amount_text}元.pdf"
            raw_new = os.path.join(os.path.dirname(old), new_name)
            new = build_non_conflicting_path(raw_new)
            os.rename(old, new)
        if any(data.get(k) in (None, "", "Not Found") for k in ["invoice_date", "invoice_number", "total_amount"]):
            error_reason = (error_reason + ";FIELD_NOT_FOUND").strip(";")
        mm = collect_classification_mismatches(task_info, data, mail_subject)
        return data, error_reason, mm
    except Exception as e:
        return None, f"PROCESSING_EXCEPTION: {str(e)}", []


# ---------------------------------------------------------------------------
# 七、主流程 run_pipeline：入口函数，串起「邮箱 → 邮件 → 附件 → AI → Excel → 调试 JSON」
# ---------------------------------------------------------------------------

def run_pipeline(cfg: RunConfig, log: Optional[LogFn] = None) -> List[Dict[str, Any]]:
    """执行完整一轮；返回值 = 所有邮件里「本机类型 ≠ AI 类型」的附件清单，给 Streamlit 展示。"""
    log = log or _noop_log
    all_mismatches: List[Dict[str, Any]] = []
    # 7.0 前置检查：没有 Key 无法调模型，直接结束
    if not (cfg.dashscope_api_key or "").strip():
        log("错误：未配置 DashScope API Key。请在 .streamlit/secrets.toml 中设置 DASHSCOPE_API_KEY，或设置环境变量 DASHSCOPE_API_KEY。")
        return []

    # 7.1 注入全局 Key，并创建桌面输出子目录
    dashscope.api_key = cfg.dashscope_api_key.strip()
    ensure_output_dirs(cfg)

    task_summary = {}
    excel_rows = []
    mail = None
    try:
        # 7.2 连接邮箱：SSL + 登录；必要时发网易专用 ID
        host = (cfg.imap_host or "").strip() or "imap.163.com"
        mail = imaplib.IMAP4_SSL(host)
        mail.login(cfg.imap_user.strip(), cfg.imap_password)

        if should_send_netease_id(host, cfg.use_netease_id):
            imaplib.Commands['ID'] = ('AUTH', '(("name" "com.netease.mail") ("version" "1.0.0") ("vendor" "netease"))')
            mail._simple_command('ID', '("name" "com.netease.mail" "version" "1.0.0" "vendor" "netease")')

        # 7.3 进收件箱，只拉「未读」列表
        mail.select("INBOX")
        _, data = mail.search(None, "UNSEEN")
        mail_ids = data[0].split()

        log(f"--- 任务开始：检测到 {len(mail_ids)} 封未读邮件 ---")

        # 7.4 逐封处理：主题不含「发票」的整封跳过
        for m_id in mail_ids:
            msg_id_str = m_id.decode()
            _, h_data = mail.fetch(m_id, "(BODY[HEADER.FIELDS (SUBJECT DATE)])")
            msg_h = email.message_from_bytes(h_data[0][1])
            subject = decode_str(msg_h["Subject"])
            friendly_date = safe_parse_mail_date(msg_h.get("Date"))

            if "发票" in subject:
                # 7.5 拉整封 MIME，遍历部件：落盘 pdf / zip，并对每个 pdf 做 local_inspect
                _, f_data = mail.fetch(m_id, "(RFC822)")
                msg = email.message_from_bytes(f_data[0][1])
                pdf_count, idx = 0, 0
                task_summary[msg_id_str] = {"subject": subject, "files": [], "error_reason": ""}

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
                        fname = clean_filename(fname, idx)
                        save_p = os.path.join(cfg.extract_dir, f"Msg{msg_id_str}_{fname}")
                        with open(save_p, "wb") as f:
                            f.write(part.get_payload(decode=True))
                        f_type, f_text = local_inspect_pdf(save_p)
                        task_summary[msg_id_str]["files"].append({
                            "local_path": save_p,
                            "identified_type": f_type,
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
                            for p in extracted:
                                pdf_count += 1
                                f_type, f_text = local_inspect_pdf(p)
                                task_summary[msg_id_str]["files"].append({
                                    "local_path": p,
                                    "identified_type": f_type,
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
                    audit_res, error_reason, mm = call_ai_audit_and_rename(
                        task_summary[msg_id_str], mail_subject=subject
                    )
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
                        excel_rows.append({
                            "invoice_date": audit_res.get("invoice_date", "Not Found"),
                            "invoice_number": audit_res.get("invoice_number", "Not Found"),
                            "total_amount": audit_res.get("total_amount", "Not Found"),
                        })

        # 7.7 所有邮件处理完后：把本轮汇总行写入 Excel，并写 task_debug.json
        excel_error, excel_target_file = append_to_total_excel(
            excel_rows,
            cfg.total_excel_file,
            cfg.fallback_total_excel_file,
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
        log("\n--- 任务完成！所有文件已根据 AI 结果自动重命名排序 ---")
        log(f"输出目录: {cfg.base_path}")
        return all_mismatches
    except Exception as e:
        log(f"错误: {e}")
        if mail:
            try:
                mail.logout()
            except Exception:
                pass
        return all_mismatches
