# ABR → Krita 完整参数映射清单

> 引擎：现阶段仅像素引擎（paintbrush）。源数据来自 `海怪笔刷…18支.abr`（v6.2，20 预设）。
> ✅ = 已实现，⛔ = 不映射（原因见右侧），❓ = 待你确认。

## 1. 笔尖与基础属性

| ABR 键 | 含义 | → Krita | 映射公式 | 状态 |
|--------|------|---------|----------|------|
| `samp` 位图 + `sampledData` UUID | 采样笔尖 | png_brush 内嵌笔尖 | 灰度 PNG，白=墨（base64 内嵌） | ✅ |
| `Brsh.Dmtr` | 主直径 px | `scale` | `scale = Dmtr / 笔尖宽度` | ✅ |
| `Brsh.Spcn` | 间距 % | `spacing` | `spacing = Spcn / 100` | ✅ |
| `Brsh.Angl` | 角度（度） | `angle` | 直接 | ✅ |
| `Brsh.Rndn` | 圆度 % | （采样笔刷不映射，见注①） | — | ⛔ |
| `Brsh.Hrdn` | 硬度 % | （采样笔刷不映射，见注①） | — | ⛔ |
| `Nm ` | 笔刷名 | preset name | 直接 | ✅ |

> 注①：圆度/硬度只对「计算笔刷」有意义（采样笔刷的圆度已固化在笔尖位图里）。

## 2. 计算笔刷（无位图 → auto_brush）

| ABR 键 | → Krita | 映射公式 | 状态 |
|--------|---------|----------|------|
| `Dmtr` | `MaskGenerator.diameter` | 直接 | ✅ |
| `Hrdn` | `hfade`/`vfade` | `(100 - Hrdn) / 100` | ✅ |
| `Rndn` | `ratio` | `Rndn / 100` | ✅ |
| `Angl` | `angle` | 直接 | ✅ |
| `Spcn` | `spacing` | `Spcn / 100` | ✅ |

## 3. 形状动态（Shape Dynamics）

| ABR 键 | 条件 | → Krita | 映射公式 | 状态 |
|--------|------|---------|----------|------|
| `szVr.bVTy==2` | 压力 | `PressureSize=true` | — | ✅ |
| `minimumDiameter` | — | `SizecommonCurve` | `0,{minD/100};1,1;` | ✅ |
| `angleDynamics.bVTy==6` | 方向 | `RotationSensor=drawingangle` | sensorslist + fuzzy | ✅ |
| `angleDynamics.jitter` | — | fuzzy 曲线 | `0,0;1,{jitter/100};` | ✅ |
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
| `useTexture` + `patt` 区 | 纹理 | Krita 纹理引擎不同；patt 可另存 pattern |
| `dualBrush` | 双笔刷 | Krita 是 masking brush 机制，语义不同 |
| `useColorDynamics`（`H`/`Strt`/`Brgh`/`purity`/`clVr`） | 颜色动态 | 两软件颜色模型差异大 |
| `Wtdg`（湿边） | 湿边 | 像素引擎需「Wet」选项，暂不启用 |
| `Nose`/`Rpt`（喷嘴/重复） | — | 喷枪特性，暂不映射 |
| `brushProjection` / `useBrushPose` | 投影/姿态 | 高级倾斜特性，暂不映射 |

## 7. 控制源编码（bVTy → Krita 传感器）

| bVTy | Photoshop | Krita 传感器 | 状态 |
|------|-----------|--------------|------|
| 0 | 关 | 无 | ✅ |
| 1 | 渐隐 | 无对应（未映射） | ⛔ |
| 2 | 压力 | `pressure` | ✅ |
| 3 | 倾斜 | `tilt`（预留，未启用） | ⛔ |
| 4 | 转轮 | 无对应 | ⛔ |
| 5 | 旋转 | 无对应 | ⛔ |
| 6 / 7 | 初始方向/方向 | `drawingangle` | ✅ |

## 8. 去重规则

- 完全重复的预设（同名 + 同 UUID + 同 Dmtr）只保留首个。当前文件因此去掉 1 个重复的「淘宝店」横幅笔刷（20 → 19）。
