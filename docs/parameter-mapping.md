# ABR → Krita 完整参数映射清单

> 引擎：现阶段仅像素引擎（paintbrush）。源数据来自 `海怪笔刷…18支.abr`（v6.2，20 预设）。
> ✅ = 已实现，⛔ = 不映射（原因见右侧），⚠️ = 已尽量映射但仍保留提示（如 `TxtC` / `protectTexture` 在 Krita 无对应）；
> ❓ = 待确认（**历史记号**，本表现已无待确认项：早期两处已全部落地，见任务 16 的 `docs/文档对齐报告.md`）。

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
| `angleDynamics.bVTy==0` 且 jitter>0 | 无控制源（纯随机抖动） | `PressureRotation=true` + fuzzy-only 传感器 | `RotationValue=jitter/100`；fuzzy/fuzzystroke 曲线 `0,0;1,1;`，**不含** drawingangle（不随笔迹方向） | ✅（待 Krita 实测复核） |
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
| `Txtr.Nm` | `Name` / `PatternFileName` | 资源名由内嵌字节派生：`tex_<sha256(内嵌PNG)前12>.png`（v1.2.1；旧版 `tex_<uuid前8>.png` 与内容无关，会与用户图案库撞名） | ✅ |
| PNG 字节 | `PatternMD5Sum` | md5 hex（`PatternMD5` 留空，绕开 Krita 5.0 写二进制 bug） | ✅ |
| `textureScale`（%） | `Texture/Pattern/Scale` | `textureScale/100`，clamp [0.01, 10] | ✅（用户已确认一致） |
| `textureDepth`（%） | `Texture/Strength/Value` | 高度系 `min(1, 1.2×textureDepth/100)`；非高度系 `textureDepth/100`（0..1，v1.2.1） | ✅ |
| `textureDepthDynamics.bVTy==2` | `PressureTexture/Strength/` + `Texture/Strength/UseCurve` | `true` + `true`（压感曲线，最小值见下） | ✅ |
| `textureDepthDynamics.bVTy==0` | `PressureTexture/Strength/` + `Texture/Strength/UseCurve` | `true` + `false`（效果强度开启、无传感器，恒定用 Value） | ✅ |
| `textureDepthDynamics.Mnm` | `Texture/Strength/commonCurve` | 高度系 `0,min(1,1.2×Mnm/100);1,1;`；非高度系 `0,Mnm/100;1,1;`（仅 bVTy==2，v1.2.1） | ✅ |
| `InvT` | `Texture/Pattern/Invert` | 直接 | ✅ |
| `textureBlendMode` | `Texture/Pattern/TexturingMode` | 按 `mapping._TEXTURING_MODE`（v1.3.0 起 **34 个键**）取 Krita 枚举值：`linearHeight`→15、`height`→14、`hardMix`→10、`Mltp`→0、`Sbtr`→1、`Dfrn`→1、`blendSubtraction`→1、`Ovrl`→5、`CDdg`→6、`CBrn`→7、`linearDodge`→8、`linearBurn`→9 等；未登记的模式回退 Multiply(0) 并保留一条警告 | ✅ 22 / 🧪 1 / ⛔ 24（逐模式见 §7.2） |
| `textureBrightness`（PS -150..150） | `Texture/Pattern/Brightness` | **默认（calibrated，v1.2.0 起）**：`b_k = (1-k)/(510k) - v/255`（C≥0）或 `(1-k)/(510k) - v/(255k)`（C<0），保留 3 位小数，clamp [-1,1]；**历史方案（legacy，v1.1.2，可回退）**：`round(0.10-v/250,2)`，clamp [-1,1] | ✅ / 🧪 |
| `textureContrast`（PS -50..100） | `Texture/Pattern/Contrast` | 两条路径共用 PS 中心因子 `k`（C<0：1+C/100；C≥0：1/(1-C/100)），clamp [0,2]；calibrated（默认）保留 3 位小数，legacy 保留 2 位 | ✅ / 🧪 |
| `TxtC` / `interpretation` | — | Krita 无对应，静默忽略（源文件默认值） | ⚠️ |
| `protectTexture` | — | Krita 无对应，保留轻量警告 | ⚠️ |

