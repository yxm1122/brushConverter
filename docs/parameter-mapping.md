# ABR → Krita 完整参数映射清单

> 引擎：现阶段仅像素引擎（paintbrush）。源数据来自 `海怪笔刷…18支.abr`（v6.2，20 预设）。
> ✅ = 已实现，⛔ = 不映射（原因见右侧），❓ = 待你确认。

## 1. 笔尖与基础属性

| ABR 键 | 含义 | → Krita | 映射公式 | 状态 |
|--------|------|---------|----------|------|
| `samp` 位图 + `sampledData` UUID | 采样笔尖 | png_brush 内嵌笔尖 | 灰度 PNG，白=墨（base64 内嵌） | ✅ |
| `Brsh.Dmtr` | 主直径 px | `scale` | `scale = Dmtr / max(笔尖宽, 笔尖高)`；采样笔尖内嵌 PNG 补零为正方形 | ✅（Krita 显示尺寸修正） |
| `Brsh.Spcn` | 间距 % | `spacing` | `spacing = Spcn / 100` | ✅ |
| `Brsh.Angl` | 角度（度） | `angle` | `math.radians(Angl % 360)`（度→弧度，负角先 normalize 到 [0,360)） | ✅ |
| `Brsh.Rndn` | 圆度 % | （采样笔刷不映射，见注①） | — | ⛔ |
| `Brsh.Hrdn` | 硬度 % | （采样笔刷不映射，见注①） | — | ⛔ |
| `Nm ` | 笔刷名 | preset name | 直接 | ✅ |

> 注①：圆度/硬度只对「计算笔刷」有意义（采样笔刷的圆度已固化在笔尖位图里）。

## 2. 计算笔刷（无位图 → auto_brush）

| ABR 键 | → Krita | 映射公式 | 状态 |
|--------|---------|----------|------|
| `Dmtr` | `MaskGenerator.diameter` | 直接 | ✅ |
| `Hrdn` | `hfade`/`vfade` | `Hrdn / 100`（fade=实心区占比，与硬度同向） | ✅ |
| `Rndn` | `ratio` | `Rndn / 100` | ✅ |
| `Angl` | `angle` | `math.radians(Angl % 360)`（度→弧度，负角 normalize） | ✅ |
| `Spcn` | `spacing` | `Spcn / 100` | ✅ |

> 注①：圆度/硬度只对「计算笔刷」有意义。硬度映射 `fade = Hrdn/100`（Krita 的 `hfade/vfade` 是「实心区占半径比例」，硬度 100%→1.0 硬边、0%→0.0 全软，与 Photoshop 硬度同向，不是 `(100-Hrdn)/100`）。角度 `angle` 在 Krita XML 里存**弧度**、UI 范围 **[0,360) 度**，负角度需先 `% 360` normalize，否则被角度控件 clamp 到 0。

## 3. 形状动态（Shape Dynamics）

| ABR 键 | 条件 | → Krita | 映射公式 | 状态 |
|--------|------|---------|----------|------|
| `szVr.bVTy==2` | 压力 | `PressureSize=true` | — | ✅ |
| `minimumDiameter` | — | `SizecommonCurve` | `0,{minD/100};1,1;` | ✅ |
| `angleDynamics.bVTy==6` | 方向 | `RotationSensor=drawingangle` | sensorslist + fuzzy | ✅ |
| `angleDynamics.jitter` | `RotationValue`（旋转-效果强度） | fuzzy/fuzzystroke 曲线 | `RotationValue=jitter/100`；两条随机度曲线固定 `0,0;1,1;`（Krita UI 对应 -180°..+180°，XML 坐标归一化为 0..1） | ✅（按手调 KPP 校准） |
| `angleDynamics.bVTy==2` | 压力 | `RotationSensor=pressure` | — | ✅ |
| `roundnessDynamics.bVTy==2` | 压力 | `PressureRatio=true` | — | ✅ |
| `minimumRoundness` | — | `RatiocommonCurve` | `0,{minR/100};1,1;` | ✅ |

