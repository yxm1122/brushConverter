# -*- coding: utf-8 -*-
"""连续两次 GUI 渲染，检查同参数结果是否逐像素一致（幂等验证）。"""
import json
import os
import sys

sys.path.insert(0, "tools")
import krita_render_runner as runner
from PIL import Image, ImageChops

preset = "converted/kpp/003_大怪兽-圆头虚_用数位板.kpp"
out1 = "tools/krita_probe/idem_run1.png"
out2 = "tools/krita_probe/idem_run2.png"
r1 = runner.render_one(preset, out1, size=(200, 200), stroke=120, pressure=1.0)
r2 = runner.render_one(preset, out2, size=(200, 200), stroke=120, pressure=1.0)
print("run1", json.dumps({k: r1.get(k) for k in ("ok", "alpha_nonzero", "elapsed_s", "reason")}, ensure_ascii=False))
print("run2", json.dumps({k: r2.get(k) for k in ("ok", "alpha_nonzero", "elapsed_s", "reason")}, ensure_ascii=False))
if r1.get("ok") and r2.get("ok"):
    a1 = Image.open(out1).convert("RGBA").getchannel("A")
    a2 = Image.open(out2).convert("RGBA").getchannel("A")
    diff = ImageChops.difference(a1, a2)
    bbox = diff.getbbox()
    maxdiff = max(diff.getdata()) if bbox else 0
    print("alpha 差异包围盒=", bbox, "最大差=", maxdiff)
    rgbdiff = ImageChops.difference(Image.open(out1).convert("RGB"), Image.open(out2).convert("RGB"))
    print("RGB 差异包围盒=", rgbdiff.getbbox(), "最大差=", max(rgbdiff.getdata()).__getitem__(0) if rgbdiff.getbbox() else 0)
