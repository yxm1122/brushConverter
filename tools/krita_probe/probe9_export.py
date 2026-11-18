# -*- coding: utf-8 -*-
"""探测 9：不落笔，仅验证无头「建文档 -> 写像素 -> 导出 PNG」是否可行（用于界定能力边界）。"""
import os
import sys


def log(m):
    sys.stderr.write("[STEP] " + m + "\n")
    sys.stderr.flush()


import krita

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "out_probe9.png"))
inst = krita.Krita.instance()
doc = inst.createDocument(64, 64, "p9", "RGBA", "U8", "", 72.0)
node = doc.activeNode()
log("doc=%r node=%r paintAbility=%s" % (doc, node, node.paintAbility()))
data = bytes([200, 30, 30, 255]) * (64 * 64)
log("setPixelData=%r" % (node.setPixelData(data, 0, 0, 64, 64),))
doc.refreshProjection()
raw = doc.pixelData(0, 0, 64, 64)
alpha = bytes(raw)[3::4]
log("pixelData 字节数=%d alpha 非零像素=%d" % (len(raw), sum(1 for a in alpha if a > 0)))
info = krita.InfoObject()
info.setProperty("alpha", True)
info.setProperty("compression", 6)
info.setProperty("forceSRGB", False)
log("导出到 %s" % OUT)
log("exportImage=%r 文件存在=%s 大小=%s" % (doc.exportImage(OUT, info), os.path.exists(OUT), os.path.getsize(OUT) if os.path.exists(OUT) else None))
doc.close()
