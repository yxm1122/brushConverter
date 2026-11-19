# 模式-A（v0.4 · 任务 34 · A 批 16 个 PS 纹理模式逐模式定论）

> 执行者：modea34｜日期：2026-11-20｜任务书：`docs/工作流程/34-模式批A实测与转正.md`
> 数据：`docs/数据/mode-batch.json` 的 batch=A（16 个成员）＋ `tests/baselines/mode/`（任务 33 真值与基线）
> 判读脚本：`tests/baselines/mode-A/judge_a.py`（配套机器可读结果 `judge-result.json`）

---

## 0. 一句话结论

**A 批 16 个模式：8 个「直接可映射」（掩码空间 6/6 组 0.00%、max_delta ≤1）、1 个「需分模式校准」（差异→减去，6/6 组 0.00%）、3 个「结构性不可映射」（变暗/溶解/强光）、4 个「不可判定（真值饱和）」。
达标 9 个已按任务书转正进 `src/brush_converter/mapping.py` 的 `_TEXTURING_MODE`；改前/改后全量回归数字见 `docs/验收报告/34-报告.md`。**

---

## 1. 判据与口径（与任务书、`01-通用规范`、任务 33 完全一致，未放宽）

| 项 | 取值 | 出处 |
|---|---|---|
| 主口径 | **掩码空间（Alpha 通道）差异比例 ≤ 2% 且 max_delta ≤ 2** | 任务书 §一.1、`执行进度.md`「做 v0.3 之前」第 2 条 |
| 差异像素 | `|Δalpha| > 2`（tol=2，吸收 PNG 量化与抗锯齿抖动） | `tools/imgdiff.py` 默认 `DEFAULT_TOL` |
| 差异比例分母 | 全部像素 `W×H = 10000`（不是非零像素数） | `tools/imgdiff.py` 模块 docstring |
| 饱和过滤 | 真值 `alpha_unique ≤ 1` 的图**无判据信息**，单列标记、不参与判定 | `tests/baselines/mode/README.md` §七.2 |
| 判像素不看文件 sha256 | PS PNG 含 XMP iTXt，两次保存字节必不同 | `docs/研究报告/真值与基线.md` §3.1、`执行进度.md` U-5 |

**「掩码复刻」在本批的具体含义**：PS 真值图是一枚铺满整张画布的不透明笔尖章（base=255），
所以**真值图的 Alpha 通道就是该模式在掩码空间的输出**；Krita 侧同理（渲染基线 `alpha_nonzero=10000`）。
于是「PS 真值 vs 掩码复刻」= 同一 100×100 掩码空间里两张 Alpha 的逐像素比较，与「把 PS 数值直传成
PS 语义掩码再比」是同口径（任务 13 已用该口径转正 calibrated 公式）。

**候选集（本批的关键方法）**：A 批只有 3 个模式在 `mode-align.json` 里有 `krita_value`，
其余 13 个是「Krita TexturingMode 枚举无对应」。判「能不能映射」的唯一硬办法是：
把它的 PS 真值同组像素，与「若映射成 Krita 模式 X」的**真机渲染图**逐像素比，X 遍历任务 33
已渲染的 14 个模式（`tests/baselines/mode/krita/`）。**候选侧全部是真机像素，不依赖任何公式建模。**

**公式族交叉验证（辅助，不进判定）**：任务 33 的 `.kpp` 全部 `use_soft_texturing=true`，
而 `tools/texture_mask.py` 不含软纹理分支（它按 `KPP` 默认 false 写）。本批另用
`out = dst + (op(dst,src) − dst)·s`、`s = round(strength·255)/255` 的软纹理算子族复算，
**先反核**：对 Krita 真机渲染 8/8 张（Mltp/Drkn/Ovrl/Sbtr 各 2 组）差异 **0.0000%、max_delta ≤1**，
证明该算子族与 Krita 5.3.3 实际像素一致；随后它给出的候选排序与真机候选完全吻合
（例如「变暗族在 base=1 时 ≡ Multiply」这一条正是用它算出来再被真机图证实的）。

---

## 2. 逐模式结论（16 行，无「跳过」）

数据来源：`tests/baselines/mode-A/judge-result.json`；数字由 `judge_a.py` 复算。
「差异比例」列给出该模式**判定所用候选**在 6 组参数上的区间；饱和组按口径不参与判定，
表中括号注明「饱和」。

