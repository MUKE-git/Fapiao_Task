"""T3-1 研发自测固定后端状态；文案草案仍待用户审核。"""
from __future__ import annotations

import importlib
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class FakeStreamlit(types.ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self.session_state = {}
        self.messages = []
        self.secrets = {}

    @contextmanager
    def form(self, _name):
        yield

    @contextmanager
    def expander(self, _name):
        self.messages.append(("expander", _name))
        yield

    def set_page_config(self, **_kwargs):
        pass

    def title(self, *_args):
        pass

    def caption(self, *_args):
        pass

    def selectbox(self, _label, options):
        return options[0]

    def text_input(self, *_args, **_kwargs):
        return ""

    def checkbox(self, *_args, **_kwargs):
        return False

    def form_submit_button(self, *_args, **_kwargs):
        return False

    def warning(self, message):
        self.messages.append(("warning", message))

    def info(self, message):
        self.messages.append(("info", message))

    def write(self, message):
        self.messages.append(("write", message))

    def success(self, message):
        self.messages.append(("success", message))

    def error(self, message):
        self.messages.append(("error", message))


fake_st = FakeStreamlit()
sys.modules["streamlit"] = fake_st
fake_pipeline = types.ModuleType("pipeline")
fake_pipeline.run_pipeline = lambda *_args, **_kwargs: ([], {})
sys.modules["pipeline"] = fake_pipeline
app = importlib.import_module("app_demo")


class FrontendViewTests(unittest.TestCase):
    def setUp(self):
        fake_st.messages.clear()
        fake_st.session_state.clear()

    def test_imap_presets_match_current_password_login_support(self):
        self.assertEqual(app.IMAP_PRESETS, {
            "163 邮箱": "imap.163.com",
            "QQ 邮箱": "imap.qq.com",
            "126 邮箱": "imap.126.com",
            "Yeah 邮箱": "imap.yeah.net",
            "其他（手动输入）": "__custom__",
        })

    def test_one_mail_card_keeps_file_numbers_and_reasons(self):
        mails = [{"mail_id": "Msg1", "subject": "报销凭证", "mail_date": "2031-02-03 04:05",
                  "files": [{"file_seq": "01", "original_name": "凭证.xml"},
                            {"file_seq": "02", "original_name": "凭证.ofd"}]},
                 {"mail_id": "Msg2", "subject": "另一封", "mail_date": ""}]
        alerts = [{"mail_id": "Msg1", "file_seq": "01", "flags": ["缺少字段", "缺凭证"]},
                  {"mail_id": "Msg1", "file_seq": "02", "flags": ["转换失败"]}]
        cards = app.mail_notice_cards(mails, alerts, [])
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["subject"], "报销凭证")
        self.assertEqual([detail["filename"] for detail in cards[0]["details"]],
                         ["凭证.xml", "凭证.xml", "凭证.ofd"])
        app._render_mail_alerts(mails, alerts, [])
        self.assertEqual(sum(kind == "warning" for kind, _ in fake_st.messages), 1)
        self.assertEqual(sum(kind == "expander" for kind, _ in fake_st.messages), 1)
        self.assertIn("文件 02「凭证.ofd」", fake_st.messages[-1][1])

    def test_missing_mail_location_and_classification_deduplicated_by_code(self):
        mails = [{"mail_id": "Msg1", "subject": "", "mail_date": "",
                  "files": [{"file_seq": "01", "original_name": "行程单.pdf"}]}]
        alerts = [{"mail_id": "Msg1", "file_seq": "01", "code": "P10",
                   "flags": ["类别判断不一致"]}]
        cards = app.mail_notice_cards(mails, alerts,
                                      [{"mail_id": "Msg1", "file_seq": "01"}])
        self.assertEqual(cards[0]["subject"], "（无主题）")
        self.assertEqual(cards[0]["mail_date"], "（邮件时间不可用）")
        self.assertEqual(len(cards[0]["details"]), 1)

    def test_download_only_from_checked_backend_state(self):
        ready = {"delivery_status": "ready", "delivery_zip_bytes": b"PKfixed",
                 "summary_row_count": 2}
        self.assertEqual(app.delivery_view(ready)["status"], "ready")
        app._show_delivery_result(ready, [], [], [], None, None)
        self.assertEqual(fake_st.session_state["last_zip_bytes"], b"PKfixed")
        self.assertIn("2 条汇总记录", fake_st.messages[-1][1])
        for state, message in (("summary_failed", "汇总表未能生成"),
                               ("package_failed", "下载包未能生成")):
            fake_st.session_state.clear()
            fake_st.messages.clear()
            app._show_delivery_result({"delivery_status": state}, [], [], [], None, None)
            self.assertNotIn("last_zip_bytes", fake_st.session_state)
            self.assertIn(message, fake_st.messages[-1][1])
        self.assertEqual(app.delivery_view({"delivery_status": "ready"})["status"],
                         "package_failed")

    def test_mail_level_follows_structured_backend_level(self):
        mails = [{"mail_id": "Msg1", "subject": "发票", "mail_date": "2031-01-01 00:00"}]
        alerts = [{"mail_id": "Msg1", "level": "info", "flags": ["重复来源已归并"]},
                  {"mail_id": "Msg1", "level": "error", "flags": ["文件读取失败"]}]
        self.assertEqual(app.mail_notice_cards(mails, alerts, [])[0]["level"], "error")
        app._render_mail_alerts(mails, alerts, [])
        self.assertEqual(fake_st.messages[0][0], "error")


if __name__ == "__main__":
    unittest.main()
