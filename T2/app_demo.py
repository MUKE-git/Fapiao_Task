"""Streamlit entry: process one run in a temporary directory and deliver a checked ZIP."""
from __future__ import annotations

import os
import json
import shutil
import tempfile
from datetime import datetime

import streamlit as st

from invoice_pipeline import RunConfig, _imap_error_hint
from delivery_exporter import build_delivery_zip
from pipeline import run_pipeline


def _dashscope_api_key() -> str:
    env_key = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if env_key:
        return env_key
    try:
        return str(st.secrets.get("DASHSCOPE_API_KEY", "") or "").strip()
    except (FileNotFoundError, TypeError, Exception):
        return ""


def _dashscope_model() -> str:
    env_model = (os.environ.get("DASHSCOPE_MODEL") or "").strip()
    if env_model:
        return env_model
    try:
        return str(st.secrets.get("DASHSCOPE_MODEL", "") or "").strip() or "qwen-plus"
    except (FileNotFoundError, TypeError, Exception):
        return "qwen-plus"


def _render_mail_alerts(mails: list[dict], alerts: list[dict], mismatches: list[dict]) -> None:
    """One visible notice per mail; keep file-level reasons in its details."""
    by_id = {str(mail.get("mail_id")): mail for mail in mails}
    grouped: dict[str, list[str]] = {}

    def mail_for(item: dict) -> tuple[str, dict] | None:
        mail_id = str(item.get("mail_id") or "")
        if mail_id in by_id:
            return mail_id, by_id[mail_id]
        matches = [(key, mail) for key, mail in by_id.items()
                   if mail.get("subject") == item.get("mail_subject")]
        return matches[0] if len(matches) == 1 else None

    for alert in alerts:
        target = mail_for(alert)
        if target is None:
            continue
        mail_id, mail = target
        for reason in alert.get("flags") or []:
            reason = str(reason)
            # A companion XML/OFD registration is diagnostic when usable PDF records
            # already cover the mail; it is not a separate user action.
            if "文件已登记，本阶段尚未提取" in reason and mail.get("records") and all(
                record.get("kind") != "a类" or record.get("matched_itinerary")
                for record in mail.get("records") or []
            ):
                continue
            file_name = alert.get("filename") or ""
            detail = f"{file_name}：{reason}" if file_name else reason
            if detail not in grouped.setdefault(mail_id, []):
                grouped[mail_id].append(detail)

    for mismatch in mismatches:
        target = mail_for(mismatch)
        if target is None:
            continue
        mail_id, _mail = target
        detail = f"{mismatch.get('filename') or '文件'}：发票/行程单类别判断不一致，需核对原件"
        if detail not in grouped.setdefault(mail_id, []):
            grouped[mail_id].append(detail)

    for mail in mails:
        mail_id = str(mail.get("mail_id"))
        reasons = grouped.get(mail_id) or []
        if not reasons:
            continue
        subject = mail.get("subject") or "（无主题）"
        mail_date = mail.get("mail_date") or "（邮件时间不可用）"
        st.warning(f"邮件「{subject}」有 {len(reasons)} 项需要核对。邮件时间：{mail_date}")
        with st.expander("查看该邮件的文件和原因"):
            for reason in reasons:
                st.write(reason)


st.set_page_config(page_title="发票邮件处理 Demo", layout="centered")
st.title("发票邮件处理 Demo")
st.caption("本轮在独立临时目录处理；完成后下载交付 ZIP。")

IMAP_PRESETS = {
    "163 邮箱": "imap.163.com",
    "QQ 邮箱": "imap.qq.com",
    "126 邮箱": "imap.126.com",
    "Yeah 邮箱": "imap.yeah.net",
    "Gmail": "imap.gmail.com",
    "Outlook": "outlook.office365.com",
    "其他（手动输入）": "__custom__",
}