> 亮/对比度映射依据 Krita `KisTextureMaskInfo::recalculateMask()` 与 `research/测试结果2`、`research/krita结果`：
> 所有模式统一使用普通校准（Linear Height (Photoshop) 曾用 0.30 基线/对比度倒数的特殊映射，实测效果不好，2026-08-27 确认取消）。
> Krita UI 显示两位小数：legacy 严格按 0.01 舍入（v1.1.2 行为）；
> calibrated 保留 3 位小数（Krita 用 `getDouble` 原样读取预设值，理由与风险见 §7.1 局限 5）。

### 7.1 纹理亮度/对比度：两条公式与开关（v1.2.0 任务 13）

**开关**：环境变量 `BRUSHCONVERTER_TEXTURE_FORMULA`，取值 `calibrated`（默认，v1.2.0 起转正）
或 `legacy`（v1.1.2 历史方案）。读取集中在 `src/brush_converter/kpp/preset_xml.py::_texture_formula()`；
非法取值回退**默认公式 calibrated** 并向 stderr 打印一次警告。两条路径的实现都保留在
`preset_xml.py` 里，旧函数未删除。回退命令：

```bash
BRUSHCONVERTER_TEXTURE_FORMULA=legacy python cli.py convert tests/测试用.abr -o converted
# GUI 同理：启动 GUI 前设置同一环境变量即可
```

**历史方案（legacy，v1.1.2 及更早；非默认，可用环境变量回退）**

- 亮度 `b_k = round(0.10 - PS亮度/250, 2)`，clamp [-1,1]；
- 对比度 `c_k = k`（PS 中心因子），round 两位，clamp [0,2]。
- 问题（任务 10/12 实测）：它只是一个「经验标尺」，PS 纹理亮度为 0 时 Krita 仍写入 0.10
  （掩码整体压暗约 26/255）；PS 亮度在整段范围上系统性偏移。掩码空间差异比例 70.75%（61 组可表达参数组）。

**现行方案（calibrated，v1.2.0 起默认；A/B 掩码空间 0.00% vs 旧公式 70.75%）**

PS 侧前置变换（任务 12 拟合，`docs/研究报告/lut-拟合.md` 第 4 节）：对比度中心 `p=127`（0..255），
因子 `k`，且**顺序随对比度符号翻转**：

```text
C >= 0：先亮度（步内先截断到 0..255）后对比度   U = k·clip(t + b) + 127·(1-k)
C <  0：先对比度后亮度                          U = clip(k·t + 127·(1-k) + b)
```

Krita 侧前置（`KisTextureMaskInfo::recalculateMask()`）：先 `mask -= brightness`，再
`mask = (mask-0.5)*contrast + 0.5`，最后统一 clamp 0..1；换成 0..255 尺度即
`U_k = k·t - 255·k·b_k + 127.5·(1-k)`。把两侧在未截断区逐项对齐，解出唯一闭式：

```text
c_k = k
b_k = (1-k)/(510k) - b/255        （C >= 0，亮度在对比度之前）
b_k = (1-k)/(510k) - b/(255k)     （C <  0，亮度在对比度之后 → 反解需除 k）
```

其中 `(1-k)/(510k)` 是「Krita 对比度中心 127.5」与「PS 中心 127」的半点修正。
截断区可证明等价：C≥0 时 PS 的「先截断亮度」与 Krita 的「最后统一截断」在饱和区给出同样的 0/255。
所以该闭式在整个可表达定义域内逐点等价，不需要数值搜索。

**A/B 结论（掩码空间，tol=2，纹理 0..255 全取值；详见 `docs/验收报告/13-报告.md`）**

| 范围 | legacy 差异比例 / 最大差 | calibrated 差异比例 / 最大差 |
|---|---|---|
| 参数网格中可表达的 61/81 组 | 70.75% / 最高 140 | **0.00% / ≤1** |
| 真实纹理笔刷 15 支（测试用.abr 6 + 海怪…18支.abr 9） | 14 支 40.23%–100% | **14 支 0.00% / ≤1**（剩余 1 支 C=84 属结构性） |
| 真实 PS 真值图 29 张（可表达参数，d100 正片叠底/减去） | 74.53% / 57 | **0.00% / 1** |

**局限（如实记录，不回避）**

1. **Krita 对比度硬上限 2**：PS 对比度 > 50 时 `k > 2`，无法表达（C=75 需 4、C=100 为二值化极限）；
   本任务按 2 截断，属结构性失真。建议 PS 侧对比度控制在 ≤ 50。
   实测「KYLE 的橡皮擦 - 自然边缘」C=84 就是这种情况。
