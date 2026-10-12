"""仅执行 T2-3 研发自测与 T2-1/T2-2 研发回归，证据归档于本阶段。"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
STAGE = Path(__file__).resolve().parent
RESULT = STAGE / "result" / "revision_20261009"
BASELINE = STAGE / "baseline" / "revision_20261009"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def hashes(paths):
    return {
        path.relative_to(ROOT).as_posix(): {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "mtime_ns": path.stat().st_mtime_ns,
        }
        for path in sorted(paths) if path.is_file()
    }


def protected_files():
    return (list((ROOT / "03 tests").rglob("*"))
            + [p for p in ROOT.glob("*.py") if p.name != "itinerary_matcher.py"]
            + list((ROOT / "02 build/T2-1").glob("*.py"))
            + list((ROOT / "02 build/T2-2").glob("*.py"))
            + [ROOT / "01 docs/01 project/固定用例库_首批覆盖范围.md"])


def snapshot():
    RESULT.mkdir(parents=True, exist_ok=True)
    BASELINE.mkdir(parents=True, exist_ok=True)
    before = RESULT / "protected_before.json"
    if before.exists():
        raise SystemExit("已有本次开工快照，拒绝覆盖")
    write_json(before, hashes(protected_files()))
    targets = [ROOT / "itinerary_matcher.py", STAGE / "test_itinerary_matcher.py",
               STAGE / "test_matching_integration.py", STAGE / "T2-3_匹配规则确认.md",
               STAGE / "T2-3_研发交接.md", STAGE / "result/latest.md",
               ROOT / "01 docs/01 project/重构方案.md", ROOT / "01 docs/01 project/计划分工.md"]
    write_json(RESULT / "changed_before.json", hashes(targets))
    for path in targets:
        destination = BASELINE / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    print(f"已保存开工证据：{len(hashes(protected_files()))} 个受保护文件")


def validate():
    if not (RESULT / "protected_before.json").exists():
        raise SystemExit("缺少本次开工快照")
    if (RESULT / "commands.json").exists():
        attempt = 1
        while (RESULT / f"attempt_{attempt:02}").exists():
            attempt += 1
        archive = RESULT / f"attempt_{attempt:02}"
        archive.mkdir()
        for filename in ("commands.json", "boundary_checks.json", "source_hashes.json",
                         "protected_after.json", "t2_3_dev.log", "t2_1_dev_regression.log",
                         "t2_2_dev_regression.log"):
            shutil.copy2(RESULT / filename, archive / filename)
    # 所有测试临时输出与自动清理均局限于已授权的本阶段目录。
    temporary = RESULT / "tmp"
    temporary.resolve().relative_to(ROOT)
    temporary.mkdir(exist_ok=True)
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8",
                   "TMPDIR": str(temporary), "TMP": str(temporary), "TEMP": str(temporary)}
    commands = []
    for stage, pattern, filename in (
        ("T2-3", "test_*.py", "t2_3_dev.log"),
        ("T2-1", "test_*.py", "t2_1_dev_regression.log"),
        ("T2-2", "test_incremental.py", "t2_2_dev_regression.log"),
    ):
        args = [sys.executable, "-B", "-m", "unittest", "discover", "-s",
                f"02 build/{stage}", "-p", pattern, "-v"]
        result = subprocess.run(args, cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                                env=environment)
        (RESULT / filename).write_text(result.stdout, encoding="utf-8")
        commands.append({"args": args, "cwd": str(ROOT), "exit_code": result.returncode,
                         "log": filename, "temporary_directory": str(temporary)})
        print(f"{stage}: exit={result.returncode}\n" + "\n".join(result.stdout.splitlines()[-5:]))
    write_json(RESULT / "commands.json", commands)
    before = json.loads((RESULT / "protected_before.json").read_text(encoding="utf-8"))
    after = hashes(protected_files())
    write_json(RESULT / "protected_after.json", after)
    changed = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
    checked = [ROOT / "itinerary_matcher.py"] + list(STAGE.glob("*.py"))
    for path in checked:
        source = path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(path))
        if any(line.rstrip() != line for line in source.splitlines()):
            raise SystemExit(f"行尾空白：{path}")
    write_json(RESULT / "source_hashes.json", hashes(checked))
    write_json(RESULT / "boundary_checks.json", {
        "protected_file_count": len(before), "changed_protected_files": changed,
        "independent_acceptance_executed": False,
        "ast_and_trailing_whitespace": "passed",
    })
    print(f"受保护文件变化：{changed}")
    return int(bool(changed) or any(item["exit_code"] for item in commands))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", action="store_true")
    if parser.parse_args().snapshot:
        snapshot()
    else:
        raise SystemExit(validate())
