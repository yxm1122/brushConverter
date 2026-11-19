# tests/baselines/mode —— 任务 33 真值与基线（PS 真值 + Krita 渲染）

本目录是 v0.4「每个纹理模式下定论」的可比素材库：同一笔、同一纹理、同一缩放，
每个模式 6 组参数各出一张 PS 真值与（有对应模式下）一张 Krita 渲染基线。

**图片不入库**（本目录 .gitignore 已忽略 *.png、*.kpp、_work/）；
入库的只有 README.md、manifest.json、.gitignore。

## 一、命名规则（确定性，同参数跑两遍同名同内容）

    ps__mode__<序号>_<模式>__d<深度>_b<亮度>_c<对比度>.png      PS 真值
    krita__mode__<序号>_<模式>__d<深度>_b<亮度>_c<对比度>.png   Krita 渲染基线

- <序号>：docs/数据/mode-align.json 里 ps_members 的 0 基下标（两位补零）。
  **必须带序号**，因为清单里 Nrml 出现两次（0 与 27）。
- <模式>：ps_member 去掉 4CC 尾部空格后的文件名安全形式（Clr 尾部空格 → Clr；H   → H）。
- <深度>：PS textureDepth（10 / 60 / 100）。
- <亮度>/<对比度>：PS textureBrightness / textureContrast，负号原样保留（如 b-50_c-50）。

同一组参数在 PS 与 Krita 侧文件名只差前缀，便于 34/35/36 直接配对。

## 二、6 组参数（顺序固定）

深度 {10, 60, 100} × 亮度/对比度组 {(0, 0), (-50, -50)} = 6 组：

| 序号 | depth | brightness | contrast | 说明 |
|---|---|---|---|---|
| 1 | 10 | 0 | 0 | 低深度、中性 |
| 2 | 10 | -50 | -50 | 低深度、双负向 |
| 3 | 60 | 0 | 0 | 中深度、中性 |
| 4 | 60 | -50 | -50 | 中深度、双负向 |
| 5 | 100 | 0 | 0 | 全深度、中性 |
| 6 | 100 | -50 | -50 | 全深度、双负向 |

选 (-50, -50) 而不是 (+50, +50) 的理由：负亮度用来检查「Krita 亮度是减法」这一符号翻转
（calibrated 公式里 b_k = center_fix - b/255），负对比度 k=0.5 不在 Krita 上限 2 的截断区，
差异不会被饱和吃掉。

## 三、口径（改口径必须整批重出）

**PS 真值**：画布 100×100 / 72ppi / RGB 透明底；笔刷 tests/定量测试专用.abr 里的
「定量测试用」计算笔刷（Dmtr=200、硬度 100、间距 10）；纹理 research/测试用纹理.png
原始字节定义图案（图案名 bc_mode_texture），textureScale=100%、invertTexture=true；
落笔 = 画布中心 (50,50)→(50.4,50.4) 的 0.4px 路径（整张图只有一个笔尖章）。
导出 PNG 保留 Alpha，RGB 恒为 0，**信息全在 Alpha**。

**Krita 基线**：同一支笔（同一 ABR 预设走项目转换链）、同一纹理文件字节
（内嵌时追加一个 tEXt 块改 md5，避免与图案库同 md5 被 Krita 静默禁用）、
Texture/Pattern/Scale=1.0（=100%）、画布 100×100、stroke=60、压感 1.0。
深度 → Texture/Strength/Value（=depth/100，高度系 ×1.2 封顶 1.0，转换链口径）；
亮度/对比度 → 转换链 calibrated 公式（preset_xml.texture_brightness_contrast）；
混合模式 → docs/数据/mode-align.json 的 krita_value（脚本会断言转换链映射与之一致）。

> 注意：Krita 侧是「当前转换器口径 conv」而不是「PS 数值直传 raw」。这样
> PS↔Krita 的逐像素差异就正好等于转换器本身的误差，34/35/36 判「可映射/需校准」时
> 读到的就是转换器的真实偏差。

## 四、哪些模式有 Krita 基线

判定清单共 47 个 PS 模式；其中 **14 个**在 Krita TexturingMode（16 成员）里有对应或
待确认对应，出 6 张 Krita 图；其余 **33 个**是「无对应」（PS 图层/其它传递模式，
Krita 纹理混合模式没有该成员），**只出 PS 真值**，判据是结构性的。

