# 模式批 C 实测与结论（任务 36 · v1.3.0）

> 数据文件：`tests/baselines/mode-C/judge-result.json`（逐模式逐组三方数字）、
> `tests/baselines/mode-C/ps_rerender_result.json`（补出的 5 个模式 PS 真值登记）。
> 判读脚本：`tests/baselines/mode-C/judge_c.py`；补真值脚本：`tests/baselines/mode-C/ps_rerender_c.py`。
> 验收证据与回归数字见 `docs/验收报告/36-报告.md`。

## 一、一句话结论

C 批 15 个模式：**6 个可映射（全部达标，差异比例 0.00%、max_delta ≤2）、0 个需分模式校准、9 个结构性不可映射**。
其中「高度系」（38 height / 39 linearHeight）是 v1.2.1 已转正的映射，本批用真值把「达标」钉死为数字；
新增 4 条有像素证据的等价映射（37/40/41 → Multiply、42 → Subtract）；其余 9 个在 Krita
`TexturingMode` 里没有成员，PS 侧也分别呈「纹理结构完全丢失」或「枚举根本不存在」两种硬证据。

## 二、判据与口径（与任务书/01-通用规范一致，未放宽）

| 项 | 取值 | 出处 |
|---|---|---|
| 差异比例 | 差异像素数 / (W×H)，分母是全部像素 | `tools/imgdiff.py` 模块注释 |
| 差异像素 | `|Δalpha| > 2`（吸收量化+抗锯齿抖动） | `imgdiff.DEFAULT_TOL = 2` |
| 达标门槛 | 差异比例 ≤ **2%** 且 max_delta ≤ **2** | 任务书「硬性要求」第 2 条 + `执行进度.md`「做 v0.3 之前」第 2 条 |
| 只比 Alpha | RGB 在 PS 侧恒 0，信息全在 Alpha | `tests/baselines/mode/README.md` §三 |
| 饱和过滤 | `alpha_unique == 1`（整片常量）的图**不参与判定**，只登记 | README §七.2；任务简报「高度系 alpha_unique 饱和」 |
| 像素取值 | 只看 IDAT/像素，不看文件 sha256（PS 写 XMP iTXt 会污染整文件哈希） | `docs/研究报告/真值与基线.md` §3.1 |

**「PS 真值 vs 谁」的两个口径，本文都用，区别写在表头：**

1. **同模式对拷（psK）**：PS 真值 vs 该模式自己的 Krita 渲染。只有 `krita_value` 非空的模式有这一列
   （C 批只有 5 个：38/39/44/45/46）。
2. **跨模式等价（best）**：PS 真值 vs「假设把它映射成 Krita 模式 X」时任务 33 已经渲染出来的真实 Krita 图
   （X 取 14 个已有基线的模式）。这是「Krita 里没有这个枚举，但能不能用别的模式等价替代」的唯一可比口径。

> 为什么需要第 2 种：Krita `TexturingMode` 只有 16 个成员（0..15，上限 15），C 批有 10 个模式不在其中；
> 「有没有对应」不是文字问题而是像素问题——把它喂给最接近的那个枚举，误差有多大，数字说了算。

## 三、C 批 15 个模式逐模式结论（一行一个，全部有数字）

