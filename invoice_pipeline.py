"""
发票邮件处理流水线（后厨）：无 Streamlit 依赖，由 app_demo 等入口调用 run_pipeline。

【文件里大致顺序 — 从上到下读即可】
1. 配置与输出路径（RunConfig、桌面文件夹名清洗、是否发网易 IMAP 兼容指令）
2. 通用小工具（重名避让、金额/日期格式化、邮件头解码、附件文件名清洗）
3. AI 失败时的本机正则兜底（从 PDF 片段里硬抽金额/票号/日期）
4. ZIP 解压出 PDF、往报销 Excel 追加行
5. 本机 PDF 关键词分类 + 与 AI 返回的 file_classifications 比对（人工确认列表）
6. 调百炼 API：要 JSON + 按本机类型重命名 PDF
7. run_pipeline：连邮箱 → 扫未读 → 主题含「发票/报销/报销凭证/电子发票/开票」才处理 → 写 Excel → 写 task_debug.json
"""
from __future__ import annotations

from datetime import datetime
import email
import hashlib
import imaplib
import io
import json
import os
import re
import shutil
import time
import traceback
import zipfile
from dataclasses import dataclass
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# (SUMMARY_COLUMNS / TRANSPORT_RECEIPT_KEYWORDS 已拆分至 config.py，由上方 import 承接)

import dashscope
import pdfplumber
from dashscope import Generation
from openpyxl import Workbook, load_workbook

from config import (
    RunConfig,
    SUMMARY_COLUMNS,
    TRANSPORT_RECEIPT_KEYWORDS,
    _imap_error_hint,
    ensure_output_dirs,
    safe_desktop_subfolder,
    should_send_netease_id,
)

from utils import (
    _normalize_date_text,
    build_non_conflicting_path,
    clean_filename,
    clean_receipt_type,
    decode_str,
    format_amount_text,
    format_invoice_date_cn,
    format_travel_date_cn,
    safe_parse_mail_date,
    sanitize_excel_date_display,
    zip_directory_to_bytes,
)

from type_classifier import (
    _resolve_ai_role_for_file,
    classify_type_from_attachment_name,
    collect_classification_mismatches,
    merge_type_from_name_and_local,
    normalize_doc_role,
    resolve_effective_type_for_rename,
    triple_type_disagreement,
)

from ai_client import (
    _rename_pdfs_with_audit,
    call_ai_audit_and_rename,
)

from fallback_extractor import build_fallback_audit_result

from excel_writer import append_to_summary_excel

from attachment_processor import (
    extract_pdfs_from_zip,
    local_inspect_pdf,
)

LogFn = Callable[[str], None]


def _noop_log(_: str) -> None:
    """无网页时的空日志，避免每处都判断 log 是否为 None。"""
    pass


def _log_exc(log: LogFn, prefix: str = "异常") -> None:
    """把当前正在处理的异常的完整 traceback 写到 log。

    用法：在 except 块里直接调用 `_log_exc(log, "场景名")`。
    必须在 except 块内调用，否则 traceback.format_exc() 拿不到当前异常。
    """
    log(f"❌ {prefix}：\n{traceback.format_exc()}")


# (配置与输出路径：safe_desktop_subfolder / RunConfig / _imap_error_hint /
#  should_send_netease_id / ensure_output_dirs 已拆分至 config.py，由上方 import 承接)

# (通用小工具：build_non_conflicting_path / format_amount_text / clean_receipt_type /
#  format_travel_date_cn / format_invoice_date_cn / _normalize_date_text
#  已拆分至 utils.py，由上方 import 承接)


# (本机正则兜底：build_fallback_audit_result 已拆分至 fallback_extractor.py，由上方 import 承接)


# (ZIP 附件处理：extract_pdfs_from_zip 已拆分至 attachment_processor.py，由上方 import 承接)


# (报销总表写入：append_to_summary_excel 已拆分至 excel_writer.py，由上方 import 承接)


# (通用小工具：zip_directory_to_bytes / clean_filename / decode_str / safe_parse_mail_date /
#  sanitize_excel_date_display 已拆分至 utils.py，由上方 import 承接)


# (文档类型：classify_type_from_attachment_name / merge_type_from_name_and_local /
#  resolve_effective_type_for_rename / triple_type_disagreement / normalize_doc_role /
#  _resolve_ai_role_for_file / collect_classification_mismatches 已拆分至 type_classifier.py)