| # | 模式（4CC） | PS 语义 | runtime_id | Krita | 可判组 | 差异比例 | max_delta | 结论 | 改了哪个文件 |
|---:|---|---|---:|---:|---|---|---|---|---|
| 0 | `Nrml` | Normal 普通 | 0 | 无对应 | 6/6 | **0.00%**（6/6 组） | 0–1 | ✅ **直接可映射**：PS 真值与 `Mltp` 真值 **Alpha 哈希完全相同**（6/6 组），映射到 Multiply(0) 后与真机 Mltp 渲染 0.00% | `mapping.py:64` 新增 `"Nrml": 0` |
| 1 | `Dslv` | Dissolve 溶解 | 9 | 无对应 | 4/6 | 最优候选 97.11%–98.42%（d60 两组）；d100 两组 0.00% | 132–102 | ⛔ **结构性不可映射**：随机抖动。d10 两组真值**整片饱和**（`alpha_unique=1`，全 255）；d60 两组真值与「满掩码」同像素（与 Mltp d100 同哈希）→ 深度被忽略；Krita 16 个枚举成员全是确定性算子，无随机成员 | 不改（保留回退 Multiply + 警告） |
| 2 | `Bhnd` | Behind 背后 | 28 | 无对应 | 6/6 | **0.00%**（6/6 组） | 0–1 | ✅ **直接可映射**：与 `Nrml` 同哈希 | `mapping.py:65` `"Bhnd": 0` |
| 3 | `Clar` | Clear 清除 | 16 | 无对应 | 6/6 | **0.00%**（6/6 组） | 0–1 | ✅ **直接可映射**：与 `Nrml` 同哈希 | `mapping.py:66` `"Clar": 0` |
| 4 | `Mltp` | Multiply 正片叠底 | 7 | 0 MULTIPLY | 6/6 | **0.00%**（6/6 组） | 0–1 | ✅ **直接可映射**（最强证据：**自有 Krita 真机渲染** vs PS 真值 6/6 组 0.00%/max≤1） | 不改（`"Mul ": 0` 已在 `mapping.py:33`；另补 `mapping.py:63` 的 `"Mltp": 0` 同义键） |
| 5 | `Scrn` | Screen 滤色 | 8 | 无对应 | **0/6** | 无（真值 6/6 组全 255） | — | ⬜ **不可判定（真值饱和）**：6/6 组 `alpha_unique=1`（全 255）。任何输出 255 的候选都「0.00%」，判据无信息 → 既不转正也不证伪 | 不改 |
| 6 | `Ovrl` | Overlay 叠加 | 17 | 5 OVERLAY | **0/6** | 自有 Krita 6/6 组 0.00%，但两侧**全饱和** | 0 | ⬜ **不可判定（真值饱和）**：PS 与 Krita 在 6/6 组都整片 255（不透明笔尖下叠加=恒等）。0.00% 是真空达标，**不能据此转正** | 不改（`"Ovrl": 5` 保持） |
| 7 | `SftL` | Soft Light 柔光 | 19 | 无对应 | **0/6** | 无（真值 6/6 组全 255） | — | ⬜ **不可判定（真值饱和）**：同上 | 不改 |
| 8 | `HrdL` | Hard Light 强光 | 18 | 无对应 | 6/6 | 最佳候选 2.79%–62.84%（5/6 组 ≥13.13%） | 3–253 | ⛔ **结构性不可映射**：Krita 枚举 16 成员里**没有 Hard Light**（最接近的是 HARD_MIX_PHOTOSHOP=10，实测 62.17%–100%）；把 14 个已渲染候选全试一遍，**没有任何一个能达到 ≤2%** | 不改（保留回退 + 警告） |
| 9 | `Drkn` | Darken 变暗 | 1 | 4 DARKEN | 6/6 | 自有 Krita **69.04%–98.60%**（d10/d60 四组）＋0.00%（d100 两组）；最佳单候选 SUBTRACT：d10/d60 四组 0.00% 但 d100 两组 **99.29%/98.65%** | 24–153 | ⛔ **结构性不可映射（单一算子）**：PS 侧 Darken 的传递律**随深度换族**——d<100 与 `Dfrn`/`Xclu`/`Sbtr` 的 Alpha **同哈希**（= 255−s·u），d=100 与 `Mltp` **同哈希**（= u）。Krita 没有任何单一算子同时覆盖这两段（见 §3.3 决策项） | 不改（保持 4） |
| 10 | `Lghn` | Lighten 变亮 | 2 | 无对应 | **0/6** | 无（真值 6/6 组全 255） | — | ⬜ **不可判定（真值饱和）**：同上 | 不改 |
| 11 | `Dfrn` | Difference 差值 | 23 | 无对应 | 6/6 | **0.00%**（6/6 组，候选 SUBTRACT） | 0–1 | ✅ **需分模式校准**：PS 侧 Difference 的 Alpha 恒等于 `Sbtr`（d<100 同哈希、d=100 同为 255−u）；映射改成 Krita SUBTRACT(1) 后 6/6 组 0.00%。**按像素实测校准，不是按名字对应** | `mapping.py:74` `"Dfrn": 1`（新增） |
| 12 | `H   ` | Hue 色相 | 3 | 无对应 | 6/6 | **0.00%**（6/6 组） | 0–1 | ✅ **直接可映射**：与 `Nrml` 同哈希（颜色域模式在 Alpha 通路不产生独立传递律） | `mapping.py:67` `"H   ": 0` |
| 13 | `Strt` | Saturation 饱和度 | 4 | 无对应 | 6/6 | **0.00%**（6/6 组） | 0–1 | ✅ **直接可映射** | `mapping.py:68` `"Strt": 0` |
| 14 | `Clr ` | Color 颜色 | 5 | 无对应 | 6/6 | **0.00%**（6/6 组） | 0–1 | ✅ **直接可映射** | `mapping.py:69` `"Clr ": 0` |
| 15 | `Lmns` | Luminosity 明度 | 6 | 无对应 | 6/6 | **0.00%**（6/6 组） | 0–1 | ✅ **直接可映射** | `mapping.py:70` `"Lmns": 0` |

