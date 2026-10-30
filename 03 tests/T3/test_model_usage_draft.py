"""研发制作、待用户审核：固定服务响应验证模型记录，不访问网络。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from ai_client import ModelFileInput, extract_single_file, call_ai_audit_and_rename
from data_model import FileRecord, MailRecord, mail_to_dict

class ModelUsage(unittest.TestCase):
    def test_vision_called_and_serialized(self):
        trace = {"configured_model": "vision-test", "config_source": "secrets.toml"}
        req = ModelFileInput("01", "file.jpg", "file.jpg", "图片", images=((b"x", "image/jpeg"),))
        seen = []
        def service(**kw):
            seen.append(kw["model"])
            response = {"file_classifications": [{"file": "file.jpg", "role": "Unknown"}]}
            return SimpleNamespace(status_code=200, output=SimpleNamespace(text=json.dumps(response)))
        extract_single_file(req, SimpleNamespace(dashscope_vision_model="vision-test"), transport=service, diagnostics=trace)
        self.assertEqual(seen, ["vision-test"])
        self.assertEqual((trace["call_attempted"], trace["successful_response"], trace["extraction_source"]), (True, True, "model_response"))
        out = mail_to_dict(MailRecord(mail_id="M", files=[FileRecord(file_seq="01", model_usage=trace)]))
        self.assertEqual(out["files"][0]["model_usage"]["config_source"], "secrets.toml")
    def test_no_key_does_not_claim_call(self):
        req = ModelFileInput("01", "x", "x", "图片", images=((b"x", "image/jpeg"),))
        trace = {}
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": ""}):
            extract_single_file(req, SimpleNamespace(dashscope_api_key=""), diagnostics=trace)
        self.assertFalse(trace["call_attempted"])
        self.assertEqual(trace["result"], "MODEL_API_KEY_MISSING")
    def test_pdf_bad_json_uses_regex(self):
        trace = {"configured_model": "text-test", "config_source": "environment"}
        task = {"model_usage": trace, "files": [{"local_path": "x.pdf", "identified_type": "Invoice", "text_snapshot": "*运输服务*客运服务费 94.12"}]}
        response = SimpleNamespace(status_code=200, output=SimpleNamespace(text="bad json"))
        with patch("ai_client.Generation") as generation, patch("ai_client._rename_pdfs_with_audit", return_value=([], [])):
            generation.call.return_value = response
            data, error, _, _ = call_ai_audit_and_rename(task, model="text-test")
        self.assertEqual(generation.call.call_args.kwargs["model"], "text-test")
        self.assertTrue(trace["call_attempted"])
        self.assertTrue(trace["successful_response"])
        self.assertEqual(trace["extraction_source"], "local_regex")
        self.assertEqual(data["receipt_type"], "客运服务费")
        self.assertIn("JSON_PARSE_ERROR", error)

if __name__ == "__main__": unittest.main()
