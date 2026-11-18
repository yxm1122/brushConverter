# -*- coding: utf-8 -*-
"""Krita 侧单笔渲染脚本（任务 06）。

本文件由 Krita 内置 Python 3.13 执行（经 kritarunner.exe），**不能用 conda 的 python 直接跑**。

【契约】
    kritarunner.exe tools/krita_render.py --preset <kpp> --size 200x200 \
        --stroke 120 --pressure 1.0 --out <png>

【实测到的三条 kritarunner 限制（任务 04 证据）】
1) kritarunner 把脚本当模块加载（`__name__` = 模块名，不是 `__main__`），脚本必须写在模块级执行；
   并且 **位置参数不会传进 sys.argv**（实测 sys.argv == ['']）。
   → conda 侧外壳用环境变量 `KRITA_RENDER_ARGS`（JSON 数组）传参，本脚本两种来源都读。
2) kritarunner **不转发脚本退出码**：`sys.exit(2)` 与未捕获异常都返回退出码 0（实测 probe14/probe15）。
   → 本脚本必须在 stdout 打印机器可读的结果标记 `KRITA_RENDER_RESULT {json}`，
     外壳据「结果标记 + PNG 是否存在」判成败，不看退出码。
3) 任务 04 门结论：kritarunner 里 `windows=0 / activeWindow=None`，建立视图（`addView`）必崩 0xC0000005，
   而 `Node.paintLine()` 在源码里硬依赖「当前主窗口→当前视图」。
   → 在无视图环境里本脚本**如实返回 ok=false**（不主动触发崩溃，崩溃复现见 tools/krita_probe/probe7、probe11）；
     将来在 Krita GUI 的 Scripter 里执行（有视图）时，脚本会走真正的落笔+导出路径。
"""

import json
import os
import sys
import time

RESULT_PREFIX = "KRITA_RENDER_RESULT "