## 4. 传递（Transfer）

| ABR 键 | 条件 | → Krita | 映射公式 | 状态 |
|--------|------|---------|----------|------|
| `opVr.bVTy==2`（不透明度） | 压力 | `PressureOpacity=true` | — | ✅ |
| `opVr.Mnm` | — | `OpacitycommonCurve` | `0,{Mnm/100};1,1;` | ✅ |
| `prVr.bVTy==2`（流量） | 压力 | `PressureFlow=true` + `FlowUseCurve=true` | — | ✅ |
| `prVr.Mnm` | — | `FlowcommonCurve` | `0,{Mnm/100};1,1;` | ✅ |
| `wtVr`（湿度） | — | — | 像素引擎无对应 | ⛔ |
| `mxVr`（混合） | — | — | 像素引擎无对应 | ⛔ |

## 5. 散布（Scattering）

| ABR 键 | 含义 | → Krita | 映射公式 | 状态 |
|--------|------|---------|----------|------|
| `useScatter` | 启用散布 | `PressureScatter=true`（散布开关） | — | ✅ |
| `scatterDynamics.jitter` | 散布量（0–1000%） | `ScatterValue`（0–5.0） | `ScatterValue = jitter / 400` | ✅ |
| `scatterDynamics.bVTy==2` | 散布量随压感 | `ScatterSensor=pressure` + `ScatterUseCurve=true` | — | ✅ |
| `scatterDynamics.bVTy==0` | 散布恒定 | `ScatterUseCurve=false`（关闭曲线） | — | ✅ |
| `bothAxes` | 两轴 | `AxisX`/`AxisY` | True→AxisX=Y=True；False→AxisX=False,Y=True | ✅ |
| `Spcn`（顶层） | 散布间距 | — | 不映射到散布量（见注②） | ⛔ |
| `Cnt`（数量） | — | — | Krita 像素引擎无「数量」 | ⛔ |

> 注②：Krita 里 `PressureScatter` 是「散布选项是否启用」的开关（`isChecked` 序列化名），并非"压感控制"；压感控制由 `ScatterSensor` 决定。散布量真正来源是 `scatterDynamics.jitter`（顶层 `Spcn` 实为间距，非散布量）。
>
> 散布量换算 `÷400`：PS 散布值 0–1000% 用高斯分布（值代表最大范围但集中度高），Krita `ScatterValue` 0–5.0（UI 0–500%）用均匀分布，两者视觉密度不同，线性 `÷200` 会偏大。经实测锚点校准（PS 145% ≈ Krita 35%，即 `ScatterValue 0.35`），经验系数约 `÷4`（即 `jitter/400`）。该系数可在 `preset_xml.py` 的散布分支调整。

| `countDynamics` | — | — | 同上 | ⛔ |

## 6. 不映射（引擎差异大，已列为后续增强）

| ABR 键 | 含义 | 原因 |
|--------|------|------|
| `dualBrush` | 双笔刷 | Krita 是 masking brush 机制，语义不同 |
| `useColorDynamics`（`H`/`Strt`/`Brgh`/`purity`/`clVr`） | 颜色动态 | 两软件颜色模型差异大 |
| `Wtdg`（湿边） | 湿边 | 像素引擎需「Wet」选项，暂不启用 |
| `Nose`/`Rpt`（喷嘴/重复） | — | 喷枪特性，暂不映射 |
| `brushProjection` / `useBrushPose` | 投影/姿态 | 高级倾斜特性，暂不映射 |

## 7. 纹理（Texture）✅（v1.1 新增）

> 纹理数据源：ABR `patt` 区段（按 UUID 匹配 `Txtr.Idnt`），资源以 PNG 内嵌
> （`type="patterns"`，与 Krita 5.x 官方样本一致）。格式细节见
> `docs/developer-guide.md` 与 `docs/texture-mapping-proposal.md`。

