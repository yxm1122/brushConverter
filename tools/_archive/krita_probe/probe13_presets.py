# -*- coding: utf-8 -*-
"""探测 13：列出 krita 资源里的预设名，确认 converted/测试用.bundle 是否已安装。"""
import sys

import faulthandler

faulthandler.enable()


def log(m):
    sys.stderr.write("[STEP] " + m + "\n")
    sys.stderr.flush()


import krita

inst = krita.Krita.instance()
presets = inst.resources("preset")
log("预设总数=%d" % len(presets))
names = []
for p in presets:
    try:
        names.append("%s | %s" % (p.name(), p.filename()))
    except Exception as exc:
        names.append("ERR " + repr(exc))
hit = [n for n in names if any(k in n for k in ("大怪兽", "勾线", "圆头", "测试用"))]
log("匹配大怪兽/勾线/圆头/测试用 的预设数=%d" % len(hit))
for n in hit:
    log("HIT " + n)
