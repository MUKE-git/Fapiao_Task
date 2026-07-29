"""
Streamlit Demo：填写邮箱后在本机桌面或临时目录生成结果，并可下载 zip 包。
启动: streamlit run app_demo.py
"""
from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from typing import Optional

import streamlit as st

from invoice_pipeline import (
    RunConfig,
    _imap_error_hint,
    run_pipeline,
    safe_desktop_subfolder,
    zip_directory_to_bytes,
)


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


def _use_cloud_output(force: bool) -> bool:
    """是否使用临时目录 + 打包下载（Streamlit Cloud / 环境变量 / 用户勾选）。"""
    if force:
        return True
    e = os.environ
    if e.get("FAPIAO_USE_TEMP_OUTPUT", "").strip().lower() in ("1", "true", "yes"):
        return True
    base = (e.get("STREAMLIT_SERVER_BASE_URL") or "") + (e.get("STREAMLIT_SERVER_BASE_URL_PATH") or "")
    if "streamlit.app" in base.lower() or "streamlitcloud" in base.lower():
        return True
    if e.get("STREAMLIT_SHARING_BASE_URL"):
        return True
    return False


st.set_page_config(page_title="发票邮件处理 Demo", layout="centered")
st.title("发票邮件处理 Demo")

with st.expander("高级选项"):
    force_temp = st.checkbox(
        "强制使用临时目录并打包下载（本机测试 zip 流程）",
        value=False,
        help="不勾选时：在 Streamlit Community Cloud 等环境会自动使用临时目录；本机默认仍写入桌面。",
    )

if _use_cloud_output(force_temp):
    st.caption(
        "当前为「临时目录 + 下载」模式：处理完成后请下载 zip；服务器不会长期保存您的文件。"
    )
else:
    st.caption("请在您本机运行本程序；生成文件将保存在您本机桌面上的指定文件夹。")

with st.form("mail_form"):
    imap_host = st.text_input("IMAP 服务器", value="imap.163.com")
    imap_user = st.text_input("邮箱账号")
    imap_password = st.text_input("邮箱授权码（或密码）", type="password")
    output_folder = st.text_input(
        "输出文件夹名",
        value="Invoice_Task",
        help="本机模式下为桌面下的子文件夹名；临时目录模式下为任务子目录名。",
    )
    use_netease = st.checkbox(
        "这是网易邮箱（163/126/yeah）",
        value=False,
        help="若 IMAP 地址里已含 163.com 等，可不勾，程序会自动发送网易兼容指令。"
        "若使用 QQ 邮箱等，请勿勾选，否则可能无法登录。",
    )
    submitted = st.form_submit_button("开始处理")

if submitted:
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
            use_temp = _use_cloud_output(force_temp)
            output_root: Optional[str] = None
            if use_temp:
                output_root = tempfile.mkdtemp(prefix="fapiao_")

            cfg = RunConfig(
                imap_host=imap_host.strip(),
                imap_user=imap_user.strip(),
                imap_password=imap_password,
                output_folder_name=output_folder,
                use_netease_id=use_netease,
                dashscope_api_key=api_key,
                dashscope_model=_dashscope_model(),
                output_root=output_root,
            )

            with st.status("正在处理…", expanded=True) as status:

                def log(msg: str) -> None:
                    status.write(msg)

                mismatches, stats = run_pipeline(cfg, log=log)

            imap_err = stats.get("imap_error")
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

                if ut == 0:
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
                elif ikw == 0:
                    st.warning(
                        f"检测到 **{ut}** 封未读邮件，但主题中**均不含「发票」**，未下载、未解压附件。"
                        "（仅处理主题含「发票」的邮件。）"
                    )
                    st.session_state.pop("last_zip_bytes", None)
                    st.session_state.pop("last_zip_name", None)
                else:
                    st.success("本轮脚本已结束。")

                manual_u = stats.get("manual_unknown_alerts") or []
                if manual_u:
                    st.warning(
                        "**以下文件在「附件原名 / PDF 正文 / AI」三种类型依据上存在不一致，已加「（待人工核查）」；请对照原邮件核对：**"
                    )
                    for u in manual_u:
                        st.markdown(
                            f"- **{u.get('filename', '')}**  \n"
                            f"  - 邮件主题：`{u.get('mail_subject', '')}`  \n"
                            f"  - 邮件日期：`{u.get('mail_date', '')}`  \n"
                            f"  - 采用类型：`{u.get('type_used', '')}`  \n"
                            f"  - AI 角色（若有）：`{u.get('ai_role_used') or '（无）'}`"
                        )

                nf = stats.get("not_found_alerts") or []
                if nf:
                    st.warning(
                        "**以下邮件存在未识别字段、兜底未抽全或 AI 调用异常；请到邮箱中按「邮件时间 / 主题」打开原邮件，自行核对附件 PDF：**"
                    )
                    for n in nf:
                        st.markdown(
                            f"- **主题：** `{n.get('mail_subject', '')}`  \n"
                            f"  - 邮件时间（Date 头）：`{n.get('mail_date_header', '')}`  \n"
                            f"  - 解析日期：`{n.get('mail_date', '')}`  \n"
                            f"  - 说明：`{'; '.join(n.get('flags', []))}`"
                        )

                if mismatches:
                    st.warning(
                        f"**以下 {len(mismatches)} 个文件需要人工确认**："
                        "「附件原名优先合并后的类型」与 AI 的 file_classifications 不一致（重命名仍以原名>AI>正文为准）。"
                    )
                    for m in mismatches:
                        sub = m.get("mail_subject") or "（无主题）"
                        fn = m.get("filename", "")
                        lt = m.get("local_type", "")
                        at = m.get("ai_type", "")
                        st.markdown(
                            f"- **{fn}**  \n"
                            f"  - 邮件主题：`{sub}`  \n"
                            f"  - 本机分类：`{lt}`（规范化：{m.get('local_normalized', '')}）  \n"
                            f"  - AI 分类：`{at}`（规范化：{m.get('ai_normalized', '')}）"
                        )

                if not imap_err and ikw > 0:
                    arc = safe_desktop_subfolder(output_folder) or "Invoice_Task"
                    zip_bytes = zip_directory_to_bytes(cfg.base_path, arc)
                    zip_name = f"fapiao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
                    st.session_state["last_zip_bytes"] = zip_bytes
                    st.session_state["last_zip_name"] = zip_name

                    if use_temp and output_root:
                        try:
                            shutil.rmtree(output_root, ignore_errors=True)
                        except OSError:
                            pass

                    if use_temp:
                        st.info(
                            "输出已打包为 zip；汇总表见包内 **发票信息汇总表.xlsx**（列顺序：发票日期、发票号码、报销金额），"
                            "调试信息见 **task_debug.json**。（临时目录已清理，请以本下载为准。）"
                        )
                    else:
                        st.info(
                            f"输出目录：`{cfg.base_path}`\n\n"
                            f"调试 JSON：`{cfg.debug_file}`\n\n"
                            "也可点击下方按钮下载 zip 备份。"
                        )

if st.session_state.get("last_zip_bytes"):
    st.download_button(
        label="下载本轮输出（zip）",
        data=st.session_state["last_zip_bytes"],
        file_name=st.session_state.get("last_zip_name") or "fapiao_output.zip",
        mime="application/zip",
    )