| # | PS 模式 | Krita 值 | 6 组数字（差异比例 / max_delta） | 结论 | 硬证据 |
|---:|---|---|---|---|---|
| 32 | `SnrB`（真名 `BrnS`） | 无 | PS 6/6 组 `alpha_unique=1`、整片 255；对任何 Krita 参考都 0.00%/max 0（**两侧都饱和，不可判读**） | **结构性不可映射** | ① Krita `TexturingMode` 16 成员里无该名；② PS 侧 6/6 组纹理结构完全丢失（alpha 恒 255，深度/亮度/对比度全不进入像素），PS 自己也没把它当纹理算子实现 |
| 33 | `MnrB`（真名 `BrnM`） | 无 | 同上：6/6 组 u=1、255 | **结构性不可映射** | 同 32 |
| 34 | `HnrB`（真名 `BrnH`） | 无 | PS 6/6 组 u=1，整片电平随深度变化 242/242/178/178/127/127；最接近的 Krita 参考 59.80%–97.82%、max 12–128 | **结构性不可映射** | ① Krita 无该枚举；② PS 侧 6/6 组是**常量图**（纹理结构丢失），最佳替代也 59.80% 起步，远超 2% |
| 35 | `ttsD`（真名 `Dstt`） | 无 | PS 6/6 组 u=1，电平 249/249/217/217/192/192；最佳替代 65.40%–97.52%、max 8–192 | **结构性不可映射** | 同 34 |
| 36 | `Str `（=saturate） | 无 | PS 6/6 组 u=1、255（同 32） | **结构性不可映射** | 同 32 |
| 37 | `passThrough` | 无（可用 Multiply 等价） | 6/6 组 vs Krita **Multiply** 渲染：0.00%/max 0,1,0,1,0,1（差异像素 0） | **可直接映射 → Multiply(0)** | PS 侧实测与 Mltp 真值逐像素相同（13 个模式同图，见 §五.1） |
| 38 | `height` | 14 | 6/6 组 **0.00% / max 2,2,0,0,0,0**（差异像素 0） | **直接可映射（达标）** | psK 列；d60/d100 两侧均 u=1 饱和，判读只用 d10 两档 |
| 39 | `linearHeight` | 15 | 6/6 组 **0.00% / max 2,2,0,0,0,0**（差异像素 0） | **直接可映射（达标）** | 同 38；注意 PS 侧 height 与 linearHeight 真值**逐字节相同**（见 §五.2） |
| 40 | `lighterColor` | 无（可用 Multiply 等价） | 6/6 组 vs Krita **Multiply**：0.00%/max 0,1,0,1,0,1 | **可直接映射 → Multiply(0)** | 同 37 |
| 41 | `darkerColor` | 无（可用 Multiply 等价） | 6/6 组 vs Krita **Multiply**：0.00%/max 0,1,0,1,0,1 | **可直接映射 → Multiply(0)** | 同 37 |
| 42 | `blendSubtraction` | 无（可用 Subtract 等价） | 6/6 组 vs Krita **Subtract**：0.00%/max 0,1,0,1,0,1 | **可直接映射 → Subtract(1)**（本批唯一「原来映射错」的） | PS 侧实测与 `Sbtr` 真值逐像素相同；转换器原来把它回退成 Multiply(0)，属**映射错误**，本批修正 |
| 43 | `blendDivide` | 无 | PS 6/6 组 u=1、255；对任何参考 0.00%（两侧饱和，不可判读） | **结构性不可映射** | ① Krita 无 DIVIDE；② PS 侧 6/6 组常量 255（纹理未进入笔迹） |
| 44 | `Lght` | 2 | PS **无真值**（PS 拒绝该枚举值）；Krita 侧 6/6 组 u=1、255（自身也退化成常量） | **结构性不可映射（无 PS 可比真值）** | ① mode-ids：PS 两张模式表都没有 `Lght`，4CC/字符串两种喂法都不改变模式；② 本仓库 ABR 实测 `BlnM` 值域里 `Lght` 出现 **0** 次 |
| 45 | `Grdn` | 3 | PS **无真值**（同上）；Krita 侧有真实纹理调制（u=27/14/147/77/221/127，均值 244.3→88.9） | **结构性不可映射（无 PS 可比真值）** | 同 44（`Grdn` 在 PS 里同样未定位、ABR 出现 0 次） |
| 46 | `HdMxS` | 11 | PS **无真值**（同上）；Krita 侧 6/6 组 u=1、255 | **结构性不可映射（无 PS 可比真值）** | 同 44（`HdMxS` 5 字符，charID 只收 4 字符；静态 0 命中、ABR 0 次） |

**分类计数（C 批 15）**：直接可映射 **6**（37/38/39/40/41/42）／需分模式校准 **0**／结构性不可映射 **9**（32/33/34/35/36/43/44/45/46）。
**达标计数**：**6**，差异比例范围 **0.00%–0.00%**，max_delta 范围 **0–2**。

## 四、关键数字（逐组全表，可逐条复算）

### 4.1 有 Krita 同模式基线的 5 个（psK 口径）

