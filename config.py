"""
配置与输出路径模块（由 invoice_pipeline.py 拆分而来，契约 = A 类：仅换 import，逻辑不变）。

单次运行的一份配置 RunConfig，决定：
- 邮箱登录信息（IMAP 主机 / 账号 / 授权码）
- 输出子文件夹名 + 结果落盘根目录（本机桌面 or 云上临时目录）
- 是否对网易邮箱发送兼容 ID 指令
- AI Key / 模型名

另含：汇总表列定义、交通类票面类型关键词、路径清洗、网易 IMAP 判定、目录初始化、IMAP 错误提示映射。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# 汇总表列顺序：表头与 rows 字典键对应；扩展时在列表末尾追加 (表头, 键名)。
SUMMARY_COLUMNS: List[Tuple[str, str]] = [
    ("发票日期", "invoice_date"),
    ("发票号码", "invoice_number"),
    ("报销金额", "total_amount"),
    ("发票类型", "invoice_type"),
    ("票面类型", "receipt_type"),
    ("出行时间", "travel_time"),
    ("销售方", "seller"),
    ("出发地/目的地", "origin_dest"),
]

# 交通类票面类型关键词：匹配到这些关键词的发票，强制要求有行程单
# 判定为子串匹配（any(kw in receipt_type)），"客运服务"已含"客运服务"，并同时覆盖"客运服务费"（哈啰/嗖嗖开具差异）
TRANSPORT_RECEIPT_KEYWORDS = [
    "客运服务",
    "代收通行费",
    "运输服务费",
    "通行费",
    "网约车服务费",
    "出租车服务费",
]


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