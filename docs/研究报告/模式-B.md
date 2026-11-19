# 模式批 B 实测与结论（任务 35 / v0.4）

> 执行：modeb35（v1.3.0 / v0.4 核心能力 C）　日期：2026-11-20　工作目录：<REPO>
> 覆盖：`docs/数据/mode-batch.json` 里 batch="B" 的 **16 个模式**（order 16–31）
> 素材：任务 33 的 PS 真值（tests/baselines/mode/ps/）与 Krita 基线（tests/baselines/mode/krita/）
> 判读脚本：`tests/baselines/mode-B/judge_b.py`（与 C 批 `judge_c.py` 同口径）→ `judge-result.json`
> 本文只给结论与数字；逐条验收证据见 `docs/验收报告/35-报告.md`。

---

## 1. 一句话结论

B 批 16 个模式全部给出定论：**可映射 5 条、需分模式校准 1 条、结构性不可映射 10 条**；其中
**数值达标（差异比例 ≤2% 且 max_delta ≤2）6 条**，但**只有 5 条能作为转正依据**——`Nrml` 虽然数值 0.00%，
在 base=1 判据材料上属**数学退化**（Normal(1,t) 与 Multiply(1,t) 恒等），不能当语义等价证据（详见 §4）。
本批改 `src/` **2 行**（`linearDodge`→8、`linearBurn`→9，原先都静默回退 Multiply(0)）。

---

## 2. 判据与口径（先写清，避免 32-报告 D-3 那类「两个口径都对但没写清」）

| 项 | 本批口径 | 出处 |
|---|---|---|
| 「掩码空间」 | PNG 的 **Alpha 通道**（PS 导出 RGB 恒黑，纹理信息全在 Alpha；100×100 单 dab 铺满 = 覆盖度即掩码） | `tools/imgdiff.py` 模块 docstring、33-报告 §6.3 |
| 差异比例 | `\|Δalpha\| > 2` 的像素数 ÷ **全部像素数（W×H）** | `tools/imgdiff.py` 的 `diff_alpha`（DEFAULT_TOL=2） |
| 达标 | 差异比例 **≤ 2%** 且 **max_delta ≤ 2**（两条都要） | 任务书 §三【已知现场】、01-通用规范铁律 3 |
| 饱和过滤 | `alpha_unique == 1`（整片 255）的图**无信息量**：数字照算、但不据此判定 | 任务书「高度系模式注意 alpha_unique 饱和」+ 33-报告 §6.5 |
| 三方 | ①PS 真值 ②Krita 渲染（真实引擎） ③掩码复刻（`tools/texture_mask.py`，只实现 0/1/10/14/15） | 07/07A-报告 |
| 「无对应」模式怎么判 | 取该模式 PS 真值，与**任务 33 已渲染的全部 14 个 Krita 模式**逐像素比，取最近者（`best_ref`）：≤门槛 ⇒ 可映射；全都超门槛 ⇒ 不可映射 | 与 C 批 `judge_c.py` 同法（37 合流需一致） |

### 2.1 掩码复刻在本批的可用范围（实测，不是推测）

同引擎校验：复刻 `compute_mask` vs **真实 Krita 渲染**（同为 mode 0/1/10）：

| 模式 | 组 | 强度 s | 差异比例 | max_delta |
|---|---|---:|---:|---:|
| Mltp(0) | d100 b0 c0 / b-50 c-50 | 1.00 | 0.00% / 0.00% | 0 / 0 |
| Sbtr(1) | d100 b0 c0 / b-50 c-50 | 1.00 | 0.00% / 0.00% | 0 / 0 |
| hardMix(10) | d100 b0 c0 / b-50 c-50 | 1.00 | 0.00% / 0.00% | 0 / 0 |
| Mltp(0) | d10 b0 c0 / b-50 c-50 | 0.10 | 100.00% / 100.00% | 229 / 229 |
| Sbtr(1) | d10 b0 c0 / b-50 c-50 | 0.10 | 100.00% / 100.00% | 252 / 243 |
| hardMix(10) | d10 b0 c0 / b-50 c-50 | 0.10 | 95.33% / 96.11% | 255 / 255 |

**结论：`tools/texture_mask.py` 只在强度 s=1（depth=100%）时与真实渲染逐像素一致；s<1 时整体偏移（缺
`useSoftTexturing` 分支，实测常数差 ≈ +229/+102，与「执行进度.md『做 v0.3 之前』」第 3 条一致）。
因此**本批判据数字一律取自真实渲染（PS 真值 ↔ Krita 基线）**，掩码复刻只用于 d100 的同引擎交叉校验，
不用它去判 d10/d60。**不许**拿 s<1 的复刻结果当「不达标」的证据（那会把引擎能力差算成映射错）。

