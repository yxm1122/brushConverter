# -*- coding: utf-8 -*-
"""统计 research/krita结果 下历史真值图属性（临时分析脚本，供任务 06A 清单设计与报告取数）。"""
import hashlib
import json
import os

from PIL import Image

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "research", "krita结果")
rows = []
for dirpath, _dirnames, filenames in os.walk(ROOT):
    for fn in sorted(filenames):
        if not fn.lower().endswith(".png"):
            continue
        path = os.path.join(dirpath, fn)
        with open(path, "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()
        with Image.open(path) as im:
            rgba = im.convert("RGBA")
            nonzero = sum(1 for v in rgba.getchannel("A").getdata() if v > 0)
            rgb_nonzero = sum(1 for p in rgba.getdata() if p[0] > 0 or p[1] > 0 or p[2] > 0)
            rows.append({
                "file": fn,
                "group": os.path.basename(dirpath),
                "size": list(im.size),
                "mode": im.mode,
                "alpha_nonzero": nonzero,
                "rgb_nonzero": rgb_nonzero,
                "sha256": sha,
                "bytes": os.path.getsize(path),
            })
print(json.dumps({"root": ROOT, "count": len(rows), "rows": rows}, ensure_ascii=False, indent=1))
