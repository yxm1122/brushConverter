# ABR → Krita 完整参数映射清单

> 引擎：现阶段仅像素引擎（paintbrush）。源数据来自 `海怪笔刷…18支.abr`（v6.2，20 预设）。
> ✅ = 已实现，⛔ = 不映射（原因见右侧），⚠️ = 已尽量映射但仍保留提示（如 `TxtC` / `protectTexture` 在 Krita 无对应）；
> ❓ = 待确认（**历史记号**，本表现已无待确认项：早期两处已全部落地，见任务 16 的 `docs/归档/文档对齐报告.md`）。

## 1. 笔尖与基础属性

| ABR 键 | 含义 | → Krita | 映射公式 | 状态 |
|--------|------|---------|----------|------|
| `samp` 位图 + `sampledData` UUID | 采样笔尖 | png_brush 内嵌笔尖 | 灰度 PNG，**深=墨 / 255=白=透明**（ABR 原始字节 255=墨，`samples._decode_pixels` 做一次 `255−raw`；与 Krita 极性一致，见 developer-guide §7.1 第 2/3 条） | ✅ |
| `Brsh.Dmtr` | 主直径 px | `scale` | `scale = Dmtr / max(笔尖宽, 笔尖高)`；采样笔尖内嵌 PNG 补零为正方形 | ✅（Krita 显示尺寸修正） |
| `Brsh.Spcn` | 间距 % | `spacing` | 计算笔刷：`spacing = Spcn/100 × 标称直径/位图足印边长`（v1.5.0 起计算笔刷也是 png_brush，按足印反算基准）；采样笔刷：`spacing = Spcn/100 × 原始笔尖短边/长边`（v1.4.0 任务 107 基准补偿） | ✅ / ⚠️需校准（131 复测 9/9 ≤2%，见 §10.1） |
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
> 106 单周期实测：25.28%→**0.11%**、28.09%→**0.18%**、32.46%→**3.53%**；
> **131 用 2 次重复、9/9 组复测 ≤2%（最大 0.73%），3.53% 是 106 少周期量测伪差**（100% 点仅约 3.6 个周期，见 §10.1）。

> 注①：圆度/硬度只对「计算笔刷」有意义（采样笔刷的圆度已固化在笔尖位图里）。

## 2. 计算笔刷（无位图 → png_brush，v1.5.0 起）

> **v1.5.0（任务 133）把计算笔刷的承载从 `auto_brush` 换成按 PS 硬度剖面生成的采样位图 `png_brush`**：
> Krita `auto_brush` 的 `hfade/vfade` 是 [0,1] 线性剖面，PS 硬度剖面在低硬度端更宽，
> h0 过渡带实测 +15.60px（19.8%）属结构性不可达；改位图后 h0/h50/h100 过渡带
> **Δ0.858 / 0.058 / 0.485 px**（D=200，136 抽检几何级 3/3 过），h50 由 +7.33px 降到 +0.04px。
> 生成器 `src/brush_converter/hardness_tip.py`，剖面表 `HARDNESS_PROFILES`。

| ABR 键 | → Krita | 映射公式 | 状态 |
|--------|---------|----------|------|
| `Dmtr` | `BrushDef/size`（=位图边长） | 不直接写：位图边长 = `2·ceil(r_vis)+2`（h0/h50/h100 @ D=200 → **290/250/204**），`size` = 位图边长 = **PS 实际足印** | ✅（v1.5.0） |
| `Hrdn` | 位图（`png_brush`） | 按剖面表生成位图，**不再写 `hfade/vfade`** | ✅（v1.5.0；Krita 里硬度/fade 不可再调） |
| `Rndn` | 位图（`png_brush`） | 圆度 <100 时把剖面沿短轴压缩成椭圆再栅格化（轴序与旧 `ratio` 相同） | ✅ |
| `Angl` | `angle` | `math.radians(Angl % 360)`（度→弧度，负角 normalize） | ✅ |
| `Spcn` | `spacing` | `Spcn/100 × 标称直径/位图边长`（硬度越高位图越大，步长按标称直径反算，否则 h0 步长会被放大 1.45 倍） | ✅ |

