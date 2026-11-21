# -*- coding: utf-8 -*-
"""验证 Krita 侧压感是否真实注入：同预设、压感 0.2 vs 1.0，比较 Alpha 统计。"""
import json
import sys

sys.path.insert(0, "tools")
import krita_render_runner as runner
from PIL import Image

preset = "converted/kpp/003_大怪兽-圆头虚_用数位板.kpp"
outs = {}
for p in (0.2, 1.0):
    out = "tools/krita_probe/pressure_test_%s.png" % str(p).replace(".", "_")
    r = runner.render_one(preset, out, size=(200, 200), stroke=120, pressure=p, route="gui")
    print("pressure", p, "ok=", r.get("ok"), "elapsed=", r.get("elapsed_s"), "alpha=", r.get("alpha_nonzero"), "reason=", (r.get("reason") or "")[:80])
    outs[p] = out
for p, path in outs.items():
    with Image.open(path) as im:
        alpha = im.convert("RGBA").getchannel("A")
        width, height = im.size
        rows = []
        for x in range(width):
            col = [alpha.getpixel((x, y)) for y in range(height)]
            rows.append(sum(1 for v in col if v > 10))
        print("pressure", p, "max_col_width=", max(rows), "sum_width=", sum(rows), "sha_head=", __import__("hashlib").sha256(open(path, "rb").read()).hexdigest()[:12])
