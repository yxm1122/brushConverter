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
        "texture_hint_body": "检测到 {n} 支笔刷使用了纹理。\\n\\n纹理「亮度/对比度」按 Photoshop 公式校准（v1.5.0 起为唯一公式，legacy 开关已删除），同引擎掩码空间差异 0.00%。v1.5.0 已落地：纹理缩放 >100% 改为 PS 同核预缩放内嵌（200% 由差异 93.72% 降到 0.00%；单预设体积 +0.8 KB~+434 KB）；对比度 >50 不再截断（C=84 写 6.25，Krita 滑块只显示 0..2 属预期）；最小深度曲线 0 端点改为比值；hardMix 满覆盖笔尖 3/3 = 0.00%。仍有登记残差：hardMix base<1（现行 10.65%–96.81%，结构性）；亮度越界区已改为预烘焙（v1.6.0 候选：12/12 = 0.000%，但命中区纹理亮度/对比度/反相失去 PS 语义、旧 .kpp 必重转）。\\n\\n纹理混合模式：Photoshop「纹理 > 模式」下拉只有 10 项可选，本工具对这 10 项全部支持——8 项可映射、1 项需校准（hardMix，满覆盖与压感 0.00%、b×C 双非零真残差 56.87%/38.50%（修法候选 0.000% 未落地））、1 项不可映射（变暗 Drkn，PS 随深度换算子，按正片叠底近似并保留警告，不是等价转换）。其余 37 个是 PS 内部枚举值，界面里选不到、与用户无关，不计为转换限制。\\n\\nv1.5.0 已修掉的旧残差：硬度 h0/h50 结构性偏差 → 计算笔刷改按 PS 剖面生成 png_brush（过渡带 Δ0.858/0.058/0.485px）；散布 100%/300% 残差 39.7%/51.1% → 系数 ÷400 改 ÷200（a/D 相对差 0.65%/1.69%）；旋转 j<50 旧产物偏小约 2 倍 → RotationValue=min(1, j/50)（v1.5.0 在双随机源下使幅度约为 PS 的 2 倍，v1.6.0 候选改单 fuzzy 源修正）；纹理缩放 200% 残差 32.4% → 预缩放后 0.00%。尺寸与角度不偏。\\n\\n仍存在的残差（如实披露）：间距非方形 100% 点的 3.53% 是少周期量测伪差（多周期复测 9/9 ≤2%、最大 0.73%）；大直径硬边（h=100、直径 ≳453px 超 2px，最坏 +4.78px）、中间硬度 h25/h75 过渡带偏宽（+19.9/+8.2px）；散布 1000% 端点生产实现 18.21% 超阈；旋转 j≥50 跨度已饱和、分布形状不等价（整圈均匀 vs PS 环绕）；b×C 交互区 9/10 模式 0.000%，仅 hardMix 真残差 56.87%/38.50%。\\n\\n产物语义变化（v1.5.0 与 v1.6.0 候选；旧 .kpp 必须重转）：计算笔刷 auto_brush → png_brush，Krita 里 fade/硬度不可再调、尺寸显示=PS 实际足印（PS 标称 200 → Krita 290/250/204）；散布数值 ×2；旋转抖动 j<50 旧产物幅度约为 PS 的 2 倍（v1.6.0 候选修正）；纹理缩放 >100% 预缩放内嵌（资源名/哈希变化）。传感器写法与 v1.4.0 相同（合法 pressure 占位，禁止空 <params/>）。\\n\\n是否继续？",
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
        "texture_hint_body": "{n} brush(es) use a texture.\\n\\nThe texture Brightness/Contrast follows the Photoshop formula (the only formula since v1.5.0; the legacy switch has been removed); the difference in the same-engine mask space is 0.00%. Shipped in v1.5.0: texture scale >100% is pre-scaled with the PS-matched kernel and embedded (200% went from 93.72% difference to 0.00%; +0.8 KB to +434 KB per preset); contrast >50 is no longer truncated (C=84 writes 6.25; Krita shows only 0..2 on its slider, which is expected); the minimumDepth curve endpoint is now a ratio; hardMix on full-coverage tips is 3/3 = 0.00%, and 0.00% under pressure too. New in the v1.6.0 candidate: the brightness out-of-range region (contrast -50 with |brightness| > 127) is now pre-baked (12/12 = 0.000%), but the texture brightness/contrast/invert fields lose their PS semantics in the hit region and old .kpp files must be re-converted. Structural residual still registered: hardMix with base<1 (10.65%-96.81%).\\n\\nTexture blend modes: the Photoshop Texture > Mode dropdown offers only 10 selectable modes, and this tool handles all 10 - 8 map directly, 1 needs calibration (hardMix: 0.00% on full-coverage tips and under pressure; the only true b x C residual is 56.87%/38.50% (fix candidate 0.000%, not landed)) and 1 cannot be mapped (Darken/Drkn, because PS switches operator with depth; approximated with Multiply and keeps a warning, not an equivalent conversion). The other 37 are internal Photoshop enum values, not selectable in the UI and irrelevant to users, so they are not counted as conversion limitations.\\n\\nResiduals fixed in v1.5.0: hardness h0/h50 structural bias -> computed brushes are now generated as png_brush from the PS profile (transition width deltas 0.858/0.058/0.485 px); scatter 100%/300% residuals 39.7%/51.1% -> coefficient changed from /400 to /200 (a/D relative difference 0.65%/1.69%); rotation j<50 -> RotationValue=min(1, j/50) (with the old double random source this made the amplitude about 2x the PS scale; fixed in the v1.6.0 candidate by keeping a single fuzzy source); texture scale 200% residual 32.4% -> 0.00% after pre-scaling. Size and angle are unbiased.\\n\\nRemaining residuals (disclosed as measured): the 3.53% at the non-square 100% spacing point is a short-period measurement artifact (multi-period re-measurement: 9/9 <=2%, max 0.73%); large-diameter hard edges (h=100, diameter >~453px exceeds the 2px criterion, worst +4.78px) and intermediate hardness h25/h75 transition widths (+19.9/+8.2px); the scatter 1000% endpoint is 18.21% over the criterion in the production path; rotation j>=50 is saturated in span but its distribution shape is not equivalent (uniform full circle vs PS wrap-around); in the b x C interaction region 9/10 modes are 0.000% and only hardMix has a true residual of 56.87%/38.50%.\\n\\nOutput semantics changed (v1.5.0 and the v1.6.0 candidate; old .kpp files must be re-converted): computed brushes auto_brush -> png_brush, fade/hardness can no longer be adjusted in Krita, the displayed size equals the PS actual footprint (PS nominal 200 -> Krita 290/250/204); scatter values x2; rotation jitter j<50 in old outputs was about 2x the PS scale (fixed in the v1.6.0 candidate); texture scale >100% is pre-scaled and embedded (resource name/hash change). Sensor XML is unchanged from v1.4.0 (valid pressure placeholder, empty <params/> forbidden).\\n\\nContinue?",
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

