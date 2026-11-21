# -*- coding: utf-8 -*-
"""探测 5：不同 openWindow / addView 变体，找不崩溃的建视图路径。变体由环境变量 VARIANT 指定。"""
import os
import sys


def log(msg):
    sys.stderr.write("[STEP] " + msg + "\n")
    sys.stderr.flush()


variant = os.environ.get("VARIANT", "1")
log("variant=" + variant)
import krita
inst = krita.Krita.instance()
log("instance=%r" % (inst,))

if variant == "3":
    log("先建文档")
    doc = inst.createDocument(64, 64, "p5", "RGBA", "U8", "", 72.0)
    log("doc=%r" % (doc,))

log("openWindow")
win = inst.openWindow()
log("win=%r" % (win,))

if variant in ("1", "3"):
    log("show qwindow")
    win.qwindow().show()
    log("shown")
elif variant == "4":
    log("activate")
    win.activate()
    log("activated")

if variant != "3":
    log("createDocument")
    doc = inst.createDocument(64, 64, "p5", "RGBA", "U8", "", 72.0)
    log("doc=%r" % (doc,))

log("addView")
view = win.addView(doc)
log("view=%r" % (view,))
log("activeView=%r" % (win.activeView(),))
log("done")