| 组 | 38 height PS(u/mean) | 38 Krita(u/mean) | 0.00%/max | 39 linearHeight PS | 39 Krita | 0.00%/max |
|---|---|---|---|---|---|---|
| d10_b0_c0 | 164 / 198.413 | 166 / 197.019 | 0.00% / 2 | 164 / 198.413 | 166 / 197.019 | 0.00% / 2 |
| d10_b-50_c-50 | 127 / 145.682 | 127 / 143.862 | 0.00% / 2 | 127 / 145.682 | 127 / 143.862 | 0.00% / 2 |
| d60_b0_c0 | 1 / 255（饱和） | 1 / 255（饱和） | 0.00% / 0 | 1 / 255 | 1 / 255 | 0.00% / 0 |
| d60_b-50_c-50 | 1 / 255（饱和） | 1 / 255（饱和） | 0.00% / 0 | 1 / 255 | 1 / 255 | 0.00% / 0 |
| d100_b0_c0 | 1 / 255（饱和） | 1 / 255（饱和） | 0.00% / 0 | 1 / 255 | 1 / 255 | 0.00% / 0 |
| d100_b-50_c-50 | 1 / 255（饱和） | 1 / 255（饱和） | 0.00% / 0 | 1 / 255 | 1 / 255 | 0.00% / 0 |

> 6/6 组的差异像素都是 **0**；d10 两档 `max_delta = 2` 正好压在门槛上（≤2 达标）。
> d60/d100 四组 PS 与 Krita **同时**饱和成整片 255，只能证明「转换器跟着 PS 一起饱和」，
> 不能证明公式正确——真正的判读信息全部来自 d10 两档。这一点与任务简报的提醒一致。

### 4.2 掩码复刻（`tools/texture_mask.py`）保真度：38/39 六组全部 0.00%/max 0

复刻用转换链当前口径的值（`strength = min(1, 1.2×depth/100)`、calibrated 亮度/对比度、mode 14/15、
图案相位 (0,0)、base=1），与真实 Krita 渲染逐像素 **0.00% / max 0**（6/6 组），
说明「复刻器 == 真 Krita」这一步在高度系上是可信的；PS 与它们差 max 2 也只是 8-bit 量化档位差。

### 4.3 9 个「无 Krita 成员」模式的 PS 侧形态（用于判不可映射）

| # | PS 形态（6 组） | 最佳 Krita 替代的差异比例 | 判读 |
|---:|---|---|---|
| 32 | 恒 255（u=1） | 0.00%（对手也恒 255） | 两侧饱和，不可判读 → 不可映射 |
| 33 | 恒 255（u=1） | 0.00%（同上） | 同上 |
| 34 | 常量 242/178/127（u=1） | 59.80%–97.82% | 纹理结构丢失 → 不可映射 |
| 35 | 常量 249/217/192（u=1） | 65.40%–97.52% | 同上 |
| 36 | 恒 255（u=1） | 0.00%（对手也恒 255） | 两侧饱和 → 不可映射 |
| 37 | 真实纹理（u=27…221） | **0.00%（Multiply）** | **可映射** |
| 40 | 真实纹理（u=27…221） | **0.00%（Multiply）** | **可映射** |
| 41 | 真实纹理（u=27…221） | **0.00%（Multiply）** | **可映射** |
| 42 | 真实纹理（u=27…221） | **0.00%（Subtract）** | **可映射** |
| 43 | 恒 255（u=1） | 0.00%（对手也恒 255） | 两侧饱和 → 不可映射 |

## 五、本批新发现（都会影响 34/35/37 的判读，建议进总表）

### 5.1 PS 侧 34 个「模式」实际只出 7 种像素（模式塌缩）

在任务 33 的同一口径下（单个满覆盖笔尖 + 同一纹理），34 个拿到真值的 PS 模式按下式**逐字节相同**分组
（`alpha_sha256` 完全相同）：