2. **Krita 亮度范围 [-1,1]**：C<0 且反解出的 `b_k` 越界时按边界截断。实测阈值约
   `C=-50 且 PS 亮度 < -127 或 > 127`（参数网格中 b=±150 与 C=-50 的两组），属结构性。
3. **Krita 纹理强度上限 1.0 与 PS 深度 > 83% 的冲突（任务 12 指出）**：PS 高度/线性高度用
   `12·q`（`q = round(depth·2.55)`）加权，Krita 同一算子写 `10·s`（`s = Texture/Strength/Value`），
   换算约 `strength ≈ 1.2·depth/100`；因此 PS 深度 > 约 83% 时 Krita 无法精确表达（会提前饱和）。
   本任务不改深度映射，只在此记录；要精确表达需另开任务（例如改用非 Photoshop 版 Height 算子或拆分强度）。
4. **C=+100 理论二值化边界**：允许 ±128 的单像素翻转（任务 12 第 5.4 节）。
5. **判据口径**：本节的 A/B 是「掩码空间」同引擎判据——Krita 掩码复刻（`tools/texture_mask.py`，
   对 Krita 真值渲染 23/23 张 ≤2%、最大差 1）vs PS 拟合掩码（任务 12 参数化公式 0.01%）。
   「PS 图 vs Krita 图」整图比对 73.13%~100.00%（任务 09 组装验收；任务 10 的跨引擎基线最小
   22.36%）结构性不可达，不能作为转正判据。
6. **默认值与回退**：v1.2.0 起默认公式为 `calibrated`。旧公式完整保留，可一键回退：
   `BRUSHCONVERTER_TEXTURE_FORMULA=legacy python cli.py convert ...`（GUI 同理，启动前设环境变量）。
   非法取值回退默认 `calibrated` 并警告一次。转正依据与回归数字见
   `docs/验收报告/13-报告.md` 的「转正执行」一节。
7. **calibrated 的 3 位小数**：依据是 Krita `KisTextureOptionData::read` 用 `getDouble` 原样读取
   （`research/krita_texture/KisTextureOptionData.cpp:17`）；2 位小数在 k=2 时舍入误差达 2.55 灰度级，
   不满足 ≤2% 判据。若用户在 Krita 里打开「笔刷设置」编辑器，控件可能把值舍入到两位
   （潜在 ≤3 灰度级偏差）——建议人工在 Krita 里抽查 1 支笔。

**v1.2.1 追加的两条（均由实验实测证实，不是理论推测）**

8. **高度系纹理强度增益 ×1.2（PS W=12 vs Krita W=10）**。Photoshop 的「高度 / 线性高度」系算子
   按 `12·q` 加权（`q = round(2.55·depth)`），Krita 同名 Photoshop 算子写 `10·s`
   （`s = Texture/Strength/Value`），归一化后两侧固定相差 `2q`，与量化无关
   （d10 时两侧 q 同为 26，量化阶差最多 2 灰度级）。修法：高度系
   `Texture/Strength/Value = min(1.0, 1.2 × depth/100)`，压感曲线路径的 0 端点同样用
   `min(1.0, 1.2 × depth_min/100)`；**非高度系（multiply / subtract / hardMix）一律不动**。
   - 依据：`docs/验收报告/实验-归因-报告.md` §4.2/§4.3（根因 `PS=12q−u` vs `Krita=10q−u`，差 2q）；
     `docs/验收报告/实验-Krita侧-报告.md` §17（2026-11-19 fixA 实测）。
   - 实测（d10/scale=100，PS 真值对照）：mean\|Δ\| **43.31 → 1.39**、max **52 → 2**、
     超阈像素 **95.36% → 0.00%**，与模型预测完全一致。
   - d60/d100 两侧本就饱和（Alpha 恒 255），加增益后不变（d100 被 1.0 封顶，与旧行为相同）。
   - 常数集中在 `src/brush_converter/convert.py` 的 `HEIGHT_STRENGTH_GAIN = 1.2`
     与 `_HEIGHT_BLEND_MODES`，换算函数 `_texture_strength()` / `_texture_strength_min()`。
   - 不覆盖：scale≠100% 的残差属缩放重采样（PS vs Krita Qt 平滑），**不应再调 1.2 系数**
     （调系数会破坏 s100 已取得的 0.00%，见 §17.4）。

