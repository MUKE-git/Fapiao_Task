"""Run the already confirmed fixed cases with their existing assertions.

This is a regression baseline, not a T2-4 acceptance suite or a full-field check.
The shared fixed-case reports and expected files are never overwritten.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path


SCRIPT = Path(__file__).resolve()
PROJECT = SCRIPT.parents[3]
FIXTURES = PROJECT / "03 tests" / "owner test" / "固定用例库"
RESULT = SCRIPT.parents[1] / "result"
RUNNER = FIXTURES / "run_fixed_cases.py"
SOURCES = (PROJECT / "dedup_arbitrator.py", PROJECT / "pipeline.py",
           PROJECT / "data_model.py", PROJECT / "stage_ports.py", RUNNER,
           FIXTURES / "index.json")


def file_hashes() -> dict[str, str]:
    paths = list(SOURCES) + sorted((FIXTURES / "cases").rglob("*"))
    return {str(path.relative_to(PROJECT)).replace("\\", "/"):
            hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths if path.is_file()}


def main() -> int:
    before = file_hashes()
    spec = importlib.util.spec_from_file_location("t24_fixed_runner", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    index = json.loads((FIXTURES / "index.json").read_text(encoding="utf-8"))
    results = [module.execute_variant(case["id"], variant, real_pdf=False)
               for case in index["cases"] for variant in case["variants"]]
    after = file_hashes()
    RESULT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    path = RESULT / f"existing_fixed_regression_{stamp}.json"
    path.write_text(json.dumps({
        "scope": "18 confirmed mandatory fixed cases; 26 variants",
        "kind": "existing controlled-PDF assertions, not T2-4 acceptance",
        "command": 'python -B "03 tests/T2-4/scripts/run_existing_fixed_regression.py"',
        "runner": str(RUNNER.relative_to(PROJECT)).replace("\\", "/"),
        "before_sha256": before,
        "after_sha256": after,
        "sources_and_inputs_stable": before == after,
        "passed": sum(item["status"] == "PASS" for item in results),
        "total": len(results),
        "results": results,
        "limit": "Existing assertions omit many JSON/Excel business fields and contain no T2-4 duplicate scenario.",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"result={path}")
    print(f"passed={sum(item['status'] == 'PASS' for item in results)}/{len(results)}")
    print(f"sources_and_inputs_stable={before == after}")
    for item in results:
        if item["errors"]:
            print(f"{item['case_id']}/{item['variant']}: {'; '.join(item['errors'])}")
    return int(any(item["status"] != "PASS" for item in results) or before != after)


if __name__ == "__main__":
    raise SystemExit(main())