---

## 3. 逐模式结论（16 行，一行一条；数字为 6 组的实际取值）

`krita_value` 取自 `mode-align.json`；「饱和」列写 `alpha_unique==1` 的组数。

| # | ps_member | runtime_id | Krita | PS 真值 | 差异比例（6 组） | max_Δ | 结论 | 关键证据 |
|---:|---|---:|---|---|---|---:|---|---|
| 16 | `Xclu` | 24 | 无对应 | 6/6 齐 | vs 最近参考 `#20 Sbtr` **0.00%**；vs 回退 Multiply **69.04%–99.29%** | 0–1（vs Sbtr） | **结构性不可映射** | Krita `TexturingMode` 16 成员（0..15）里没有 EXCLUSION；0.00% 属 base=1 退化（Exclusion(1,t)≡Subtract(1,t)，见 §4） |
| 17 | `CDdg` | 25 | **6** | 6/6 齐 | **0.00%**（全 6 组） | 0 | **直接可映射**（已在表，无需改） | PS 真值 ↔ Krita 渲染 0.00%/max 0；**6/6 组饱和 u=1**，判别力弱；回退 Multiply 差 96.11%–100% |
| 18 | `CBrn` | 26 | **7** | 6/6 齐 | **0.00%**（全 6 组） | 0 | **直接可映射**（已在表，无需改） | 同 CDdg（饱和 6/6） |
| 19 | `Add ` | 38 | 无对应 | **0/6（PS 落笔报错）** | — | — | **不可映射**（无 PS 可比真值） | PS 2025 纹理落笔不支持 Add：jsx 第 238 行「该功能可能无法用于此版本的 Photoshop」 |
| 20 | `Sbtr` | 39 | **1** | 6/6 齐 | **0.00%**（全 6 组） | **0–1** | **直接可映射**（已在表，无需改） | 非饱和（u=14..221），6 组全过；这是本批**判别力最强**的一条 |
| 21 | `hardMix` | 35 | **10** | 6/6 齐 | 0.00%×5 + **1.03%**（d100_b0_c0） | **255**（同上组） | **需分模式校准**（未达标：max_Δ 超阈） | 103 px 全部落在纹理掩码 T==1.0（即处理后 src==0）处：Krita 判 0、PS 判 255（严格 `>` 边界），见 §5 |
| 22 | `linearDodge` | 30 | **8** | 6/6 齐 | **0.00%**（全 6 组） | 0 | **直接可映射 → 转正** | PS 真值 ↔ Krita 渲染 0.00%；**6/6 饱和 u=1**；当前回退 Multiply 差 **96.11%–100%**（映射缺失是实质错误） |
| 23 | `linearBurn` | 31 | **9** | 6/6 齐 | **0.00%**（全 6 组） | **0–1** | **直接可映射 → 转正** | 非饱和（u=14..221），6 组全过；但 Krita 0 与 9 的渲染在本材料上逐像素相同（§4），转正依据 = 静态对应（mode-ids: LINEAR_BURN）+ 无冲突证据 |
| 24 | `linearLight` | 32 | 无对应 | 6/6 齐 | vs 最近参考 **7.24%–63.20%**（6/6 超阈） | 4–254 | **结构性不可映射** | Krita 无 LINEAR_LIGHT 成员；最近参考也不达标（最好 7.24% @ d10_b-50_c-50），见 §6 |
| 25 | `vividLight` | 33 | 无对应 | 6/6 齐 | vs 最近参考 0.00%，**但 6/6 饱和（u=1..2）** | 0 | **不可映射**（Krita 无成员；数值无判别力） | 饱和使 0.00% 不具证明力（同 §4 逻辑）；Krita 无 VIVID_LIGHT 成员 |
| 26 | `pinLight` | 34 | 无对应 | 6/6 齐 | vs 最近参考 **2.79%–62.84%**（6/6 超阈，最小 2.79%） | 3–253 | **结构性不可映射** | 连最好的候选也超 2% 门槛（d10_b-50_c-50 = 2.79%），Krita 无 PIN_LIGHT 成员 |
| 27 | `Nrml` | 0 | 无对应 | 6/6 齐 | vs 最近参考 `#04 Mltp` **0.00%**（6 组） | 0–1 | **结构性不可映射**（数值达标但**退化不可判读**） | Normal(1,t)≡Multiply(1,t) 恒等（§4）；`docs/研究报告/静态-纹理.md` 第 249 行已判「Nrml 回退 Multiply = 语义错误」→ 不改 src，保留回退+警告 |
| 28 | `Rvrt` | 22 | 无对应 | **0/6（PS 忽略该枚举值）** | — | — | **不可映射**（无 PS 可比真值） | 字符串键写不进 textureBlendMode：JSX 调用成功但回读停在上一模式（33-报告 §五 类 1） |
| 29 | `SgdD` | 10 | 无对应 | **0/6（同上）** | — | — | **不可映射**（无 PS 可比真值） | 同 Rvrt；另注：真实 4CC 是 `DdgS`（mode-ids.json 的 note），`SgdD` 是 Ghidra 小端渲染 |
| 30 | `MgdD` | 11 | 无对应 | **0/6（同上）** | — | — | **不可映射**（无 PS 可比真值） | 同 Rvrt |
| 31 | `HgdD` | 12 | 无对应 | **0/6（同上）** | — | — | **不可映射**（无 PS 可比真值） | 同 Rvrt |