# 170逐支控件：两种语言共用同一组键，数值判断仍来自转换器。
_STRINGS["zh"].update({
    "file_section": "文件",
    "reload": "重新载入",
    "processing": "逐支处理与编辑能力",
    "process_help": "点击笔刷行或“查看/调整”编辑三项处理；列表复选框只决定是否导出。",
    "no_current": "请选择一支可见笔刷查看处理说明。",
    "reset_notice": "适用项默认开启；换文件或重新载入会恢复条件默认，不记忆手动开关。",
    "search": "搜索名称、稳定 ID 或源序号…",
    "list_empty": "打开 ABR 文件，即可预览并选择要转换的笔刷。",
    "list_no_matches": "没有符合当前筛选的笔刷。可清空搜索或更换筛选；隐藏的已选笔刷仍会导出。",
    "filter_label": "筛选：",
    "sort_label": "排序：",
    "filter_all": "全部笔刷",
    "filter_selected": "仅已选",
    "filter_computed": "计算笔刷",
    "filter_sampled": "采样笔刷",
    "filter_affected": "有适用处理",
    "sort_source": "源顺序",
    "sort_name_asc": "名称升序",
    "sort_name_desc": "名称降序",
    "select_all": "全选可见项",
    "select_none": "取消可见项选择",
    "count": "共 {total} 支 · 可见 {visible} 支 · 总已选 {sel} 支 · 隐藏已选 {hidden} 支",
    "edit_brush": "查看/调整",
    "brush_identity": "源序号 {ordinal} · {id}",
    "current_selected": "本支已选中导出（即使隐藏也会导出）",
    "current_unselected": "本支未选中，不会导出；处理开关可先调整。",
    "switch_rasterize_computed": "计算笔刷位图化",
    "switch_prescale_texture": "纹理预缩放",
    "switch_bake_texture_bc": "亮度越界预烘焙",
    "use_processing": "采用该处理（外观优先）",
    "applicable": "适用",
    "not_applicable": "不适用（不可开启）",
    "switch_on": "开启",
    "switch_off": "关闭／原生替代",
    "limited": "受限",
    "switch_status": "{applicability} · 当前{enabled} · 条件默认{default}",
    "choice_consequences": "开启：{on}\n\n关闭：{off}",
    "expected_state": "预计：{state}",
    "actual_applied": "执行外观优先处理",
    "actual_alternative": "使用原生可编辑替代",
    "actual_not_applicable": "不执行此项（不适用）",
    "actual_not_processed": "未处理",
    "effect_rasterize_computed_on": "按 PS 硬度剖面生成位图；Krita 的 fade/硬度不能再调，尺寸显示为 PS 实际足印。",
    "effect_rasterize_computed_off": "保留原生 auto_brush，直径/间距/角度/圆度/硬度可调（受限）。Krita fade 仅在 1.00R 内，PS 软边会伸出标称半径；既有 h0/h50 过渡带差为 −19.81%/+22.63%。尺寸为标称直径（当前硬度 {hardness}%），不再下发 hardMix 满覆盖偏置。",
    "effect_prescale_texture_on": "把预缩放后的图案内嵌，导出 Scale=1；源图案尺寸/缩放基准被改写，之后调节 Scale 不再沿用源图案基准。",
    "effect_prescale_texture_off": "保留源纹理尺寸和原生 Scale={scale}。代价：Krita Qt 与 PS 重采样不同，150%/200% 既有相对残差约 22%/32%，外观可能不同。{scale_note}",
    "scale_limit_note": "受限：Scale 超过 10，目标 Krita 是否截断尚待实机确认。",
    "effect_bake_texture_bc_on": "把亮度/对比度/反相烘进纹理像素，导出中性字段；这些字段不再具有源 PS 参数语义。",
    "effect_bake_texture_bc_off": "保留 Krita 原生亮度/对比度/反相字段（受限）；亮度反解 {raw:.4f} → 按 [-1,1] 截断写入 {written:.4f}。PS 越界区不能完整表达：既有 12 组中 8 组超阈，最大残差 61.125%/max23；不烘焙、不换另一条烘焙链。",
    "skipped_tip": "缺少采样笔尖位图：预计跳过整支，三项处理均不计入预计命中。",
    "export_title": "导出前逐支确认",
    "export_intro": "最终选中 {n} 支（含隐藏已选 {hidden} 支）。以下是预计处理清单，不是成功记录；实际结果以本次运行记录为准。",
    "export_counts": "预计执行 {applied} 项 · 原生替代 {alternative} 项（受限 {limited} 项）· 预计跳过 {skipped} 支",
    "export_target": "输出格式 {format} · 目录 {directory}",
    "export_caution": "关闭处理可保留部分编辑能力，但不保证 Photoshop 外观一致；既有残差不在本轮校准。请在目标 Krita 中确认实际效果。",
    "confirm_export": "确认并导出",
    "cancel_export": "返回调整",
    "forecast_brush": "[{ordinal}] {name} · {id}",
    "forecast_switch": "  {title}：{state}；{reason}",
    "missing_digest": "解析未返回源字节摘要，不能安全建立逐支身份，请重新载入。",
    "failure_phase": "失败阶段（技术代码）：{phase}",
    "failure_status": "交付状态：{status}",
    "retained_files": "保留现场（请核对路径，不自动清理）：",
    "blocked_files": "受阻项目（保留原始字段与原因）：",
    "rolled_back_files": "已按清单回滚：",
    "transaction_failed": "写出失败，请核对保留清单",
    "transaction_rolled_back": "写出失败，已按清单回滚",
    "transaction_partial_blocked": "部分受阻：有现场保留，不能宣称全有或全无",
    "transaction_cancelled": "已取消，未交付本批文件",
    "raw_details": "原始技术详情（保留原始输出语言）：",
    "run_heading": "本次运行记录（处理过不等于已交付）",
    "run_missing": "该支未产生处理记录（跳过或未处理），不计为命中。",
    "result_committed": "本批已完整交付",
    "result_reused_only": "本批全部复用同内容文件（未重写）",
    "result_empty": "未选择笔刷：未写出文件",
    "result_all_skipped": "全部跳过：未写出文件",
    "result_cancelled": "已取消：本批未交付文件",
    "result_unknown": "未确认交付，请核对原始详情",
    "result_detail": "跳过 {skipped} 支 · 未处理 {not_processed} 支",
    "busy_close": "后台任务尚未结束，请等本批结束后再关闭窗口。"
})
_STRINGS["en"].update({
    "file_section": "File",
    "reload": "Reload",
    "processing": "Per-brush processing & editability",
    "process_help": "Click a brush row or View / Adjust for its three processing options; list checkboxes control export selection only.",
    "no_current": "Choose a visible brush to inspect its processing options.",
    "reset_notice": "Applicable options start enabled. Opening or reloading a file resets conditional defaults; manual switches are not remembered.",
    "search": "Search name, stable ID or source ordinal…",
    "list_empty": "Open an ABR file to preview and select brushes for conversion.",
    "list_no_matches": "No brushes match these filters. Clear the search or change the filter; hidden selected brushes are still exported.",
    "filter_label": "Filter:",
    "sort_label": "Sort:",
    "filter_all": "All brushes",
    "filter_selected": "Selected only",
    "filter_computed": "Computed brushes",
    "filter_sampled": "Sampled brushes",
    "filter_affected": "Has applicable processing",
    "sort_source": "Source order",
    "sort_name_asc": "Name ascending",
    "sort_name_desc": "Name descending",
    "select_all": "Select visible",
    "select_none": "Deselect visible",
    "count": "Total {total} · Visible {visible} · Selected {sel} · Hidden selected {hidden}",
    "edit_brush": "View / Adjust",
    "brush_identity": "Source #{ordinal} · {id}",
    "current_selected": "Selected for export (including when hidden)",
    "current_unselected": "Not selected; will not be exported. Processing options can still be adjusted.",
    "switch_rasterize_computed": "Rasterize computed brush",
    "switch_prescale_texture": "Pre-scale texture",
    "switch_bake_texture_bc": "Bake out-of-range brightness",
    "use_processing": "Apply this processing (appearance first)",
    "applicable": "Applicable",
    "not_applicable": "Not applicable (cannot enable)",
    "switch_on": "enabled",
    "switch_off": "disabled / native alternative",
    "limited": "limited",
    "switch_status": "{applicability} · Now {enabled} · Conditional default {default}",
    "choice_consequences": "Enabled: {on}\n\nDisabled: {off}",
    "expected_state": "Expected: {state}",
    "actual_applied": "Apply appearance-first processing",
    "actual_alternative": "Use editable native alternative",
    "actual_not_applicable": "Do not apply (not applicable)",
    "actual_not_processed": "Not processed",
    "effect_rasterize_computed_on": "Generate a bitmap from the PS hardness profile. Krita fade/hardness is no longer editable; size shows the actual PS footprint.",
    "effect_rasterize_computed_off": "Keep native auto_brush with editable diameter/spacing/angle/roundness/hardness (limited). Krita fade acts only within 1.00R, while PS soft edges extend beyond the nominal radius; existing h0/h50 transition-width differences are -19.81%/+22.63%. Size is nominal (current hardness {hardness}%), and the hardMix full-coverage bias is not applied.",
    "effect_prescale_texture_on": "Embed the pre-scaled pattern with exported Scale=1. The original pattern dimensions/scale basis change; later Scale edits no longer use the original pattern basis.",
    "effect_prescale_texture_off": "Keep original pattern dimensions and native Scale={scale}. Cost: Krita Qt and PS resampling differ; existing relative residuals at 150%/200% are about 22%/32%, so appearance may differ. {scale_note}",
    "scale_limit_note": "Limited: Scale exceeds 10; clipping in the target Krita still needs real-application verification.",
    "effect_bake_texture_bc_on": "Bake brightness/contrast/invert into texture pixels and export neutral fields; these fields no longer have the source PS parameter semantics.",
    "effect_bake_texture_bc_off": "Keep native Krita brightness/contrast/invert fields (limited); reverse-mapped brightness {raw:.4f} is clipped to [-1,1] and written as {written:.4f}. PS out-of-range values cannot be fully expressed: 8 of 12 existing cases exceeded tolerance, with maximum residual 61.125%/max23. No baking or replacement baking chain.",
    "skipped_tip": "Missing sampled-tip bitmap: this entire brush is expected to be skipped; none of its three processing options count as expected hits.",
    "export_title": "Confirm per-brush export",
    "export_intro": "Final selection: {n} brushes (including {hidden} hidden selected). This is an expected processing list, not a success record; consult this run's actual record for results.",
    "export_counts": "Expected processing {applied} · Native alternatives {alternative} (limited {limited}) · Brushes expected to skip {skipped}",
    "export_target": "Format {format} · Directory {directory}",
    "export_caution": "Disabling processing can retain some editability but does not guarantee Photoshop appearance. Existing residuals are not recalibrated here. Verify the actual result in the target Krita.",
    "confirm_export": "Confirm & Export",
    "cancel_export": "Back to options",
    "forecast_brush": "[{ordinal}] {name} · {id}",
    "forecast_switch": "  {title}: {state}; {reason}",
    "missing_digest": "Parsing returned no source-byte digest. Stable per-brush identity cannot be established safely; reload the file.",
    "failure_phase": "Failure phase (technical code): {phase}",
    "failure_status": "Delivery status: {status}",
    "retained_files": "Retained scene (verify paths; no automatic cleanup):",
    "blocked_files": "Blocked items (original fields and reasons preserved):",
    "rolled_back_files": "Rolled back according to the manifest:",
    "transaction_failed": "Writing failed; inspect the retained list",
    "transaction_rolled_back": "Writing failed; manifest rollback completed",
    "transaction_partial_blocked": "Partly blocked: retained files remain; not an all-or-nothing result",
    "transaction_cancelled": "Cancelled; this batch was not delivered",
    "raw_details": "Raw technical details (original output language preserved):",
    "run_heading": "This run's processing record (processed does not mean delivered)",
    "run_missing": "No processing record for this brush (skipped or not processed); not counted as a hit.",
    "result_committed": "The whole batch was delivered",
    "result_reused_only": "The whole batch reused identical existing files (not rewritten)",
    "result_empty": "No brushes selected: no files written",
    "result_all_skipped": "All brushes skipped: no files written",
    "result_cancelled": "Cancelled: no files from this batch were delivered",
    "result_unknown": "Delivery is unconfirmed; inspect the raw details",
    "result_detail": "Skipped {skipped} · Not processed {not_processed}",
    "busy_close": "A background task is still running. Wait for this batch to finish before closing."
})

