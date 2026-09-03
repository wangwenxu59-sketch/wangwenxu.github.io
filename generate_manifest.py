#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动扫描 files/ 文件夹，生成 files.json 文件清单。
本地预览或 GitHub Actions 中均可运行（仅用标准库）。"""
import os
import json
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
FILES_DIR = os.path.join(BASE, "files")

items = []
if os.path.isdir(FILES_DIR):
    for root, dirs, files in os.walk(FILES_DIR):
        # 跳过隐藏目录
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.startswith("."):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, FILES_DIR).replace("\\", "/")
            st = os.stat(full)
            items.append({
                "name": name,
                "path": "files/" + rel,
                "size": st.st_size,
                "date": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            })

items.sort(key=lambda x: x["date"], reverse=True)

out = os.path.join(BASE, "files.json")
with open(out, "w", encoding="utf-8") as fp:
    json.dump(items, fp, ensure_ascii=False, indent=2)

print("generate_manifest: %d files -> files.json" % len(items))