> **代价（发布说明必写）**：① Krita 里 `fade`/硬度不可再调（已烘焙进位图）；② 尺寸显示值 = PS 实际足印
> （PS 标称 200 → Krita 290/250/204）；③ 每支笔多 3–12 KB（h100 @D=200 的 .kpp 5,588→9,143 B）。
> 生成位图必须**保持正方形 + 边框 255**（铁律 17；`build_tip` 不经 `_square_tip`，正方形与边框是
> 「径向剖面 + 外环强制归零」的结构性质，勿改回补白）。
> `auto_brush` 承载链路在 v1.5.0 起已不在生产路径（`convert._computed_brush_def` 保留但不再调用）。
> 证据：`docs/验收报告/133-报告.md` §二/§四、`docs/验收报告/136-报告.md` §2.1。

## 3. 形状动态（Shape Dynamics）

| ABR 键 | 条件 | → Krita | 映射公式 | 状态 |
|--------|------|---------|----------|------|
| `szVr.bVTy==2` | 压力 | `PressureSize=true` | — | ✅ |
| `minimumDiameter` | — | `SizecommonCurve` | `0,{minD/100};1,1;` | ✅ |
| `angleDynamics.bVTy==6` | 方向 | `RotationSensor=drawingangle` | sensorslist + fuzzy | ✅ |
| `angleDynamics.jitter` | `RotationValue`（旋转-效果强度） | fuzzy/fuzzystroke 曲线 | `RotationValue=min(1, jitter/50)`（v1.5.0 任务 134-2）；两条随机度曲线固定 `0,0;1,1;`（Krita UI 对应 -180°..+180°，XML 坐标归一化为 0..1） | ✅（j>50 饱和属 B 级未量测，见 §10.3） |
| `angleDynamics.bVTy==2` | 压力 | `RotationSensor=pressure` | — | ✅ |
| `angleDynamics.bVTy==0` 且 jitter>0 | 无控制源（纯随机抖动） | `PressureRotation=true` + fuzzy-only 传感器 | `RotationValue=min(1, jitter/50)`（v1.5.0 任务 134-2）；fuzzy/fuzzystroke 曲线 `0,0;1,1;`，**不含** drawingangle（不随笔迹方向） | ✅（j=50 跨度 309.90°、复渲 1.51%，见 §10.3） |
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
> （实测出现散布 500%、效果强度 9% 等）。`d448d24`（2026-11-21 历史重写前的短哈希，现已失效）的「空传感器」写法本身不完整，现已改为
> 合法 `pressure` 占位（回归 `tests/test_sensor_defaults_v140.py`）。**旧版转换出的 `.kpp`
> 必须重转**；此前把界面异常归因为 Krita UI bug 的结论已撤回。

## 5. 散布（Scattering）

| ABR 键 | 含义 | → Krita | 映射公式 | 状态 |
|--------|------|---------|----------|------|
| `useScatter` | 启用散布 | `PressureScatter=true`（散布开关） | — | ✅ |
| `scatterDynamics.jitter` | 散布量（0–1000%） | `ScatterValue`（0–5.0） | `ScatterValue = jitter / 200`（v1.5.0 任务 134-1；端点 1000%→5.0，不加 clamp） | ✅ / ⚠️需校准（a/D 0.65%/1.69%；1000% 端点未量测，见 §10.3） |
| `scatterDynamics.bVTy==2` | 散布量随压感 | `ScatterSensor=pressure` + `ScatterUseCurve=true` | — | ✅ |
| `scatterDynamics.bVTy==0` | 散布恒定 | `ScatterSensor=pressure`（合法占位）+ `ScatterUseCurve=false`（关闭曲线） | — | ✅ |
| `bothAxes` | 两轴 | `AxisX`/`AxisY` | True→AxisX=Y=True；False→AxisX=False,Y=True | ✅ |
| `Spcn`（顶层） | 散布间距 | — | 不映射到散布量（见注②） | ⛔ |
| `Cnt`（数量） | — | — | Krita 像素引擎无「数量」 | ⛔ |
| `countDynamics` | — | — | 同上 | ⛔ |

