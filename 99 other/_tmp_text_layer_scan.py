# -*- coding: utf-8 -*-
"""临时分析脚本：扫描 task_debug.json 中的 text_snapshot，输出文本层规律摘要。"""
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PATH = r"C:\Users\lenovo\Desktop\T1-7\task_debug.json"

with open(PATH, encoding="utf-8") as f:
    data = json.load(f)

for mid, item in data.items():
    files = item.get("files", [])
    print("=" * 90)
    subj = (item.get("subject") or "")[:60]
    print("MSG %s | %s | files=%d" % (mid, subj, len(files)))
    audit = item.get("audit_result") or {}
    print("  audit: amount=%s no=%s date=%s rec_type=%s seller=%s" % (
        audit.get("total_amount"), audit.get("invoice_number"),
        audit.get("invoice_date"), audit.get("receipt_type"),
        (audit.get("seller") or "")[:20],
    ))
    for i, f in enumerate(files):
        ts = f.get("text_snapshot", "")
        print("-" * 90)
        print("  [%d] %s | local=%s | merged=%s | len=%d" % (
            i, f.get("attachment_original_name", ""), f.get("local_pdf_type"),
            f.get("identified_type"), len(ts),
        ))
        print("  HEAD400: %r" % ts[:400])
