"""Re-run the confirmed fixed fixtures without overwriting their shared reports.

This uses the existing controlled-PDF test entry point. It is a baseline of the
current assertions, not the planned full-field business acceptance check.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path


HERE = Path(__file__).resolve()
PROJECT = HERE.parents[3]
FIXTURES = PROJECT / "03 tests" / "owner test" / "固定用例库"
RUNNER = FIXTURES / "run_fixed_cases.py"
RESULT = HERE.parents[1] / "result"
SOURCES = (
    PROJECT / "itinerary_matcher.py",
    PROJECT / "pipeline.py",
    PROJECT / "stage_ports.py",
    PROJECT / "data_model.py",
    RUNNER,
    FIXTURES / "index.json",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes() -> dict[str, str]:
    paths = list(SOURCES) + sorted((FIXTURES / "cases").rglob("*"))
    return {path.relative_to(PROJECT).as_posix(): sha256(path)
            for path in paths if path.is_file()}


def main() -> int:
    before = hashes()
    spec = importlib.util.spec_from_file_location("fixed_case_runner", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    index = json.loads((FIXTURES / "index.json").read_text(encoding="utf-8"))
    results = [module.execute_variant(case["id"], variant, real_pdf=False)
               for case in index["cases"] for variant in case["variants"]]
    after = hashes()
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    RESULT.mkdir(parents=True, exist_ok=True)
    target = RESULT / f"confirmed_baseline_{stamp}.json"
    target.write_text(json.dumps({
        "test_kind": "independent controlled-PDF baseline using existing assertions",
        "scope": "18 confirmed mandatory cases; 26 variants",
        "runner": str(RUNNER.relative_to(PROJECT)).replace("\\", "/"),
        "command": 'python -B "03 tests/T2-3/scripts/run_confirmed_baseline.py"',
        "started_source_hashes": before,
        "ended_source_hashes": after,
        "inputs_stable_during_run": before == after,
        "passed": sum(item["status"] == "PASS" for item in results),
        "total": len(results),
        "results": results,
        "limit": "Existing checks do not compare every JSON/Excel business field; no real AI/IMAP.",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"result={target}")
    print(f"passed={sum(item['status'] == 'PASS' for item in results)}/{len(results)}")
    print(f"inputs_stable_during_run={before == after}")
    for item in results:
        if item["errors"]:
            print(f"{item['case_id']}/{item['variant']}: {'; '.join(item['errors'])}")
    return 0 if all(item["status"] == "PASS" for item in results) and before == after else 1


if __name__ == "__main__":
    raise SystemExit(main())
