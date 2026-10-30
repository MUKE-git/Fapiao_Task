"""测试工程师制作、待用户审核：Streamlit 真实脚本运行，后端状态固定。

AppTest 执行实际 app_demo.py 渲染路径；替换 run_pipeline 返回值，
所以本文件只验证页面控件与状态呈现，不证明后端识别、ZIP 内容或浏览器传输。
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app_demo.py"


class PageStateDraft(unittest.TestCase):
    def setUp(self):
        self.previous_key = os.environ.get("DASHSCOPE_API_KEY")
        os.environ["DASHSCOPE_API_KEY"] = "fixed-page-test"

    def tearDown(self):
        if self.previous_key is None:
            os.environ.pop("DASHSCOPE_API_KEY", None)
        else:
            os.environ["DASHSCOPE_API_KEY"] = self.previous_key

    def run_state(self, state: str, zip_bytes: bytes | None = None):
        app = AppTest.from_file(str(APP)).run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual([item.label for item in app.text_input],
                         ["邮箱账号", "邮箱授权码（或密码）"])
        self.assertFalse(any("桌面" in item.label or "输出文件夹" in item.label
                             for item in app.text_input))
        self.assertEqual([item.label for item in app.button], ["开始处理"])

        app.text_input[0].set_value("user@example.test")
        app.text_input[1].set_value("fixed-password")
        mails = [{
            "mail_id": "Msg1", "mail_uid": "101", "subject": "电子发票 A",
            "mail_date": "2026-04-27 09:00",
            "files": [{"file_seq": "01", "original_name": "A.xml"}],
            "records": [], "itineraries": [], "dedup_skipped": [], "error_reason": "",
        }]
        def backend(cfg, log=None):
            output = Path(cfg.debug_file)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(mails, ensure_ascii=False), encoding="utf-8")
            return [], {
                "delivery_status": state, "delivery_zip_bytes": zip_bytes,
                "summary_row_count": 0, "unseen_total": 1,
                "invoice_keyword_mails": 1, "duplicate_record_count": 0, "not_found_alerts": [{
                    "mail_id": "Msg1", "mail_uid": "101", "mail_subject": "电子发票 A",
                    "mail_date": "2026-04-27 09:00", "file_seq": "01",
                    "filename": "A.xml", "flags": ["缺凭证，回原邮件核对"]
                }],
            }
        with patch("pipeline.run_pipeline", side_effect=backend), \
             patch("streamlit.download_button") as download:
            app.button[0].click().run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("发票相关" in x.value and "发票记录" in x.value and "重复记录" in x.value for x in app.info))
        self.assertTrue(any("A.xml" in item.value and "缺凭证" in item.value
                            for item in app.markdown),
                        [item.value for item in app.markdown])
        self.assertTrue(any("电子发票 A" in item.value for item in app.warning))
        self.assertTrue(any("2026-04-27 09:00" in item.value for item in app.warning))
        return app, download

    def test_ready_has_existing_zip_button_only(self):
        app, download = self.run_state("ready", b"fixed-checked-zip")
        self.assertEqual(download.call_count, 1)
        self.assertEqual(download.call_args.kwargs["label"], "下载本轮结果")
        self.assertEqual(download.call_args.kwargs["data"], b"fixed-checked-zip")
        self.assertFalse(any("备份" in x.value for x in app.markdown))

    def test_summary_failure_shows_p25_without_download(self):
        app, download = self.run_state("summary_failed")
        self.assertEqual(download.call_count, 0)
        errors = [item.value for item in app.error]
        self.assertEqual(sum("汇总表未能生成" in text for text in errors), 1)
        self.assertFalse(any("下载包未能生成" in text for text in errors))

    def test_package_failure_shows_p26_without_download(self):
        app, download = self.run_state("package_failed")
        self.assertEqual(download.call_count, 0)
        errors = [item.value for item in app.error]
        self.assertEqual(sum("下载包未能生成" in text for text in errors), 1)
        self.assertFalse(any("汇总表未能生成" in text for text in errors))


if __name__ == "__main__":
    unittest.main()
