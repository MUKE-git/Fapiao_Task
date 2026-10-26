"""Make a temporary byte-exact copy of frozen fixtures without editing them.

Some Windows checkouts have core.autocrlf=true, which changes .eml bytes while
Git considers the tree clean. The case inventories hash the committed blobs.
This tool selects the version whose SHA-256 and byte count match each approved
inventory, from the Git object or checkout, into an ignored result directory.
The original run_fixed_cases.py can then run there unchanged.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]
SOURCE = PROJECT / "03 tests" / "owner test" / "固定用例库"
DEST = PROJECT / "03 tests" / "T3" / "result" / "frozen_blob_copy" / "03 tests" / "owner test" / "固定用例库"


def prepare() -> dict:
    DEST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE, DEST, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("result", "__pycache__"))
    restored = 0
    for manifest in SOURCE.rglob("input_inventory.json"):
        source_case = manifest.parent
        destination_case = DEST / source_case.relative_to(SOURCE)
        entries = json.loads(manifest.read_text(encoding="utf-8"))
        for item in entries:
            relative = Path(item["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe manifest path {relative}")
            original = source_case / relative
            target = destination_case / relative
            git_path = original.relative_to(PROJECT).as_posix()
            blob = subprocess.check_output(["git", "show", "HEAD:" + git_path], cwd=PROJECT)
            checkout = original.read_bytes()
            expected = (item["sha256"], item["bytes"])
            fingerprint = lambda content: (hashlib.sha256(content).hexdigest(), len(content))
            if fingerprint(blob) == expected:
                content = blob
            elif fingerprint(checkout) == expected:
                content = checkout
            else:
                raise ValueError(f"neither Git blob nor checkout matches approved inventory: {git_path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            restored += 1
    return {"temporary_case_root": str(DEST), "restored_declared_inputs": restored,
            "original_case_root_untouched": str(SOURCE)}


if __name__ == "__main__":
    print(json.dumps(prepare(), ensure_ascii=False, indent=2))