### 3.1 计数

| 结论 | 条数 | 模式 |
|---|---:|---|
| 直接可映射 | **5** | CDdg、CBrn、Sbtr、linearDodge、linearBurn |
| 需分模式校准 | **1** | hardMix |
| 结构性不可映射 | **10** | Xclu、Add、linearLight、vividLight、pinLight、Nrml、Rvrt、SgdD、MgdD、HgdD |
| 合计 | **16** | 与 mode-batch.json 的 B 批成员逐条对齐 |

- **数值达标（≤2% 且 max_Δ≤2）= 6 条**（上表 5 条可映射 + Nrml）；**可据以转正 = 5 条**。
- 达标模式的差异比例范围：**0.00%（5 条全部）**；max_delta 范围 **0–1**。
- 未达标但不属结构的只有 1 条：hardMix（1.03% / max 255）。
- 10 条不可映射里，**5 条有 PS 真值**（Xclu/linearLight/vividLight/pinLight/Nrml），**5 条没有**（Add/Rvrt/SgdD/MgdD/HgdD）。
  「没有 PS 真值」不是「Krita 不匹配」，是 **PS 侧写不进去或落笔报错**（33-报告 §五），本批按任务书 §五 口径收口。

---

## 4. 关键局限：本判据材料在 base=1 上数学退化（必须写进结论，否则会过度声称）

判据材料是「硬度 100、间距 10 的计算笔刷，单 dab 铺满 100×100 画布」，即笔尖覆盖度 base ≡ 1。
在 base=1 时若干混合函数**恒等**（不是测试噪声，是公式必然）：

    Multiply(1,t)     = 1·t     = t
    LinearBurn(1,t)   = 1+t−1   = t
    Normal(1,t)       = t                 （PS「普通」直接用纹理值）
    Subtract(1,t)     = 1−t
    Exclusion(1,t)    = 1+t−2·1·t = 1−t

实测验证（见 35-报告 §四）：Multiply vs LinearBurn 最大差 1.1e-16、Multiply vs Normal **0.0**、
Subtract vs Exclusion 1.1e-16。像素上也逐张对上了：

| 关系 | 6 组逐像素差 | 结论 |
|---|---|---|
| PS `Nrml` 真值 vs PS `linearBurn` 真值 | max Δ = **0**（6/6 组） | 数学恒等，非巧合 |
| Krita `Mltp`(0) 渲染 vs Krita `linearBurn`(9) 渲染 | max Δ = **0**（6/6 组） | Krita 侧同样退化 ⇒ 0 与 9 在**本材料上**不可分辨 |
| PS `Xclu` 真值 vs PS `Sbtr` 真值 | max Δ = **0**（6/6 组） | Exclusion(1,t)≡Subtract(1,t) |

**这个局限怎么用**（本批的处理原则，也是 37 合流时要统一的一条）：

1. 退化对**同一等价类内部**的判定不成立效力：`Nrml→Multiply`、`Xclu→Subtract`、
   `linearBurn→0` 三者的 0.00% 都**不能**单独当「语义可替换」的证据。
2. 退化**不**影响「映射缺失 = 实质错误」的判定：`linearDodge` 回退 Multiply 差 96%–100%，
   因为 8 号（LINEAR_DODGE）在 base=1 走的是**饱和**类（全 255），跟 t 类完全不同——这条证据是硬的。
3. 因此 `Nrml` 判「不可映射（保持回退+警告）」而不是判「可映射到 0」：静态结论
   `docs/研究报告/静态-纹理.md` 第 249 行已经写明「Normal 不是 Multiply」，像素证据不足以推翻它。
