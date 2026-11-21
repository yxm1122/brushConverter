# -*- coding: utf-8 -*-
"""PyKrita 启动插件 v3：路线 A（真实 Krita GUI 会话内自动落笔）。

行为：
1) 等主窗口出现（QTimer 轮询；启动阶段若出现 Sessions/异常对话框会自动关闭）；
2) 新建透明画布（移除默认白底背景层，新建 paintlayer），保证 Alpha 通道能真实反映落笔；
3) 从资源库按名字子串挑预设 → setCurrentBrushPreset → Node.paintLine；
4) 导出 PNG（投影图），统计 Alpha 非零像素并写结果 JSON；
5) 关闭文档（不保存）后退出；先排程 os._exit 兜底，避免「保存修改？」弹窗把进程挂住。

环境变量：BC_AUTO_LOG / BC_AUTO_OUT / BC_AUTO_PRESULT / BC_AUTO_PRESET / BC_AUTO_SIZE / BC_AUTO_STROKE
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
    """把结构化结果写到文件（GUI 进程 stdout 不可靠，统一走文件）。"""
    path = os.environ.get("BC_AUTO_PRESULT")
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
    except Exception as exc:
        log("写结果文件失败：%r" % (exc,))


def close_blocking_dialogs():
    """关闭会阻塞启动的对话框（Sessions 会话管理器 / Python 异常对话框）。"""
    from PyQt5.QtWidgets import QApplication

    closed = []
    for widget in QApplication.instance().topLevelWidgets():
        try:
            if not widget.isVisible():
                continue
            title = widget.windowTitle() or ""
            if title == "Sessions" or widget.__class__.__name__ == "ExceptHookDialog":
                widget.close()
                closed.append("%s:%s" % (widget.__class__.__name__, title))
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
        log("setup() 被调用")

    def createActions(self, window):
        log("createActions() 被调用")

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
        out = os.environ.get("BC_AUTO_OUT")
        preset_hint = os.environ.get("BC_AUTO_PRESET", "")
        started = time.time()

        log("BEGIN size=%dx%d stroke=%d out=%r preset_hint=%r" % (width, height, stroke, out, preset_hint))
        doc = inst.createDocument(width, height, "bc_auto_render", "RGBA", "U8", "", 72.0)
        self.document = doc
        log("createDocument=%r" % (doc,))
        view = win.addView(doc)
        log("addView=%r activeView=%r" % (view, win.activeView()))
        if view is None:
            write_result({"ok": False, "stage": "view", "reason": "addView 返回空"})
            return

        # 新建透明画布：删掉默认白底背景层，换成空 paintlayer（Alpha 才能反映笔迹）
        for node in list(doc.topLevelNodes()):
            log("移除默认图层 name=%r type=%s" % (node.name(), node.type()))
            node.remove()
        layer = doc.createNode("bc_paint", "paintlayer")
        doc.rootNode().addChildNode(layer, None)
        doc.setActiveNode(layer)
        doc.refreshProjection()
        base_alpha = count_alpha_nonzero(doc.projection(0, 0, width, height))
        log("落笔前 alpha_nonzero=%d（应为 0）" % base_alpha)

        presets = inst.resources("preset")
        preset_list = list(presets.values()) if isinstance(presets, dict) else list(presets)
        log("资源库预设数=%d" % len(preset_list))
        dump_path = os.environ.get("BC_AUTO_PRESET_DUMP")
        if dump_path:
            rows = []
            for preset in preset_list:
                try:
                    rows.append({"name": preset.name(), "filename": preset.filename()})
                except Exception as exc:
                    rows.append({"error": repr(exc)})
            try:
                with open(dump_path, "w", encoding="utf-8") as fh:
                    json.dump(rows, fh, ensure_ascii=False, indent=1)
                log("预设清单已写 %s（%d 条）" % (dump_path, len(rows)))
            except Exception as exc:
                log("写预设清单失败：%r" % (exc,))
        chosen = None
        for preset in preset_list:
            try:
                name = preset.name() or ""
            except Exception:
                continue
            if preset_hint and preset_hint in name:
                chosen = preset
                break
        if chosen is None:
            write_result({"ok": False, "stage": "preset", "reason": "资源库里找不到名字含 %r 的预设（共 %d 个）" % (preset_hint, len(preset_list))})
            return
        log("选用预设=%r" % (chosen.name(),))
        view.setCurrentBrushPreset(chosen)

        y0 = height // 2
        x0 = max(1, (width - stroke) // 2)
        layer.paintLine(QPoint(x0, y0), QPoint(min(width - 1, x0 + stroke), y0), 1.0, 1.0)
        log("paintLine 已调用")
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
            "elapsed_s": round(time.time() - started, 3),
        })

    def _finish(self):
        # 先排程强制退出兜底，再触发正常退出，避免「保存修改」对话框阻塞
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
