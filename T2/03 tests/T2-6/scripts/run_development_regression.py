"""Re-run development-authored suites with a sandbox-writable temporary root.

These results are regression evidence, never independent acceptance counts.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "03 tests" / "T2-6" / "result"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
TEMP = OUT / "temp"
TEMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(TEMP)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    results = []
    for stage in range(1, 7):
        name = f"T2-{stage}"
        directory = ROOT / "02 build" / name
        pattern = "test_incremental.py" if stage == 2 else "test_*.py"
        suite = unittest.defaultTestLoader.discover(str(directory), pattern=pattern)
        stream = io.StringIO()
        outcome = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        log_path = OUT / f"dev_regression_{name}_{STAMP}.log"
        log_path.write_text(stream.getvalue(), encoding="utf-8")
        results.append({"stage": name, "tests_run": outcome.testsRun,
                        "failures": len(outcome.failures), "errors": len(outcome.errors),
                        "passed": outcome.wasSuccessful(),
                        "log": str(log_path.relative_to(ROOT)),
                        "test_sources": {str(path.relative_to(ROOT)): sha(path) for path in directory.glob(pattern)}})
    source_names = ("attachment_processor.py", "data_model.py", "pipeline.py",
                    "stage_ports.py", "field_validator.py", "utils.py",
                    "dedup_arbitrator.py", "itinerary_matcher.py")
    report = {"scope": "development-authored regression, independently re-executed",
              "source_sha256": {name: sha(ROOT / name) for name in source_names},
              "results": results}
    path = OUT / f"dev_regression_{STAMP}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(path), "results": [
        {key: item[key] for key in ("stage", "tests_run", "failures", "errors", "passed")}
        for item in results]}, ensure_ascii=False))
    return 0 if all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