# 172真实阶段与安全关闭：数字仅来自生产事件，不虚构验证/交付百分比。
_STRINGS["zh"].update({
    "cancel": "取消本批",
    "abort_close": "取消关闭（不撤回停止请求）",
    "progress_count": "已处理 {completed}/{total} 支（含跳过，非整批交付进度）",
    "progress_read": "正在读取源文件；当前步骤没有可测百分比。",
    "progress_parse": "正在解析 ABR；当前解析步骤完成后才响应取消。",
    "progress_map": "正在映射笔刷；在步骤边界检查取消，不强行中断。",
    "progress_prepare": "准备本批安全暂存；尚未交付，可取消。",
    "progress_convert": "逐支生成/暂存校验：已处理 {completed}/{total} 支（含跳过），尚未交付。稳定 ID：{preset_id}",
    "progress_verify": "逐支生成已结束（{completed}/{total} 支，含跳过）；正在准备/验证本批产物，尚未交付。",
    "progress_verified": "全部产物已通过暂存回读校验；等待最后安全检查点，仍可请求取消。",
    "progress_delivering": "最终交付中：不可取消。正在放置/回读产物，请等待完整交付或事务失败清单。",
    "progress_cancel_requested": "取消请求已接收，等待安全检查点；若最终交付已开始，本批仍将完成，不代表已经取消。",
    "progress_stopping": "正在安全停止并清理本批暂存；清理与线程结束确认前，不能再次运行。",
    "progress_finishing": "后台正在收尾；以线程结束后的实际结果为准，输入仍被冻结。",
    "parse_cancelled": "解析已取消：没有导出文件，可重新载入。",
    "result_cancel_late": "取消请求晚于最终安全检查点，未中断本批；实际结果：{result}",
    "worker_no_result": "后台线程已结束但没有返回结果；交付状态未确认，请保留现场。",
    "close_title": "后台任务尚未结束",
    "close_question": "等待安全停止后关闭，还是取消本次关闭？\n等待会向可取消阶段发出停止请求；最终交付阶段不能中断，只能等待本批完成。不会强杀线程或 Krita/PS。",
    "close_wait": "等待安全停止后关闭",
    "close_keep": "取消本次关闭",
    "close_waiting": "已安排安全退出：线程结束后关闭；可取消关闭，但已发出的停止请求不能撤回。",
})
_STRINGS["en"].update({
    "cancel": "Cancel batch",
    "abort_close": "Keep window open (stop request remains)",
    "progress_count": "Processed {completed}/{total} brushes (includes skipped; not batch delivery progress)",
    "progress_read": "Reading the source file; no measurable percentage for this step.",
    "progress_parse": "Parsing ABR; cancellation is checked after this parsing step completes.",
    "progress_map": "Mapping brushes; cancellation is checked at step boundaries without forced interruption.",
    "progress_prepare": "Preparing safe staging for this batch; not delivered yet, cancellable.",
    "progress_convert": "Generating/staging verification: processed {completed}/{total} brushes (includes skipped), not delivered. Stable ID: {preset_id}",
    "progress_verify": "Per-brush generation ended ({completed}/{total}, includes skipped); preparing/verifying batch files, not delivered yet.",
    "progress_verified": "All staged files passed read-back verification; awaiting the last safe checkpoint, cancellation can still be requested.",
    "progress_delivering": "Final delivery: cannot cancel. Placing/reading back files; wait for complete delivery or the transaction failure manifest.",
    "progress_cancel_requested": "Cancellation request received; awaiting a safe checkpoint. If final delivery has started, this batch will still finish; it is not cancelled yet.",
    "progress_stopping": "Stopping safely and cleaning this batch staging; another run is blocked until cleanup and thread completion are confirmed.",
    "progress_finishing": "Background work is finishing; the final result is confirmed after thread completion. Inputs remain frozen.",
    "parse_cancelled": "Parsing cancelled: no export files; the source can be loaded again.",
    "result_cancel_late": "Cancellation arrived after the last safe checkpoint and did not interrupt this batch; actual result: {result}",
    "worker_no_result": "The background thread ended without a result; delivery is unconfirmed. Preserve the scene.",
    "close_title": "Background task still active",
    "close_question": "Wait for a safe stop and close, or keep this window open?\nWaiting requests cancellation in cancellable stages. Final delivery cannot be interrupted: wait for this batch to finish. Threads and Krita/PS are never forcibly killed.",
    "close_wait": "Wait for safe stop, then close",
    "close_keep": "Keep window open",
    "close_waiting": "Safe exit is pending: the window closes after the thread ends. You may keep it open, but an issued stop request cannot be withdrawn.",
})