4. **饱和组（u=1）同样不产生证据**：CDdg/CBrn/linearDodge 的 0.00% 是「两边都被 clip 到 255」，
   只能证明「没有冲突」，不能证明「语义一致」。所以我把它们标成「可映射（判别力弱）」而不是「强证据」。

> 想把这 3 条从「不冲突」升级成「语义等价」，判据材料必须换：笔尖 α<1（半透明 dab）或双 dab 叠加，
> 让 base∈(0,1) 再出一次 PS/Krita 基线。这是本批**做不了**的事（33 已定口径、重出要整批重跑），写进 §9 遗留。

---

## 5. hardMix 的 1.03% 是什么（唯一未达标的非结构项）

`krita__mode__21_hardMix__d100_b0_c0.png` 的 alpha 只有 2 个值：**0（103 px）+ 255（9897 px）**，
PS 同组是 **255（10000 px）**；差异 103 px = **1.03%**，max_delta = **255**（只差在「0 vs 255」上）。

定位：这 103 px 正是**纹理掩码 T==1.0 的全部像素**（全图 T==1 的像素数也正好 103）。
经过 invert=true、b=0、c=1 之后 src = 1−T = 0，而 Krita 的 Hard Mix (Photoshop) 算子是

    (src + dst·s > 1) ? 1 : 0        # dst=1, s=1
    => src=0 时 0+1 > 1 不成立 => 0
    => src>0 时 src+1 > 1 => 1

PS 在同一点给 255。即**边界取严格大于**（`>`）导致 src==0 的像素翻到 0；这是 Krita 算子实现层的边界差，
**转换侧没有任何参数能修**（调 depth/brightness/contrast 只会让更多像素进/出这一边界，不能消掉它）。
判「需分模式校准」而非「可映射」，是因为 max_delta 255 明确超阈；不判「不可映射」，是因为 Krita 确有该模式、
且 5/6 组完全一致（0.00%），残差已被定位到一个可解释的边界条件。

> 复刻侧同点：`compute_mask` 也给出 0（因为抄的是同一公式），所以「渲染↔复刻」在 d100 是 0.00%/max 0——
> 复刻能**预测**这个 0，说明这不是渲染异常，是公式本身的分支。

---

## 6. 「无对应」五条的最近参考距离（不可映射的硬证据，逐数字）

用该模式 PS 真值 vs 任务 33 已渲染的**全部 14 个 Krita 模式**渲染图（同组），取差异最小者：

| 模式 | d10_b0_c0 | d10_b-50_c-50 | d60_b0_c0 | d60_b-50_c-50 | d100_b0_c0 | d100_b-50_c-50 | 最近候选 |
|---|---:|---:|---:|---:|---:|---:|---|
| Xclu | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | `#20 Sbtr`（**退化**，§4 已解释，不作等价证据） |
| linearLight | 60.03% | 7.24% | 63.20% | 14.28% | 62.17% | 14.28% | `#06 Ovrl` / `#21 hardMix`，**6/6 超阈** |
| vividLight | 0.00%* | 0.00%* | 0.00%* | 0.00%* | 0.00%* | 0.00%* | `#06 Ovrl`（*=饱和组，无可判读性） |
| pinLight | 58.09% | 2.79% | 62.84% | 13.13% | 62.17% | 14.28% | **6/6 超阈**（最小 2.79%） |
| Nrml | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | `#04 Mltp`（**退化**，§4） |

即：**linearLight / pinLight 的「不可映射」有硬数字**（最近候选都差 2.79%–63.20%）；
**Xclu / Nrml / vividLight 的「不可映射」依据的是「Krita `TexturingMode` 16 成员里没有该成员」+「像素证据退化/饱和」**，
不是「差得远」。两类证据必须分开写，否则会被误读成「测出来不匹配」。

Krita 16 成员清单（0..15）：MULTIPLY、SUBTRACT、LIGHTNESS、GRADIENT、DARKEN、OVERLAY、COLOR_DODGE、
COLOR_BURN、LINEAR_DODGE、LINEAR_BURN、HARD_MIX_PHOTOSHOP、HARD_MIX_SOFTER_PHOTOSHOP、HEIGHT、LINEAR_HEIGHT、
HEIGHT_PHOTOSHOP、LINEAR_HEIGHT_PHOTOSHOP（`docs/研究报告/静态-纹理.md` §6.2 与 `docs/数据/mode-align.json`）。
EXCLUSION / LINEAR_LIGHT / VIVID_LIGHT / PIN_LIGHT / NORMAL / REVERT / DODGE-SHADOW|MID|HIGHLIGHT 都不在其中。

---

## 7. src/ 转正（本批只动 2 行）

