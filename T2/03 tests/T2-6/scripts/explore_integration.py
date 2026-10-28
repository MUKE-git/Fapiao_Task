"""Exploratory reproductions only; not approved fixed cases or acceptance counts."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from config import RunConfig  # noqa: E402
from pipeline import run_pipeline  # noqa: E402
from processor_contracts import DocumentResult  # noqa: E402

BASE = ROOT / "03 tests" / "T2-6" / "result" / ("exploratory_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
NUMBER = "26327000000775232739"


class FakeImap:
    def __init__(self, raw): self.raw = raw
    def login(self, *_): return "OK", []
    def select(self, *_): return "OK", []
    def status(self, *_): return "OK", [b"INBOX (MESSAGES 1 UNSEEN 1)"]
    def logout(self): return "BYE", []
    def uid(self, command, *_):
        return ("OK", [b"701"]) if command == "SEARCH" else ("OK", [(b"body", self.raw)])


def run(label: str, changed: dict) -> dict:
    output = BASE / label
    output.mkdir(parents=True, exist_ok=True)
    message = EmailMessage()
    message["Subject"] = "电子发票"
    message["Date"] = "Thu, 15 Oct 2026 09:00:00 +0800"
    message.set_content("T2-6 exploratory synthetic input")
    message.add_attachment(b"controlled invoice bytes", maintype="application", subtype="pdf", filename="发票.pdf")
    raw = message.as_bytes()
    (output / "input.eml").write_bytes(raw)
    values = {"invoice_date": "2026-10-15", "invoice_number": NUMBER,
              "total_amount": "100.00", "invoice_type": "", "receipt_type": "办公用品", "seller": "甲公司"}
    values.update(changed)
    (output / "controlled_fields.json").write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    cfg = RunConfig("imap.test", "user", "unused", output_folder_name="output",
                    output_root=str(output), dashscope_api_key="controlled-key")

    def extract(file, *_):
        return DocumentResult(file_seq=file.meta.file_seq, role="Invoice", fields=values,
                              source_file_seq=[file.meta.file_seq])

    with patch("pipeline.imaplib.IMAP4_SSL", return_value=FakeImap(raw)), \
         patch("pipeline.process_pdf", side_effect=extract):
        mismatches, stats = run_pipeline(cfg)
    saved = json.loads(Path(cfg.debug_file).read_text(encoding="utf-8"))[0]
    workbook = load_workbook(cfg.summary_excel_file, read_only=True)
    row = list(workbook.active.values)[1]
    workbook.close()
    return {"input": changed, "status": saved["records"][0]["status"],
            "fields": saved["records"][0]["fields"], "error_reason": saved["error_reason"],
            "alerts": stats["not_found_alerts"], "excel_row": row,
            "mismatches": mismatches, "imap_error": stats["imap_error"],
            "input_sha256": hashlib.sha256(raw).hexdigest()}


def main() -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    results = {"numeric_seller": run("numeric_seller", {"seller": 123}),
               "numeric_receipt_type": run("numeric_receipt_type", {"receipt_type": 123}),
               "malformed_amount": run("malformed_amount", {"total_amount": "1,2,3"})}
    source = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
              ("pipeline.py", "field_validator.py", "data_model.py", "stage_ports.py",
               "03 tests/T2-6/scripts/explore_integration.py")}
    report = {"scope": "exploratory, unapproved case candidates", "source_sha256": source,
              "results": results}
    path = BASE / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(path), "results": {k: {"status": v["status"],
                "fields": v["fields"], "error_reason": v["error_reason"],
                "excel_row": v["excel_row"]} for k, v in results.items()}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