# 177包信息与原生逐支标签：明确区分GUI预览、包内记录和Krita实际显示。
_STRINGS["zh"].update({
    "metadata_heading": "资源包信息（仅 .bundle）",
    "metadata_name": "包名（包内标题）",
    "metadata_description": "说明",
    "metadata_author": "原作者／版权归属（可空）",
    "metadata_license": "笔刷素材版权许可（可空）",
    "metadata_version": "资源包版本（仅包内）",
    "metadata_source": "来源文件名（只读）",
    "metadata_optional": "未知可留空；不推断、不继承上次输入",
    "metadata_default_description": "留空使用：Converted from Photoshop ABR",
    "metadata_help": "包名写入包内标题，不改变按来源、程序版本和内容指纹生成的文件名。作者与许可只按本次输入写入；项目 MIT 不是笔刷素材许可。不读取系统身份，不自动写日期。",
    "metadata_bundle_scope": ".bundle：包信息、公共及逐支标签将写入资源包；以下标签是每支笔刷的原生关联，不是包级标签。",
    "metadata_kpp_scope": "独立 .kpp：仅保存笔刷预设，不承载本表包信息、原生标签或包内追溯；这些输入已停用且不会写出。同一文件切回 bundle 可继续编辑，重载恢复默认。",
    "metadata_limits": "显示范围（按 Krita 5.3.3 目标源码，实机待187）：说明、作者、许可有原生详情字段；包内标题不作管理器标题，资源包版本、来源及详细追溯不在界面显示。Krita 重存可能丢失自定义字段及追溯；请保留原始导出包。",
    "metadata_invalid": "元信息／标签输入无效：{detail}",
    "metadata_preview": "包信息预览（非成功记录）",
    "metadata_value": "{label}：{value}",
    "metadata_not_provided": "未提供（不推断）",
    "metadata_readback": "本次交付的包信息与标签（来自回读，不是表单值）",
    "metadata_readback_brief": "资源包回读：{n} 支预设 · {tagged} 支有标签 · {links} 个逐笔标签关联；身份 {scheme}/{hash}",
    "metadata_kpp_result": "已交付独立 .kpp；该格式不承载包信息、原生标签或包内追溯（不是回读失败）。",
    "metadata_not_verified": "没有可用的成功交付回读；不能声称包信息或标签已写出。",
    "metadata_trace_readback": "包内追溯已回读：{n} 支；源文件 {source}（界面不显示，重存可能丢失）。",
    "tags_base": "公共与来源标签（全体笔刷）",
    "tags_per_preset": "当前笔刷：逐支增删与最终标签",
    "tags_common": "公共标签",
    "tags_source": "来源标签（可改可删）",
    "tags_added": "单支追加",
    "tags_remove": "单支移除（优先）",
    "tags_help": "每行一个标签，空格属于标签；来源默认取文件名主干。公共和来源标签可整批修改／删除，也可在单支移除中排除。改名可先移除旧标签、再追加新标签；移除优先于追加。",
    "tags_one_per_line": "每行一个标签；可以留空",
    "tags_final_heading": "最终标签及来源关系",
    "tags_final": "最终标签：{tags}",
    "tags_empty": "无标签",
    "tags_from_common": "公共",
    "tags_from_source": "来源",
    "tags_from_added": "单支追加",
    "tag_relation": "  {tag} ← {origins}",
    "tags_removed": "单支已排除：{tags}",
    "tags_no_current": "请选择一支可见笔刷查看最终标签；公共与来源编辑仍作用于隐藏已选。",
    "tags_card": "最终标签（bundle）：{tags}",
    "tags_not_written": "此支预计跳过，标签不会写出。",
    "tags_readback_row": "  {file}：{tags}",
})
_STRINGS["en"].update({
    "metadata_heading": "Bundle information (.bundle only)",
    "metadata_name": "Package name (internal title)",
    "metadata_description": "Description",
    "metadata_author": "Original author / copyright owner (optional)",
    "metadata_license": "Brush-material copyright license (optional)",
    "metadata_version": "Package version (inside bundle only)",
    "metadata_source": "Source filename (read-only)",
    "metadata_optional": "Leave unknown fields empty; no guessing or previous input",
    "metadata_default_description": "Empty uses: Converted from Photoshop ABR",
    "metadata_help": "The package name is an internal title; output filenames still use the source name, program version and content fingerprint. Only this run's author/license input is written. The project's MIT license is not the brush-material license. No system identity or automatic dates are read.",
    "metadata_bundle_scope": ".bundle: package information and common/per-brush tags are written into the bundle. Tags are native associations for each brush, not package-level tags.",
    "metadata_kpp_scope": "Standalone .kpp: brush presets only; no package information, native tags or bundle trace from this form. These inputs are disabled and not written. Switching back to bundle retains edits for this file; reload resets defaults.",
    "metadata_limits": "Display scope (Krita 5.3.3 target source; real UI pending task 187): description, author and license have native detail fields. The internal title is not the manager title; package version, source and detailed trace are not displayed. Krita resaving may discard custom fields and trace. Keep the original exported bundle.",
    "metadata_invalid": "Invalid metadata / tag input: {detail}",
    "metadata_preview": "Package information preview (not a success record)",
    "metadata_value": "{label}: {value}",
    "metadata_not_provided": "Not provided (not inferred)",
    "metadata_readback": "Delivered package information and tags (read back, not form values)",
    "metadata_readback_brief": "Bundle read-back: {n} presets; {tagged} tagged; {links} per-brush tag associations; identity {scheme}/{hash}",
    "metadata_kpp_result": "Standalone .kpp files were delivered. This format carries no package information, native tags or bundle trace (not a read-back failure).",
    "metadata_not_verified": "No successful delivery read-back is available; metadata or tag writing cannot be claimed.",
    "metadata_trace_readback": "Bundle trace read back: {n} brushes; source {source} (not displayed; may be lost on resave).",
    "tags_base": "Common and source tags (all brushes)",
    "tags_per_preset": "Current brush: edits and final tags",
    "tags_common": "Common tags",
    "tags_source": "Source tags (editable / removable)",
    "tags_added": "Add to this brush",
    "tags_remove": "Remove from this brush (takes priority)",
    "tags_help": "One tag per line; spaces belong to the tag. Source defaults to the filename stem. Edit/remove common and source tags for the whole batch, or exclude them for one brush. Rename by removing the old tag and adding the new one. Removal takes priority over addition.",
    "tags_one_per_line": "One tag per line; may be empty",
    "tags_final_heading": "Final tags and origin relationships",
    "tags_final": "Final tags: {tags}",
    "tags_empty": "No tags",
    "tags_from_common": "Common",
    "tags_from_source": "Source",
    "tags_from_added": "Per-brush addition",
    "tag_relation": "  {tag} <- {origins}",
    "tags_removed": "Excluded for this brush: {tags}",
    "tags_no_current": "Choose a visible brush for final tags; common/source edits still apply to hidden selected brushes.",
    "tags_card": "Final tags (bundle): {tags}",
    "tags_not_written": "This brush is expected to be skipped; its tags will not be written.",
    "tags_readback_row": "  {file}: {tags}",
})

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