> 注②：Krita 里 `PressureScatter` 是「散布选项是否启用」的开关（`isChecked` 序列化名），并非"压感控制"；压感控制由 `ScatterSensor` 决定。散布量真正来源是 `scatterDynamics.jitter`（顶层 `Spcn` 实为间距，非散布量）。
>
> 散布量换算 `÷200`（v1.5.0 任务 134-1 落地）：旧 `÷400` 来自 107 的单点锚定（PS 145% ≈ Krita 35%），
> 129 用 60 样本、分布型统计量（a/D）实测判定 `÷400` 在 5/5 配置超 10%（最大 50.01%）、`÷200` 最大相对差 0.21%；
> 134 端到端抽检 a/D 相对差 **0.65%（j=100）/ 1.69%（j=300）**，两次渲染复现 0.08%/0.24%。
> 散布档与旋转档是**两把尺子**（散布 `j/200`、旋转 `min(1, j/50)`），禁止共用系数。
> 残差：1000% 端点只做参数/单测、端到端只到 300%（见 §10.3 的 B7）。

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
| `textureScale`（%） | `Texture/Pattern/Scale` | **v1.5.0 起**：s>100 先用 PS 同核预缩放把纹理重采样后内嵌、`Scale` 写 1.0；s≤100 仍 `textureScale/100`；两处写入 clamp 已解除（Krita 读取无 clamp，UI 滑块 0..2） | ✅（200% 预缩放后 0.00%/max 1，见 §10.4；<100% 核未量测） |
| `textureDepth`（%） | `Texture/Strength/Value` | 高度系 `min(1, 1.2×textureDepth/100)`；非高度系 `textureDepth/100`（0..1，v1.2.1） | ✅（>83% 是 Krita Strength≤1.0 参数域上限、饱和段无像素后果，见 §7.1 局限 3 与 §10.4） |
| `textureDepthDynamics.bVTy==2` | `PressureTexture/Strength/` + `Texture/Strength/UseCurve` | `true` + `true`（压感曲线，最小值见下） | ✅ |
| `textureDepthDynamics.bVTy==0` | `PressureTexture/Strength/` + `Texture/Strength/UseCurve` | `true` + `false`（效果强度开启、无传感器，恒定用 Value） | ✅ |
| `textureDepthDynamics.Mnm` | `Texture/Strength/commonCurve` | **v1.5.0（135-5）0 端点写比值** `0,s_m/s_d;1,1;`（`s_m` = 最小深度对应强度、`s_d` = 深度对应强度；各自高度系 ×1.2 后封顶）。Krita 强度是乘性曲线，比值可避免低压力端二次相乘（仅 bVTy==2） | ✅ |
| `InvT` | `Texture/Pattern/Invert` | 直接 | ✅ |
| `textureBlendMode` | `Texture/Pattern/TexturingMode` | 按 `mapping._TEXTURING_MODE`（v1.3.0 起 **34 个键**）取 Krita 枚举值：`linearHeight`→15、`height`→14、`hardMix`→10、`Mltp`→0、`Sbtr`→1、`Dfrn`→1、`blendSubtraction`→1、`Ovrl`→5、`CDdg`→6、`CBrn`→7、`linearDodge`→8、`linearBurn`→9 等；未登记的模式回退 Multiply(0) 并保留一条警告；v1.5.0（135-2）另加：hardMix + 满覆盖笔尖 + 恒定深度 → Strength=1.0 + 亮度 ±1/255 偏置 | UI 10 项：✅ 8 / 🧪 1 / ⛔ 1（另 37 内部值不计限制，见 §7.2） |
| `textureBrightness`（PS -150..150） | `Texture/Pattern/Brightness` | `b_k = (1-k)/(510k) - v/255`（C≥0）或 `(1-k)/(510k) - v/(255k)`（C<0），保留 3 位小数，写入 clamp [-1,1]；满覆盖笔尖 + 恒定深度时另写 ±1/255 偏置（135-2）。越界区（C=−50 且 |v|>127）8/12 组超阈、最大 61.125%，只登记不落地（B10） | ✅ / ⚠️（v1.5.0 起唯一公式，legacy 已删除） |
| `textureContrast`（PS -50..100） | `Texture/Pattern/Contrast` | 中心因子 `k`（C<0：1+C/100；C≥0：1/(1-C/100)）**原值直写、不再 min(2.0, k) 截断**（135-4）；C>50 用 9 位小数 + `_format_number`；写入域无 clamp（Krita 读取无 clamp，仅 UI 滑块 0..2 显示越界） | ✅（C=84→6.25、C=100→1e6，见 §10.4） |
| `TxtC` / `interpretation` | — | Krita 无对应，静默忽略（源文件默认值） | ⚠️ |
| `protectTexture` | — | Krita 无对应，保留轻量警告 | ⚠️ |

