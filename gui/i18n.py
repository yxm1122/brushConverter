"""GUI 多语言支持（中文 / English）。

轻量方案：不引入 Qt 翻译文件，使用键值字典 + 运行时重绘。
当前语言保存在模块级，切换语言后由 MainWindow 重建界面文本。
"""

from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

_LANG = "zh"

_STRINGS: dict[str, dict[str, str]] = {
    "zh": {
        "window_title": "brushConverter — ABR → Krita 笔刷转换",
        "open_abr": "打开 ABR 文件…",
        "no_file": "未选择文件",
        "brush_list": "笔刷预览（勾选要转换的笔刷）",
        "select_all": "全选",
        "select_none": "全不选",
        "count": "共 {total} 支 · 已选 {sel} 支",
        "output": "输出",
        "format": "产物格式：",
        "kpp": ".kpp（每支一个文件）",
        "bundle": ".bundle（Krita 资源包）",
        "out_dir": "输出目录：",
        "browse": "浏览…",
        "convert": "开始转换",
        "computed_brush": "计算笔刷",
        "sampled_brush": "采样笔刷",
        "diameter": "直径 {d}px",
        "warn_prefix": "⚠ 未映射：",
        "list_sep": "、",
        "colon": "：",
        "select_abr": "选择 ABR 文件",
        "abr_filter": "Photoshop 笔刷 (*.abr);;所有文件 (*)",
        "parsing": "正在解析…",
        "parse_failed": "解析失败",
        "loaded": "已加载 {n} 支笔刷",
        "no_brushes_title": "没有读到笔刷",
        "no_brushes_body": (
            "文件解析成功，但没有找到任何笔刷，因此无法转换。\n\n"
            "文件版本：{version}\n{detail}\n\n"
            "这通常说明该文件是不受支持的 ABR 格式变体。"
            "你可以把该 .abr 反馈到项目的 GitHub issue，以便适配。"),
        "no_brushes_no_detail": "（无更多结构信息）",
        "select_out_dir": "选择输出目录",
        "hint": "提示",
        "need_select": "请至少勾选一支笔刷。",
        "unmapped_title": "无法映射的参数",
        "unmapped_body": "以下笔刷包含无法映射的参数，转换后将丢失这些效果：\n\n{lines}\n\n是否继续？",
        "texture_hint_title": "纹理亮度/对比度提示",
        "texture_hint_body": "检测到 {n} 支笔刷使用了纹理。\n\n纹理「亮度/对比度」已按 Photoshop 公式校准（calibrated 方案，v1.2.0 起为默认），同引擎掩码空间差异 0.00%。仍有两处结构性限制无法精确表达：PS 对比度 > 50（Krita 上限为 2）、PS 深度 > 83%（Krita 纹理强度上限 1.0）。如需 v1.1.2 的旧行为，请在启动本程序前设置环境变量 BRUSHCONVERTER_TEXTURE_FORMULA=legacy。\n\n是否继续？",
        "converting": "正在转换…",
        "convert_failed": "转换失败",
        "done_status": "完成：{n} 支笔刷",
        "done_title": "转换完成",
        "done_body": "成功转换 {n} 支笔刷。\n\n输出位置：\n{paths}",
    },
    "en": {
        "window_title": "brushConverter — ABR → Krita Brush Converter",
        "open_abr": "Open ABR File…",
        "no_file": "No file selected",
        "brush_list": "Brush preview (check brushes to convert)",
        "select_all": "Select All",
        "select_none": "Select None",
        "count": "{total} brushes · {sel} selected",
        "output": "Output",
        "format": "Output format:",
        "kpp": ".kpp (one file per brush)",
        "bundle": ".bundle (Krita resource pack)",
        "out_dir": "Output directory:",
        "browse": "Browse…",
        "convert": "Start Conversion",
        "computed_brush": "Computed brush",
        "sampled_brush": "Sampled brush",
        "diameter": "Diameter {d}px",
        "warn_prefix": "⚠ Not mapped: ",
        "list_sep": ", ",
        "colon": ": ",
        "select_abr": "Select ABR file",
        "abr_filter": "Photoshop Brushes (*.abr);;All Files (*)",
        "parsing": "Parsing…",
        "parse_failed": "Parse Failed",
        "loaded": "Loaded {n} brushes",
        "no_brushes_title": "No Brushes Found",
        "no_brushes_body": (
            "The file parsed successfully but contains no brushes, so it cannot be converted.\n\n"
            "File version: {version}\n{detail}\n\n"
            "This usually means the file is an unsupported ABR variant. "
            "Please report the .abr on the project's GitHub issue so support can be added."),
        "no_brushes_no_detail": "(no further structural details)",
        "select_out_dir": "Select output directory",
        "hint": "Notice",
        "need_select": "Please select at least one brush.",
        "unmapped_title": "Unmapped Parameters",
        "unmapped_body": "These brushes contain parameters that cannot be mapped and will be lost after conversion:\n\n{lines}\n\nContinue?",
        "texture_hint_title": "Texture Brightness/Contrast Notice",
        "texture_hint_body": "{n} brush(es) use a texture.\n\nThe texture \"Brightness/Contrast\" is calibrated to the Photoshop formula (the \"calibrated\" scheme, default since v1.2.0); the difference in the same-engine mask space is 0.00%. Two structural limits cannot be expressed exactly: PS contrast > 50 (Krita's maximum is 2) and PS depth > 83% (Krita texture strength caps at 1.0). For the old v1.1.2 behaviour, set the environment variable BRUSHCONVERTER_TEXTURE_FORMULA=legacy before starting the app.\n\nContinue?",
        "converting": "Converting…",
        "convert_failed": "Conversion Failed",
        "done_status": "Done: {n} brushes",
        "done_title": "Conversion Complete",
        "done_body": "Successfully converted {n} brushes.\n\nOutput location:\n{paths}",
    },
}

