"""研发制作、待用户审核；模拟模型连接，不调用外部服务。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from ai_client import call_ai_audit_and_rename, _connection_failure_category
from fallback_extractor import build_fallback_audit_result

def task(text="行程单"):
    return {"files": [{"local_path": "x.pdf", "identified_type": "Itinerary", "text_snapshot": text}]}

class RetrySafety(unittest.TestCase):
    def test_connection_failure_then_success(self):
        t = task()
        payload = {"match_amount": "112.22", "travel_time": "2026-03-26 08:59", "origin_dest": "甲→乙", "file_classifications": []}
        response = SimpleNamespace(status_code=200, output=SimpleNamespace(text=json.dumps(payload)))
        with patch("ai_client.Generation") as gen, patch("ai_client.time.sleep") as sleep, patch("ai_client._rename_pdfs_with_audit", return_value=([], [])):
            gen.call.side_effect = [ConnectionError("secret details"), response]
            data, error, _, _ = call_ai_audit_and_rename(t, model="test")
        self.assertEqual(data["match_amount"], "112.22")
        self.assertEqual(t["model_usage"]["attempt_count"], 2)
        self.assertEqual(t["model_usage"]["attempt_errors"], ["ConnectionError"])
        self.assertTrue(t["model_usage"]["successful_response"])
        self.assertEqual(sleep.call_count, 1)
    def test_three_failures_then_local_fallback(self):
        t = task()
        with patch("ai_client.Generation") as gen, patch("ai_client.time.sleep") as sleep:
            gen.call.side_effect = ConnectionError("secret details")
            data, error, _, _ = call_ai_audit_and_rename(t)
        self.assertIsNone(data)
        self.assertEqual(error, "PROCESSING_EXCEPTION:ConnectionError")
        self.assertEqual(t["model_usage"]["attempt_count"], 3)
        self.assertEqual(t["model_usage"]["attempt_errors"], ["ConnectionError"] * 3)
        self.assertEqual(sleep.call_count, 2)
    def test_no_retry_programming_error(self):
        t = task()
        with patch("ai_client.Generation") as gen, patch("ai_client.time.sleep") as sleep:
            gen.call.side_effect = ValueError("details")
            _, error, _, _ = call_ai_audit_and_rename(t)
        self.assertEqual(t["model_usage"]["attempt_count"], 1)
        self.assertEqual(error, "PROCESSING_EXCEPTION:ValueError")
        sleep.assert_not_called()
    def test_safe_connection_categories(self):
        self.assertEqual(_connection_failure_category(ConnectionError("NameResolutionError secret-value")), "dns")
        self.assertEqual(_connection_failure_category(ConnectionError("ProxyError secret-value")), "proxy")
        self.assertEqual(_connection_failure_category(ConnectionError("certificate verify failed")), "tls")
        self.assertNotIn("secret-value", _connection_failure_category(ConnectionError("ProxyError secret-value")))

    def test_unrelated_dates_not_joined(self):
        text = "申请日期：2026-04-27 行程时间：2026-03-26至2026-04-14\n1 经济型 2026-03-26 广州市春兰花\n08:59:28 园"
        result = build_fallback_audit_result([{"identified_type": "Itinerary", "text_snapshot": text}])
        self.assertEqual(result["travel_time"], "Not Found")
    def test_adjacent_datetime_preserved_and_header_excluded(self):
        for text, expected in [("用车时间 2026-03-26 08:59", "2026-03-26 08:59"), ("申请时间 2026-04-27 08:59", "Not Found"), ("行程时间 2026-03-26至2026-04-14 08:59", "Not Found")]:
            with self.subTest(text=text):
                result=build_fallback_audit_result([{"identified_type": "Itinerary", "text_snapshot": text}])
                self.assertEqual(result["travel_time"], expected)

if __name__ == "__main__": unittest.main()
