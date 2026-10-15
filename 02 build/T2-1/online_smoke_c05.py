"""手动运行的 C05 百炼在线冒烟；最多两次真实请求，不参与 unittest。"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import dashscope
from dashscope import Generation

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from attachment_processor import CollectedFile
from config import RunConfig, ensure_output_dirs
from data_model import FileRecord
from pdf_processor import process_pdf


CASE = ROOT / "03 tests/owner test/固定用例库/cases/C05/variants/v1"
RESULT = Path(__file__).with_name("C05_真实AI在线冒烟_20261015.json")
SECRETS = ROOT / ".streamlit/secrets.toml"


def main() -> None:
    secrets = tomllib.loads(SECRETS.read_text(encoding="utf-8-sig"))
    api_key = str(secrets.get("DASHSCOPE_API_KEY") or "").strip()
    model = str(secrets.get("DASHSCOPE_MODEL") or "qwen-plus").strip()
    if not api_key:
        raise RuntimeError("项目配置没有百炼密钥；未发起请求")
    dashscope.api_key = api_key
    expected = json.loads((CASE / "expected.json").read_text(encoding="utf-8"))["expected"]
    invoice_expected = expected["excel_field_checks"]["发票.pdf"]
    sources = [
        ("01", "发票.pdf", "Invoice", {
            key: invoice_expected[key] for key in
            ("invoice_date", "invoice_number", "total_amount", "invoice_type", "receipt_type", "seller")
        }),
        ("02", "行程单.pdf", "Itinerary", {
            "match_amount": invoice_expected["total_amount"],
            "travel_time": invoice_expected["travel_time"],
            "origin_dest": invoice_expected["origin_dest"],
        }),
    ]
    report = {"case": "C05/v1", "sample_kind": "已审核合成文字型 PDF",
              "model": model, "request_cap": 2, "actual_requests": 0,
              "api_statuses": [], "documents": []}
    original_call = Generation.call

    def capped_call(*args, **kwargs):
        if report["actual_requests"] >= 2:
            raise RuntimeError("在线冒烟请求数已达到 2 次上限")
        report["actual_requests"] += 1
        response = original_call(*args, **kwargs)
        report["api_statuses"].append(getattr(response, "status_code", None))
        return response

    temp_root = Path(__file__).with_name(".online_smoke_temp")
    temp_root.mkdir(exist_ok=True)
    tempfile.tempdir = str(temp_root.resolve())
    run_id = "C05_online_" + datetime.now().strftime("%Y%m%d%H%M%S")
    try:
        with tempfile.TemporaryDirectory(dir=temp_root) as output_root:
            cfg = RunConfig("", "", "", output_root=output_root,
                            dashscope_api_key=api_key, dashscope_model=model)
            ensure_output_dirs(cfg)
            with patch.object(Generation, "call", side_effect=capped_call):
                for seq, name, role, targets in sources:
                    original = CASE / "attachments/direct" / name
                    internal = f"Msg1_{seq}_{name}"
                    local = Path(cfg.extract_dir) / internal
                    shutil.copyfile(original, local)
                    file = CollectedFile(FileRecord(seq, "PDF", name, internal_name=internal),
                                         str(local))
                    result = process_pdf(file, cfg, "电子发票及行程单 C05", "2026-09-28", run_id)
                    actual = {key: str(result.fields.get(key, "Not Found")) for key in targets}
                    checks = {}
                    for key, wanted in targets.items():
                        got = actual[key]
                        checks[key] = (got.replace(" ", "") == wanted.replace(" ", "")
                                       if key == "origin_dest" else got == wanted)
                    report["documents"].append({
                        "file": name, "role_expected": role, "role_actual": result.role,
                        "pre_read_type": file.meta.pre_read_type,
                        "api_success": bool(report["api_statuses"] and report["api_statuses"][-1] == 200),
                        "fallback_used": result.fields.get("data_source") == "Fallback_Local_Regex",
                        "error_reason": result.error_reason,
                        "expected": targets, "actual": actual, "field_checks": checks,
                    })
                    RESULT.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
                    if not report["documents"][-1]["api_success"]:
                        break
    finally:
        # 仅清理本次临时副本；审核报告保留在 T2-1。
        if temp_root.exists() and not any(temp_root.iterdir()):
            temp_root.rmdir()
        log_file = ROOT / "04 logs" / f"debug-{run_id}.log"
        if log_file.is_file():
            log_file.unlink()
    print(json.dumps({"report": str(RESULT), "model": model,
                      "requests": report["actual_requests"],
                      "statuses": report["api_statuses"],
                      "checks": [item["field_checks"] for item in report["documents"]]},
                     ensure_ascii=True))


if __name__ == "__main__":
    if sys.argv[1:] != ["--run-once"]:
        raise SystemExit("本脚本会产生最多 2 次真实付费请求；仅手动传入 --run-once 执行")
    main()
