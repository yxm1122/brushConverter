# -*- coding: utf-8 -*-
"""探测 3：预设资源清单 + Qt 应用类型 + openWindow 可行性。"""
import json
import os
import sys


def main():
    info = {}
    try:
        from PyQt5 import QtWidgets, QtCore
        app = QtWidgets.QApplication.instance()
        info["qapp_class"] = type(app).__name__ if app is not None else None
    except Exception as exc:
        info["qapp_error"] = repr(exc)

    import krita
    inst = krita.Krita.instance()
    info["version"] = inst.version()
    try:
        presets = inst.resources("preset")
        info["preset_count"] = len(presets)
        rows = []
        for p in presets:
            try:
                rows.append({"name": p.name(), "filename": p.filename(), "type": p.type()})
            except Exception as exc:
                rows.append({"error": repr(exc)})
        info["presets_first20"] = rows[:20]
        info["presets_matching"] = [r for r in rows if any(k in str(r.get("name", "")) for k in ("大怪兽", "测试", "勾线", "圆头"))]
    except Exception as exc:
        info["preset_error"] = repr(exc)

    try:
        win = inst.openWindow()
        info["openWindow"] = repr(win)
        if win is not None:
            info["window_qwindow"] = repr(win.qwindow())
            info["window_views"] = len(win.views())
            doc = inst.createDocument(100, 100, "probe3", "RGBA", "U8", "", 72.0)
            info["doc"] = repr(doc)
            view = win.addView(doc)
            info["addView"] = repr(view)
            info["activeView"] = repr(win.activeView())
            if view is not None:
                info["view_document"] = repr(view.document())
            info["windows_after"] = len(inst.windows())
    except Exception as exc:
        info["openWindow_error"] = repr(exc)

    print("PROBE3_BEGIN")
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print("PROBE3_END")
    sys.stdout.flush()


main()