**分类计数**：直接可映射 **8**（Nrml/Bhnd/Clar/Mltp/H/Strt/Clr/Lmns）｜需分模式校准 **1**（Dfrn）｜
结构性不可映射 **3**（Dslv/Drkn/HrdL）｜不可判定（真值饱和）**4**（Scrn/Ovrl/SftL/Lghn）＝ 16。

---

## 3. 关键实测发现（本批新增，任务 33 未登记）

### 3.1 PS 侧「Alpha 族等价」：8 个模式的真值逐像素相同

`manifest.json` 的 `alpha_sha256` 直接给出证据：`Nrml / Bhnd / Clar / Mltp / H / Strt / Clr / Lmns`
在**全部 6 组**参数上同哈希（13 张一组的重复簇，含别批同族成员）。
即：PS 纹理管线的 Alpha 通路对这 8 个模式是同一个函数，与「颜色域/图层域语义」无关。
复算命令见 §5，证据字段：`tests/baselines/mode-A/judge-result.json` 的 `ps_alpha_sha256`。

### 3.2 PS 侧「变暗族」在 depth<100 时同哈希，depth=100 时分裂

`Drkn / Dfrn / Xclu / Sbtr / blendSubtraction` 在 d10/d60 四组同哈希；d100 两组里
`Dfrn = Xclu = Sbtr = blendSubtraction`（= 255−u），而 `Drkn` 并入 `Mltp` 簇（= u）。
这就是 `Drkn` 判「单一算子不可覆盖」的直接原因：**PS 自己在这两个深度段用了两条不同的律**。

### 3.3 拟合出的两条 PS 传递律（用 Mltp d100 真值当 u，逐像素最小二乘）

```
PS Darken（d10/d60）: out = 255 − s·u     （a=−0.10198/−0.59975，截距 254.99/254.99，残差 max 0.51）
PS Darken（d100）   : out = u            （与 Mltp d100 同哈希，max Δ=0）
Krita 对应算子：
  MULTIPLY(0) 软纹理: 255(1−s) + s·u
  SUBTRACT(1) 软纹理: 255(1−s) + s·g = 255 − s·u   （g = 255−u，与 PS Darken d<100 同形）
  DARKEN(4)   软纹理: 同 MULTIPLY（base=1 时 min(1,src)=src）
```

### 3.4 Krita 侧两处易误判的算子退化（复刻时必须知道）

1. **GRADIENT(3) 在预设里静默回退成 SUBTRACT**：`research/krita_texture/kis_texture_option.cpp:74-76`
   （`!SupportsGradientMode && GRADIENT → SUBTRACT`）。实测 `krita__mode__45_Grdn` 与
   `krita__mode__20_Sbtr` **像素完全相同**（d10_b0_c0 与 d60_b-50_c-50 两组已核）。
   所以 `Dfrn` 的「两个达标候选」实际只有 SUBTRACT 一个算子。
2. **base=1 时 MULTIPLY / DARKEN / LINEAR_BURN 三算子像素相同**（软纹理下都退化成
   `255(1−s)+s·u`）。这解释了 `Mltp` 的「全组达标候选」出现 3 个同分数，
   **选 Multiply 是因为名字语义 + 已是既有默认，不是因为这三个算子能区分**。

---

## 4. 不可映射 / 不可判定的硬证据（逐条）

