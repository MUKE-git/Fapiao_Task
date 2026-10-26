"""Read-only coverage audit of the 18 frozen mandatory cases.

It never edits, extends or derives an expected answer from observed output.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "owner test" / "固定用例库"
JSON_KEYS = {"invoice_date", "invoice_number", "total_amount",
             "invoice_type", "receipt_type", "seller"}
EXCEL_KEYS = JSON_KEYS | {"travel_time", "origin_dest"}


def audit() -> dict:
    index = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
    gaps = []
    invoices = 0
    complete_json = 0
    complete_excel = 0
    for case in index["cases"]:
        for variant in case["variants"]:
            folder = ROOT / "cases" / case["id"] / "variants" / variant
            expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))["expected"]
            docs = json.loads((folder / "extract.json").read_text(encoding="utf-8"))["documents"]
            for document in docs:
                if document["role"] != "Invoice":
                    continue
                invoices += 1
                name = document["original_name"]
                json_checked = set(expected.get("field_checks", {}).get(name, {}))
                excel_checked = set(expected.get("excel_field_checks", {}).get(name, {}))
                missing_json = sorted(JSON_KEYS - json_checked)
                missing_excel = sorted(EXCEL_KEYS - excel_checked)
                complete_json += not missing_json
                complete_excel += not missing_excel
                if missing_json or missing_excel:
                    gaps.append({"case_id": case["id"], "variant": variant,
                                 "invoice": name, "json_missing": missing_json,
                                 "excel_missing": missing_excel})
    return {"frozen_case_count": len(index["cases"]),
            "frozen_variant_count": sum(len(case["variants"]) for case in index["cases"]),
            "invoice_document_count": invoices,
            "complete_json_invoice_checks": complete_json,
            "complete_excel_invoice_checks": complete_excel,
            "invoice_field_gaps": gaps,
            "note": "只读核对已审核 expected.json 的逐字段检查覆盖；缺项须另编待审核预期，不从实际结果补。"}


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2))
