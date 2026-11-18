# -*- coding: utf-8 -*-
"""探测 8：无视图时调用 Node.paintLine（源码中会访问 currentMainwindow()->activeView()）。"""
import faulthandler
import sys

faulthandler.enable(all_threads=True)


def log(m):
    sys.stderr.write("[STEP] " + m + "\n")
    sys.stderr.flush()


import krita
from PyQt5 import QtCore

inst = krita.Krita.instance()
doc = inst.createDocument(64, 64, "p8", "RGBA", "U8", "", 72.0)
log("doc=%r activeNode=%r" % (doc, doc.activeNode()))
log("topLevelNodes=%r" % (doc.topLevelNodes(),))
node = doc.activeNode()
if node is None:
    node = doc.rootNode()
log("node=%r paintAbility=%s" % (node, node.paintAbility()))
log("调用 paintLine（预期崩溃）")
node.paintLine(QtCore.QPoint(10, 10), QtCore.QPoint(50, 50), 1.0, 1.0)
log("paintLine 返回")
