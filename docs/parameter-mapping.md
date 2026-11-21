# ABR → Krita 完整参数映射清单

> 引擎：现阶段仅像素引擎（paintbrush）。源数据来自 `海怪笔刷…18支.abr`（v6.2，20 预设）。
> ✅ = 已实现，⛔ = 不映射（原因见右侧），⚠️ = 已尽量映射但仍保留提示（如 `TxtC` / `protectTexture` 在 Krita 无对应）；
> ❓ = 待确认（**历史记号**，本表现已无待确认项：早期两处已全部落地，见任务 16 的 `docs/归档/文档对齐报告.md`）。

## 1. 笔尖与基础属性

| ABR 键 | 含义 | → Krita | 映射公式 | 状态 |
|--------|------|---------|----------|------|
| `samp` 位图 + `sampledData` UUID | 采样笔尖 | png_brush 内嵌笔尖 | 灰度 PNG，白=墨（base64 内嵌） | ✅ |
| `Brsh.Dmtr` | 主直径 px | `scale` | `scale = Dmtr / max(笔尖宽, 笔尖高)`；采样笔尖内嵌 PNG 补零为正方形 | ✅（Krita 显示尺寸修正） |
| `Brsh.Spcn` | 间距 % | `spacing` | 计算笔刷：`spacing = Spcn / 100`；采样笔刷：`spacing = Spcn/100 × 原始笔尖短边/长边`（v1.4.0 任务 107 基准补偿） | ✅ / ⚠️需校准（仅非方形 100% 点，见 §10.1） |
| `Brsh.Angl` | 角度（度） | `angle` | `math.radians(Angl % 360)`（度→弧度，负角先 normalize 到 [0,360)） | ✅ |
| `Brsh.Rndn` | 圆度 % | （采样笔刷不映射，见注①） | — | ⛔ |
| `Brsh.Hrdn` | 硬度 % | （采样笔刷不映射，见注①） | — | ⛔ |
| `Nm ` | 笔刷名 | preset name | 直接 | ✅ |

> **v1.4.0（任务 107）已证实并修正**（原 2026-11-20 的「待验证」反馈已闭环）：
> 补白把 Krita 的尺寸/间距基准从「原始笔尖短边」换成了「补白后正方形边长（=长边）」，
> PS 对非方形笔尖的间距基准是渲染后短边 → 非方形笔刷的 Krita 步长被放大 ≈ 长宽比。
> 任务 106 实测（`docs/研究报告/参数偏差-2026-11-20.md` §4）：Krita/PS 步长比
> 1.253 / 1.281 / 1.325（长宽比 1.280），方形组 1.013–1.020，计算笔刷 0.982–1.000。
> 修法**只动间距换算这一层**：`spacing × 短边/长边`（比值来自 `BrushPreset.spacing_basis`，
> 由 `mapping.py` 从原始笔尖宽高算出、在 `convert._sampled_brush_def` 里乘上）。
> **补白代码一行未改**（铁律 17）；间距基准比为**严格正方形=1；近方形按实际短/长边；计算笔刷不补偿**
> （如 86×84 → 0.977，方形组实测误差 1.69–2.01% → 0.68–1.18%；计算笔刷 spacing 原样，走 `_computed_brush_def()`）。
> 非方形三点实测：25.28%→**0.11%**、28.09%→**0.18%**、32.46%→**3.53%**（最后一点超阈，
> 原因见 §10.1）。

> 注①：圆度/硬度只对「计算笔刷」有意义（采样笔刷的圆度已固化在笔尖位图里）。

## 2. 计算笔刷（无位图 → auto_brush）

| ABR 键 | → Krita | 映射公式 | 状态 |
|--------|---------|----------|------|
| `Dmtr` | `MaskGenerator.diameter` | 直接 | ✅ |
| `Hrdn` | `hfade`/`vfade` | `Hrdn / 100`（fade=实心区占比，与硬度同向） | ⚠️需校准（h=0 结构性不可达，见 §10.2） |
| `Rndn` | `ratio` | `Rndn / 100` | ✅ |
| `Angl` | `angle` | `math.radians(Angl % 360)`（度→弧度，负角 normalize） | ✅ |
| `Spcn` | `spacing` | `Spcn / 100` | ✅ |

> 注①：圆度/硬度只对「计算笔刷」有意义。硬度映射 `fade = Hrdn/100`（Krita 的 `hfade/vfade` 是「实心区占半径比例」，硬度 100%→1.0 硬边、0%→0.0 全软，与 Photoshop 硬度同向，不是 `(100-Hrdn)/100`）。角度 `angle` 在 Krita XML 里存**弧度**、UI 范围 **[0,360) 度**，负角度需先 `% 360` normalize，否则被角度控件 clamp 到 0。
> **v1.4.0（任务 107）实测：硬度判「需校准」**，代码未改（证据见 §10.2）。

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