| 模式 | 硬证据 | 出处 |
|---|---|---|
| `Dslv` | ① d10 两组真值 `alpha_unique=1`（全 255）；② d60 两组真值与 `Mltp` d100 同哈希（深度被忽略）；③ `KisTextureOptionData::TexturingMode` 16 个成员全部是确定性算子，无随机/抖动成员 | `manifest.json`、`research/krita_texture/KisTextureOptionData.h:29-46`、`kis_texture_option.cpp:335-351` |
| `Drkn` | ① 自有 Krita DARKEN 在 4/6 组 69.04%–98.60%；② 最佳单候选 SUBTRACT 在另 2 组 99.29%/98.65%；③ PS 侧 d<100 与 d=100 换族（同哈希证据，§3.2）→ 不存在单一 Krita 算子 | `judge-result.json`、§3.2 |
| `HrdL` | ① 枚举 16 成员无 Hard Light（有 HARD_MIX_PHOTOSHOP / HARD_MIX_SOFTER）；② 14 个真机候选全部 ≥2.79%，5/6 组 ≥13.13%、最高 62.84% | `KisTextureOptionData.h:29-46`、`judge-result.json` |
| `Scrn`/`SftL`/`Lghn` | 真值 6/6 组全 `alpha_unique=1`（全 255）→ 判据无信息（`README.md` §七.2 的过滤口径） | `manifest.json` |
| `Ovrl` | 同「全饱和」；且**自有 Krita 渲染也全 255** → 有对应关系但无法用像素证明（不透明笔尖下 Overlay 恒等） | `judge-result.json` |

---

## 5. 复现命令

```bash
cd /d/PycharmProjects/brushConverter
PY="<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe"

# 1) 逐模式判读（打印 16 模式 × 6 组表格 + 写 judge-result.json）
PYTHONIOENCODING=utf-8 "$PY" tests/baselines/mode-A/judge_a.py

# 2) 饱和/哈希证据（Alpha 统计与重复簇）
"$PY" - <<'PYEOF'
import json; from collections import defaultdict
m=json.load(open(r'tests/baselines/mode/manifest.json',encoding='utf-8'))['entries']
g=defaultdict(list)
for e in m:
    if e['kind']=='ps' and e['ok']: g[e['alpha_sha256']].append(e['file'].split('/')[-1])
for k,v in g.items():
    if len(v)>1: print(len(v), v[:6])
PYEOF
```

---

## 5.1 掩码复刻工具与真机渲染的差（为什么判定用真机候选）

`tools/texture_mask.py` 的 `blend()` 没有 `use_soft_texturing=true` 分支（模块 docstring 自己写明
「KPP 默认 false」），而任务 33 的 `.kpp` 全部 `use_soft_texturing=true`（`kpp_table.json` 84/84）。
实测（Mltp，唯一同时可算「工具复刻」与「真机」的模式）：

| 组 | 工具复刻 vs PS 真值 | 软纹理口径复刻 vs PS 真值 | 真机渲染 vs PS 真值 |
|---|---|---|---|
| d10_b0_c0 | 100.00% / max 229 | 0.00% / max 0 | 0.00% / max 0 |
| d10_b-50_c-50 | 100.00% / max 230 | 0.00% / max 1 | 0.00% / max 1 |
| d60_b0_c0 | 100.00% / max 102 | 0.00% / max 0 | 0.00% / max 0 |
| d60_b-50_c-50 | 100.00% / max 103 | 0.00% / max 1 | 0.00% / max 1 |
| d100_b0_c0 | 0.00% / max 0 | 0.00% / max 0 | 0.00% / max 0 |
| d100_b-50_c-50 | 0.00% / max 1 | 0.00% / max 1 | 0.00% / max 1 |

结论：s<1 时「工具复刻」必然 100% 差异，**不是映射错**，是工具缺分支；本批判定因此改用真机候选
（`judge_a.py` 同时保留 `replica_hard`/`replica_soft` 两列，便于后续任务补分支时对照）。

## 6. 遗留（本任务未做 / 需后续任务）

1. **4 个饱和模式（Scrn/Ovrl/SftL/Lghn）没有拿到可判数据**：要判它们必须在**非饱和探测几何**下重出真值
   （例如笔尖不透明度 50%，或把 base 改成半透明底），PS + Krita 两侧都要重出。属新任务（会动任务 33 的口径）。
2. **`Drkn` 的深度分档方案未落地**：若允许「depth<100 → 减去 / depth=100 → 正片叠底」，A 批可再达标 1 个；
   但这是**按深度切算子**，不是常数换算，需 lead 拍板（见 34-报告 §六 决策 2）。
3. **跨引擎整图比对仍不可达**：本批全部结论只在掩码空间（Alpha=1 基底）成立；笔尖半透明边缘
   （base<1）区域的可映射性未验证——PS 侧 Darken/Hard Light 在 base<1 时的律没有真值。
4. **`tools/texture_mask.py` 不含软纹理分支**：本批判定用「真机候选」规避了这一点，
   但若后续任务要用复刻公式做判定，必须先给 `blend()` 补 `use_soft_texturing=true` 分支（附上本批的 8/8 反核用例）。