| 组 | 成员（模式序号:名） | 该组像素 |
|---|---|---|
| A（13 个） | 00 Nrml, 02 Bhnd, 03 Clar, 04 Mltp, 12 H, 13 Strt, 14 Clr, 15 Lmns, 23 linearBurn, 27 Nrml, **37 passThrough**, **40 lighterColor**, **41 darkerColor** | u=27 / mean 239.7 |
| B（11 个） | 01 Dslv, 05 Scrn, 06 Ovrl, 07 SftL, 10 Lghn, 17 CDdg, 18 CBrn, 21 hardMix, 22 linearDodge, 25 vividLight, **43 blendDivide** | 恒 255（u=1） |
| C（5 个） | 09 Drkn, 11 Dfrn, 16 Xclu, 20 Sbtr, **42 blendSubtraction** | u=27 / mean 244.3 |
| D（2 个） | 38 height, 39 linearHeight | u=164 / mean 198.4 |
| E/F/G | 08 HrdL（246.4）／24 linearLight（245.8）／26 pinLight（246.4），各自独立 | 单例 |

**含义（对 34/35/37 最重要）**：PS 的 `textureBlendMode` 只是一个「层混合模式枚举」，它接受任何层模式名，
但**纹理笔迹只实现了几种算子**，其余名会落进上面几组之一。所以：

- 「PS 真值相同」**不能**当作「两个模式语义相同」的证据（13 个不同语义的模式出同一张图）；
- 反过来说，「映射成 X 后像素 0.00%」在本基线口径下**就是**可达的（这就是本批判「可映射」的依据），
  但它只对「满覆盖笔迹 + 100% 倒相纹理」这一口径成立，边缘/压感下的行为本批没有证据。

### 5.2 PS 的 height 与 linearHeight 真值逐字节相同

38 与 39 的 6 组 `alpha_sha256` 两两相等（d10: `122853690177…`/`741c8a32a564…`，d60/d100 全 `2bf7158cb3d8…`）。
**Krita 侧也一样**：14（HEIGHT_PHOTOSHOP）与 15（LINEAR_HEIGHT_PHOTOSHOP）的渲染图逐像素差 max=0
（d10_b0_c0 / d10_b-50_c-50 / d60_b0_c0 / d100_b0_c0 四组实测，`px>2 = 0`）。
两侧源码里它们是不同算子（15 多一个 `max((1−src)·md, md−src)` 分支），但在 base=1（不透明笔尖章）口径下
两个分支给出同一结果，所以**本基线无法区分这 4 个组合（PS 两模式 × Krita 两枚举）**。

因此「39 达标」的严格含义是「Krita 15 与 Krita 14 一样，都跟这张真值一致」——
**不能**据此断言 15 比 14 更准，也不能用本基线验证「linearHeight 必须映射到 15」这条规则。
要区分得换口径（半透明笔尖或压感边缘，让 `md−src` 小于 1 时才现出差异）。这条要在 37 的总表里写清楚。

### 5.3 任务 33 判「PS 真值失败」的 5 个 C 批模式，其实是「枚举写法错」，已重出成功

任务 33 有 13 个模式 PS 真值失败，C 批占 5 个。复查后根因不是「PS 没有该模式」，而是喂错了名字/类型
（依据 `docs/数据/mode-ids.json` 的 `name_corrections` 与 `jsx_readback`）：

| 序号 | mode-align 里的名字 | 33 喂的值（失败） | 正确处理（本次实测成功） | 读回 |
|---:|---|---|---|---|
| 32 | `SnrB` | stringID("SnrB") | charID **"BrnS"** | `burnInS` ✅ |
| 33 | `MnrB` | stringID("MnrB") | charID **"BrnM"** | `burnInM` ✅ |
| 34 | `HnrB` | stringID("HnrB") | charID **"BrnH"** | `burnInH` ✅ |
| 35 | `ttsD` | stringID("ttsD") | charID **"Dstt"** | `desaturate` ✅ |
| 36 | `Str ` | stringID("Str ") | charID **"Str "** | `saturate` ✅ |

`SnrB/MnrB/HnrB/ttsD` 是 Ghidra 把小端立即数反向渲染出来的伪名（mode-ids 已双证：喂伪名被忽略、喂真名成功）。
本次按修正后的写法**重出 30 张全部 ok 且 verify=True**（`ps_rerender_result.json`，
耗时 55.6 s，30/30 通过），于是这 5 个模式第一次有了 PS 可比真值——结论是「纹理结构完全丢失」（见 §4.3）。
**建议 37 把这 5 个模式在 mode-batch.json/manifest 里的状态从「fail：值被 PS 忽略」更正为
「PS 真值已补出：常量图，纹理未生效」**，并把 30 张图的登记并入（图在 `tests/baselines/mode-C/ps/`，不入库）。

