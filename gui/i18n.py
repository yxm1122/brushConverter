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
        "warn_prefix": "⚠ 未映射参数：",
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
        "texture_hint_title": "纹理与混合模式提示",
        "texture_hint_body": "检测到 {n} 支笔刷使用了纹理。\\n\\n纹理「亮度/对比度」按 Photoshop 公式校准（v1.5.0 起为唯一公式，legacy 开关已删除），同引擎掩码空间差异 0.00%。v1.5.0 已落地：纹理缩放 >100% 改为 PS 同核预缩放内嵌（200% 由差异 93.72% 降到 0.00%；单预设体积 +0.8 KB~+434 KB）；对比度 >50 不再截断（C=84 写 6.25，Krita 滑块只显示 0..2 属预期）；最小深度曲线 0 端点改为比值；hardMix 满覆盖笔尖 3/3 = 0.00%。仍有两处登记残差：亮度越界区（对比度 −50 且亮度绝对值 >127，真实样本无命中）、hardMix base<1（10.0%–39.7%）。\\n\\n纹理混合模式：Photoshop「纹理 > 模式」下拉只有 10 项可选，本工具对这 10 项全部支持——8 项可映射、1 项需校准（hardMix，满覆盖 0.00%、边界残差 1.03%）、1 项不可映射（变暗 Drkn，PS 随深度换算子，按正片叠底近似并保留警告，不是等价转换）。其余 37 个是 PS 内部枚举值，界面里选不到、与用户无关，不计为转换限制。\\n\\nv1.5.0 已修掉的旧残差：硬度 h0/h50 结构性偏差 → 计算笔刷改按 PS 剖面生成 png_brush（过渡带 Δ0.858/0.058/0.485px）；散布 100%/300% 残差 39.7%/51.1% → 系数 ÷400 改 ÷200（a/D 相对差 0.65%/1.69%）；旋转 j<50 偏小约 2 倍 → RotationValue=min(1, j/50)；纹理缩放 200% 残差 32.4% → 预缩放后 0.00%。尺寸与角度不偏。\\n\\n仍存在的残差（如实披露）：间距非方形 100% 点的 3.53% 是少周期量测伪差（多周期复测 9/9 ≤2%、最大 0.73%）；硬度大直径（h≥95、直径 ≳420px 超 2px）、中间硬度/椭圆/小直径未量测；散布 1000% 端点、旋转 j>50 饱和未量测；b×C 交互区（multiply/subtract/linearBurn 76.7%、hardMix 57.6%）。\\n\\nv1.5.0 产物语义变化（旧 .kpp 必须重转）：计算笔刷 auto_brush → png_brush，Krita 里 fade/硬度不可再调、尺寸显示=PS 实际足印（PS 标称 200 → Krita 290/250/204）；散布数值 ×2；旋转抖动 j<50 ×2；纹理缩放 >100% 预缩放内嵌（资源名/哈希变化）。传感器写法与 v1.4.0 相同（合法 pressure 占位，禁止空 <params/>）。\\n\\n是否继续？",
        "converting": "正在转换…",
        "convert_failed": "转换失败",
        "done_status": "完成：{n} 支笔刷",
        "done_title": "转换完成",
        "done_body": "输出位置：\n{paths}",
        # v1.3.1 产物加固：摘要带真实纹理数、产物文件名与内容短哈希
        "done_summary": "预设 {n} 个（含真实纹理 {tex} 个）",
        "done_bundle": "资源包：{name}（短哈希 {hash}）",
        "renamed_notice": "产物已重命名以避免覆盖：{path}",
        "reused_notice": "已存在同内容产物，未重复写入：{path}",
        "verify_failed": "产物回读校验失败：{detail}",
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
        "warn_prefix": "⚠ Unmapped parameters: ",
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
        "texture_hint_title": "Texture & Blend Mode Notice",
        "texture_hint_body": "{n} brush(es) use a texture.\\n\\nThe texture Brightness/Contrast follows the Photoshop formula (the only formula since v1.5.0; the legacy switch has been removed); the difference in the same-engine mask space is 0.00%. Shipped in v1.5.0: texture scale >100% is pre-scaled with the PS-matched kernel and embedded (200% went from 93.72% difference to 0.00%; +0.8 KB to +434 KB per preset); contrast >50 is no longer truncated (C=84 writes 6.25; Krita shows only 0..2 on its slider, which is expected); the minimumDepth curve endpoint is now a ratio; hardMix on full-coverage tips is 3/3 = 0.00%. Two registered residuals remain: the brightness out-of-range region (contrast -50 with |brightness| > 127; no hits in real samples) and hardMix with base<1 (10.0%-39.7%).\\n\\nTexture blend modes: the Photoshop Texture > Mode dropdown offers only 10 selectable modes, and this tool handles all 10 - 8 map directly, 1 needs calibration (hardMix: 0.00% on full-coverage tips, 1.03% edge residual) and 1 cannot be mapped (Darken/Drkn, because PS switches operator with depth; approximated with Multiply and keeps a warning, not an equivalent conversion). The other 37 are internal Photoshop enum values, not selectable in the UI and irrelevant to users, so they are not counted as conversion limitations.\\n\\nResiduals fixed in v1.5.0: hardness h0/h50 structural bias -> computed brushes are now generated as png_brush from the PS profile (transition width deltas 0.858/0.058/0.485 px); scatter 100%/300% residuals 39.7%/51.1% -> coefficient changed from /400 to /200 (a/D relative difference 0.65%/1.69%); rotation j<50 was about 2x too small -> RotationValue=min(1, j/50); texture scale 200% residual 32.4% -> 0.00% after pre-scaling. Size and angle are unbiased.\\n\\nRemaining residuals (disclosed as measured): the 3.53% at the non-square 100% spacing point is a short-period measurement artifact (multi-period re-measurement: 9/9 <=2%, max 0.73%); large-diameter hardness (h>=95, diameter >~420px exceeds the 2px criterion), intermediate hardness/ellipse/small diameters are unmeasured; the scatter 1000% endpoint and rotation j>50 saturation are unmeasured; the b x C interaction region (multiply/subtract/linearBurn 76.7%, hardMix 57.6%).\\n\\nv1.5.0 output semantics changed (old .kpp files must be re-converted): computed brushes auto_brush -> png_brush, fade/hardness can no longer be adjusted in Krita, the displayed size equals the PS actual footprint (PS nominal 200 -> Krita 290/250/204); scatter values x2; rotation jitter j<50 x2; texture scale >100% is pre-scaled and embedded (resource name/hash change). Sensor XML is unchanged from v1.4.0 (valid pressure placeholder, empty <params/> forbidden).\\n\\nContinue?",
        "converting": "Converting…",
        "convert_failed": "Conversion Failed",
        "done_status": "Done: {n} brushes",
        "done_title": "Conversion Complete",
        "done_body": "Output location:\n{paths}",
        # v1.3.1 output hardening: real-texture count, bundle name and content hash
        "done_summary": "Presets: {n} (with real texture: {tex})",
        "done_bundle": "Bundle: {name} (short hash {hash})",
        "renamed_notice": "Output renamed to avoid overwriting: {path}",
        "reused_notice": "An identical output already exists; not rewritten: {path}",
        "verify_failed": "Output read-back verification failed: {detail}",
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
        inner = m.group(1).replace("回退Multiply", "not mapped to a Krita mode; falls back to Multiply")
        return f"Texture blend mode ({inner})"
    m = re.fullmatch(r"旋转控制源\(bVTy=(\d+) 未映射\)", warning)
    if m:
        return f"Rotation control source (bVTy={m.group(1)} not mapped)"
    return _WARNINGS_EN.get(warning, warning)
