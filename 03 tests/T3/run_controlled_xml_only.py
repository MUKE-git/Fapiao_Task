"""T3 draft controlled-chain check; engineer-made input, pending user review.

Uses the existing fixed-case FakeImap entry. Only the XML processor result is
replaced by a value written below before execution. It does not test true XML
parsing, a live mailbox or an external model. JSON and Excel are compared
against fixtures/xml_only_expected_draft.json, never against each other.
"""
from __future__ import annotations

import json
import sys
import tempfile
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
FIXED = PROJECT / "03 tests" / "owner test" / "固定用例库"
sys.path.insert(0, str(FIXED))
sys.path.insert(0, str(PROJECT))

import run_fixed_cases as fixed  # noqa: E402, supplies offline SDK and FakeImap
from compare_full_fields import compare_files  # noqa: E402
from config import RunConfig  # noqa: E402
from dedup_arbitrator import arbitrate_records  # noqa: E402
from field_validator import validate_records  # noqa: E402
from itinerary_matcher import match_itineraries  # noqa: E402
from pipeline import run_pipeline  # noqa: E402
from processor_contracts import DocumentResult  # noqa: E402
from stage_ports import StagePorts  # noqa: E402
from voucher_matcher import combine_vouchers  # noqa: E402


FIELDS = {
    "invoice_date": "2026-10-20", "invoice_number": "26330000000000000001",
    "total_amount": "121.74", "invoice_type": "电子发票（普通发票）",
    "receipt_type": "办公用品", "seller": "测试销售方",
}


def main() -> int:
    source = HERE / "fixtures" / "xml_only_synthetic.xml"
    message = EmailMessage()
    message["Subject"] = "测试电子发票 XML"
    message["Date"] = "Mon, 26 Oct 2026 09:00:00 +0800"
    message.set_content("合成 XML 附件；无下载链接。")
    message.add_attachment(source.read_bytes(), maintype="application", subtype="xml",
                           filename=source.name)
    fake = fixed.FakeImap(["9101"], {"9101": message.as_bytes()}, 1)
    calls: list[dict] = []

    def fixed_xml(item, _cfg, _subject, _mail_date, _run_id):
        calls.append({"file_seq": item.meta.file_seq,
                      "file_type": item.meta.file_type,
                      "original_name": item.meta.original_name})
        if item.meta.file_type != "XML" or Path(item.path).read_bytes() != source.read_bytes():
            raise AssertionError("受控 XML 输入与事先制作的样本不同")
        evidence = {key: [{"file_seq": item.meta.file_seq, "format": "XML",
                            "location": "/EInvoice/" + key, "raw": value,
                            "normalized": value}]
                    for key, value in FIELDS.items()}
        return DocumentResult(file_seq=item.meta.file_seq, role="Invoice",
                              fields=dict(FIELDS), source_file_seq=[item.meta.file_seq],
                              field_evidence=evidence)

    ports = StagePorts(document_handlers={"XML": fixed_xml},
                       combine_vouchers=combine_vouchers,
                       match_itineraries=match_itineraries,
                       arbitrate_records=arbitrate_records,
                       validate_records=validate_records)
    with tempfile.TemporaryDirectory(prefix="t3-controlled-xml-") as directory:
        cfg = RunConfig("imap.fixture.test", "fixture", "no-real-password",
                        output_root=directory, dashscope_api_key="controlled-no-network")
        with patch("pipeline.imaplib.IMAP4_SSL", return_value=fake), \
             patch("pipeline.load_stage_ports", return_value=ports):
            _, stats = run_pipeline(cfg)
        expected = HERE / "fixtures" / "xml_only_expected_draft.json"
        comparison = compare_files(expected, Path(cfg.debug_file),
                                   Path(cfg.summary_excel_file), "draft")
        report = {
            "author": "测试工程师制作、待用户审核",
            "comparison_kind": "草案比较；非正式验收",
            "called": {"run_pipeline": True, "fixed_xml": calls,
                       "real_attachment_and_t2_stages": True},
            "replaced": ["XML 文件级处理器返回；IMAP 外部服务由固定 EML 替代"],
            "checked": ["事先写好的 JSON 全业务字段/来源/状态",
                        "Excel 八列列映射、逐行逐字段、金额单元格类型"],
            "not_proved": ["真实 XML 提取", "真实邮箱/模型", "用户产品验收"],
            "pipeline_errors": {"imap_error": stats.get("imap_error"),
                                "run_error": stats.get("run_error"),
                                "excel_error": stats.get("excel_error")},
            "comparison": comparison,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if comparison["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