### 5.4 「无 Krita 成员」的 C 批模式在真实 ABR 样本里出现 0 次

`docs/数据/abr_keys.json`（7 个 ABR 样本、79 个预设、5240 次键出现）里，转换器真正读的那个键
`Brsh[].textureBlendMode` 共 38 次，实测值**只有 7 种**：
`Mltp 17 / linearHeight 10 / Hght 4 / CBrn 3 / Sbtr 2 / CDdg 1 / hardMix 1`。
（另有 `Brsh[].dualBrush.BlnM` 44 次：Mltp 26 / CBrn 9 / Drkn 4 / hardMix 2 / CDdg 1 / Ovrl 1 / linearBurn 1；
`Brsh[].toolOptions.Md` 19 次全是 Nrml——这两个都不是纹理模式的取值，别混用。）

**C 批 15 个模式在这 38 次纹理模式取值里一个都没出现**（含 37/40/41/42 这 4 个「可映射」的）。
所以本批的 4 条新增映射属于「按真值预置、样本里尚未命中」，不是「修好了现有用户看得见的 bug」——
唯一例外是 42：如果将来有 ABR 写 `blendSubtraction`，旧代码会错误地转成 Multiply。

## 六、转正（改 `src/` 的内容）

改动**只有一处**：`src/brush_converter/mapping.py` 的 `_TEXTURING_MODE` 表**末尾追加 4 行**（不重排、不格式化）。

| 新增键 | 值 | 依据（同组 6/6 全命中） |
|---|---:|---|
| `passThrough` | 0 Multiply | PS 真值 vs Krita Multiply 渲染 0.00%、max ≤1 |
| `lighterColor` | 0 Multiply | 同上 |
| `darkerColor` | 0 Multiply | 同上 |
| `blendSubtraction` | 1 Subtract | PS 真值 vs Krita Subtract 渲染 0.00%、max ≤1（旧代码回退 Multiply，属错误） |

**没有改的（有意）**：
- 38/39 的 `height`/`linearHeight` 映射与高度系 ×1.2 已在 v1.2.1 转正，本批只验证，不动公式（跨批公共公式见 36-报告 §六 决策 2）。
- `Lght/Grdn/HdMxS` 三条键**没有删**（`tests/test_texture_xml.py:216` 断言了 `HdMxS == 11`，删了会红）：
  它们无 PS 依据但也不造成回归，是否保留交 lead 拍板（36-报告 §六 决策 1）。
- 「回退 Multiply」的警告文案**没有动**：`gui/i18n.py:156` 按字面量 `回退Multiply` 做英文替换，
  改文案会连带动 GUI 翻译，超出本批范围（写进 36-报告 §五 遗留）。

## 七、复现命令

```powershell
$py = '<LOCAL_APP_DIR>\anaconda3\envs\brushConverter\python.exe'
cd <REPO>

# 1) 补出 5 个失败模式的 PS 真值（抢 ps.lock，单实例；30 张约 56 s）
& $py tests/baselines/mode-C/ps_rerender_c.py

# 2) 逐模式判读（三方对齐，写 judge-result.json）
$env:PYTHONIOENCODING='utf-8'
& $py tests/baselines/mode-C/judge_c.py | Tee-Object tests/baselines/mode-C/judge-c.log
```

## 八、遗留（不属本任务范围，已登记）

1. **基线口径的天花板**：单笔尖满覆盖 + 单纹理 + 6 组参数，导致 34 个 PS 模式塌缩成 7 张图、
   4 个模式在 PS 侧恒 255。要判「边缘/压感下的模式行为」需要新口径（多笔尖、半透明、压感曲线），
   属新任务，不在 34/35/36 范围内。
2. **37 个模式里 33 个无 Krita 基线**，本批只能对 C 批里 5 个做 psK 对拷；其余靠跨模式等价。
3. `height` 与 `linearHeight` 在 PS 侧不可区分（§5.2），「哪个 Krita 枚举更准」本批无法判定。
4. `Lght/Grdn/HdMxS` 三条「凭空命名」键仍在表里（见 §六）。