> **传感器默认（v1.4.0 起，任务 112 人工复验 + 2026-11-21 修正）**：`.kpp` 基础模板的 17 组
> `*Sensor` 必须写**合法的传感器 XML**（默认写 `pressure` 占位），未映射动态用
> `Pressure*=false` + `*UseCurve=false` 关闭。**禁止写空 `<params/>`**：Krita 5.3.3 的
> `KisKritaSensorPack::read()` 会在 sensorId 为空时触发 `!sensorId.isEmpty()` 断言并早退，
> 后面的 `UseCurve` / `Value` / `commonCurve` 全部不读，界面就会显示旧曲线或错误数值
> （实测出现散布 500%、效果强度 9% 等）。`d448d24` 的「空传感器」写法本身不完整，现已改为
> 合法 `pressure` 占位（回归 `tests/test_sensor_defaults_v140.py`）。**旧版转换出的 `.kpp`
> 必须重转**；此前把界面异常归因为 Krita UI bug 的结论已撤回。

## 5. 散布（Scattering）

| ABR 键 | 含义 | → Krita | 映射公式 | 状态 |
|--------|------|---------|----------|------|
| `useScatter` | 启用散布 | `PressureScatter=true`（散布开关） | — | ✅ |
| `scatterDynamics.jitter` | 散布量（0–1000%） | `ScatterValue`（0–5.0） | `ScatterValue = jitter / 400` | ⚠️需校准（残差 39.7% / 51.1%，见 §10.3） |
| `scatterDynamics.bVTy==2` | 散布量随压感 | `ScatterSensor=pressure` + `ScatterUseCurve=true` | — | ✅ |
| `scatterDynamics.bVTy==0` | 散布恒定 | `ScatterSensor=pressure`（合法占位）+ `ScatterUseCurve=false`（关闭曲线） | — | ✅ |
| `bothAxes` | 两轴 | `AxisX`/`AxisY` | True→AxisX=Y=True；False→AxisX=False,Y=True | ✅ |
| `Spcn`（顶层） | 散布间距 | — | 不映射到散布量（见注②） | ⛔ |
| `Cnt`（数量） | — | — | Krita 像素引擎无「数量」 | ⛔ |
| `countDynamics` | — | — | 同上 | ⛔ |

> 注②：Krita 里 `PressureScatter` 是「散布选项是否启用」的开关（`isChecked` 序列化名），并非"压感控制"；压感控制由 `ScatterSensor` 决定。散布量真正来源是 `scatterDynamics.jitter`（顶层 `Spcn` 实为间距，非散布量）。
>
> 散布量换算 `÷400`：**v1.4.0（任务 107）实测后确认无法用确定性换算修到 ≤2%，保留原系数并判「需校准」**（残差、拟合式与回退理由见 §10.3）。旧说明（PS 高斯分布 vs Krita 均匀分布、锚点 PS 145% ≈ Krita 35%）仍成立，但只解释了「为什么需要系数」，解释不了「为什么 100%/300% 同时偏小近一半」。

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
> `docs/developer-guide.md` 与 `docs/归档/texture-mapping-proposal.md`。

| ABR 键 | → Krita 参数 | 映射公式 | 状态 |
|--------|--------------|----------|------|
| `useTexture` | `Texture/Pattern/Enabled` | `true` | ✅ |
| `Txtr.Idnt` | 资源查找 | patt 记录按 UUID 匹配，PNG 内嵌 | ✅ |
| `Txtr.Nm` | `Name` / `PatternFileName` | 资源名由内嵌字节派生：`tex_<sha256(内嵌PNG)前12>.png`（v1.2.1；旧版 `tex_<uuid前8>.png` 与内容无关，会与用户图案库撞名） | ✅ |
| PNG 字节 | `PatternMD5Sum` | md5 hex（`PatternMD5` 留空，绕开 Krita 5.0 写二进制 bug） | ✅ |
| `textureScale`（%） | `Texture/Pattern/Scale` | `textureScale/100`，clamp [0.01, 10] | ⚠️需校准（200% 残差 32.4%，见 §10.4；50%/100% 已达标：0.00%/max 1、0.00%/max 0） |
| `textureDepth`（%） | `Texture/Strength/Value` | 高度系 `min(1, 1.2×textureDepth/100)`；非高度系 `textureDepth/100`（0..1，v1.2.1） | ✅ |
| `textureDepthDynamics.bVTy==2` | `PressureTexture/Strength/` + `Texture/Strength/UseCurve` | `true` + `true`（压感曲线，最小值见下） | ✅ |
| `textureDepthDynamics.bVTy==0` | `PressureTexture/Strength/` + `Texture/Strength/UseCurve` | `true` + `false`（效果强度开启、无传感器，恒定用 Value） | ✅ |
| `textureDepthDynamics.Mnm` | `Texture/Strength/commonCurve` | 高度系 `0,min(1,1.2×Mnm/100);1,1;`；非高度系 `0,Mnm/100;1,1;`（仅 bVTy==2，v1.2.1） | ✅ |
| `InvT` | `Texture/Pattern/Invert` | 直接 | ✅ |
| `textureBlendMode` | `Texture/Pattern/TexturingMode` | 按 `mapping._TEXTURING_MODE`（v1.3.0 起 **34 个键**）取 Krita 枚举值：`linearHeight`→15、`height`→14、`hardMix`→10、`Mltp`→0、`Sbtr`→1、`Dfrn`→1、`blendSubtraction`→1、`Ovrl`→5、`CDdg`→6、`CBrn`→7、`linearDodge`→8、`linearBurn`→9 等；未登记的模式回退 Multiply(0) 并保留一条警告 | UI 10 项：✅ 8 / 🧪 1 / ⛔ 1（另 37 内部值不计限制，见 §7.2） |
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