def tr_applicability(reason: str) -> str:
    """仅翻译契约返回的理由，不另造一套适用性判断。"""
    if get_language() == "zh":
        return reason
    exact = {
        "计算笔刷（无自带位图），现行路径按硬度剖面生成采样笔尖位图":
            "Computed brush without a built-in bitmap; the current path generates a sampled tip from its hardness profile.",
        "采样笔尖自带位图，无需位图化":
            "The sampled tip already has a bitmap; rasterization is unnecessary.",
        "无纹理图案位图（现行管线不写纹理，预缩放/烘焙都无从发生）":
            "No texture-pattern bitmap; the current pipeline writes no texture, so neither pre-scaling nor baking can occur.",
        "纹理位图为空，现行管线不做预缩放":
            "The texture bitmap is empty; the current pipeline does not pre-scale it.",
        "亮度/对比度反解超出 Krita 亮度域 [-1,1]，现行管线预烘焙":
            "Reverse-mapped brightness/contrast exceeds Krita's brightness domain [-1,1]; the current pipeline pre-bakes it.",
        "亮度/对比度在 Krita 可表达范围内，现行管线写原生字段":
            "Brightness/contrast is within Krita's expressible range; the current pipeline writes native fields.",
    }
    if reason in exact:
        return exact[reason]
    patterns = (
        (r"纹理缩放 (.+)% ≤ 100%（或不是有限数），现行管线不做预缩放",
         "Texture scale {0}% is <=100% (or not finite); no pre-scaling."),
        (r"纹理位图维度异常 shape=(.+)（现行管线会报错，不做预缩放）",
         "Invalid texture bitmap dimensions {0}; conversion will report an error, not pre-scale it."),
        (r"纹理缩放 (.+)% 无法计算目标尺寸",
         "Cannot calculate the target dimensions for texture scale {0}%."),
        (r"预缩放后边长仍为 (.+)（四舍五入到原尺寸），现行管线保持原图",
         "Rounded pre-scaled dimensions remain {0}; the original image is kept."),
        (r"纹理缩放 (.+)% > 100% 且目标边长变为 (.+)，现行管线预缩放",
         "Texture scale {0}% is >100% and dimensions change to {1}; the current pipeline pre-scales it."),
    )
    for pattern, template in patterns:
        match = re.fullmatch(pattern, reason)
        if match:
            return template.format(*match.groups())
    _log.warning("适用性理由缺少英文翻译: %s", reason)
    return reason


def processing_reason(preset, outcome) -> str:
    """按生产计划展示代价；数值换算复用生产helper，不改actual或limited。"""
    if not outcome.applicable:
        return tr_applicability(outcome.actual_reason)
    suffix = "on" if outcome.actual == "applied" else "off"
    values = {}
    if suffix == "off":
        if outcome.switch_id == "rasterize_computed":
            values["hardness"] = f"{50 if preset.hardness is None else preset.hardness:g}"
        elif outcome.switch_id == "prescale_texture":
            values["scale"] = f"{float(preset.texture.scale) / 100:g}"
            values["scale_note"] = tr("scale_limit_note") if outcome.limited else ""
        elif outcome.switch_id == "bake_texture_bc":
            from brush_converter.kpp.preset_xml import texture_brightness_truncation
            values = texture_brightness_truncation(
                preset.texture.brightness, preset.texture.contrast)
    return tr(f"effect_{outcome.switch_id}_{suffix}", **values)


def outcome_label(outcome) -> str:
    """预计/实际共用术语，受限必须显式标出，不能只显示“关闭”。"""
    text = tr(f"actual_{outcome.actual}")
    return f"{text} ({tr('limited')})" if outcome.limited else text
