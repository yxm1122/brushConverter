# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "tools")
import feel_curve, feel_compare
src = "tests/baselines/krita/krita__大怪兽-圆头虚_用数位板__plain__s200x200_p1.0.png"
curve = feel_curve.extract_curves(src)
print("max width px =", max(curve["width"]), "opacity range =", min(curve["opacity"]), max(curve["opacity"]))
self_res = feel_compare.compare_curves(curve, curve)
print("自比:", self_res["verdict"], "width_dev=", self_res["width_dev"], "opacity_dev=", self_res["opacity_dev"])
feel_compare.stretch_image_horizontally(src, "tools/krita_probe/stretch_case.png", 1.10)
stretched = feel_curve.extract_curves("tools/krita_probe/stretch_case.png")
res = feel_compare.compare_curves(stretched, curve)
print("拉伸10%: ", res["verdict"], "width_dev=%.4f (%.2f%%)" % (res["width_dev"], res["width_dev_percent"]), "opacity_dev=", res["opacity_dev"])