| ABR 键 | → Krita 参数 | 映射公式 | 状态 |
|--------|--------------|----------|------|
| `useTexture` | `Texture/Pattern/Enabled` | `true` | ✅ |
| `Txtr.Idnt` | 资源查找 | patt 记录按 UUID 匹配，PNG 内嵌 | ✅ |
| `Txtr.Nm` | `Name` / `PatternFileName` | 安全文件名 `tex_<uuid前8>.png` | ✅ |
| PNG 字节 | `PatternMD5Sum` | md5 hex（`PatternMD5` 留空，绕开 Krita 5.0 写二进制 bug） | ✅ |
| `textureScale`（%） | `Texture/Pattern/Scale` | `textureScale/100`，clamp [0.01, 10] | ✅（用户已确认一致） |
| `textureDepth`（%） | `Texture/Strength/Value` | `textureDepth/100`（0..1） | ✅ |
| `textureDepthDynamics.bVTy==2` | `PressureTexture/Strength/` | `true` + `Strength/UseCurve=true` | ✅ |
| `textureDepthDynamics.Mnm` | `Texture/Strength/commonCurve` | `0,{Mnm/100};1,1;` | ✅ |
| `InvT` | `Texture/Pattern/Invert` | 直接 | ✅ |
| `textureBlendMode` | `Texture/Pattern/TexturingMode` | 优先使用 Krita `(Photoshop)` 模式：`linearHeight`→Linear Height (Photoshop)=15、`Hght`/`height`→Height (Photoshop)=14、`hardMix`→Hard Mix (Photoshop)=10；其余模式按 Krita 枚举映射（`Mul `→0、`Sbtr`→1、`Ovrl`→5 等）；未知回退 0 + 警告 | ✅ |
| `textureBrightness`（PS -150..150） | `Texture/Pattern/Brightness` | 普通模式：`round(0.10-v/250,2)`；Linear Height Photoshop：`round(0.30-v/250,2)`；clamp [-1,1] | ✅（现有 Krita 对照） |
| `textureContrast`（PS -50..100） | `Texture/Pattern/Contrast` | 普通模式使用 PS 因子；Linear Height Photoshop 使用其倒数；统一 round 到 0.01 并 clamp [0,2] | ✅（现有 Krita 对照） |
| `TxtC` / `interpretation` | — | Krita 无对应，静默忽略（源文件默认值） | ⚠️ |
| `protectTexture` | — | Krita 无对应，保留轻量警告 | ⚠️ |

> 亮/对比度映射依据 Krita `KisTextureMaskInfo::recalculateMask()` 与 `research/测试结果2`、`research/krita结果`：
> 当前候选是模式相关校准，最终目标是让 Krita 模式合成结果匹配 Photoshop，而不是匹配中间纹理值。
> Krita UI 只能精确到两位小数，所有候选值必须先四舍五入到 0.01 再验证。

## 8. 控制源编码（bVTy → Krita 传感器）

| bVTy | Photoshop | Krita 传感器 | 状态 |
|------|-----------|--------------|------|
| 0 | 关 | 无 | ✅ |
| 1 | 渐隐 | 无对应（未映射） | ⛔ |
| 2 | 压力 | `pressure` | ✅ |
| 3 | 倾斜 | `tilt`（预留，未启用） | ⛔ |
| 4 | 转轮 | 无对应 | ⛔ |
| 5 | 旋转随机 | `RotationValue` + `fuzzy`/`fuzzystroke` | ✅（若源文件使用此控制源，当前仍按方向旋转分支处理） |
| 6 / 7 | 初始方向/方向 | `drawingangle` | ✅ |

## 9. 去重规则

- 完全重复的预设（同名 + 同 UUID + 同 Dmtr）只保留首个。当前文件因此去掉 1 个重复的「淘宝店」横幅笔刷（20 → 19）。