# 未映射警告来自 mapping.py，中文固定文案；英文界面下显示时翻译。
_WARNINGS_EN: dict[str, str] = {
    "颜色动态": "Color dynamics",
    "湿边": "Wet edges",
    "喷嘴": "Airbrush / nozzle",
    "笔刷姿态": "Brush pose",
    "双笔刷": "Dual brush",
    "纹理(图案缺失)": "Texture (pattern missing)",
    "纹理(protectTexture 未映射)": "Texture (protectTexture not mapped)",
}


def set_language(lang: str) -> None:
    """切换界面语言（'zh' / 'en'），非法值回退中文。"""
    global _LANG
    _LANG = lang if lang in _STRINGS else "zh"


def get_language() -> str:
    return _LANG


def tr(key: str, **kwargs) -> str:
    """取当前语言的字符串，支持 {name} 占位符。"""
    table = _STRINGS.get(_LANG, _STRINGS["zh"])
    text = table.get(key, _STRINGS["zh"].get(key, key))
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            # 静默回退会让界面直接显示 "{n}" 之类的占位符且毫无线索；
            # 保留一条日志，开发/打包环境都能定位到坏掉的模板。
            _log.warning("i18n 占位符不匹配: lang=%s key=%s kwargs=%s",
                         _LANG, key, sorted(kwargs))
            return text
    return text


def tr_warning(warning: str) -> str:
    """把 mapping.py 产生的中文警告翻译为英文（中文界面原样返回）。"""
    if _LANG == "zh":
        return warning
    # fullmatch：避免把「纹理混合模式(...) 其它后缀」这样的字符串也吞掉
    m = re.fullmatch(r"纹理混合模式\((.*)\)", warning)
    if m:
        inner = m.group(1).replace("回退Multiply", "falls back to Multiply")
        return f"Texture blend mode ({inner})"
    m = re.fullmatch(r"旋转控制源\(bVTy=(\d+) 未映射\)", warning)
    if m:
        return f"Rotation control source (bVTy={m.group(1)} not mapped)"
    return _WARNINGS_EN.get(warning, warning)
