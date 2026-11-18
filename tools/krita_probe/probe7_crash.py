# -*- coding: utf-8 -*-
"""探测 7：faulthandler 捕获 addView 的访问违例，拿到崩溃时的 Python 调用栈。"""
import faulthandler
import sys

faulthandler.enable(all_threads=True)


def log(m):
    sys.stderr.write("[STEP] " + m + "\n")
    sys.stderr.flush()


import krita

inst = krita.Krita.instance()
log("openWindow")
win = inst.openWindow()
log("win=%r" % (win,))
log("createDocument")
doc = inst.createDocument(64, 64, "p7", "RGBA", "U8", "", 72.0)
log("doc=%r" % (doc,))
log("调用 addView（预期在此崩溃）")
view = win.addView(doc)
log("addView 返回 %r" % (view,))