def emit(ok, **fields):
    """打印机器可读的结果标记（外壳只认这一行）。

    ensure_ascii=True：中文原因写成 JSON 的 Unicode 转义形式，避免 kritarunner 代码页导致的乱码，
    外壳 json.loads 后仍是正确中文。
    """
    payload = {"ok": bool(ok)}
    payload.update(fields)
    payload["elapsed_s"] = round(time.time() - START_TIME, 3)
    sys.stdout.write(RESULT_PREFIX + json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()
    return 0 if ok else 1


def print_usage():
    sys.stdout.write(
        "用法: kritarunner.exe tools/krita_render.py --preset <kpp 路径> "
        "--size 200x200 --stroke 120 --pressure 1.0 --out <png 路径>\n"
        "说明: 全部参数必填；kritarunner 不转发命令行参数，外壳请用环境变量 "
        "KRITA_RENDER_ARGS（JSON 数组）传参。\n"
    )
    sys.stdout.flush()


START_TIME = time.time()


def _argv_from_env():
    """从 KRITA_RENDER_ARGS 读参数（外壳用），返回 list 或 None。"""
    raw = os.environ.get("KRITA_RENDER_ARGS")
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except Exception:
        return None
    return value if isinstance(value, list) else None


def parse_args(argv):
    """极简参数解析：返回 dict；缺参数返回 None（同时打印用法）。"""
    if not argv:
        return None
    args = {}
    i = 0
    while i < len(argv):
        key = argv[i]
        if key not in ("--preset", "--size", "--stroke", "--pressure", "--out"):
            return None
        if i + 1 >= len(argv):
            return None
        args[key[2:]] = argv[i + 1]
        i += 2
    for required in ("preset", "size", "stroke", "pressure", "out"):
        if required not in args:
            return None
    return args


def parse_size(text):
    """把 '200x200' 解析成 (宽, 高)；非法返回 None。"""
    parts = str(text).lower().split("x")
    if len(parts) != 2:
        return None
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def find_preset(inst, preset_path):
    """在 Krita 资源库里按名字查找预设。

    注意（任务 04 证据）：Krita 的 Python API 没有「按 .kpp 文件路径构造 Resource」的入口，
    Resource 只能来自资源库（`Resource::resource()` 走 KisResourceLocator::resourceForId）。
    所以这里按「文件名主干」在已加载的预设里匹配；匹配不到就如实失败。
    """
    stem = os.path.splitext(os.path.basename(preset_path))[0]
    for res in inst.resources("preset"):
        try:
            name = res.name() or ""
            filename = os.path.basename(res.filename() or "")
            if stem in name or stem in filename:
                return res
        except Exception:
            continue
    return None


def count_alpha_nonzero(image):
    """统计 QImage 里 Alpha > 0 的像素数（兼容不同像素格式：先转 ARGB32）。"""
    from PyQt5 import QtGui

    img = image.convertToFormat(QtGui.QImage.Format_ARGB32)
    width, height = img.width(), img.height()
    total = 0
    for y in range(height):
        line = img.scanLine(y)
        data = bytes(line.asarray(width * 4))
        for x in range(3, len(data), 4):
            if data[x] > 0:
                total += 1
    return total


def main(argv):
    args = parse_args(argv)
    if args is None:
        print_usage()
        emit(False, stage="args", reason="参数缺失或非法：--preset/--size/--stroke/--pressure/--out 全部必填")
        return

    preset_path = args["preset"]
    out_path = args["out"]
    size = parse_size(args["size"])
    if size is None:
        emit(False, stage="args", reason="--size 必须形如 200x200，实际=%r" % (args["size"],))
        return
    try:
        stroke = int(args["stroke"])
        pressure = float(args["pressure"])
    except ValueError:
        emit(False, stage="args", reason="--stroke 必须是整数、--pressure 必须是小数")
        return
    if not os.path.isfile(preset_path):
        emit(False, stage="preset", reason="预设文件不存在：%s" % preset_path)
        return

    import krita
    from PyQt5 import QtCore

    inst = krita.Krita.instance()
    if inst is None:
        emit(False, stage="instance", reason="Krita.instance() 为空，进程不是 Krita 内置 Python")
        return

    # 先建文档：这一步在 kritarunner 里实测可用（任务 04 probe_env）。
    document = inst.createDocument(size[0], size[1], "krita_render", "RGBA", "U8", "", 72.0)
    if document is None:
        emit(False, stage="document", reason="createDocument 返回空")
        return

    windows = len(inst.windows())
    active_window = inst.activeWindow()
    if windows == 0 or active_window is None:
        # 任务 04 门结论：无视图 → 落笔接口必崩（addView 0xC0000005）。这里如实失败，不主动触发崩溃。
        emit(
            False,
            stage="view",
            reason=(
                "无主窗口/视图：任务04结论 kritarunner 无法无头落笔"
                "（Window.addView 触发 0xC0000005 访问违例，Node.paintLine 依赖 activeView）"
            ),
            windows=windows,
            documents=len(inst.documents()),
        )
        return

    # 以下路径只在 Krita GUI（Scripter / 自动化插件）里才会走到：此时有视图，可以真正落笔。
    view = active_window.activeView() or (active_window.views()[0] if active_window.views() else None)
    if view is None:
        emit(False, stage="view", reason="窗口存在但没有视图（views 为空）")
        return

    preset = find_preset(inst, preset_path)
    if preset is None:
        emit(
            False,
            stage="preset",
            reason=(
                "资源库里找不到该预设：%s（Krita Python API 不支持按 .kpp 路径加载，"
                "需先把 .kpp 装进资源目录）" % os.path.basename(preset_path)
            ),
        )
        return

    view.setCurrentBrushPreset(preset)
    node = document.topLevelNodes()[0] if document.topLevelNodes() else None
    if node is None:
        emit(False, stage="node", reason="文档里没有可绘制的图层")
        return

    x0 = max(1, (size[0] - stroke) // 2)
    y0 = size[1] // 2
    node.paintLine(
        QtCore.QPoint(x0, y0),
        QtCore.QPoint(min(size[0] - 1, x0 + stroke), y0),
        pressure,
        pressure,
    )
    document.refreshProjection()

    image = document.projection(0, 0, size[0], size[1])
    if image is None or image.isNull():
        emit(False, stage="export", reason="projection() 返回空图")
        return
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    if not image.save(out_path, "PNG"):
        emit(False, stage="export", reason="QImage.save 写 PNG 失败：%s" % out_path)
        return

    alpha_nonzero = count_alpha_nonzero(image)
    emit(
        True,
        stage="done",
        out_path=os.path.abspath(out_path),
        width=size[0],
        height=size[1],
        alpha_nonzero=alpha_nonzero,
        preset=preset.name(),
    )


# kritarunner 把本文件当模块执行（`__name__` != "__main__"），因此这里必须在模块级调用。
_argv = _argv_from_env()
if _argv is None:
    _argv = sys.argv[1:]
main(_argv)
