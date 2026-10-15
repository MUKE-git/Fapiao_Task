"""T2-5 controlled source-chain exploration; not an approved fixed case."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
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

BASE = ROOT / "03 tests" / "T2-5" / "result" / ("exploratory_" + datetime.now().strftime("%Y%m%d_%H%M%S"))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeImap:
    def __init__(self, raws): self.raws = raws
    def login(self, *_): return "OK", []
    def select(self, *_): return "OK", []
    def status(self, *_): return "OK", [b"INBOX (MESSAGES 2 UNSEEN 2)"]
    def logout(self): return "BYE", []
    def uid(self, command, *args):
        if command == "SEARCH": return "OK", [b"701 702"]
        uid = args[0].decode() if isinstance(args[0], bytes) else str(args[0])
        return "OK", [(b"body", self.raws[uid])]


def message(attachments):
    msg = EmailMessage()
    msg["Subject"] = "电子发票"
    msg["Date"] = "Thu, 15 Oct 2026 09:00:00 +0800"
    msg.set_content("T2-5 source exploration")
    for name, data, subtype in attachments:
        msg.add_attachment(data, maintype="application", subtype=subtype, filename=name)
    return msg.as_bytes()


def main():
    BASE.mkdir(parents=True, exist_ok=True)
    invoice_one = b"synthetic-invoice-701"
    trip = b"synthetic-itinerary-701"
    invoice_two = b"synthetic-invoice-702"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("资料/行程单.pdf", trip)
        archive.writestr("重复发票.pdf", invoice_one)
    raws = {
        "701": message([("甲发票.pdf", invoice_one, "pdf"), ("附件.zip", buffer.getvalue(), "zip")]),
        "702": message([("乙发票.pdf", invoice_two, "pdf")]),
    }
    for uid, raw in raws.items():
        (BASE / f"input_{uid}.eml").write_bytes(raw)
    cfg = RunConfig("imap.test", "user", "unused", output_folder_name="output",
                    output_root=str(BASE), dashscope_api_key="controlled-key")

    def extract(file, *_):
        if file.meta.original_name == "资料/行程单.pdf":
            role = "Itinerary"
            fields = {"match_amount": "900.00", "travel_time": "2026-10-15 09:05",
                      "origin_dest": "杭州→上海"}
        else:
            role = "Invoice"
            fields = {"invoice_date": "2026-10-15",
                      "invoice_number": "26327000000775232739" if file.meta.original_name == "甲发票.pdf" else "26327000000775232740",
                      "total_amount": "100.00" if file.meta.original_name == "甲发票.pdf" else "200.00",
                      "invoice_type": "", "receipt_type": "客运服务费" if file.meta.original_name == "甲发票.pdf" else "办公用品",
                      "seller": "甲公司"}
        file.meta.pre_read_type = role
        return DocumentResult(file.meta.file_seq, role, fields, [file.meta.file_seq])

    with patch("pipeline.imaplib.IMAP4_SSL", return_value=FakeImap(raws)), \
         patch("pipeline.process_pdf", side_effect=extract):
        mismatches, stats = run_pipeline(cfg)
    saved = json.loads(Path(cfg.debug_file).read_text(encoding="utf-8"))
    workbook = load_workbook(cfg.summary_excel_file, read_only=True)
    rows = list(workbook.active.values)
    workbook.close()
    differences = []

    def check(label, actual, expected):
        if actual != expected:
            differences.append({"path": label, "actual": actual, "expected": expected})

    check("mismatches", mismatches, [])
    check("imap_error", stats["imap_error"], None)
    check("mail_uids", [m["mail_uid"] for m in saved], ["701", "702"])
    check("mail_ids", [m["mail_id"] for m in saved], ["Msg1", "Msg2"])
    check("file_sequences", [[f["file_seq"] for f in m["files"]] for m in saved], [["01", "02"], ["01"]])
    check("original_names", [[f["original_name"] for f in m["files"]] for m in saved],
          [["甲发票.pdf", "资料/行程单.pdf"], ["乙发票.pdf"]])
    check("dedup_skip", [d["filename"] for d in saved[0]["dedup_skipped"]], ["重复发票.pdf"])
    check("record_sources", [[r["source"]["source_file_seq"] for r in m["records"]] for m in saved],
          [[ ["01"] ], [ ["01"] ]])
    check("itinerary_source", saved[0]["itineraries"][0]["record_id"], "Msg1-02")
    check("matched_itinerary", saved[0]["records"][0].get("matched_itinerary"),
          {"file_seq": "02", "match_amount": "900.00"})
    check("statuses", [m["records"][0]["status"] for m in saved], ["complete", "complete"])
    check("excel_row_count", len(rows) - 1, 2)
    check("excel_invoice_numbers", [r[1] for r in rows[1:]],
          ["26327000000775232739", "26327000000775232740"])
    check("excel_travel", [r[5] for r in rows[1:]], ["2026-10-15 09:05", None])
    check("excel_routes", [r[7] for r in rows[1:]], ["杭州→上海", None])
    seen = set()
    expected_bytes = [invoice_one, trip, invoice_two]
    for m, contents in zip(saved, (expected_bytes[:2], expected_bytes[2:])):
        for f, content in zip(m["files"], contents):
            internal = f["internal_name"]
            if internal in seen or len(internal) > 120:
                differences.append({"path": "internal_name", "actual": internal,
                                    "expected": "unique and <=120 chars"})
            seen.add(internal)
            actual_path = Path(cfg.extract_dir) / internal
            check(f"file:{internal}:sha256", digest(actual_path.read_bytes()), digest(content))
            check(f"file:{internal}:exported_name", f["exported_name"], "")
    report = {"scope": "exploratory, not fixed-case acceptance", "passed": not differences,
              "differences": differences, "source_sha256": {
                  name: digest((ROOT / name).read_bytes()) for name in
                  ("attachment_processor.py", "pipeline.py", "field_validator.py",
                   "03 tests/T2-5/scripts/explore_trace.py")},
              "input_sha256": {uid: digest(raw) for uid, raw in raws.items()},
              "json_sha256": digest(Path(cfg.debug_file).read_bytes()),
              "excel_sha256": digest(Path(cfg.summary_excel_file).read_bytes())}
    (BASE / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(BASE / "report.json"), "passed": report["passed"],
                      "differences": differences}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