| 文件:行 | 改动 | 依据 |
|---|---|---|
| `src/brush_converter/mapping.py`（`_TEXTURING_MODE` 表内追加 2 行） | `"linearDodge": 8` | PS runtime_id=30 / mode-ids `krita_member="LINEAR_DODGE"`；PS 真值 ↔ Krita 8 号渲染 0.00%（6/6）；**原状态静默回退 Multiply 差 96.11%–100%** |
| 同上 | `"linearBurn": 9` | PS runtime_id=31 / `krita_member="LINEAR_BURN"`；PS 真值 ↔ Krita 9 号渲染 0.00%（6/6，非饱和）；原状态回退 Multiply（本材料上同像素，但语义不同；`静态-纹理.md` 已登记 `LBur` 为「凭空命名」键，真实写入值就是字符串 `linearBurn`） |

- **不改**：`CDdg/CBrn/Sbtr/hardMix`（已在表，实测一致）、`Xclu/linearLight/vividLight/pinLight/Nrml/Add/Rvrt/SgdD/MgdD/HgdD`（Krita 无对应或无可判读证据，保留回退并继续由 `mapping._collect_warnings` 报警「回退Multiply」）。
- **不新增键**：`Mltp`（A 批）、`Nrml`（§4 已解释为什么不收）。
- 回归：改前 **251 passed / 0 failed / 0 skipped（69.05 s）**；改后数字见 `docs/验收报告/35-报告.md` §三（含 2 条已登记的 Krita GUI 偶发失败与三条反证）。

### 7.1 合并状态（写给 37 合流）

本批改完后，A 批（任务 34）在同一张表尾继续追加了 9 个键（`Mltp/Nrml/Bhnd/Clar/H  /Strt/Clr /Lmns/Dfrn`）。
**同一文件的最终形态**：`_TEXTURING_MODE` 共 **30 个键**、**无重复键**（AST 逐键核对），本批的 4 行仍在
`mapping.py:54-57`（未被改动/重排），合并后 `linearDodge=8 / linearBurn=9` 仍生效。

**一处口径分歧（37 必须统一）**：`Nrml` 本批判「退化、不构成映射证据」故未登记，A 批登记成 `Nrml: 0`。
数值一致（都指向 0），但证据强度不同（A 批按名字/默认收，B 批按退化判不收）。本文不改 A 批结论，只把分歧写明。

---

## 8. 复现

    PY="<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe"
    cd <REPO>

    # 逐模式判读（打印 16 模式 × 6 组三方表，并写 tests/baselines/mode-B/judge-result.json）
    "$PY" tests/baselines/mode-B/judge_b.py

---

## 9. 遗留与风险

1. **base=1 退化（本批最大局限，见 §4）**：`Nrml/Xclu/linearBurn` 这类「等价类内部」的映射
   **只能测到「不冲突」，测不到「语义等价」**。要升级需重出「半透明 dab」或「双 dab」基线（判据材料变更 →
   33 的 manifest/命名要整批重做），本批不做。
2. **hardMix 的边界残差不可修**：1.03%/max 255 落在 src==0 的 103 px 上。若后续要收，只能改判据（对 max_delta
   加「差异像素 ≤ 纹理零值像素数」的例外）——属判据变更，需 lead 拍板，本批**没改判据**。
3. **`tools/texture_mask.py` 缺 `useSoftTexturing` 分支**：s<1 时复刻不可用（实测差 229/102）。本批绕开
   （只用真实渲染），但 07/07A 的「复刻=真值」只对 s=1 成立，建议 37 合流时统一在报告里声明这一前提。
4. **10 条不可映射里有 5 条没有 PS 真值**：这 5 条不能写「Krita 不匹配」，只能写「PS 侧写不进/落笔失败」——
   与 33-报告 §五 口径一致。GUI 文案若要写「不可映射」，对这 5 条会与事实不符（见 35-报告 §六 决策 1）。
5. **饱和组（CDdg/CBrn/linearDodge/vividLight）4 条**：0.00% 判别力弱。若使用者目检发现「颜色减淡/线性减淡在
   真实半透明笔刷上仍有偏差」，本批判据覆盖不到（§4）。
6. **未做**：不改 `gui/`、不改 `tests/` 既有用例、不动 A/C 批的模式键、不加功能开关、不 git commit。
7. **与 A 批的一处口径分歧**（详见 §7.1）：`Nrml` 我判「退化、不作证据」→ 未登记；A 批登记 `Nrml: 0`。
   37 合流需在「退化等价类」上统一一条规则，否则后续每批都会各自拍。（本批建议：退化等价类只在「与已有默认值一致」
   时才显式登记，且必须在报告里标注「判别力=退化」。）
