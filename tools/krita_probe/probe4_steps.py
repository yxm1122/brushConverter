# -*- coding: utf-8 -*-
"""探测 4：分步日志，定位 openWindow 是否可用/在哪一步崩。"""
import sys


def log(msg):
    sys.stderr.write("[STEP] " + msg + "\n")
    sys.stderr.flush()


log("1 开始导入 krita")
import krita
log("2 krita 导入完成")
inst = krita.Krita.instance()
log("3 instance=%r version=%s" % (inst, inst.version()))
presets = inst.resources("preset")
log("4 预设数量=%d" % len(presets))

log("5 准备 openWindow")
win = inst.openWindow()
log("6 openWindow=%r" % (win,))
if win is not None:
    log("7 qwindow=%r views=%d" % (win.qwindow(), len(win.views())))
    doc = inst.createDocument(100, 100, "probe4", "RGBA", "U8", "", 72.0)
    log("8 doc=%r" % (doc,))
    view = win.addView(doc)
    log("9 addView=%r activeView=%r" % (view, win.activeView()))
    log("10 windows=%d documents=%d" % (len(inst.windows()), len(inst.documents())))
log("11 结束")