> 亮/对比度映射依据 Krita `KisTextureMaskInfo::recalculateMask()` 与 `research/测试结果2`、`research/krita结果`：
> 所有模式统一使用普通校准（Linear Height (Photoshop) 曾用 0.30 基线/对比度倒数的特殊映射，实测效果不好，2026-08-27 确认取消）。
> Krita UI 显示两位小数：legacy 严格按 0.01 舍入（v1.1.2 行为）；
> calibrated 保留 3 位小数（Krita 用 `getDouble` 原样读取预设值，理由与风险见 §7.1 局限 5）。

### 7.1 纹理亮度/对比度：单条公式（v1.2.0 转正，v1.5.0 起 legacy 开关已删除）

**v1.5.0（任务 135）删除了公式开关**：`BRUSHCONVERTER_TEXTURE_FORMULA` 环境变量与 legacy 分支已移除
（`preset_xml.py` 的引用 13 处 → 0），只剩 calibrated 一条公式；**回退只能 `git revert` + 重转**，
不提供运行时开关（任务 135 裁决，见 `docs/验收报告/135-报告.md` §四）。

**历史方案（legacy，v1.1.2 及更早；v1.5.0 已删除，仅留档）**

- 亮度 `b_k = round(0.10 - PS亮度/250, 2)`，clamp [-1,1]；
- 对比度 `c_k = k`（PS 中心因子），round 两位，clamp [0,2]。
- 问题（任务 10/12 实测）：它只是一个「经验标尺」，PS 纹理亮度为 0 时 Krita 仍写入 0.10
  （掩码整体压暗约 26/255）；PS 亮度在整段范围上系统性偏移。掩码空间差异比例 70.75%（61 组可表达参数组）。

**现行方案（calibrated，v1.2.0 起默认、v1.5.0 起唯一；A/B 掩码空间 0.00% vs 旧公式 70.75%）**

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

1. **对比度越界（v1.5.0 已解除参数截断，仅剩 UI 显示限制）**：PS 对比度 > 50 时 `k > 2`（C=75 → 4、
   C=84 → 6.25、C=100 → 1e6）。v1.5.0（135-4）改为 **k 原值直写 + `_format_number` 9 位小数**，
   Krita 读取层无 clamp，掩码可表达网格 79/81 组差异 0.00%/max≤1；**遗留**是 Krita 笔刷编辑器滑块只显示
   0..2（值越界属预期，需人工在 Krita 里抽查 1 支确认可编辑性，尚未做）。
2. **Krita 亮度范围 [-1,1]**：C<0 且反解出的 `b_k` 越界时按边界截断。实测阈值约
   `C=-50 且 PS 亮度 < -127 或 > 127`（参数网格中 b=±150 与 C=-50 的两组），属结构性。
3. **PS 深度 > 83% 与 Krita `Strength≤1.0`（v1.5.0 复核：参数域上限，不是结构性缺陷）**：PS 高度/线性高度用
   `12·q`（`q = round(depth·2.55)`）加权，Krita 同一算子写 `10·s`，换算 `strength ≈ 1.2·depth/100`。
   PS 深度 > 约 83% 时映射值被封顶 1.0，但**饱和段与原值同像素（无像素后果）**——PS 高度系本身在该区已饱和，
   所以这是「参数域上限」而非「失真」；126 C-8 复核结论，136 §五-3 N-10 同步。不改深度映射。