9. **纹理资源命名与内嵌字节 tEXt 标记（Krita 5.3.3 两个静默坑）**。v1.2.1 起：
   - 内嵌纹理 PNG 在 IEND 前追加一个只含元数据的 **tEXt 块**（keyword=`brushconverter`，
     内容=`sha256=<像素数组 sha256 hex>`），**像素数据逐字节不变**（IDAT 与源 PNG 相同）；
   - 图案资源名改为 `tex_<sha256(内嵌PNG字节)前12>.png`（旧版 `tex_<uuid前8>.png` 与内容无关）；
   - `Texture/Pattern/PatternMD5Sum` 仍是**内嵌字节**的 md5（含元数据块），与资源
     `md5sum` 一致。
   - 坑 1（**同 md5 → 静默禁用**）：内嵌纹理与用户图案库里的文件 md5 相同时，Krita 5.3.3
     解析该内嵌资源失败，纹理被静默禁用——预设加载正常、字段回读全对，但落笔 Alpha 恒 255，
     无任何报错（首轮 52 张实验图全部作废）。追加 tEXt 块后内嵌字节 md5 必然不同，实测恢复正常。
   - 坑 2（**同名 → 串用图案**）：资源名与库内图案同名时会被遮蔽，不同字节也渲染出库内那张
     错图（实测 `tex_66e2987f.png` 撞车，Alpha 均值 211.6）。内容哈希命名保证「重名 ⇒ 同内容」。
   - 为什么不能只改名字：md5 去重坑还在；为什么不能只在撞车时才加标记：转换器不知道接收方
     Krita 图案库里有什么文件，无法预判撞车。所以两件事必须一起做、且每次都做。
   - 依据：`docs/验收报告/实验-Krita侧-报告.md` §8.2；修复回归用例
     `tests/test_texture_v121_fixes.py`。

### 7.2 纹理混合模式逐模式结论（v1.3.0；`docs/数据/mode-table.json` 为唯一真相）

判定口径：PS 真值 vs Krita 渲染，同一支笔、同一纹理、6 组参数（深度 10/60/100 × 亮度对比度两档），
差异比例 ≤2% 且 max_delta ≤2 才算达标，分母 W×H。判据材料是**单笔尖满覆盖（base=1）+ 单纹理**，
所以下面所有结论只在这个口径成立。

**47 个模式：可映射 22 / 需校准 1 / 不可映射 24 / 不可判定 0（无结论 0）。**

| 结论 | 条数 | 模式（order） |
|---|---:|---|
| 可映射·证据强 | 2 | `Mltp`(4)、`Sbtr`(20)：自有 Krita 同模式真机渲染，两侧非饱和，6/6 组 0.00% / max≤1 |
| 可映射·证据中 | 4 | `Dfrn`(11)、`blendSubtraction`(42)、`height`(38)、`linearHeight`(39)：跨模式候选或部分组两侧饱和 |
| 可映射·证据弱-饱和 | 4 | `Ovrl`、`CDdg`、`CBrn`、`linearDodge`：两侧 6/6 组饱和，0.00% 只证明「不冲突」 |
| 可映射·证据弱-退化 | 1 | `linearBurn`：base=1 数学退化（9 号与 0 号在本材料上同像素） |
| 可映射·证据弱-塌缩 | 11 | `Nrml`×2、`Bhnd`、`Clar`、`H`、`Strt`、`Clr`、`Lmns`、`passThrough`、`lighterColor`、`darkerColor` |
| 需校准 | 1 | `hardMix`(21)：1.03% / max 255，边界残差已定位，**转换侧无参数可修** |
| 不可映射 | 24 | 见下两分类（16 + 8） |
| 不可判定 | 0 | 原 A 批 4 个「真值饱和」按「Krita 枚举有无该成员」改判 |

⚠ **0.00% 不等于语义等价。** 39 个有 PS 真值的模式在 6 组参数下只出 **12 类**像素，
**31 个模式与别的模式像素完全不可区分**；本表凡写 0.00% 的地方，含义一律是「当前口径下像素不可区分」。
`height` 与 `linearHeight` 两侧都逐像素不可区分（Krita 14/15 渲染 max Δ=0），**不能**写成「linearHeight 已精确复现」。

