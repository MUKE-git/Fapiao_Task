"""Exercise the fixed cases' real local PDF path with only the AI HTTP call stubbed.

The existing extract.json files remain controlled interface inputs for matching.
This runner records observations for human review; it does not promote them to
approved expected answers or change the original controlled run.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import types
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
PROJECT = Path(os.environ.get("FIXED_CASE_PROJECT_ROOT", str(ROOT.parents[2]))).resolve()
sys.path.insert(0, str(PROJECT))

# The SDK is replaced before importing production modules. A call can only
# return this offline 401 response; no credential or network access is used.
AI_CALLS: list[dict[str, str]] = []
offline_dashscope = types.ModuleType("dashscope")


class OfflineGeneration:
    @staticmethod
    def call(*_args, **kwargs):
        AI_CALLS.append({"model": str(kwargs.get("model", ""))})
        return SimpleNamespace(status_code=401, code="OFFLINE_FIXTURE",
                               message="simulated AI unavailable")


offline_dashscope.Generation = OfflineGeneration
offline_dashscope.api_key = ""
sys.modules["dashscope"] = offline_dashscope

import ai_client  # noqa: E402
from attachment_processor import CollectedFile, local_inspect_pdf  # noqa: E402
from config import RunConfig  # noqa: E402
from data_model import FileRecord, amount_to_json  # noqa: E402
from fallback_extractor import build_fallback_audit_result  # noqa: E402
from pdf_processor import process_pdf  # noqa: E402
from type_classifier import (  # noqa: E402
    classify_type_from_attachment_name,
    merge_type_from_name_and_local,
    normalize_doc_role,
)


def json_default(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def relative_link(target: Path, source_dir: Path) -> str:
    return os.path.relpath(target, source_dir).replace("\\", "/")


def markdown_value(value) -> str:
    if value is None:
        return "（无）"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=json_default)
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def declared_document(folder: Path, inventory_item: dict) -> dict | None:
    source = json.loads((folder / "extract.json").read_text(encoding="utf-8"))
    for document in source["documents"]:
        if (document.get("source_sha256") == inventory_item["sha256"]
                and document.get("original_name") == inventory_item["name"]):
            return document
    return None


def inspect_pdf(case_id: str, variant: str, seq: int, inventory_item: dict,
                folder: Path, selected_mails: int, temp_root: Path) -> dict:
    path = folder / inventory_item["path"]
    raw = path.read_bytes()
    actual_sha = hashlib.sha256(raw).hexdigest()
    input_ok = (actual_sha == inventory_item["sha256"]
                and len(raw) == inventory_item["bytes"])
    intended = declared_document(folder, inventory_item)

    # These are actual production helper outputs, not values copied from
    # extract.json. A second call through process_pdf below checks integration.
    local_type, text = local_inspect_pdf(str(path))
    readable = bool(text.strip()) and not text.startswith("读取失败:")
    name_hint = classify_type_from_attachment_name(inventory_item["name"])
    merged_type = normalize_doc_role(
        merge_type_from_name_and_local(inventory_item["name"], local_type)
    )
    fallback = build_fallback_audit_result([{
        "identified_type": merged_type, "text_snapshot": text[:3000],
    }])

    meta = FileRecord(file_seq=f"{seq:02d}", file_type="PDF",
                      original_name=inventory_item["name"],
                      internal_name=path.name)
    cfg = RunConfig("imap.fixture.test", "fixture", "no-real-password",
                    output_root=str(temp_root), dashscope_api_key="offline-fixture-only")
    calls_before = len(AI_CALLS)
    processing_error = ""
    document_result = None
    # ai_client writes a debug trace beside its module; redirect that trace
    # into the temporary directory while keeping its real fallback path active.
    with patch.object(ai_client, "__file__", str(temp_root / "ai_client.py")):
        try:
            document_result = process_pdf(
                CollectedFile(meta=meta, path=str(path)), cfg,
                "合成固定用例", "2026-09-29", f"local-{case_id}-{variant}-{seq:02d}",
            )
        except Exception as exc:
            processing_error = f"{type(exc).__name__}: {exc}"
    ai_calls = len(AI_CALLS) - calls_before
    result_dict = asdict(document_result) if document_result else None
    observed_role = document_result.role if document_result else None
    observed_fields = document_result.fields if document_result else {}
    expected_unreadable = bool(intended and
                               "PDF_READ_ERROR" in str(intended.get("error_reason", "")))
    checks = {
        "input_hash_and_size": input_ok,
        "readability_matches_declared_error": readable != expected_unreadable,
        "offline_ai_called_once": ai_calls == 1,
        "real_fallback_reached": (
            observed_fields.get("data_source") == "Fallback_Local_Regex"
            and bool(document_result)
            and "AI_API_OFFLINE_FIXTURE" in document_result.error_reason
        ),
        "pre_read_type_matches_direct_check": meta.pre_read_type == merged_type,
        "pre_read_invoice_no_matches_direct_check": (
            meta.pre_read_invoice_no == fallback.get("invoice_number")
        ),
        "pre_read_amount_matches_direct_check": (
            amount_to_json(meta.pre_read_amount)
            == amount_to_json(fallback.get("total_amount"))
        ),
    }
    intended_role = intended.get("role") if intended else None
    role_difference = (intended_role is not None and observed_role != intended_role)
    comparison = []
    if intended:
        for field, expected_value in intended.get("fields", {}).items():
            if field not in fallback:
                actual_value = "（本地兜底无此字段）"
            else:
                actual_value = fallback[field]
            comparison.append({
                "field": field,
                "controlled_extract_draft": expected_value,
                "local_fallback_observed": actual_value,
                "same": str(expected_value) == str(actual_value),
            })
    return {
        "case_id": case_id,
        "variant": variant,
        "selected_in_original_main_flow": selected_mails > 0,
        "input": {
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "original_name": inventory_item["name"],
            "source": inventory_item["source"],
            "bytes": len(raw),
            "sha256": actual_sha,
        },
        "declared_controlled_extract": {
            "role": intended_role,
            "fields": intended.get("fields", {}) if intended else None,
            "status": "待审核草案；不是本地兜底标准答案",
        },
        "actual_stages": {
            "pdf_pre_read": {"readable": readable, "local_pdf_type": local_type,
                             "text": text},
            "name_merge": {"name_hint": name_hint, "pre_read_type": merged_type},
            "local_regex_fallback": fallback,
            "offline_ai_response": {"status_code": 401,
                                    "code": "OFFLINE_FIXTURE", "calls": ai_calls},
            "process_pdf": {
                "pre_read_type": meta.pre_read_type,
                "pre_read_invoice_no": meta.pre_read_invoice_no,
                "pre_read_amount": amount_to_json(meta.pre_read_amount),
                "document_result": result_dict,
                "raised": processing_error,
            },
        },
        "checks": checks,
        "role_difference_from_controlled_draft": role_difference,
        "field_comparison_for_review": comparison,
    }


def write_snapshot(item: dict, seq: int) -> Path:
    case_id, variant = item["case_id"], item["variant"]
    path = ROOT / "result" / "local_pdf" / case_id / variant / f"{seq:02d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = ROOT / item["input"]["path"]
    review = ROOT / "cases" / case_id / "review.md"
    stages = item["actual_stages"]
    comparison = item["field_comparison_for_review"]
    lines = [
        f"# {case_id} / {variant} / {item['input']['original_name']}",
        "",
        "本页是**实际运行快照**。AI HTTP 响应固定为离线 401；`extract.json` "
        "只作预设接口结果对照，人工标准答案仍待审核。",
        "",
        f"[PDF 输入](<{relative_link(pdf, path.parent)}>) · "
        f"[原用例审核页](<{relative_link(review, path.parent)}>) · "
        f"[总索引](<{relative_link(ROOT / 'result' / 'local_pdf_latest.md', path.parent)}>)",
        "",
        "| 环节 | 输入 | 实际输出 |",
        "| --- | --- | --- |",
        f"| 1 PDF 字节 | `{item['input']['path']}` | "
        f"{item['input']['bytes']} 字节；SHA-256 `{item['input']['sha256']}` |",
        "| 2 本地预读 | PDF 字节 | "
        f"可读：{stages['pdf_pre_read']['readable']}；"
        f"正文粗分：{markdown_value(stages['pdf_pre_read']['local_pdf_type'])} |",
        "| 3 原名合并 | 附件原名＋正文粗分 | "
        f"原名提示：{markdown_value(stages['name_merge']['name_hint'])}；"
        f"预读类型：{markdown_value(stages['name_merge']['pre_read_type'])} |",
        "| 4 本地正则兜底 | 预读类型＋前 3000 字 | "
        f"`data_source={markdown_value(stages['local_regex_fallback'].get('data_source'))}` |",
        "| 5 离线 AI 失败 | 本地预读结果＋提示词 | "
        f"固定 HTTP 401，调用 {stages['offline_ai_response']['calls']} 次 |",
        "| 6 单文件接口 | 以上各环节 | "
        f"最终角色：{markdown_value((stages['process_pdf']['document_result'] or {}).get('role'))}；"
        f"错误：{markdown_value((stages['process_pdf']['document_result'] or {}).get('error_reason'))} |",
        "",
        "## PDF 实际抽取文字",
        "",
        "~~~text",
        stages["pdf_pre_read"]["text"],
        "~~~",
        "",
        "## 本地正则兜底实际输出",
        "",
        "~~~json",
        json.dumps(stages["local_regex_fallback"], ensure_ascii=False,
                   indent=2, default=json_default),
        "~~~",
        "",
        "## `process_pdf` 实际返回",
        "",
        "~~~json",
        json.dumps(stages["process_pdf"], ensure_ascii=False,
                   indent=2, default=json_default),
        "~~~",
        "",
        "## 与受控提取草案对照（供人工审核，差异不直接算兜底失败）",
        "",
        "| 字段 | `extract.json` 草案 | 本地兜底实测 |",
        "| --- | --- | --- |",
    ]
    for row in comparison:
        lines.append("| " + " | ".join(markdown_value(row[key]) for key in (
            "field", "controlled_extract_draft", "local_fallback_observed")) + " |")
    if not comparison:
        lines.append("| （无） | （无） | （无） |")
    lines += ["", "## 检查与待审差异", ""]
    for name, passed in item["checks"].items():
        lines.append(f"- {'通过' if passed else '未通过'}：`{name}`")
    lines.append("- 角色与受控草案" +
                 ("有差异，需审核" if item["role_difference_from_controlled_draft"]
                  else "一致"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    index = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
    items = []
    variant_rows = []
    with tempfile.TemporaryDirectory(prefix="fixed-local-pdf-") as temp:
        temp_root = Path(temp)
        for case in index["cases"]:
            for variant in case["variants"]:
                folder = ROOT / "cases" / case["id"] / "variants" / variant
                inventory = json.loads((folder / "input_inventory.json").read_text(
                    encoding="utf-8"))
                expected = json.loads((folder / "expected.json").read_text(
                    encoding="utf-8"))["expected"]
                pdf_inputs = [entry for entry in inventory
                              if Path(entry["path"]).suffix.lower() == ".pdf"]
                variant_rows.append((case["id"], variant, len(pdf_inputs)))
                for seq, entry in enumerate(pdf_inputs, 1):
                    item = inspect_pdf(case["id"], variant, seq, entry, folder,
                                       expected.get("selected_mails", 0), temp_root)
                    snapshot = write_snapshot(item, seq)
                    item["snapshot"] = str(snapshot.relative_to(ROOT)).replace("\\", "/")
                    items.append(item)

    summary = {
        "case_count": len(index["cases"]),
        "variant_count": len(variant_rows),
        "pdf_count": len(items),
        "readable_pdf_count": sum(x["actual_stages"]["pdf_pre_read"]["readable"]
                                  for x in items),
        "all_checks_pass_count": sum(all(x["checks"].values()) for x in items),
        "checks_need_attention_count": sum(not all(x["checks"].values()) for x in items),
        "role_difference_count": sum(x["role_difference_from_controlled_draft"]
                                     for x in items),
        "field_comparison_difference_file_count": sum(
            any(not row["same"] for row in x["field_comparison_for_review"])
            for x in items),
    }
    output = {
        "type": "真实本地 PDF 阶段实测；AI HTTP 401 为离线替身；非人工标准答案",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "items": items,
    }
    result_dir = ROOT / "result"
    result_dir.mkdir(exist_ok=True)
    (result_dir / "local_pdf_latest.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8")

    lines = [
        "# 固定用例：真实本地 PDF 阶段实测",
        "",
        "实际运行 `local_inspect_pdf`、原名合并分类、正则兜底及 `process_pdf`；"
        "只把 AI HTTP 响应模拟为 401，不连接邮箱或 AI。",
        "本报告是**实际输出**；`extract.json` 是受控接口草案，人工标准答案仍待审核。",
        "PDF 组件逐份运行，包含主流程因主题不匹配而不会处理的附件。",
        "",
        f"{summary['case_count']} 条用例、{summary['variant_count']} 个变体、"
        f"{summary['pdf_count']} 份 PDF；可读 {summary['readable_pdf_count']} 份；"
        f"基础检查全部通过 {summary['all_checks_pass_count']} 份；"
        f"需关注 {summary['checks_need_attention_count']} 份；"
        f"与受控角色草案不同 {summary['role_difference_count']} 份；"
        f"字段对照有差异 {summary['field_comparison_difference_file_count']} 份"
        "（两者属于不同处理层，差异不直接算失败）。",
        "",
        "## 用例索引（保持原顺序）",
        "",
        "| 用例 | 变体 | PDF 数 | 原审核页 |",
        "| --- | --- | ---: | --- |",
    ]
    for case_id, variant, count in variant_rows:
        review = f"../cases/{case_id}/review.md"
        lines.append(f"| {case_id} | {variant} | {count} | "
                     f"[打开](<{review}>) |")
    lines += [
        "",
        "## 逐文件快照",
        "",
        "| 用例 / 变体 | 输入 PDF 与快照 | 正文粗分 → 原名合并 | "
        "兜底金额 | 基础检查 | 角色草案对照 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in items:
        stages = item["actual_stages"]
        pdf_link = relative_link(ROOT / item["input"]["path"], result_dir)
        snapshot_link = relative_link(ROOT / item["snapshot"], result_dir)
        status = "通过" if all(item["checks"].values()) else "需关注"
        role_status = ("有差异，待审" if item["role_difference_from_controlled_draft"]
                       else "一致")
        lines.append(
            f"| {item['case_id']} / {item['variant']} | "
            f"[PDF](<{pdf_link}>) · [快照](<{snapshot_link}>) | "
            f"{markdown_value(stages['pdf_pre_read']['local_pdf_type'])} → "
            f"{markdown_value(stages['name_merge']['pre_read_type'])} | "
            f"{markdown_value(stages['local_regex_fallback'].get('total_amount'))} | "
            f"{status} | {role_status} |"
        )
    lines += [
        "",
        "基础检查只确认输入完整、预期可读性、真实本地链路运行及离线兜底到达。"
        "与 `extract.json` 的字段差异用于审核，不能直接判为兜底失败。",
        "",
    ]
    (result_dir / "local_pdf_latest.md").write_text(
        "\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
