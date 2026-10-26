"""Compile an independent, review-pending field answer from frozen inputs.

This reads only approved case metadata, fixed extract.json, and the original
expected.json. It never reads program JSON, Excel, or a prior test result.
Run only while drafting; the checked-in JSON is the review artifact.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "owner test" / "固定用例库"
HERE = Path(__file__).resolve().parent
INVOICE_KEYS = ("invoice_date", "invoice_number", "total_amount",
                "invoice_type", "receipt_type", "seller")
TRIP_KEYS = ("match_amount", "travel_time", "origin_dest")
COLUMNS = (
    ("发票日期", "invoice_date"), ("发票号码", "invoice_number"),
    ("报销金额", "total_amount"), ("发票类型", "invoice_type"),
    ("票面类型", "receipt_type"), ("出行时间", "travel_time"),
    ("销售方", "seller"), ("出发地/目的地", "origin_dest"),
)


def compile_draft() -> dict:
    index = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
    assert len(index["cases"]) == 18
    variants = []
    for case in index["cases"]:
        for variant in case["variants"]:
            folder = ROOT / "cases" / case["id"] / "variants" / variant
            fixed = json.loads((folder / "extract.json").read_text(encoding="utf-8"))
            original = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
            assert original["case_id"] == case["id"] and original["variant"] == variant
            assert original["type"] == "已审核人工标准答案"
            approved = original["expected"]
            docs = fixed["documents"]
            invoices = [doc for doc in docs if doc["role"] == "Invoice"]
            trips = [doc for doc in docs if doc["role"] == "Itinerary"]
            assert len(invoices) == approved["record_count"]
            assert len(trips) == approved["itinerary_count"]
            trips_by_name = {doc["original_name"]: doc for doc in trips}
            invoice_names = {doc["original_name"] for doc in invoices}
            assert set(approved["matched_by_invoice"]) <= invoice_names
            assert set(approved["matched_by_invoice"].values()) <= set(trips_by_name)
            invoice_expected = []
            excel_rows = []
            for doc in invoices:
                name = doc["original_name"]
                fields = {key: doc["fields"][key] for key in INVOICE_KEYS}
                target_name = approved["matched_by_invoice"].get(name)
                target = trips_by_name.get(target_name)
                expanded_fields = {**fields, **({
                    "travel_time": target["fields"]["travel_time"],
                    "origin_dest": target["fields"]["origin_dest"],
                } if target else {})}
                for key, wanted in approved.get("field_checks", {}).get(name, {}).items():
                    assert expanded_fields[key] == wanted, (case["id"], variant, name, key)
                invoice_expected.append({
                    "original_name": name,
                    "fields": expanded_fields,
                    "matched_itinerary": ({
                        "original_name": target_name,
                        "match_amount": target["fields"]["match_amount"],
                    } if target else None),
                })
                # The approved zero-row case has all three primary keys missing.
                eligible = any(fields[key] != "Not Found" for key in
                               ("invoice_date", "invoice_number", "total_amount"))
                if eligible:
                    row = {**fields,
                           "travel_time": target["fields"]["travel_time"] if target else "",
                           "origin_dest": target["fields"]["origin_dest"] if target else ""}
                    for key, wanted in approved.get("excel_field_checks", {}).get(name, {}).items():
                        assert row[key] == wanted, (case["id"], variant, name, key)
                    excel_rows.append({"invoice_original_name": name,
                                       "fields": {key: row[key] for _, key in COLUMNS}})
            assert len(excel_rows) == approved["excel_rows"], (case["id"], variant)
            variants.append({
                "case_id": case["id"], "variant": variant,
                "source": {
                    "fixed_extract": str((folder / "extract.json").relative_to(ROOT)).replace("\\", "/"),
                    "approved_expected": str((folder / "expected.json").relative_to(ROOT)).replace("\\", "/"),
                },
                "counts": {key: approved[key] for key in
                           ("selected_mails", "file_count", "record_count",
                            "itinerary_count", "excel_rows")},
                "json_invoice_records": invoice_expected,
                "json_itinerary_records": [
                    {"original_name": doc["original_name"],
                     "fields": {key: doc["fields"][key] for key in TRIP_KEYS}}
                    for doc in trips],
                "excel_rows": excel_rows,
            })
    assert len(variants) == 26
    return {
        "author": "测试工程师制作、待用户审核",
        "review_status": "测试工程师制作、待用户审核",
        "purpose": "已审核18条/26变体之外的逐字段扩展草案；不覆盖原预期",
        "basis": "固定 extract.json 提取字段 + 原 expected.json 已审核匹配/行数/字段结论 + 已确认的八列映射与空值规则；程序实际输出未参与编制",
        "schema": {
            "json_invoice_fields": list(INVOICE_KEYS) + ["travel_time", "origin_dest"],
            "json_itinerary_fields": list(TRIP_KEYS),
            "excel_columns": [[title, key] for title, key in COLUMNS],
            "formats": {
                "invoice_date": "YYYY-MM-DD 或 Not Found",
                "invoice_number": "20位数字字符串或 Not Found",
                "total_amount_json": "十进制字符串或 Not Found",
                "total_amount_excel": "数字单元格且显示格式 0.00；缺失为 Not Found 字符串",
                "invoice_type": "文本或空字符串",
                "receipt_type": "文本或 Not Found",
                "seller": "文本或 Not Found",
                "match_amount": "十进制字符串或 Not Found",
                "travel_time": "JSON 匹配时 YYYY-MM-DD HH:mm，否则字段不存在；Excel 未匹配时空单元格",
                "origin_dest": "JSON 匹配时文本，否则字段不存在；Excel 未匹配时空单元格",
            },
            "empty_values": {"json_unmatched_travel_fields": "absent",
                             "json_unmatched_itinerary": "absent",
                             "excel_unmatched_travel_fields": "",
                             "required_missing": "Not Found"},
            "excel_amount_number_format": "0.00",
            "row_order": "入表发票按邮件及附件来源顺序；行程单不独立入表",
        },
        "variants": variants,
    }


if __name__ == "__main__":
    output = HERE / "frozen_18_full_fields_draft.json"
    if output.exists():
        raise SystemExit(f"Refusing to overwrite review artifact: {output}")
    output.write_text(json.dumps(compile_draft(), ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(output)