**① Krita 侧没有对应能力（结构性不可映射，16 条）**

| 模式 | 一句话原因 |
|---|---|
| `Dslv` Dissolve | Krita 的 16 个纹理模式全是确定性算子，没有随机溶解 |
| `Scrn` Screen | Krita 纹理模式枚举里没有「滤色」 |
| `SftL` Soft Light | 枚举里没有「柔光」 |
| `HrdL` Hard Light | 枚举里没有「强光」；最接近的候选也差 2.79% 以上 |
| `Drkn` Darken | Krita 有「变暗」，但 PS 在纹理上按深度换算子，单个 Krita 模式覆盖不了 |
| `Lghn` Lighten | 枚举里没有「变亮」 |
| `Xclu` Exclusion | 枚举里没有「排除」；与「减去」的 0.00% 属 base=1 退化 |
| `linearLight` | 枚举里没有「线性光」；最近候选 7.24% 起 |
| `vividLight` | 枚举里没有「亮光」 |
| `pinLight` | 枚举里没有「点光」；最近候选 2.79% 起 |
| `SnrB` Burn-Shadow | 枚举里没有该变体（真名 `BrnS`，PS 真值为常量 255） |
| `MnrB` Burn-Midtone | 同上（真名 `BrnM`，常量 255） |
| `HnrB` Burn-Highlight | 同上（真名 `BrnH`，常量 242/178/127；最佳替代 59.80% 起） |
| `ttsD`（Dstt） | 同上（真名 `Dstt`，常量 249/217/192；最佳替代 65.40% 起） |
| `Str ` Saturation | 枚举里没有「饱和度」 |
| `blendDivide` Divide | 枚举里没有「划分」（PS 真值恒 255） |

**② PS 侧产不出可比真值（8 条）**：`Add`、`Rvrt`、`SgdD`、`MgdD`、`HgdD`、`Lght`、`Grdn`、`HdMxS`。
这些模式 PS 不接受/忽略了枚举值，或落笔直接报错（`Add` 提示该模式不可用于此版本；
`SgdD/MgdD/HgdD` 是 Ghidra 小端反序伪名，真名为 `DdgS/DdgM/DdgH`），拿不到可逐像素比对的真值。
结论由枚举与静态证据给出，**不等于**「Krita 一定没有对应」。

**v1.3.0 登记变更**：`_TEXTURING_MODE` 由 19 键扩到 **34 键（新增 15 个）**。三处是实质修复：
`Dfrn`（Difference）实测 PS 侧像素 ≡ 减去 → SUBTRACT(1)（旧行为回退 Multiply，实测差 69.04%~99.29%）；
`blendSubtraction` 同样 → SUBTRACT(1)（旧行为回退 Multiply 属「把减去当正片叠底」的错误）；
`linearDodge`/`linearBurn` 显式登记 8/9（旧行为静默回退 Multiply，差 96.11%~100%）。
其余新增键（`Mltp`、`Nrml`、`Bhnd`、`Clar`、`H   `、`Strt`、`Clr `、`Lmns`、`passThrough`、
`lighterColor`、`darkerColor`）让已实测达标的模式不再落进回退分支。

> brightness/contrast 的证据口径：改这两个值确实**会改像素**（multiply 97.45%、linearHeight 67.31% 的改值差异），
> 说明它们是像素通路的真实输入；但**具体哪段代码消费它们算像素尚未定位**，所以公式是按实测校准的，不是按源码复刻的。

## 8. 控制源编码（bVTy → Krita 传感器）

| bVTy | Photoshop | Krita 传感器 | 状态 |
|------|-----------|--------------|------|
| 0 | 关 | 无 | ✅ |
| 1 | 渐隐 | 无对应（未映射） | ⛔ |
| 2 | 压力 | `pressure` | ✅ |
| 3 | 倾斜 | `tilt`（预留，未启用） | ⛔ |
| 4 | 转轮 | 无对应 | ⛔ |
| 5 | 旋转随机 | 无对应（未映射；jitter>0 时在 GUI 提示） | ⛔ |
| 6 / 7 | 初始方向/方向 | `drawingangle` | ✅ |

## 9. 去重规则

- 完全重复的预设（同名 + 同 UUID + 同 Dmtr）只保留首个。当前文件因此去掉 1 个重复的「淘宝店」横幅笔刷（20 → 19）。
