"""Run approved C07 synthetic MIME/ZIP input through the online T2 pipeline.

The IMAP server alone is a fixture. The approved expected.json supplies the
acceptance oracle; extract.json is recorded only as a supplementary observation.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import tomllib
from datetime import datetime
from email import message_from_bytes
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

import dashscope
from dashscope import Generation
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import ai_client  # noqa: E402
import pdf_processor  # noqa: E402
from config import RunConfig  # noqa: E402
from pipeline import run_pipeline  # noqa: E402

FIXTURE = ROOT / "03 tests/owner test/固定用例库/cases/C07/variants/v1"
RUN = ROOT / "03 tests/T2/result" / f"C07_online_{datetime.now():%Y%m%d_%H%M%S}"
RUN.mkdir(parents=True, exist_ok=False)
tempfile.tempdir = str(RUN.resolve())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FixtureImap:
    def __init__(self, uid: str, raw: bytes):
        self.uid_text, self.raw, self.events = uid, raw, []

    def login(self, *_):
        self.events.append("LOGIN")
        return "OK", []

    def select(self, *_):
        self.events.append("SELECT")
        return "OK", [b"1"]

    def status(self, *_):
        self.events.append("STATUS")
        return "OK", [b"INBOX (MESSAGES 1 UNSEEN 1)"]

    def uid(self, command, *args):
        if command == "SEARCH":
            self.events.append("SEARCH")
            return "OK", [self.uid_text.encode()]
        assert command == "FETCH"
        uid = args[0].decode() if isinstance(args[0], bytes) else str(args[0])
        assert uid == self.uid_text
        query = str(args[1])
        self.events.append("FETCH HEADER" if "HEADER.FIELDS" in query else "FETCH BODY")
        if "HEADER.FIELDS" in query:
            source = message_from_bytes(self.raw)
            header = EmailMessage()
            header["Subject"] = source["Subject"]
            header["Date"] = source["Date"]
            return "OK", [(b"header", header.as_bytes())]
        return "OK", [(b"body", self.raw)]

    def logout(self):
        self.events.append("LOGOUT")
        return "BYE", []


def main() -> int:
    secrets = tomllib.loads((ROOT / ".streamlit/secrets.toml").read_text(encoding="utf-8-sig"))
    api_key = str(secrets.get("DASHSCOPE_API_KEY") or "").strip()
    model = str(secrets.get("DASHSCOPE_MODEL") or "qwen-plus").strip()
    if not api_key:
        raise RuntimeError("DashScope key unavailable")
    dashscope.api_key = api_key
    approved = json.loads((FIXTURE / "expected.json").read_text(encoding="utf-8"))["expected"]
    state = json.loads((FIXTURE / "imap_state.json").read_text(encoding="utf-8"))
    inventory = json.loads((FIXTURE / "input_inventory.json").read_text(encoding="utf-8"))
    imap = FixtureImap(str(state["uid"]), (FIXTURE / "00_raw.eml").read_bytes())
    cfg = RunConfig("imap.fixture.test", "fixture", "unused", output_folder_name="output",
                    output_root=str(RUN), dashscope_api_key=api_key, dashscope_model=model)
    calls, documents = [], []
    original_call, original_pdf = Generation.call, pdf_processor.process_pdf

    def capped_call(*args, **kwargs):
        if len(calls) >= 2:
            raise RuntimeError("Online request cap reached: 2")
        event = {"model": str(kwargs.get("model", "")), "status": None}
        calls.append(event)
        response = original_call(*args, **kwargs)
        event["status"] = getattr(response, "status_code", None)
        return response

    def observed_pdf(file, *args, **kwargs):
        result = original_pdf(file, *args, **kwargs)
        documents.append({"file_seq": file.meta.file_seq, "name": file.meta.original_name,
                          "role": result.role, "fields": {k: str(v) for k, v in result.fields.items()
                                                          if k in ("invoice_date", "invoice_number", "total_amount",
                                                                   "invoice_type", "receipt_type", "seller", "data_source")},
                          "error_reason": result.error_reason})
        return result

    with patch("pipeline.imaplib.IMAP4_SSL", return_value=imap), \
         patch.object(Generation, "call", side_effect=capped_call), \
         patch("pipeline.process_pdf", side_effect=observed_pdf), \
         patch.object(ai_client, "__file__", str(RUN / "ai_client.py")):
        mismatches, stats = run_pipeline(cfg)

    diffs = []

    def check(path, actual, expected):
        if actual != expected:
            diffs.append({"path": path, "expected": expected, "actual": actual})

    for item in inventory:
        check(f"input.{item['path']}.sha256", sha(FIXTURE / item["path"]), item["sha256"])
    check("online.statuses", [call["status"] for call in calls], [200, 200])
    check("pdf.names", [d["name"] for d in documents], ["发票甲.pdf", "发票乙.pdf"])
    check("pdf.roles", [d["role"] for d in documents], ["Invoice", "Invoice"])
    for document in documents:
        check(f"pdf.{document['name']}.error_reason", document["error_reason"], "")
        check(f"pdf.{document['name']}.fallback",
              document["fields"].get("data_source") == "Fallback_Local_Regex", False)
    check("pipeline.mismatches", mismatches, [])
    for key, wanted in (("unseen_total", approved["unseen_total"]),
                        ("invoice_keyword_mails", approved["selected_mails"]),
                        ("imap_error", None), ("run_error", None), ("excel_error", None)):
        check(f"stats.{key}", stats.get(key), wanted)
    data = json.loads(Path(cfg.debug_file).read_text(encoding="utf-8"))
    check("JSON.mail_count", len(data), 1)
    mail = data[0] if data else {}
    check("JSON.mail_uid", mail.get("mail_uid"), str(state["uid"]))
    check("JSON.file_count", len(mail.get("files", [])), approved["file_count"])
    check("JSON.record_count", len(mail.get("records", [])), approved["record_count"])
    check("JSON.itinerary_count", len(mail.get("itineraries", [])), approved["itinerary_count"])
    check("JSON.duplicate_skipped", len(mail.get("dedup_skipped", [])), approved["duplicate_skipped"])
    check("JSON.error_reason", mail.get("error_reason"), "")
    for file in mail.get("files", []):
        actual = Path(cfg.extract_dir) / file["internal_name"]
        expected_hashes = {entry["sha256"] for entry in inventory if entry["name"] == file["original_name"]}
        check(f"file.{file['file_seq']}.hash_in_input", sha(actual) in expected_hashes if actual.is_file() else False, True)
    book = load_workbook(cfg.summary_excel_file, data_only=True)
    sheet = book.active
    check("Excel.row_count", sheet.max_row - 1, approved["excel_rows"])
    check("Excel.amounts", sorted(str(sheet.cell(row, 3).value) for row in range(2, sheet.max_row + 1)),
          ["190.69", "70.91"])
    book.close()
    source_names = ("pipeline.py", "pdf_processor.py", "ai_client.py", "attachment_processor.py",
                    "dedup_arbitrator.py", "field_validator.py", "excel_writer.py",
                    "03 tests/T2/scripts/run_c07_online_chain.py")
    report = {"case": "C07/v1", "scope": "approved synthetic EML/ZIP; IMAP fixture; real PDF, online AI and T2 chain",
              "model": model, "online_calls": calls, "imap_events": imap.events,
              "source_sha256": {path: sha(ROOT / path) for path in source_names},
              "input_sha256": {item["path"]: item["sha256"] for item in inventory},
              "output_sha256": {name: sha(path) for name, path in
                                (("task_debug.json", Path(cfg.debug_file)),
                                 ("summary.xlsx", Path(cfg.summary_excel_file)))},
              "pdf_documents": documents, "actual_json": data, "stats": stats,
              "passed": not diffs, "differences": diffs}
    result = RUN / "report.json"
    result.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(result), "passed": report["passed"],
                      "online_statuses": [call["status"] for call in calls], "differences": diffs},
                     ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