| 序号 | PS 模式 | 语义 | runtime_id | Krita TexturingMode | mode-align 状态 |
|---|---|---|---|---|---|
| 4 | Mltp | Multiply 正片叠底 | 7 | 0 | 有对应 |
| 6 | Ovrl | Overlay 叠加 | 17 | 5 | 有对应 |
| 9 | Drkn | Darken 变暗 | 1 | 4 | 有对应 |
| 17 | CDdg | Color Dodge 颜色减淡 | 25 | 6 | 有对应 |
| 18 | CBrn | Color Burn 颜色加深 | 26 | 7 | 有对应 |
| 20 | Sbtr | Subtract 减去 | 39 | 1 | 有对应 |
| 21 | hardMix | Hard Mix 实色混合 | 35 | 10 | 有对应 |
| 22 | linearDodge | Linear Dodge 线性减淡 | 30 | 8 | 有对应 |
| 23 | linearBurn | Linear Burn 线性加深 | 31 | 9 | 有对应 |
| 38 | height | Height 高度 | 40 | 14 | 有对应 |
| 39 | linearHeight | Linear Height 线性高度 | 41 | 15 | 有对应 |
| 44 | Lght | Lght |  | 2 | 待动态确认 |
| 45 | Grdn | Grdn |  | 3 | 待动态确认 |
| 46 | HdMxS | HdMxS |  | 11 | 待动态确认 |

## 五、目录

| 路径 | 内容 | 入库 |
|---|---|---|
| ps/ | PS 真值 PNG（PS 模式 × 6 组） | 否 |
| krita/ | Krita 渲染基线 PNG | 否 |
| _work/ | job.json / done.json / batch.log / .kpp / 结果 JSON | 否 |
| manifest.json | 全部图的登记（尺寸/Alpha 统计/sha256/失败原因） | 是 |
| README.md | 本文件 | 是 |

## 六、复现

    python tools/mode_batch.py --plan            # 写 docs/数据/mode-batch.json 与本 README
    python tools/mode_batch.py --ps --resume     # PS 真值（抢 ps.lock，分批，单批 24 张）
    python tools/mode_batch.py --krita-build     # 生成 84 个 .kpp
    python tools/mode_batch.py --krita-render --resume   # 串行渲染（抢 krita.lock）
    python tools/mode_batch.py --manifest        # 汇总 manifest.json
    python tools/mode_batch.py --verify-ps --verify-krita   # 确定性抽检（3 模式比 sha256）

## 七、判定注意

1. 只比 Alpha 通道；RGB 在 PS 侧恒黑。
2. PS 的 高度/线性高度 在 d60、d100 会饱和成全 255（alpha_unique==1）——
   这是 PS 真实行为，但**对拟合没有信息量**，34/35/36 拟合时应按 alpha_unique==1 过滤。
3. Krita 渲染的 Alpha 边缘有随机种子/抗锯齿抖动：docs/Krita无头渲染说明.md 实测两次运行
   最大差 4–6/255，因此**基线比对要用容差**（tools/imgdiff.py 的默认 tol），不要承诺逐像素零差。
4. **C 批 5 个模式（order 32–36）的 PS 真值是改名重出后的结果**：`SnrB/MnrB/HnrB/ttsD/Str ` 在 33 期
   用 stringID 伪名被 PS 忽略（落笔报错 / 回读停在上一模式），C 批改用 charID 真名
   （`BrnS/BrnM/BrnH/Dstt/Str`）重出 **30 张全部 ok**；原失败记录保留在 `manifest.json` 与
   `docs/数据/mode-batch.json` 的 `ps_truth.orig_images`，不要当成「PS 没有这些模式」。
5. **复刻器只在强度 s=1 可信**：`tools/texture_mask.py` 的 `blend()` 没有 `use_soft_texturing` 分支，
   s<1 时与真实渲染实测差 229/102 像素 → 复刻结果只能用于 s=1 的交叉校验，
   **不能**拿 s<1 的复刻结果当「不达标」证据。
6. **0.00% 只表示当前口径下像素不可区分**：39 个有真值的模式按 6 组 alpha_sha256 元组只有 **12 类**，
   **31 个模式与别的模式完全同图**；`height` 与 `linearHeight` 两侧都不可区分。
   写报告时不要用「语义等价」「精确复现」，统一写「当前口径下像素不可区分」。
