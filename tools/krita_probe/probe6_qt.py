# -*- coding: utf-8 -*-
"""探测 6：kritarunner 内 Qt 应用类型与最小 QWidget 是否可用。"""
import sys


def log(msg):
    sys.stderr.write("[STEP] " + msg + "\n")
    sys.stderr.flush()


log("import PyQt5")
from PyQt5 import QtWidgets, QtCore, QtGui
app = QtWidgets.QApplication.instance()
log("app=%r" % (app,))
if app is not None:
    log("platformName=%s" % app.platformName())
    log("fonts=%d" % len(QtGui.QFontDatabase().families()))
log("create QWidget")
w = QtWidgets.QWidget()
log("widget=%r" % (w,))
log("show")
w.show()
log("shown")
log("processEvents")
app.processEvents()
log("done")