4. **C=+100 理论二值化边界**：允许 ±128 的单像素翻转（任务 12 第 5.4 节）。
5. **判据口径**：本节的 A/B 是「掩码空间」同引擎判据——Krita 掩码复刻（`tools/texture_mask.py`，
   对 Krita 真值渲染 23/23 张 ≤2%、最大差 1）vs PS 拟合掩码（任务 12 参数化公式 0.01%）。
   「PS 图 vs Krita 图」整图比对 73.13%~100.00%（任务 09 组装验收；任务 10 的跨引擎基线最小
   22.36%）结构性不可达，不能作为转正判据。
6. **公式唯一、无运行时开关（v1.5.0）**：v1.2.0 起默认 `calibrated`，v1.5.0（任务 135）删除了
   `BRUSHCONVERTER_TEXTURE_FORMULA` 开关与 legacy 分支（引用 13 处 → 0），**没有运行时回退**；
   整体回退 = `git revert` + 重转。转正依据与回归数字见 `docs/验收报告/13-报告.md` 的「转正执行」一节。
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
| 0 | 关 | 无（未映射动态用 `Pressure*=false` + `UseCurve=false` 关闭） | ✅ |
| 1 | 渐隐 | 无对应（Krita `fade` 是时间渐隐，语义不等价） | ⛔ |
| 2 | 压力 | `pressure` | ✅ |
| 3 | 倾斜 | `tilt`（预留，未启用；`ytilt` 语义不等价） | ⛔ |
| 4 | 转轮 | 无对应（`tangentialpressure` 语义不等价） | ⛔ |
| 5 | **初始方向** | 无对应（`_BVTY_SENSOR=None`；真实样本新增告警） | ⛔ |
| 6 | **笔划方向** | `drawingangle` | ✅ |
| 7 | **速度** | `speed` | ✅（A 级枚举绑定；42MB 样本 0 次出现，未实机验证） |
| 8 | **旋转** | `rotation` | ✅（同上） |

> **v1.5.0（任务 134-3）按 124 A 级 9 值表修正了枚举错位**：旧文档写的「5 旋转 / 6 方向 / 7 初始方向」是错的，
> 正确为「5 初始方向 / 6 笔划方向 / 7 速度 / 8 旋转」；1/3/4 仍不映射（Krita 有同名传感器但语义不等价：
> `fade`=时间渐隐、`ytilt`=倾侧、`tangentialpressure`=转轮）。残留见 §10 的 B5/B6。

## 9. 去重规则

- 完全重复的预设（同名 + 同 UUID + 同 Dmtr）只保留首个。当前文件因此去掉 1 个重复的「淘宝店」横幅笔刷（20 → 19）。

## 10. 参数偏差实测与残差（v1.5.0 口径；判据四档）

> 证据来源：v1.4.0 = 任务 106（`docs/研究报告/参数偏差-2026-11-20.md`）+ 107（`docs/验收报告/107-报告.md`）；
> v1.5.0 = 129/131/133/134/135 四批实验 + 136 收口（`docs/验收报告/136-报告.md` §四：22 条残差登记 = 结构性 3 / 有条件 3 / 未量测 12 / 待下轮 4）。
> **判据四档（`docs/优化提示词/01-通用规范.md` 第 22 条，不许放宽）**：
> 像素级 差异比例 ≤2% 且 max_delta ≤2；几何级 绝对差 ≤2px 或相对差 ≤2%；
> 随机统计 带高中位数相对差 ≤10% 且两次渲染复现性 ≤5%；结构性 有原文证据 + Krita 无承载/有上限，登记残差不再改。
> 复算命令（只读入库图，不重渲；**别随手 `--force`**——散布随机种子每次会话不同，会把入库证据覆盖成另一组数）：
>
> ```bash
> python tools/measure_deviation.py --param spacing  --values 10,50,100 --analyze
> python tools/measure_deviation.py --param hardness --values 0,50,100   --analyze
> python tools/measure_deviation.py --param scatter  --values 0,100,300  --analyze
> python tools/measure_deviation.py --param scale    --values 50,100,200 --analyze
> python -m pytest -q tests/test_hardness_tip_v150.py tests/test_random_dynamics_v150.py tests/test_texture_v150.py
> ```
>
> 注意：`measure_deviation` 读的是 106/107 的**旧口径**存储数据（硬度 auto_brush、散布 ÷400、缩放无预缩放），
> 它复算出来的是「v1.4.0 当时的残差」；v1.5.0 的新口径由上面三条 pytest 断言。

