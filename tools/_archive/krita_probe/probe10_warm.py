# -*- coding: utf-8 -*-
"""探测 10：先等待 Krita 初始化完成（处理事件 2 秒），再做「建文档→写像素→导出 PNG→尝试建视图」。"""
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
log("应用=%r 平台=%s" % (app, app.platformName() if app else None))
t0 = time.time()
while time.time() - t0 < 2.0:
    app.processEvents()
    time.sleep(0.02)
log("预热完成 用时=%.2fs" % (time.time() - t0))

inst = krita.Krita.instance()
log("version=%s" % inst.version())
doc = inst.createDocument(64, 64, "p10", "RGBA", "U8", "", 72.0)
log("doc=%r" % (doc,))
node = doc.activeNode()
log("activeNode=%r" % (node,))
if node is not None:
    data = bytes([200, 30, 30, 255]) * (64 * 64)
    log("setPixelData=%r" % (node.setPixelData(data, 0, 0, 64, 64),))
doc.refreshProjection()
raw = bytes(doc.pixelData(0, 0, 64, 64))
log("pixelData 字节=%d alpha非零=%d" % (len(raw), sum(1 for a in raw[3::4] if a > 0)))
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out_probe10.png")
info = krita.InfoObject()
info.setProperty("alpha", True)
info.setProperty("compression", 6)
info.setProperty("forceSRGB", False)
log("exportImage=%r 文件存在=%s 大小=%s" % (doc.exportImage(out, info), os.path.exists(out), os.path.getsize(out) if os.path.exists(out) else None))
log("准备 openWindow")
win = inst.openWindow()
log("win=%r" % (win,))
log("准备 addView（如果崩溃就在这）")
view = win.addView(doc)
log("addView=%r" % (view,))
log("全部完成")
