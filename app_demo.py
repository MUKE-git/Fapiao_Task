"""
Streamlit Demo：填写邮箱后在「运行本机的用户」桌面生成结果。
启动: streamlit run app_demo.py
"""
import os

import streamlit as st

from invoice_pipeline import RunConfig, run_pipeline


def _dashscope_api_key() -> str:
    env_key = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if env_key:
        return env_key
    try:
        return str(st.secrets["DASHSCOPE_API_KEY"] or "").strip()
    except (KeyError, FileNotFoundError, TypeError, Exception):
        return ""


st.set_page_config(page_title="发票邮件处理 Demo", layout="centered")
st.title("发票邮件处理 Demo")
st.caption("请在您本机运行本程序；生成文件将保存在您本机桌面上的指定文件夹。")

with st.form("mail_form"):
    imap_host = st.text_input("IMAP 服务器", value="imap.163.com")
    imap_user = st.text_input("邮箱账号")
    imap_password = st.text_input("邮箱授权码（或密码）", type="password")
    output_folder = st.text_input("桌面上的输出文件夹名", value="Invoice_Task")
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
        cfg = RunConfig(
            imap_host=imap_host.strip(),
            imap_user=imap_user.strip(),
            imap_password=imap_password,
            output_folder_name=output_folder,
            use_netease_id=use_netease,
            dashscope_api_key=api_key,
        )
        with st.status("正在处理…", expanded=True) as status:

            def log(msg: str) -> None:
                status.write(msg)

            mismatches = run_pipeline(cfg, log=log)
        st.success("本轮脚本已结束。")
        if mismatches:
            st.warning(
                f"**以下 {len(mismatches)} 个文件需要人工确认**："
                "本机关键词分类与 AI 判断不一致（文件名仍按本机规则生成）。"
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
        st.info(f"输出目录：`{cfg.base_path}`\n\n调试 JSON：`{cfg.debug_file}`")
