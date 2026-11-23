# -*- coding: utf-8 -*-
"""PyKrita 启动插件（路线 A · 生产版）：在真实 Krita GUI 会话里自动落笔并导出 PNG。

本文件由 `tools/krita_render_runner.py` 在运行时复制为
`%APPDATA%/krita/pykrita/bc_auto_render/__init__.py`，并在 kritarc 里临时启用；
会话结束后插件被删除、kritarc 被还原（不留持久改动）。

为什么必须是「GUI 进程 + 启动插件」：kritarunner 没有主窗口/视图，`Window.addView` 必崩
（任务 04 门结论），而 `Node.paintLine` 依赖 `currentMainwindow()->activeView()`。

环境变量：
    BC_AUTO_LOG          日志文件（必填）
    BC_AUTO_OUT          输出 PNG（必填）
    BC_AUTO_PRESULT      结果 JSON 路径（必填）
    BC_AUTO_PRESET_FILE  目标预设的 .kpp 文件名（如 003_大怪兽-圆头虚_用数位板.kpp），按 Resource.filename() 后缀匹配
    BC_AUTO_PRESET       备选：预设名字子串
    BC_AUTO_SIZE         画布尺寸，默认 200x200
    BC_AUTO_STROKE       笔划长度（像素），默认 120
    BC_AUTO_PRESSURE     压感 0–1，默认 1.0（笔划起点压力）
    BC_AUTO_PRESSURE2    笔划终点压力 0–1；缺省=和 BC_AUTO_PRESSURE 相同（任务 144 新增：
                         Node.paintLine 原生支持双端压力，可出「单图梯度」）
    BC_AUTO_STROKES      任务 144 新增：JSON 数组，一次会话画多条笔迹（每条一行），
                         元素 {"pressure","pressure2","stroke","y"}；设置后忽略上面单笔参数。
                         用于「一图多档压力阶梯」，把 N 档从 N 次会话压到 1 次。
"""

import json
import os
import time
import traceback

from PyQt5.QtCore import QPoint, QTimer
from krita import Extension, Krita

LOG = os.environ.get("BC_AUTO_LOG", "")


def log(msg):
    if not LOG:
        return
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def write_result(payload):
    path = os.environ.get("BC_AUTO_PRESULT")
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
    except Exception as exc:
        log("写结果文件失败：%r" % (exc,))


def close_blocking_dialogs():
    """关闭会阻塞启动的对话框（会话管理器 / Python 异常对话框）。"""
    from PyQt5.QtWidgets import QApplication

    closed = []
    for widget in QApplication.instance().topLevelWidgets():
        try:
            if not widget.isVisible():
                continue
            if (widget.windowTitle() or "") == "Sessions" or widget.__class__.__name__ == "ExceptHookDialog":
                widget.close()
                closed.append(widget.__class__.__name__)
        except Exception:
            continue
    return closed


def count_alpha_nonzero(qimage):
    from PyQt5 import QtGui

    img = qimage.convertToFormat(QtGui.QImage.Format_ARGB32)
    width, height = img.width(), img.height()
    total = 0
    for y in range(height):
        data = bytes(img.scanLine(y).asarray(width * 4))
        for x in range(3, len(data), 4):
            if data[x] > 0:
                total += 1
    return total


