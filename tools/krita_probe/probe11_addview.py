# -*- coding: utf-8 -*-
"""探测 11：预热后测 openWindow -> createDocument -> addView 是否仍崩溃。"""
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
inst.setBatchmode(True)
log("batchmode=%s" % inst.batchmode())
win = inst.openWindow()
log("win=%r" % (win,))
doc = inst.createDocument(64, 64, "p11", "RGBA", "U8", "", 72.0)
log("doc=%r topLevel=%r" % (doc, doc.topLevelNodes()))
log("准备 addView")
view = win.addView(doc)
log("addView=%r activeView=%r" % (view, win.activeView()))
log("全部完成")
