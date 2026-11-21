# -*- coding: utf-8 -*-
"""探测 12：预热后验证「建文档 -> 写像素 -> 用 QImage 投影导出 PNG」是否可行。"""
import os
import sys
import time

import faulthandler

faulthandler.enable(all_threads=True)


def log(m):
    sys.stderr.write("[STEP] " + m + "\n")
    sys.stderr.flush()


from PyQt5 import QtWidgets

import krita

app = QtWidgets.QApplication.instance()
t0 = time.time()
while time.time() - t0 < 2.5:
    app.processEvents()
    time.sleep(0.02)
log("预热完成")
inst = krita.Krita.instance()
doc = inst.createDocument(64, 64, "p12", "RGBA", "U8", "", 72.0)
node = doc.topLevelNodes()[0]
log("doc=%r node=%r paintAbility=%s" % (doc, node, node.paintAbility()))
data = bytes([200, 30, 30, 255]) * (64 * 64)
log("setPixelData=%r" % (node.setPixelData(data, 0, 0, 64, 64),))
doc.refreshProjection()
img = doc.projection(0, 0, 64, 64)
log("projection=%r 尺寸=%sx%s" % (img, img.width(), img.height()))
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out_probe12.png")
log("QImage.save=%r 文件存在=%s 大小=%s" % (img.save(out, "PNG"), os.path.exists(out), os.path.getsize(out) if os.path.exists(out) else None))
log("全部完成")
