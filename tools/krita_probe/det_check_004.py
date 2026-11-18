# -*- coding: utf-8 -*-
"""测 004（硬圆头笔）两次渲染差异，判断是否可作确定性基准。"""
import json
import sys

sys.path.insert(0, "tools")
import krita_render_runner as runner
from PIL import Image, ImageChops

preset = "converted/kpp/004_大怪兽-圆头实_用数位板.kpp"
r1 = runner.render_one(preset, "tools/krita_probe/det_004_1.png", route="gui")
r2 = runner.render_one(preset, "tools/krita_probe/det_004_2.png", route="gui")
a1 = Image.open("tools/krita_probe/det_004_1.png").convert("RGBA")
a2 = Image.open("tools/krita_probe/det_004_2.png").convert("RGBA")
diff = ImageChops.difference(a1.getchannel("A"), a2.getchannel("A"))
print("ok1", r1["ok"], "alpha1", r1.get("alpha_nonzero"), "ok2", r2["ok"], "alpha2", r2.get("alpha_nonzero"))
print("alpha bbox", diff.getbbox(), "maxdiff", max(diff.getdata()) if diff.getbbox() else 0)
rgbdiff = ImageChops.difference(a1.convert("RGB"), a2.convert("RGB"))
print("rgb bbox", rgbdiff.getbbox())
