"""执行 B 编号 T2-1 验收测试并保存机器可读及人可读证据。"""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))
from test_t2_1_acceptance import OBSERVED  # noqa: E402


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outcomes = {}

    def addSuccess(self, test):
        super().addSuccess(test)
        self.outcomes[test._testMethodName] = {"status": "通过", "detail": "全部断言满足"}

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.outcomes[test._testMethodName] = {"status": "失败", "detail": self._exc_info_to_string(err, test)}

    def addError(self, test, err):
        super().addError(test, err)
        self.outcomes[test._testMethodName] = {"status": "失败", "detail": self._exc_info_to_string(err, test)}

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.outcomes[test._testMethodName] = {"status": "未执行", "detail": reason}


def main():
    suite = unittest.defaultTestLoader.loadTestsFromName("test_t2_1_acceptance")
    output = io.StringIO()
    runner = unittest.TextTestRunner(stream=output, verbosity=2, resultclass=RecordingResult)
    outcome = runner.run(suite)
    cases = []
    for method, item in sorted(outcome.outcomes.items()):
        case_id = method.removeprefix("test_").replace("_", "-")
        cases.append({"id": case_id, **item, "evidence": OBSERVED.get(case_id, {})})
    limits = [
        {"scope": "真实 IMAP 服务器端到端", "status": "未执行",
         "reason": "未使用专用测试邮箱；模拟 IMAP 的组件测试不等于真实服务器验证"},
        {"scope": "真实 PDF 预读与字段识别准确度", "status": "未执行",
         "reason": "B 用例对 PDF 返回值使用受控替身；该能力属于 PDF 处理器自身验收"},
        {"scope": "T2-6 正式缺字段校验", "status": "无法验证",
         "reason": "当前 T2-6 校验端口尚未接入；B05 只验证主循环保留缺字段记录"},
        {"scope": "T3 XML/OFD/图片真实提取与跨格式匹配", "status": "无法验证",
         "reason": "本轮只验文件形态分流和接口触发，真实处理脚本不在 T2-1 范围"},
    ]
    report = {"suite": "T2-1 B 编号技术验收", "total": outcome.testsRun,
              "counts": {status: sum(x["status"] == status for x in cases)
                         for status in ("通过", "失败", "未执行", "无法验证")},
              "cases": cases, "coverage_limits": limits}
    folder = PROJECT_ROOT / "03 tests" / "T2-1" / "result"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "unittest.log").write_text(output.getvalue(), encoding="utf-8")
    lines = ["# T2-1 自动化执行结果", "", "运行命令：`python -B \"03 tests/T2-1/scripts/run_t2_1_acceptance.py\"`。",
             "测试对 IMAP 和 PDF 字段返回使用受控输入；真实附件收集、主循环、数据模型及 Excel 写入按各用例指定范围执行。",
             "", f"共执行 {outcome.testsRun} 条：通过 {report['counts']['通过']}，失败 {report['counts']['失败']}。",
             "", "| 用例 | 状态 | 结果摘要 |", "|---|---|---|"]
    for case in cases:
        detail = case["detail"].strip().splitlines()[-1]
        lines.append(f"| {case['id']} | {case['status']} | {detail.replace('|', '/')} |")
    lines += ["", "## 未执行与无法验证", "", "| 范围 | 状态 | 原因 |", "|---|---|---|"]
    lines.extend(f"| {x['scope']} | {x['status']} | {x['reason']} |" for x in limits)
    lines += ["", "## 证据位置", "",
              "- `latest.json`：每例的断言结果、调用记录、实际统计和记录摘要。",
              "- `unittest.log`：原始 unittest 输出及失败堆栈。",
              "- 每例输入、被测对象、动作、观察点与业务解释见 `T2-1_自动化用例说明.md`。",
              ""]
    (folder / "latest.md").write_text("\n".join(lines), encoding="utf-8")
    print(output.getvalue())
    print(f"Evidence: {folder / 'latest.md'}")
    return 0 if outcome.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