# (PDF 预读：local_inspect_pdf 已拆分至 attachment_processor.py，由上方 import 承接)


# (AI 审计与重命名：call_ai_audit_and_rename / _rename_pdfs_with_audit 已拆分至 ai_client.py，
#  由上方 import 承接；System prompt 暂保留在其函数体内，后续可外置为 prompts 资源)


# ---------------------------------------------------------------------------
# 七、主流程 run_pipeline：入口函数，串起「邮箱 → 邮件 → 附件 → AI → Excel → 调试 JSON」
# ---------------------------------------------------------------------------

def run_pipeline(
    cfg: RunConfig, log: Optional[LogFn] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """执行完整一轮；返回 (分类不一致列表, 统计信息)。统计含 unseen_total、invoice_keyword_mails、manual_unknown_alerts 等。"""
    run_id = f"{datetime.now().month}-{datetime.now().day}-{datetime.now().hour:02d}{datetime.now().minute:02d}"
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
                if has_it and audit.get("origin_dest") in (None, "", "Not Found"):
                    flags.append("缺少或无效: origin_dest（行程单）")
                # 交通类发票强制要求行程单：票面类型含交通关键词但无行程单附件
                if not has_it and audit is not None:
                    receipt_type = clean_receipt_type(str(audit.get("receipt_type", "") or ""))
                    if receipt_type and receipt_type != "Not Found":
                        if any(kw in receipt_type for kw in TRANSPORT_RECEIPT_KEYWORDS):
                            flags.append(f"缺少行程单: 票面类型为「{receipt_type}」（交通类），但未找到行程单附件")
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
                logp = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"debug-{run_id}.log")
                with open(logp, "a", encoding="utf-8") as wf:
                    wf.write(
                        json.dumps(
                            {
                                "sessionId": run_id,
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

        # 用 UID 搜索（而非序列号）：UID 在会话间稳定，断线重连后可精确续跑当前邮件（契约§5）
        search_typ, data = mail.uid("SEARCH", "UNSEEN")
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

        # 断线重连：163 会在 AI 审计等长空闲后掐断 IMAP 连接（如 Errno 10054）。
        # 策略：宁可重做断点邮件，不可遗漏；已处理完的邮件不再触碰。
        def _reconnect_imap() -> bool:
            nonlocal mail
            try:
                try:
                    mail.logout()
                except Exception:
                    pass
                mail = imaplib.IMAP4_SSL(host)
                mail.login(cfg.imap_user.strip(), cfg.imap_password)
                if should_send_netease_id(host, cfg.use_netease_id):
                    imaplib.Commands['ID'] = ('AUTH', '(("name" "com.netease.mail") ("version" "1.0.0") ("vendor" "netease"))')
                    mail._simple_command('ID', '("name" "com.netease.mail" "version" "1.0.0" "vendor" "netease")')
                typ, _dat = mail.select("INBOX")
                return typ == "OK"
            except Exception as rc_err:
                _log_exc(log, "IMAP 重连失败")
                log(f"错误: {rc_err}")
                return False

        def _fetch_uid_with_reconnect(m_id: bytes, query: str):
            """UID FETCH + 断线重连：重连后对断点邮件整体重做（已读状态不影响 fetch）。

            最多 2 次重连，间隔 1s/3s；仍失败则抛出，由外层 IMAP 异常分支统一兜底。
            """
            nonlocal mail
            delays = (1, 3)
            last_err: Optional[Exception] = None
            for attempt in range(len(delays) + 1):
                try:
                    return mail.uid("FETCH", m_id, query)
                except (imaplib.IMAP4.abort, OSError) as e:
                    last_err = e
                    if attempt >= len(delays):
                        raise
                    wait = delays[attempt]
                    log(f"⚠️ IMAP 连接中断（{type(e).__name__}: {e}），{wait}s 后重连并重做当前邮件（第 {attempt + 1} 次重试）...")
                    _imap_ingest(
                        "H_reconnect",
                        "fetch_abort_reconnect",
                        {
                            "mail_uid": m_id.decode(errors="replace"),
                            "attempt": attempt + 1,
                            "error": f"{type(e).__name__}: {e}",
                            "query": query,
                        },
                    )
                    time.sleep(wait)
                    if not _reconnect_imap():
                        raise last_err
            raise last_err  # 理论不可达

        invoice_kw_count = 0
        # 7.4 逐封处理：主题不含发票/报销相关关键词的整封跳过
        for m_id in mail_ids:
            msg_id_str = m_id.decode()
            _, h_data = _fetch_uid_with_reconnect(m_id, "(BODY[HEADER.FIELDS (SUBJECT DATE)])")
            msg_h = email.message_from_bytes(h_data[0][1])
            subject = decode_str(msg_h["Subject"])
            friendly_date = safe_parse_mail_date(msg_h.get("Date"))

            if any(kw in subject for kw in ["发票", "报销", "报销凭证", "电子发票", "开票"]):
                invoice_kw_count += 1
                # 7.5 拉整封 MIME，遍历部件：落盘 pdf / zip，并对每个 pdf 做 local_inspect
                _, f_data = _fetch_uid_with_reconnect(m_id, "(RFC822)")
                msg = email.message_from_bytes(f_data[0][1])
                pdf_count, idx = 0, 0
                task_summary[msg_id_str] = {
                    "subject": subject,
                    "mail_date": friendly_date,
                    "mail_date_header": (msg_h.get("Date") or "").strip(),
                    "files": [],
                    "error_reason": "",
                }

                seen_hashes: set[str] = set()  # 同封邮件内 MD5 去重（直接附件 vs ZIP 内含相同 PDF）
                for part in msg.walk():
                    if part.get_content_maintype() == 'multipart':
                        continue
                    fname = part.get_filename()
                    if not fname:
                        continue
                    fname = decode_str(fname)
                    if fname.lower().endswith(".pdf"):
                        payload = part.get_payload(decode=True)
                        content_hash = hashlib.md5(payload).hexdigest()
                        if content_hash in seen_hashes:
                            task_summary[msg_id_str].setdefault("dedup_skipped", []).append({
                                "filename": fname,
                                "md5": content_hash,
                                "reason": "直接附件内重复",
                            })
                            continue
                        seen_hashes.add(content_hash)
                        pdf_count += 1
                        idx += 1
                        fname_orig = fname
                        fname = clean_filename(fname, idx)
                        save_p = os.path.join(cfg.extract_dir, f"Msg{msg_id_str}_{fname}")
                        with open(save_p, "wb") as f:
                            f.write(payload)
                        f_type, f_text = local_inspect_pdf(save_p)
                        merged = merge_type_from_name_and_local(fname_orig, f_type)
                        task_summary[msg_id_str]["files"].append({
                            "local_path": save_p,
                            "attachment_original_name": fname_orig,
                            "identified_type": merged,
                            "local_pdf_type": f_type,
                            "text_snapshot": f_text[:3000],
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
                                with open(p, "rb") as _f:
                                    content_hash = hashlib.md5(_f.read()).hexdigest()
                                if content_hash in seen_hashes:
                                    task_summary[msg_id_str].setdefault("dedup_skipped", []).append({
                                        "filename": inner_orig,
                                        "md5": content_hash,
                                        "reason": "ZIP内与直接附件重复",
                                    })
                                    os.remove(p)  # 物理删除重复文件，避免被打包进用户下载的压缩包
                                    continue
                                seen_hashes.add(content_hash)
                                pdf_count += 1
                                f_type, f_text = local_inspect_pdf(p)
                                merged = merge_type_from_name_and_local(inner_orig, f_type)
                                task_summary[msg_id_str]["files"].append({
                                    "local_path": p,
                                    "attachment_original_name": inner_orig,
                                    "identified_type": merged,
                                    "local_pdf_type": f_type,
                                    "text_snapshot": f_text[:3000],
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
                        task_summary[msg_id_str], model=cfg.dashscope_model, mail_subject=subject, mail_date_hint=friendly_date, run_id=run_id
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
                                "receipt_type": clean_receipt_type(audit_res.get("receipt_type", "Not Found")),
                                "travel_time": audit_res.get("travel_time", "Not Found"),
                                "seller": audit_res.get("seller", "Not Found"),
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
            # 收尾 logout 对死连接无意义（长空闲后 163 已掐断，logout 会抛 IMAP4.abort）；
            # 任务已全部成功，绝不能让清理动作把成功结果翻转为前端报错 → 吞掉异常
            try:
                mail.logout()
            except Exception:
                pass
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
        _log_exc(log, "IMAP 协议错误")
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
        _log_exc(log, "主流程未捕获异常")
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