### 10.1 间距（只对采样笔尖补偿短边基准）——已收口 ✅

| 组 | 10% | 50% | 100% | 判定 |
|---|---|---|---|---|
| computed（无补白） | 不可判读（调制 0.22%） | 1.88% → 1.88%（未动） | 0.55% → 0.55%（未动） | 一致 |
| square（86×84） | 1.69% → **0.68%** | 2.01% → **0.37%** | 1.30% → **1.18%** | 一致 |
| nonsquare（183×143） | 25.28% → **0.11%** | 28.09% → **0.18%** | 32.46% → **3.53%** | 1/3 点超阈 |

**3.53% 已判定为量测伪差，不是映射残差**：131 用 2 次重复、9/9 组（computed/square/nonsquare × 10/50/100）
复测全部 ≤2%，最大 0.73%（nonsquare 100%：PS 88.17px vs Krita 87.53px，−0.73%/−0.64px）。
106 的 3.53% 来自「PS 100% 点自身 −3.4%」——该点只有约 3.6 个笔尖周期，属少周期估计偏差；
补偿后的 10%/50% 两点证明短边基准关系本身正确。**结论：spacing 判据内，不调系数**（再调只会把 10%/50% 弄坏）。

另：非方形笔刷的**笔迹本身**在 Krita 里仍比 PS 高大 ~1.3×（补白给笔尖上下各加了值 255 的边，106 §4.3）。
按铁律 17 不改补白，所以「间距已对齐、笔迹仍偏大」这句观感是准确的。

### 10.2 硬度（v1.5.0 改按 PS 剖面生成 png_brush）——几何级 3/3 通过 ✅

| 硬度 @D=200 | PS 过渡宽 | v1.4.0（auto_brush hfade） | v1.5.0（png_brush） |
|---|---|---|---|
| 0% | 78.75px | 63.15px（+15.60px / 19.8%） | **Δ0.858px（1.09%）** |
| 50% | 32.38px | 39.71px（−7.33px / 22.6%） | **Δ0.058px（0.18%）** |
| 100% | 1.88px | 0.81px（1.07px，抗锯齿量级） | **Δ0.485px（25.54%，绝对差 ≤2px）** |

133 补测 18 组（3 档硬度 × 3 半径 × 2 类）几何判据 **9/9**、最大 1.603px；136 复跑 D=200 三档复现同一数字。
**残留（登记，不改）**：B1 大直径——h≥95 过渡带约 `(D/200)×1.9−1.9` px，D≳420 时超 2px 绝对判据
（D=400 实测 1.603px、余量 0.4px）；B2 中间硬度（0/50/100 之间插值）与椭圆（Rndn<100）组合未量测；
B3 D<50 量化未量测（D=19 的 h0 理论足印 27.4px、公式给 30px）。
**代价**：Krita 里 fade/硬度不可再调、尺寸显示=PS 足印（200→290/250/204）、每支 +3–12 KB（详见 §2）。

### 10.3 散布与抖动（v1.5.0 改用两把尺子）——随机档通过 ✅

| 参数 | v1.4.0 | v1.5.0 | 真值 / 判据 |
|---|---|---|---|
| 散布 `ScatterValue` | jitter/400 | **jitter/200** | a/D：j=100 实测 0.5057 vs PS 0.509（**0.65%**）；j=300 1.5189 vs 1.545（**1.69%**）；≤10% 达标 |
| 散布复现性 | — | 两次渲染 0.08% / 0.24% | ≤5% 达标 |
| 旋转 `RotationValue` | jitter/100 | **min(1, jitter/50)** | j=50 跨度 309.90°（旧 200.01°）；PS B 级参考 352°（25 dab，统计量不同，只作方向参考）；复渲 1.51% |