class BCAutoRender(Extension):
    def __init__(self, parent):
        super().__init__(parent)
        self.tries = 0
        self.document = None
        log("插件 BCAutoRender.__init__ 被调用")
        QTimer.singleShot(1200, self._tick)

    def setup(self):
        pass

    def createActions(self, window):
        pass

    def _tick(self):
        self.tries += 1
        try:
            inst = Krita.instance()
            win = inst.activeWindow()
            if win is None:
                if self.tries in (3, 10):
                    closed = close_blocking_dialogs()
                    if closed:
                        log("tick %d 关闭阻塞对话框=%r" % (self.tries, closed))
                if self.tries < 120:
                    QTimer.singleShot(500, self._tick)
                    return
                write_result({"ok": False, "stage": "window", "reason": "120 次轮询仍无主窗口"})
                self._finish()
                return
            self._run(inst, win)
        except Exception as exc:
            log("异常：%r" % (exc,))
            log(traceback.format_exc())
            write_result({"ok": False, "stage": "exception", "reason": repr(exc)})
        self._finish()

    def _run(self, inst, win):
        size_text = os.environ.get("BC_AUTO_SIZE", "200x200").lower()
        width, height = [int(part) for part in size_text.split("x")]
        stroke = int(os.environ.get("BC_AUTO_STROKE", "120"))
        pressure = float(os.environ.get("BC_AUTO_PRESSURE", "1.0"))
        pressure2 = float(os.environ.get("BC_AUTO_PRESSURE2", pressure))
        out = os.environ.get("BC_AUTO_OUT")
        preset_file = os.environ.get("BC_AUTO_PRESET_FILE", "")
        preset_hint = os.environ.get("BC_AUTO_PRESET", "")
        started = time.time()

        log("BEGIN size=%dx%d stroke=%d pressure=%.3f pressure2=%.3f preset_file=%r hint=%r"
            % (width, height, stroke, pressure, pressure2, preset_file, preset_hint))
        doc = inst.createDocument(width, height, "bc_auto_render", "RGBA", "U8", "", 72.0)
        self.document = doc
        view = win.addView(doc)
        log("addView=%r activeView=%r" % (view, win.activeView()))
        if view is None:
            write_result({"ok": False, "stage": "view", "reason": "addView 返回空"})
            return

        # 透明画布：删掉默认白底图层，新建空 paintlayer，让 Alpha 通道真实反映笔迹
        for node in list(doc.topLevelNodes()):
            log("移除默认图层 name=%r type=%s" % (node.name(), node.type()))
            node.remove()
        layer = doc.createNode("bc_paint", "paintlayer")
        doc.rootNode().addChildNode(layer, None)
        doc.setActiveNode(layer)
        doc.refreshProjection()
        log("落笔前 alpha_nonzero=%d" % count_alpha_nonzero(doc.projection(0, 0, width, height)))

        presets = inst.resources("preset")
        preset_list = list(presets.values()) if isinstance(presets, dict) else list(presets)
        log("资源库预设数=%d" % len(preset_list))
        chosen = None
        for preset in preset_list:
            try:
                filename = os.path.basename(preset.filename() or "")
                name = preset.name() or ""
            except Exception:
                continue
            if preset_file and filename == preset_file:
                chosen = preset
                break
            if preset_hint and preset_hint in name:
                chosen = preset
                break
        if chosen is None:
            write_result({"ok": False, "stage": "preset",
                          "reason": "资源库里找不到预设 file=%r hint=%r（共 %d 个）" % (preset_file, preset_hint, len(preset_list))})
            return
        log("选用预设 name=%r filename=%r" % (chosen.name(), chosen.filename()))
        view.setCurrentBrushPreset(chosen)

        strokes_env = os.environ.get("BC_AUTO_STROKES", "")
        if strokes_env:
            strokes = json.loads(strokes_env)
        else:
            strokes = [{"pressure": pressure, "pressure2": pressure2,
                        "stroke": stroke, "y": height // 2}]
        drawn = []
        for st in strokes:
            sp = float(st.get("pressure", pressure))
            sp2 = float(st.get("pressure2", sp))
            ln = int(st.get("stroke", stroke))
            y0 = int(st.get("y", height // 2))
            x0 = max(1, (width - ln) // 2)
            layer.paintLine(QPoint(x0, y0), QPoint(min(width - 1, x0 + ln), y0), sp, sp2)
            drawn.append({"pressure": sp, "pressure2": sp2, "stroke": ln, "x0": x0, "y": y0})
            log("笔迹 pressure=%.3f..%.3f stroke=%d y=%d" % (sp, sp2, ln, y0))
        doc.refreshProjection()
        image = doc.projection(0, 0, width, height)
        out_dir = os.path.dirname(os.path.abspath(out))
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        saved = image.save(out, "PNG")
        alpha = count_alpha_nonzero(image)
        log("导出 saved=%r alpha_nonzero=%d" % (saved, alpha))
        write_result({
            "ok": bool(saved and alpha > 0),
            "stage": "done",
            "out_path": os.path.abspath(out),
            "width": width,
            "height": height,
            "alpha_nonzero": alpha,
            "preset": chosen.name(),
            "preset_file": os.path.basename(chosen.filename() or ""),
            "pressure": pressure,
            "pressure2": pressure2,
            "stroke": stroke,
            "strokes": drawn,
            "elapsed_s": round(time.time() - started, 3),
        })

    def _finish(self):
        QTimer.singleShot(2000, lambda: os._exit(0))
        try:
            if self.document is not None:
                self.document.setModified(False)
                self.document.close()
                log("文档已关闭（不保存）")
        except Exception as exc:
            log("关闭文档异常：%r" % (exc,))
        try:
            action = Krita.instance().action("file_quit")
            log("file_quit action=%r" % (action,))
            if action is not None:
                action.trigger()
        except Exception as exc:
            log("file_quit 异常：%r" % (exc,))
        log("插件结束")


log("插件模块加载，注册 Extension")
try:
    Krita.instance().addExtension(BCAutoRender(Krita.instance()))
    log("Extension 已注册")
except Exception as exc:
    log("注册 Extension 异常：%r" % (exc,))
    log(traceback.format_exc())