**用户视角：PS「纹理 > 模式」UI 只有 10 项可选——可映射 8 / 需校准 1（hardMix）/ 不可映射 1（Drkn 变暗）。** 其余 37 项是 PS 内部枚举值（`evidence_grade=C`），UI 选不到、与用户无关，**不计为转换限制**。

**完整枚举研究归档（47 行）：可映射 22 / 需校准 1 / 不可映射 24 / 不可判定 0（无结论 0）。** 下表是这个完整口径的技术细节，不是用户可见的限制清单。

> **「不可映射」≠「UI 不可见」**：PS「纹理 > 模式」下拉实际只有 10 项——正片叠底(`Mltp`)、
> 减去(`Sbtr`)、变暗(`Drkn`)、叠加(`Ovrl`)、颜色减淡(`CDdg`)、颜色加深(`CBrn`)、线性加深(`linearBurn`)、
> 实色混合(`hardMix`)、线性高度(`linearHeight`)、高度(`height`)。这 10 项里 9 项有可用映射
> （`hardMix` 需校准），`Drkn` 实测不可映射（PS 在纹理上随深度换算子）。其余 37 个模式不在 PS 下拉里，
> 证据等级一律 C（本轮无法投入验证），**不一定**不可映射；真正判「不可映射」的是下面 24 个（有结构/真值证据）。

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

> 下面 24 条「不可映射」中，**只有 `Drkn`（变暗）是 PS UI 可选模式**；其余 23 条都是 UI 选不到的内部枚举值，对用户不构成限制，仅作完整枚举研究归档。

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

## 10. 参数偏差实测与残差（v1.4.0 任务 106/107；判据 2% / max_delta 2）

> 证据表：`docs/研究报告/参数偏差-2026-11-20.md`（106）；修正报告：`docs/验收报告/107-报告.md`（107）。
> 数字全部可用下面命令重算（只读已入库的 PS 真值图与 Krita 图，不重渲）：
> ```
> python tools/measure_deviation.py --param spacing  --values 10,50,100 --analyze
> python tools/measure_deviation.py --param hardness --values 0,50,100   --analyze
> python tools/measure_deviation.py --param scatter  --values 0,100,300  --analyze
> python tools/measure_deviation.py --param scale    --values 50,100,200 --analyze
> ```
> 要重渲 Krita 侧加 `--force`（`--skip-ps` 保留 PS 真值图）。**散布点别随手 `--force`**：
> 每次会话随机种子不同，带高会变（±7–13%），会把入库证据覆盖成另一组数。

### 10.1 间距（只对采样笔尖补偿短边基准）

| 组 | 10% | 50% | 100% | 判定 |
|---|---|---|---|---|
| computed（无补白） | 不可判读（调制 0.22%） | 1.88% → 1.88%（未动） | 0.55% → 0.55%（未动） | 一致 |
| square（86×84） | 1.69% → **0.68%** | 2.01% → **0.37%** | 1.30% → **1.18%** | 一致 |
| nonsquare（183×143） | 25.28% → **0.11%** | 28.09% → **0.18%** | 32.46% → **3.53%** | 1/3 点超阈 |