判据来源是 129 的分布型统计量（a/D = alpha 加权行位移 ÷ 直径）：÷400 在 5/5 配置超 10%（最大 50.01%）、÷200 最大 0.21%；
134 端到端抽检散布 j=100/300 两档 a/D 0.65%/1.69%、旋转复现 1.51%，随机档全过。
**残留（登记，不改）**：B7 散布 1000% 端点只做参数/单测（5.0、不加 clamp），端到端只到 300%
（129 线性比值 1.06，重渲染收益低）；B8 旋转 j∈(50,100) 全部写 1.0（饱和）只有 124 B 级依据；
F-6 角度抖动未按「≥25 dab × 3 档、≤15%」正式判据量测（极差估计量在 n=40 时抽样方差大，需先换模板匹配角标准差）。
旧 `÷400` 的来历（PS 145% ≈ Krita 35% 单点锚定）与为何不能用它，见 107 报告 §10.3 与 129 报告。

### 10.4 纹理（v1.5.0 四条落地）——像素级 0.00% ✅

| 项 | v1.4.0 残差 | v1.5.0 处置 | 结果 |
|---|---|---|---|
| 缩放 200% | 差异比例 93.72% / max 93 / 相对残差 32.4% | s>100 预缩放内嵌 + `Scale=1.0`（135-1；解除两处 clamp） | **0.00% / max 1**（128 预缩放 24/24 达标）；单预设 +0.8 KB~+434 KB |
| 对比度 >50 | `min(2.0,k)` 截断，C=84 结构性失真 | k 原值直写 + 9 位小数（135-4） | C=84→6.25、C=100→1e6；可表达网格 79/81 组掩码 0.00%/max≤1；UI 滑块仍只显示 0..2（越界属预期） |
| 最小深度 | 曲线 0 端点写绝对强度，低压力端二次相乘 | 0 端点改比值 `s_m/s_d`（135-5） | 乘性曲线语义正确（d=50/Mnm=10 → 0.2，旧 0.1） |
| hardMix 满覆盖 | 无偏置 | Strength=1.0 + 亮度 ±1/255 偏置（135-2） | 3/3 = **0.00% / max 0**；base<1 不下发（10.0–39.7%） |

**残留（登记，不改）**：A5/B13 预缩放 <100% 核、预缩放 + 高对比度（C>50）/ 压感深度的端到端组合未量测；
B9 b×C 交互区 multiply / subtract / linearBurn 76.680%、hardMix 57.570%（需先补 PS 真值再定修法；
预烘焙 9/10 但 subtract 需反向烘焙）；B10 亮度越界区（C=−50 且 |b|>127）8/12 组超阈、最大 61.125%/max23
（预烘焙 12/12=0.000% 但失去可编辑性；真实样本无命中）；B11 hardMix base<1：mid 10.043%–27.175%、
soft 26.425%–39.657%（须逐 base 烘焙而 base 由引擎生成 ⇒ 不可落地）；B12 hardMix + 压感偏置阈值未量测。

### 10.5 其余已登记残差（不属于参数映射修复，逐条在 136 §四）

- **结构性（3 条）**：B5 bVTy=1/3/4 未映射（语义不等价，等 F-7 一并裁决）；B11 hardMix base<1；A7 像素级残差
  （8-bit 量化 + 1px 网格 + PS 真值自身 AA，本档判据不含像素级整图比对）。
- **有条件（3 条）**：B1 大直径硬度；B8 旋转 j>50 饱和；B10 亮度越界区。
- **未量测（12 条）**：F-3 minimumDepth 作用点、F-6 角度抖动手续、F-7 bVTy 5/6、F-9 Mnm、A5 两类预缩放组合、
  B2/B3/B4、B6 bVTy 7/8 实机、B7 散布 1000%、B12 hardMix+压感、B13 纹理组合。
- **待下轮（4 条）**：A6 130 h0 的 α0 半径 −4px（可选优化，不影响几何判据）；B9 b×C 交互；B14 真实样本人工目检；B15 文档旧表述（本任务已清）。