with st.form("mail_form"):
    imap_choice = st.selectbox("IMAP 服务器", options=list(IMAP_PRESETS.keys()))
    if IMAP_PRESETS[imap_choice] == "__custom__":
        imap_host = st.text_input("手动输入 IMAP 服务器地址", placeholder="例如 imap.example.com")
    else:
        imap_host = IMAP_PRESETS[imap_choice]
    imap_user = st.text_input("邮箱账号")
    imap_password = st.text_input("邮箱授权码（或密码）", type="password")
    use_netease = st.checkbox(
        "这是网易邮箱（163/126/yeah）",
        value=False,
        help="若 IMAP 地址里已含 163.com 等，可不勾，程序会自动发送网易兼容指令。"
        "若使用 QQ 邮箱等，请勿勾选，否则可能无法登录。",
    )
    submitted = st.form_submit_button("开始处理")

if submitted:
    # 每次提交都只展示本轮结果，避免校验失败时沿用上轮下载按钮。
    for key in ("last_zip_bytes", "last_zip_name", "last_zip_partial"):
        st.session_state.pop(key, None)
    if not imap_user.strip() or not imap_password:
        st.error("请填写邮箱账号与授权码。")
    else:
        api_key = _dashscope_api_key()
        if not api_key:
            st.error(
                "未检测到有效的 DashScope API Key。\n\n"
                "**任选其一：**\n"
                "- 在系统环境变量中设置 `DASHSCOPE_API_KEY`（需重启终端 / IDE 后再运行 Streamlit）。\n"
                "- 在 **`项目目录/.streamlit/secrets.toml`** 中写入一行：\n"
                "  `DASHSCOPE_API_KEY = \"你的百炼 Key\"`  \n"
                "  **引号内不能为空**；若曾清空过该文件，请重新粘贴 Key。"
            )
        else:
            output_root = tempfile.mkdtemp(prefix="fapiao_")

            cfg = RunConfig(
                imap_host=imap_host.strip(),
                imap_user=imap_user.strip(),
                imap_password=imap_password,
                output_folder_name="Invoice_Task",
                use_netease_id=use_netease,
                dashscope_api_key=api_key,
                dashscope_model=_dashscope_model(),
                output_root=output_root,
            )

            with st.status("正在处理…", expanded=False) as status:

                def log(msg: str) -> None:
                    status.write(msg)
                    print(msg, flush=True)  # 双写到 stdout，让 Streamlit Cloud App logs 也能看到

                try:
                    mismatches, stats = run_pipeline(cfg, log=log)
                except Exception:
                    shutil.rmtree(output_root, ignore_errors=True)
                    raise

            imap_err = stats.get("imap_error")
            run_err = stats.get("run_error")
            excel_err = stats.get("excel_error")
            failed_mails = stats.get("failed_mails") or []
            registered_count = 0
            record_count = 0
            debug_error = None
            debug_mails: list[dict] = []
            if os.path.isfile(cfg.debug_file):
                try:
                    with open(cfg.debug_file, encoding="utf-8") as debug_input:
                        debug_mails = json.load(debug_input)
                    if not isinstance(debug_mails, list):
                        raise ValueError("顶层不是邮件列表")
                    registered_count = len(debug_mails)
                    record_count = sum(len(mail.get("records") or []) for mail in debug_mails)
                except (OSError, ValueError, TypeError, AttributeError) as exc:
                    debug_error = str(exc)
            st.info(
                f"本轮统计：IMAP 未读 **{stats.get('unseen_total', 0)}** 封；"
                f"主题命中 **{stats.get('invoice_keyword_mails', 0)}** 封；"
                f"JSON 登记 **{registered_count}** 封；发票记录 **{record_count}** 笔。"
            )
            if debug_error:
                st.error(f"无法读取本轮 task_debug.json：{debug_error}")
            if imap_err:
                st.error(f"IMAP 错误：{imap_err}")
                hint = _imap_error_hint(imap_err)
                if hint:
                    st.warning(hint)
                st.session_state.pop("last_zip_bytes", None)
                st.session_state.pop("last_zip_name", None)
            else:
                ut = stats.get("unseen_total", 0)
                ikw = stats.get("invoice_keyword_mails", 0)

                if run_err:
                    st.error(f"本轮后处理失败，已保存可恢复的部分 JSON；请核对后重试：{run_err}")
                if excel_err:
                    st.error(f"汇总表未成功写入：{excel_err}")
                if failed_mails:
                    st.warning(f"有 {len(failed_mails)} 封邮件处理失败；其他邮件仍继续处理。失败邮件已按 UID 保存在 JSON，请人工核对。")

                if ut == 0 and not run_err:
                    st.warning("当前收件箱（IMAP **INBOX**）**没有未读邮件**，未处理任何附件。")
                    ims = stats.get("imap_mailbox_status") or {}
                    nmsg = ims.get("messages")
                    nuns = ims.get("unseen")
                    if ims.get("error"):
                        st.caption(f"IMAP STATUS 未取到：`{ims.get('error')}`")
                    elif nmsg is not None and nmsg > 0:
                        ex_sel = ims.get("exists_from_select")
                        all_c = ims.get("all_search_count")
                        extra = ""
                        if ex_sel is not None and all_c is not None:
                            extra = (
                                f"（SELECT 的 EXISTS={ex_sel}，与 SEARCH ALL 序号数={all_c}"
                                + (" 一致" if ex_sel == all_c else " 不一致")
                                + "；均为 **IMAP 收件箱 INBOX** 内「一封邮件算一封」，不是网页会话折叠后的条数。）"
                            )
                        st.info(
                            f"服务器侧统计：收件箱 **INBOX** 内共有 **{nmsg}** 封邮件，**UNSEEN（未读）= {nuns if nuns is not None else '?'}**。"
                            f"{extra}"
                            "若网页列表比这里**多得多**，常见是：网页用了**会话/主题合并**、或列表里混入了**其它文件夹**；本程序只认 **IMAP 里的标准收件箱 INBOX**。"
                            "若网页仍显示未读而此处 UNSEEN=0，可 **标已读再标回未读** 同步状态，或确认信在 **收件箱** 而非仅推广等分类。"
                        )
                    st.session_state.pop("last_zip_bytes", None)
                    st.session_state.pop("last_zip_name", None)
                elif ikw == 0 and not failed_mails and not run_err:
                    st.warning(
                        f"检测到 **{ut}** 封未读邮件，但主题中**均不含发票/报销相关关键词**，未下载、未解压附件。"
                        "（仅处理主题含「发票」「报销」「报销凭证」「电子发票」「开票」的邮件。）"
                    )
                    st.session_state.pop("last_zip_bytes", None)
                    st.session_state.pop("last_zip_name", None)
                elif record_count == 0:
                    st.warning(
                        "本轮没有生成发票记录。下载包仅供排查，请核对 task_debug.json 中的 "
                        "files、records 和 error_reason；这不代表发票处理成功。"
                    )
                _render_mail_alerts(debug_mails, stats.get("not_found_alerts") or [], mismatches)

            try:
                if not (imap_err or run_err or excel_err or debug_error) and os.path.isfile(cfg.debug_file):
                    zip_bytes = build_delivery_zip(cfg)
                    st.session_state["last_zip_bytes"] = zip_bytes
                    st.session_state["last_zip_name"] = (
                        f"fapiao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip")
                    st.session_state["last_zip_partial"] = bool(failed_mails or record_count == 0)
                    st.success("本轮交付包已生成：汇总表、调试 JSON 和业务命名的发票/行程单 PDF。")
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                st.error(f"本次交付包未能生成：{exc}")
            finally:
                shutil.rmtree(output_root, ignore_errors=True)

if st.session_state.get("last_zip_bytes"):
    st.download_button(
        label=("下载部分结果供核对（zip）" if st.session_state.get("last_zip_partial")
               else "下载本轮输出（zip）"),
        data=st.session_state["last_zip_bytes"],
        file_name=st.session_state.get("last_zip_name") or "fapiao_output.zip",
        mime="application/zip",
    )
