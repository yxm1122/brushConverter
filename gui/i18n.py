"""GUI 多语言支持（中文 / English）。

轻量方案：不引入 Qt 翻译文件，使用键值字典 + 运行时重绘。
当前语言保存在模块级，切换语言后由 MainWindow 重建界面文本。
"""

from __future__ import annotations

import re

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
        "select_out_dir": "选择输出目录",
        "hint": "提示",
        "need_select": "请至少勾选一支笔刷。",
        "unmapped_title": "无法映射的参数",
        "unmapped_body": "以下笔刷包含无法映射的参数，转换后将丢失这些效果：\n\n{lines}\n\n是否继续？",
        "texture_hint_title": "纹理亮度/对比度提示",
        "texture_hint_body": "检测到 {n} 支笔刷使用了纹理。\n\n当前纹理「亮度/对比度」为经验映射，可能不够准确，转换后请在 Krita 的纹理选项中手动微调亮度与对比度。\n\n是否继续？",
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
        "select_out_dir": "Select output directory",
        "hint": "Notice",
        "need_select": "Please select at least one brush.",
        "unmapped_title": "Unmapped Parameters",
        "unmapped_body": "These brushes contain parameters that cannot be mapped and will be lost after conversion:\n\n{lines}\n\nContinue?",
        "texture_hint_title": "Texture Brightness/Contrast Notice",
        "texture_hint_body": "{n} brush(es) use a texture.\n\nThe texture \"Brightness/Contrast\" mapping is approximate and may not be accurate. After conversion, please fine-tune brightness and contrast in the Krita texture options.\n\nContinue?",
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
            return text
    return text


def tr_warning(warning: str) -> str:
    """把 mapping.py 产生的中文警告翻译为英文（中文界面原样返回）。"""
    if _LANG == "zh":
        return warning
    m = re.match(r"纹理混合模式\((.*)\)", warning)
    if m:
        inner = m.group(1).replace("回退Multiply", "falls back to Multiply")
        return f"Texture blend mode ({inner})"
    m = re.match(r"旋转控制源\(bVTy=(\d+) 未映射\)", warning)
    if m:
        return f"Rotation control source (bVTy={m.group(1)} not mapped)"
    return _WARNINGS_EN.get(warning, warning)