**残差（3.53%）的来源**：PS 侧 100% 点自身偏离「短边模型」——按短边算应 87.5px，
实测 84.51px（−3.4%，该点只有约 3.6 个周期，属少周期估计偏差，见 106 §4.2）；
Krita 侧补偿后恒等于 87.49px（=112×143/183，与标称 1:1）。补偿后的 10%/50% 两点
（0.11%/0.18%）证明「短边基准」这条换算关系本身是对的，100% 点的 3.53% 是 PS 量测残差，
不是换算错——**转换侧再调系数只会把 10%/50% 弄坏**，故到此为止。

另：非方形笔刷的**笔迹本身**在 Krita 里仍比 PS 高大 ~1.3×（补白给笔尖上下各加了墨色边，
106 §4.3）。本任务按铁律 17 不改补白，所以「间距已对齐、笔迹仍偏大」这句观感是准确的。

### 10.2 硬度（h=0 结构性不可达 → 整参数需校准，代码未改）

| 硬度 | PS 过渡宽 | Krita 过渡宽 | Δ | 结论 |
|---|---|---|---|---|
| 0% | 78.75px | 63.15px | **+15.60px（19.8%）** | 超阈 |
| 50% | 32.38px | 39.71px | −7.33px（22.6%） | 超阈（可单点修） |
| 100% | 1.88px | 0.81px | 1.07px | 一致（绝对差 ≤2px） |

- **h=0 不可达**：Krita 的 `hfade` 是 [0,1]，`fade=0` 已是最软档，实测过渡宽上限 63.15px，
  小于 PS 的 78.75px。要再宽只能改直径或加 `Sharpness/softness`（模糊外扩），
  而直径属「尺寸」参数（106 判不偏、禁止改），softness 会同时影响所有硬度档 → 不给硬凑。
- **h=50 可单点修**：107 探针实测 `fade=0.59 → 33.14px`、`fade=0.60 → 32.39px`
  （PS 32.38px，差 0.03%），即 `fade ≈ 1.2×Hrdn/100` 能对上 50%。但 h=0 仍 19.8%，
  整参数判据过不了，按 107 任务口径**回退不改**（避免用单点经验系数掩盖 h=0 的结构性差异）。
  下一轮若要修，必须同时解决「软边尺寸基准」（PS 软边会伸出标称直径之外，Krita 停在半径处）。
- h=100 的两侧「绝对差 1.07px」属抗锯齿量级，不算偏差。

### 10.3 散布（PS 响应超线性 + 统计复现性差 → 需校准，代码未改）

| 取值 | PS 带高 | Krita 带高（jitter/400） | 增量 PS/Krita | 残差 |
|---|---|---|---|---|
| 0% | 102 | 100 | —（锚点） | — |
| 100% | 165 | 138 | 63 / 38 | **39.7%** |
| 300% | 376 | 234 | 274 / 134 | **51.1%** |

107 实测后回退的过程（全部用 `tools/measure_deviation.py` 同一套渲染/统计）：

1. Krita 侧响应近似线性：`带高 ≈ 100 + 158·s`（`ScatterValue=s`，13 张探针，
   斜率 129–183，源码依据 `research/KisScatterOption.cpp:36`：偏移 = U(−1,1)·diameter·s）。
2. PS 侧响应超线性：增量 63 → 274 对应 jitter ×3，得 `G(j)=0.4883j+0.0014167j²`。
3. 反解 s=G/158 → 二次式 `s=0.34j′+0.07j′²`（j′=jitter/100），实测：
   100% 增量 71/66（目标 63，+12.7%/+4.8%）、300% 增量 275/197（目标 274，+0.4%/−28.1%）。
4. **同一配置 300% 两次复现差 78px（297↔375）**——`band_height` 是「约 10 个笔尖里的极值」，
   每次会话随机源不同（实测同参数两次带高 138/142/129）。判据是 2%，而该统计量的复现性
   就有 ±7–13% → **无法证明 ≤2%**，按 107 任务口径回退，保留 `jitter/400`。
5. 下一轮建议：换分布型统计量（如 Alpha 加权行标准差 + 多次重复平均）或固定随机种子再谈系数。

### 10.4 缩放（纹理 scale）

| 取值 | 差异比例 | max_delta | 相对残差 | 结论 |
|---|---|---|---|---|
| 50% | 0.00% | 1 | 0.8% | 一致 |
| 100% | 0.00% | 0 | 0（锚点） | 一致 |
| 200% | **93.72%** | **93** | **32.4%** | 需校准 |

**200% 相对残差 32.4%，是 Krita Qt 重采样差异，转换侧无常数可修**（107 决策：不改代码）。
100×100 画布上放大的图案由 Krita 自己的缩放算法生成，PS 与 Qt 的插值核不同；残差与
depth 无关、在 S=200 固定 32.4%（`docs/研究报告/缩放深度-归因实验.md` §0），
调任何映射常数都只会把已一致的 50%/100% 弄坏。
