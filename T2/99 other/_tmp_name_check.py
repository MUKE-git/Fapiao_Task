# -*- coding: utf-8 -*-
"""临时脚本：验证 task_debug.json 中附件原名是否真实乱码。"""
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PATH = r"C:\Users\lenovo\Desktop\T1-7\task_debug.json"
with open(PATH, encoding="utf-8") as f:
    data = json.load(f)

for mid, item in data.items():
    for i, f in enumerate(item.get("files", [])):
        name = f.get("attachment_original_name", "")
        # 检查是否含 U+FFFD（替换符）即真实乱码
        has_replacement = "\ufffd" in name
        if has_replacement or not name.isascii():
            print("MSG %s [%d] replacement=%s name=%r" % (mid, i, has_replacement, name))
