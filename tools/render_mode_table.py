# -*- coding: utf-8 -*-
"""把 v0.4 三批（A/B/C = 任务 34/35/36）的模式结论合流成唯一真相，
并生成 docs/参数矩阵.md 的「模式总表」一节。

用法（仓库根）：
    python tools/render_mode_table.py --build     # 重算 docs/数据/mode-table.json（确定性输出）
    python tools/render_mode_table.py             # 只重写 docs/参数矩阵.md 里的模式总表节
    python tools/render_mode_table.py --check     # 只比对，不写；不一致退出码 1
    python tools/render_mode_table.py --stdout    # 打印模式总表节

三条设计原则（任务 37 §一）：
1. 本脚本**不新增模式结论**：verdict 来自三批已出的实测数字；37 只做口径统一与取舍，
   取舍理由写在 VERDICT_DECISIONS[order]["rationale"]。
2. 数字一律**重算**（差异比例 / max_delta / 饱和组数 / 塌缩类），不抄三份报告的表格；
   PS 侧「模式塌缩」也由 manifest.json 与 ps_rerender_result.json 的 alpha_sha256 重算。
3. 输出确定性：行序 = mode-batch.json 的 order，无时间戳、无本机绝对路径。

判据（三批与任务 33 一致，未放宽）：差异比例 = |Δalpha|>2 的像素数 / (W×H)，门槛 ≤2% 且 max_delta ≤2。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent

BATCH_JSON = REPO / "docs/数据/mode-batch.json"
IDS_JSON = REPO / "docs/数据/mode-ids.json"
MANIFEST = REPO / "tests/baselines/mode/manifest.json"
RERENDER = REPO / "tests/baselines/mode-C/ps_rerender_result.json"
JUDGE = {
    "A": REPO / "tests/baselines/mode-A/judge-result.json",
    "B": REPO / "tests/baselines/mode-B/judge-result.json",
    "C": REPO / "tests/baselines/mode-C/judge-result.json",
}
TABLE_JSON = REPO / "docs/数据/mode-table.json"
MATRIX_MD = REPO / "docs/参数矩阵.md"

GROUPS = ["d10_b0_c0", "d10_b-50_c-50", "d60_b0_c0", "d60_b-50_c-50", "d100_b0_c0", "d100_b-50_c-50"]
VERDICTS = ("可映射", "需校准", "不可映射", "不可判定")
SECTION_HEAD = "## 模式总表（v0.4 三批合流 · 任务 37）"
NEXT_HEAD = "## 生成与校验"

# 37 的取舍表：order -> 结论与口径。字段含义见 mode-table.json 的 _meta.column_semantics。
# batch_verdict = 该批报告原文的结论（只记录，不覆盖；与 verdict 不同时 rationale 说明为什么）。
D = {
    0: dict(verdict="可映射", kind="像素比对（跨模式候选）", strength="弱-塌缩", dist=False, s1="不依赖",
            batch="A：直接可映射",
            note="与 Mltp 同哈希；A 登记 mapping.py:64，B 批同名模式判退化（口径分歧见 37 报告 §三-1）",
            rationale="A 批按「PS 真值与 Mltp 真值 Alpha 同哈希 + 映射后 0.00%」登记为 0；B 批判 base=1 数学退化、不构成独立证据。"
                      "37 统一：像素不可区分（base=1 退化等价类），按名字与默认算子登记为 0，不据此声称语义等价。"),
    1: dict(verdict="不可映射", kind="结构判定（枚举无成员）", strength="结构性", dist=True, s1="不依赖",
            batch="A：结构性不可映射",
            note="Krita 16 成员无随机算子；d60 两组 97–98%，d100 两组 0%（真值与满掩码同像素）",
            rationale="PS Dissolve 是随机抖动，Krita TexturingMode 16 成员全是确定性算子；d10 两组真值饱和、d60 两组 97.11%/98.42%。"),
    2: dict(verdict="可映射", kind="像素比对（跨模式候选）", strength="弱-塌缩", dist=False, s1="不依赖",
            batch="A：直接可映射", note="与 Nrml/Mltp 同哈希（塌缩类 P1）",
            rationale="与 order 0 同证据链：登记 0，但 0.00% 属塌缩/退化，不作语义等价。"),
    3: dict(verdict="可映射", kind="像素比对（跨模式候选）", strength="弱-塌缩", dist=False, s1="不依赖",
            batch="A：直接可映射", note="与 Nrml/Mltp 同哈希（塌缩类 P1）", rationale="同 order 2。"),
    4: dict(verdict="可映射", kind="像素比对（自有渲染）", strength="强", dist=True, s1="不依赖",
            batch="A：直接可映射", note="自有 Krita 渲染 6/6 组 0.00%/max≤1；本批最强证据",
            rationale="唯一同时具备「PS 真值 + Krita 同模式真机渲染」且两侧非饱和的模式；A 批用它当 G1 类的参照。"),
    5: dict(verdict="不可映射", kind="结构判定（枚举无成员）", strength="结构性+饱和", dist=False, s1="不依赖",
            batch="A：不可判定（真值饱和）",
            note="PS 真值 6/6 饱和；枚举无 SCREEN（A 原判不可判定，37 按枚举改判）",
            rationale="A 批判「不可判定（真值饱和）」；但 Krita 16 成员里没有 SCREEN 是独立于像素的结构事实，"
                      "像素饱和只降低证据强度，不改变「无等价算子」的结论。37 统一口径：结构判定优先。"),
    6: dict(verdict="可映射", kind="像素比对（自有渲染）", strength="弱-饱和", dist=False, s1="不依赖",
            batch="A：不可判定（真值饱和）",
            note="两侧 6/6 组饱和；已登记 Ovrl=5（A 原判不可判定，37 按「有成员+已登记」改判）",
            rationale="与 order 5 相反的一侧：Krita 确有 OVERLAY(5) 且转换器早已登记，只是不透明笔尖下两侧都恒 255，"
                      "像素证明不了。按 B/C 批对「有枚举成员 + 无冲突证据」的处理（CDdg/CBrn/linearDodge）统一为可映射·弱。"),
    7: dict(verdict="不可映射", kind="结构判定（枚举无成员）", strength="结构性+饱和", dist=False, s1="不依赖",
            batch="A：不可判定（真值饱和）", note="PS 真值 6/6 饱和；枚举无 SOFT_LIGHT", rationale="同 order 5。"),
    8: dict(verdict="不可映射", kind="结构判定（枚举无成员）", strength="结构性", dist=True, s1="不依赖",
            batch="A：结构性不可映射", note="枚举无 HARD_LIGHT；14 个候选最佳 2.79%（5/6 组 ≥13.13%）",
            rationale="有硬数字：把 14 个已渲染 Krita 模式全试一遍，最好的候选仍 2.79%–62.84%。"),
    9: dict(verdict="不可映射", kind="结构判定（枚举无成员）", strength="结构性", dist=True, s1="不依赖",
            batch="A：结构性不可映射（单一算子）",
            note="自有 DARKEN 69.04–98.60%；PS 按深度换族 → 无单一算子覆盖",
            rationale="PS 侧 Darken 在 d<100 与减去族同哈希、d=100 与正片叠底同哈希；Krita 无单一算子同时覆盖两段，"
                      "最佳单候选 SUBTRACT 在 d100 两组 99.29%/98.65%。"),
    10: dict(verdict="不可映射", kind="结构判定（枚举无成员）", strength="结构性+饱和", dist=False, s1="不依赖",
             batch="A：不可判定（真值饱和）", note="PS 真值 6/6 饱和；枚举无 LIGHTEN", rationale="同 order 5。"),
    11: dict(verdict="可映射", kind="像素比对（跨模式候选）", strength="中", dist=True, s1="不依赖",
             batch="A：需分模式校准",
             note="PS Difference ≡ PS Subtract；改映射 SUBTRACT(1) 后 6/6 组 0.00%/max≤1",
             rationale="A 批称「需分模式校准」，但校准结果是一个常数枚举（1 = SUBTRACT），没有经验系数或残差，"
                       "6/6 组全 0.00%/max≤1；按 37 的 verdict 定义归入「可映射」，原判记录在 batch_verdict。"),
    12: dict(verdict="可映射", kind="像素比对（跨模式候选）", strength="弱-塌缩", dist=False, s1="不依赖",
             batch="A：直接可映射", note="与 Mltp 同哈希（塌缩类 P1）",
             rationale="同 order 2。"),
    13: dict(verdict="可映射", kind="像素比对（跨模式候选）", strength="弱-塌缩", dist=False, s1="不依赖",
             batch="A：直接可映射", note="与 Mltp 同哈希（塌缩类 P1）", rationale="同 order 2。"),
    14: dict(verdict="可映射", kind="像素比对（跨模式候选）", strength="弱-塌缩", dist=False, s1="不依赖",
             batch="A：直接可映射", note="与 Mltp 同哈希（塌缩类 P1）", rationale="同 order 2。"),
    15: dict(verdict="可映射", kind="像素比对（跨模式候选）", strength="弱-塌缩", dist=False, s1="不依赖",
             batch="A：直接可映射", note="与 Mltp 同哈希（塌缩类 P1）", rationale="同 order 2。"),
    16: dict(verdict="不可映射", kind="结构判定（枚举无成员）", strength="结构性+退化", dist=False, s1="不依赖",
             batch="B：结构性不可映射",
             note="枚举无 EXCLUSION；vs Sbtr 的 0.00% 属 base=1 退化（Exclusion(1,t)≡Subtract(1,t)）",
             rationale="B 批用「16 成员无该成员 + 0.00% 属退化」判不可映射；37 保留，并把退化写进证据强度。"),
    17: dict(verdict="可映射", kind="像素比对（自有渲染）", strength="弱-饱和", dist=False, s1="不依赖",
             batch="B：直接可映射", note="自有渲染 0.00%；两侧 6/6 饱和（判别力弱，B 批已声明）",
             rationale="Krita 有 COLOR_DODGE(6)、已登记；PS 侧 6/6 组恒 255、Krita 侧同，0.00% 只证明不冲突。"),
    18: dict(verdict="可映射", kind="像素比对（自有渲染）", strength="弱-饱和", dist=False, s1="不依赖",
             batch="B：直接可映射", note="自有渲染 0.00%；两侧 6/6 饱和", rationale="同 order 17。"),
    19: dict(verdict="不可映射", kind="结构判定（PS 无真值）", strength="结构性+无真值", dist=None, s1="不依赖",
             batch="B：不可映射（无 PS 可比真值）",
             note="PS 落笔报错（jsx 提示「无法用于此版本」）；Krita 无 ADD",
             rationale="两侧都判否：PS 产不出真值 + Krita 16 成员无 ADD。"),
    20: dict(verdict="可映射", kind="像素比对（自有渲染）", strength="强", dist=True, s1="不依赖",
             batch="B：直接可映射",
             note="自有渲染 6/6 组 0.00%/max≤1，非饱和（u=14..221）",
             rationale="B 批判别力最强的一条：两侧非饱和、同模式渲染逐像素一致。"),
    21: dict(verdict="需校准", kind="需校准（边界残差）", strength="需校准-边界残差", dist=True, s1="条件成立",
             batch="B：需分模式校准",
             note="仅 d100_b0_c0 差 103 px（掩码 T==1 边界），1.03% / max 255；转换侧无参数可修",
             rationale="5/6 组 0.00%，残差定位到 Krita 算子严格 > 边界（src==0 处 Krita 给 0、PS 给 255）。"
                       "按判据 max_delta≤2 未达标 → 需校准；不判不可映射，因为 Krita 确有该模式。"),
    22: dict(verdict="可映射", kind="像素比对（自有渲染）", strength="弱-饱和", dist=False, s1="不依赖",
             batch="B：直接可映射 → 转正",
             note="自有渲染 0.00%；两侧 6/6 饱和；原回退 Multiply 差 96.11%–100%（映射缺失是实质错误）",
             rationale="8 号 LINEAR_DODGE 在 base=1 走饱和类（全 255），与 t 类完全不同 → 即使饱和也能证明「缺失映射是错的」。"),
    23: dict(verdict="可映射", kind="像素比对（自有渲染）", strength="弱-退化", dist=False, s1="不依赖",
             batch="B：直接可映射 → 转正",
             note="自有渲染 0.00%/max≤1，非饱和；但 9（LINEAR_BURN）与 0 在本材料逐像素相同",
             rationale="B 批已声明：转正依据 = 静态对应（mode-ids LINEAR_BURN）+ 无冲突证据，不是像素区分力。"),
    24: dict(verdict="不可映射", kind="结构判定（枚举无成员）", strength="结构性", dist=True, s1="不依赖",
             batch="B：结构性不可映射", note="枚举无 LINEAR_LIGHT；最近候选 7.24%–63.20%（6/6 超阈）",
             rationale="有硬数字：最近候选连 2% 都进不去。"),
    25: dict(verdict="不可映射", kind="结构判定（枚举无成员）", strength="结构性+饱和", dist=False, s1="不依赖",
             batch="B：不可映射",
             note="PS 真值 6/6 饱和（u=1..2）；枚举无 VIVID_LIGHT",
             rationale="B 批判「不可映射（Krita 无成员；数值无判别力）」；37 保留，饱和只进证据强度。"),
    26: dict(verdict="不可映射", kind="结构判定（枚举无成员）", strength="结构性", dist=True, s1="不依赖",
             batch="B：结构性不可映射", note="枚举无 PIN_LIGHT；最近候选 2.79%–62.84%",
             rationale="有硬数字；与 order 8（HrdL）数字巧合，因为两者 PS 真值几乎同分布（见 37 报告 §四-5）。"),
    27: dict(verdict="可映射", kind="像素比对（跨模式候选）", strength="弱-塌缩", dist=False, s1="不依赖",
             batch="B：结构性不可映射（退化不可判读）",
             note="与 order 0 是同一 PS 模式（33 的批次分配重复）；B 批判退化不作证据",
             rationale="Nrml 在 mode-batch.json 里出现两次（order 0 / 27），属批次分配缺陷；37 保留两行以对齐 47 行清单，"
                       "但两行结论必须一致：可映射（退化），不声称语义等价。分配缺陷记入 37 报告 §五。"),
    28: dict(verdict="不可映射", kind="结构判定（PS 无真值）", strength="结构性+无真值", dist=None, s1="不依赖",
             batch="B：不可映射（无 PS 可比真值）",
             note="PS 忽略该枚举值（回读停在上一模式）；Krita 无 REVERT",
             rationale="PS 写不进 + Krita 无成员，两侧都否。"),
    29: dict(verdict="不可映射", kind="结构判定（PS 无真值）", strength="结构性+无真值", dist=None, s1="不依赖",
             batch="B：不可映射（无 PS 可比真值）",
             note="真名 DdgS（SgdD 是 Ghidra 小端反序伪名）；PS 忽略伪名",
             rationale="mode-ids.json name_corrections：SgdD→DdgS，喂伪名被 PS 忽略；Krita 无该成员。"),
    30: dict(verdict="不可映射", kind="结构判定（PS 无真值）", strength="结构性+无真值", dist=None, s1="不依赖",
             batch="B：不可映射（无 PS 可比真值）", note="真名 DdgM（反序伪名）；Krita 无该成员",
             rationale="同 order 29。"),
    31: dict(verdict="不可映射", kind="结构判定（PS 无真值）", strength="结构性+无真值", dist=None, s1="不依赖",
             batch="B：不可映射（无 PS 可比真值）", note="真名 DdgH（反序伪名）；Krita 无该成员",
             rationale="同 order 29。"),
    32: dict(verdict="不可映射", kind="结构判定（枚举无成员）", strength="结构性+饱和", dist=False, s1="不依赖",
             batch="C：结构性不可映射",
             note="真名 BrnS；33 判 PS 失败实为枚举写法错，改名重出后真值 = 常量 255",
             rationale="C 批纠正：33 用 stringID 伪名被 PS 忽略，charID 真名成功（读回 burnInS）。"
                       "真值是常量图（纹理结构丢失）+ Krita 无 SATURATE/BURN 类成员 → 不可映射。"),
    33: dict(verdict="不可映射", kind="结构判定（枚举无成员）", strength="结构性+饱和", dist=False, s1="不依赖",
             batch="C：结构性不可映射", note="真名 BrnM；改名重出后真值 = 常量 255", rationale="同 order 32。"),
    34: dict(verdict="不可映射", kind="结构判定（枚举无成员）", strength="结构性", dist=True, s1="不依赖",
             batch="C：结构性不可映射",
             note="真名 BrnH；PS 真值 = 常量 242/178/127；最佳替代 59.80%–97.82%",
             rationale="改名重出后有真值：6/6 组常量图（纹理结构丢失），最近候选 59.80% 起步。"),
    35: dict(verdict="不可映射", kind="结构判定（枚举无成员）", strength="结构性", dist=True, s1="不依赖",
             batch="C：结构性不可映射",
             note="真名 Dstt；PS 真值 = 常量 249/217/192；最佳替代 65.40%–97.52%", rationale="同 order 34。"),
    36: dict(verdict="不可映射", kind="结构判定（枚举无成员）", strength="结构性+饱和", dist=False, s1="不依赖",
             batch="C：结构性不可映射", note="PS 真值 = 常量 255；Krita 无 SATURATE",
             rationale="枚举写法修正后真值仍是常量 255（PS 侧纹理未生效）+ Krita 无成员。"),
    37: dict(verdict="可映射", kind="像素比对（跨模式候选）", strength="弱-塌缩", dist=False, s1="不依赖",
             batch="C：可直接映射 → Multiply(0)", note="与 Mltp 同哈希（塌缩类 P1）；登记 passThrough=0",
             rationale="PS 真值与 Mltp 真值逐像素相同 → 映射 0 后 0.00%；塌缩口径下不能声称语义等价。"),
    38: dict(verdict="可映射", kind="像素比对（自有渲染）", strength="中", dist=False, s1="条件成立",
             batch="C：直接可映射（达标）",
             note="自有渲染 0.00%/max2（判读只看 d10 两档）；与 39 在本口径不可区分",
             rationale="height/linearHeight 两侧都逐像素不可区分（Krita 14 与 15 渲染 max Δ=0）→ 本行只能证明"
                       "「映射到 14 与 PS 一致」，不能证明 14 比 15 更准。"),
    39: dict(verdict="可映射", kind="像素比对（自有渲染）", strength="中", dist=False, s1="条件成立",
             batch="C：直接可映射（达标）",
             note="自有渲染 0.00%/max2；按 Krita 枚举 15 登记，与 14 不可区分",
             rationale="C 批明确：不能据此断言 15 比 14 准；37 改成「两者在当前口径下不可区分，按 Krita 枚举 14/15 分别登记」。"),
    40: dict(verdict="可映射", kind="像素比对（跨模式候选）", strength="弱-塌缩", dist=False, s1="不依赖",
             batch="C：可直接映射 → Multiply(0)", note="与 Mltp 同哈希（塌缩类 P1）", rationale="同 order 37。"),
    41: dict(verdict="可映射", kind="像素比对（跨模式候选）", strength="弱-塌缩", dist=False, s1="不依赖",
             batch="C：可直接映射 → Multiply(0)", note="与 Mltp 同哈希（塌缩类 P1）", rationale="同 order 37。"),
    42: dict(verdict="可映射", kind="像素比对（跨模式候选）", strength="中", dist=True, s1="不依赖",
             batch="C：可直接映射 → Subtract(1)",
             note="PS 真值与 Sbtr 同哈希；旧代码回退 Multiply 属映射错误，本批修正",
             rationale="唯一「原来映射错」的：PS 的 blendSubtraction 真值 ≡ Sbtr 真值，映射到 1 后 6/6 组 0.00%。"),
    43: dict(verdict="不可映射", kind="结构判定（枚举无成员）", strength="结构性+饱和", dist=False, s1="不依赖",
             batch="C：结构性不可映射", note="PS 真值 6/6 饱和（恒 255）；Krita 无 DIVIDE",
             rationale="PS 侧纹理未进入笔迹（常量 255）+ Krita 无成员。"),
    44: dict(verdict="不可映射", kind="结构判定（PS 无真值）", strength="结构性+无真值", dist=None, s1="不依赖",
             batch="C：结构性不可映射（无 PS 可比真值）",
             note="PS 不接受该枚举值（回读停在上一模式）；Krita 2 保留、无 PS 依据",
             rationale="mode-ids：PS 两张模式表都没有 Lght，ABR 值域出现 0 次；Krita 侧 6/6 组自身也退化成常量。"),
    45: dict(verdict="不可映射", kind="结构判定（PS 无真值）", strength="结构性+无真值", dist=None, s1="不依赖",
             batch="C：结构性不可映射（无 PS 可比真值）",
             note="PS 不接受该枚举值；Krita 3 保留（GRADIENT 在预设里静默回退 SUBTRACT）",
             rationale="同 order 44；另加 kis_texture_option.cpp:74-76 的 GRADIENT→SUBTRACT 回退。"),
    46: dict(verdict="不可映射", kind="结构判定（PS 无真值）", strength="结构性+无真值", dist=None, s1="不依赖",
             batch="C：结构性不可映射（无 PS 可比真值）",
             note="PS 不接受该枚举值；Krita 11 保留（5 字符名，charID 只收 4 字符）",
             rationale="同 order 44。"),
}

CHANGED = {  # order -> 本批改动的文件:行（未改的写「不改」）
    0: 'src/brush_converter/mapping.py:64（新增 "Nrml": 0）',
    2: 'src/brush_converter/mapping.py:65（新增 "Bhnd": 0）',
    3: 'src/brush_converter/mapping.py:66（新增 "Clar": 0）',
    4: 'src/brush_converter/mapping.py:63（新增 "Mltp": 0）',
    6: "不改（v1.2.1 已登记 mapping.py:38）",
    11: 'src/brush_converter/mapping.py:74（新增 "Dfrn": 1）',
    12: 'src/brush_converter/mapping.py:67（新增 "H   ": 0）',
    13: 'src/brush_converter/mapping.py:68（新增 "Strt": 0）',
    14: 'src/brush_converter/mapping.py:69（新增 "Clr ": 0）',
    15: 'src/brush_converter/mapping.py:70（新增 "Lmns": 0）',
    17: "不改（v1.2.1 已登记 mapping.py:39）",
    18: "不改（v1.2.1 已登记 mapping.py:40）",
    20: "不改（v1.2.1 已登记 mapping.py:34）",
    21: "不改（v1.2.1 已登记 mapping.py:46）",
    22: 'src/brush_converter/mapping.py:56（新增 "linearDodge": 8）',
    23: 'src/brush_converter/mapping.py:57（新增 "linearBurn": 9）',
    37: 'src/brush_converter/mapping.py:80（新增 "passThrough": 0）',
    38: "不改（v1.2.1 已登记 mapping.py:50）",
    39: "不改（v1.2.1 已登记 mapping.py:49）",
    40: 'src/brush_converter/mapping.py:81（新增 "lighterColor": 0）',
    41: 'src/brush_converter/mapping.py:82（新增 "darkerColor": 0）',
    42: 'src/brush_converter/mapping.py:86（新增 "blendSubtraction": 1）',
}
DEFAULT_CHANGED = "不改（保留回退 Multiply(0) + 警告）"


def load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def _r4(v):
    return None if v is None else round(float(v), 4)


def collapse_classes():
    """按 PS 真值 6 组 alpha_sha256 元组给有真值的模式分类（模式塌缩）。

    数据来源：tests/baselines/mode/manifest.json（33 的 34 模式 × 6 组）+
    tests/baselines/mode-C/ps_rerender_result.json（C 批改名重出的 5 模式 × 6 组）。
    """
    truth = {}
    for e in load(MANIFEST)["entries"]:
        if e.get("kind") != "ps" or not e.get("ok"):
            continue
        tag = "d%d_b%d_c%d" % (e["depth"], e["brightness"], e["contrast"])
        truth.setdefault(e["order"], {})[tag] = e
    if RERENDER.exists():
        for e in load(RERENDER)["entries"]:
            if not e.get("ok"):
                continue
            tag = "d%d_b%d_c%d" % (e["depth"], e["brightness"], e["contrast"])
            truth.setdefault(e["order"], {}).setdefault(tag, e)
    by_order, classes = {}, []
    for order in sorted(truth):
        g = truth[order]
        if not all(k in g for k in GROUPS):
            continue
        key = tuple(g[k]["alpha_sha256"] for k in GROUPS)
        hit = next((c for c in classes if c["key"] == key), None)
        if hit is None:
            d10 = g[GROUPS[0]]
            hit = {"key": key, "id": "P%d" % (len(classes) + 1), "orders": [],
                   "d10_u": d10["alpha_unique"], "d10_mean": d10["alpha_mean"],
                   "all_u1": all(g[k]["alpha_unique"] == 1 for k in GROUPS)}
            classes.append(hit)
        hit["orders"].append(order)
        by_order[order] = hit
    for c in classes:
        if c["all_u1"]:
            c["label"] = "6 组恒 255 常量（u=1）"
        elif c["d10_u"] == 1:
            c["label"] = "d10 全 255、其余组有纹理"
        else:
            c["label"] = "d10 u=%d / mean %.3f" % (c["d10_u"], c["d10_mean"])
    return by_order, classes


def judge_numbers(judge_groups):
    """从某批 judge-result.json 的 6 组里取「判定候选」的数字。

    判定候选 = 该模式自己的 Krita 渲染（若存在）⇒ 否则取跨模式最近候选 best_ref。
    """
    own, best, sat = [], [], 0
    for g in GROUPS:
        grp = judge_groups.get(g) or {}
        ps = grp.get("ps") or {}
        if ps.get("saturated"):
            sat += 1
        o = grp.get("own_krita") or grp.get("ps_vs_krita")
        if o:
            own.append(o)
        b = grp.get("best_ref")
        if b:
            best.append(b)
    pick = own or best
    ratios = [x["diff_ratio"] for x in pick if x.get("diff_ratio") is not None]
    deltas = [x["max_delta"] for x in pick if x.get("max_delta") is not None]
    return dict(
        diff_ratio=_r4(max(ratios)) if ratios else None,
        diff_ratio_best=_r4(min(ratios)) if ratios else None,
        max_delta=max(deltas) if deltas else None,
        max_delta_best=min(deltas) if deltas else None,
        saturated_groups=sat,
        judge_candidate="自有渲染" if own else ("跨模式候选" if best else "无"),
    )


def _premise(strength, cls, candidate=""):
    base = "判据材料＝单笔尖满覆盖(base=1)+100% 倒相纹理+6 组参数"
    if strength == "强":
        return base + "；判定用自有 Krita 真机渲染，两侧非饱和"
    if strength == "中":
        if candidate == "自有渲染":
            return base + "；判定用自有 Krita 真机渲染，但部分组两侧饱和（只有非饱和组有判别力）"
        return base + "；判定用跨模式真机候选（两侧非饱和）"
    if strength == "弱-饱和":
        return "PS 真值 6/6 组饱和（u=1）；0.00% 只证明「不冲突」，不证明语义一致"
    if strength == "弱-退化":
        return "base=1 数学退化：多个 Krita 算子在本材料上同像素；0.00% 不构成语义等价证据"
    if strength == "弱-塌缩":
        n = len(cls["orders"]) if cls else 0
        return "PS 侧模式塌缩：本类 %d 个 PS 模式出同一张像素；0.00%% 只证明「本口径下不可区分」" % n
    if strength == "结构性":
        return "Krita TexturingMode 只有 16 个成员（0..15），枚举里无该成员；最近候选也超 2% 门槛"
    if strength == "结构性+饱和":
        return "枚举无该成员（硬证据）+ PS 真值饱和（像素证据无判别力）"
    if strength == "结构性+退化":
        return "枚举无该成员 + 跨模式候选 0.00% 属 base=1 退化（不作等价证据）"
    if strength == "结构性+无真值":
        return "PS 侧产不出真值（落笔报错 / 枚举被忽略 / 不接受该值）；结论由枚举与静态证据支撑"
    if strength == "需校准-边界残差":
        return "PS 真值与 Krita 自有渲染只差 103 px（掩码 T==1 的边界，src==0 处 Krita 给 0、PS 给 255）；转换侧无参数可修"
    return base


def build():
    batch = load(BATCH_JSON)
    judges = {k: {m["order"]: m for m in load(p)["modes"]} for k, p in JUDGE.items()}
    corrections = (load(IDS_JSON).get("name_corrections") or {}).get("map") or {}
    by_order_cls, classes = collapse_classes()

    modes = []
    for b in batch["batches"]:
        for mem in b["members"]:
            order = mem["order"]
            dec = D[order]
            num = judge_numbers((judges[b["batch"]].get(order) or {}).get("groups") or {})
            cls = by_order_cls.get(order)
            row = dict(
                order=order, batch=b["batch"],
                ps_member=mem["ps_member"], ps_semantics=mem.get("ps_semantics", ""),
                ps_real_4cc=corrections.get(mem["ps_member"]) or None,
                runtime_id=mem.get("runtime_id"), krita_value=mem.get("krita_value"),
                krita_member=mem.get("krita_member"),
                verdict=dec["verdict"], judge_kind=dec["kind"], **num,
                evidence_strength=dec["strength"], distinguishable=dec["dist"],
                collapse_class=cls["id"] if cls else None,
                collapse_class_size=len(cls["orders"]) if cls else None,
                premise=_premise(dec["strength"], cls, num["judge_candidate"]),
                s1_premise=dec["s1"], batch_verdict=dec["batch"],
                note=dec["note"], rationale=dec["rationale"],
                changed_files=CHANGED.get(order, DEFAULT_CHANGED),
            )
            modes.append(row)

    counts = {v: sum(1 for m in modes if m["verdict"] == v) for v in VERDICTS}
    strength = {}
    for m in modes:
        strength[m["evidence_strength"]] = strength.get(m["evidence_strength"], 0) + 1
    by_batch = {}
    for m in modes:
        d = by_batch.setdefault(m["batch"], {v: 0 for v in VERDICTS})
        d[m["verdict"]] += 1
        d["合计"] = d.get("合计", 0) + 1

    d10 = {}
    for c in classes:
        d10.setdefault(c["key"][0], []).append(c)
    d10_classes = [sum(len(c["orders"]) for c in v) for v in d10.values()]

    data = {
        "schema_version": "1.0",
        "task": "37",
        "generated_by": "python tools/render_mode_table.py --build",
        "purpose": "v0.4 三批（任务 34/35/36）模式结论合流后的唯一真相：47 个 PS 模式一行一条，"
                   "含 verdict / 判别方式 / 差异数字 / 证据强度 / 前提 / 塌缩类",
        "sources": {
            "mode_list": "docs/数据/mode-batch.json（47 成员 / 三批 16+16+15）",
            "ps_numbers": ["tests/baselines/mode-A/judge-result.json",
                           "tests/baselines/mode-B/judge-result.json",
                           "tests/baselines/mode-C/judge-result.json"],
            "ps_truth_hashes": ["tests/baselines/mode/manifest.json",
                                "tests/baselines/mode-C/ps_rerender_result.json"],
            "ps_runtime_ids": "docs/数据/mode-ids.json（含 name_corrections）",
            "batch_reports": ["docs/研究报告/模式-A.md", "docs/研究报告/模式-B.md", "docs/研究报告/模式-C.md"],
            "krita_enum": "docs/数据/krita_caps.json（TexturingMode 0..15，16 成员）",
        },
        "verdicts_allowed": list(VERDICTS),
        "column_semantics": {
            "verdict": "可映射=Krita 有承载且实测达标（≤2%/max≤2）；需校准=有承载但残差超阈且已定位；"
                       "不可映射=无等价算子或 PS 侧产不出真值；不可判定=证据不足（本表 0 条，见 37 报告 §三-3）",
            "judge_kind": "像素比对（自有渲染）=PS 真值 vs 该模式自己的 Krita 渲染；像素比对（跨模式候选）=PS 真值 vs "
                          "别的 Krita 模式渲染；结构判定=靠枚举/静态证据；需校准（边界残差）",
            "diff_ratio": "判定候选在 6 组中的最差差异比例（分母 W×H，|Δalpha|>2）",
            "diff_ratio_best": "同上最好值（用来看「离门槛多近」）",
            "max_delta": "判定候选在 6 组中的最差 max_delta",
            "saturated_groups": "PS 真值 alpha_unique==1 的组数（饱和组无判别力）",
            "evidence_strength": "强 / 中 / 弱-饱和 / 弱-退化 / 弱-塌缩 / 结构性 / 结构性+饱和 / 结构性+退化 / "
                                 "结构性+无真值 / 需校准-边界残差",
            "distinguishable": "当前判据材料能否区分本行结论；false=像素不可区分（饱和/退化/塌缩）；null=无真值未测",
            "collapse_class": "PS 侧模式塌缩类（6 组 alpha_sha256 元组相同即同类）；null=无 PS 真值",
            "premise": "结论成立的判据前提（口径 / 退化 / 饱和 / 塌缩 / 枚举）",
            "s1_premise": "「条件成立」= 该行 ≤2% 结论引用了 tools/texture_mask.py 的复刻交叉校验，"
                          "而复刻只在强度 s=1 与真实渲染逐像素一致（s<1 实测差 229/102）",
            "batch_verdict": "该批报告原文结论（只记录，不覆盖）",
            "changed_files": "v0.4 本批改的 src 行；未改的写明不改",
        },
        "judging_rule": {
            "diff_tol": 2, "diff_pixel": "|Δalpha| > 2 的像素数",
            "ratio_max": 0.02, "max_delta_max": 2,
            "denominator": "W×H = 10000（不是非零像素数）",
            "criterion": "差异比例 ≤ 2% 且 max_delta ≤ 2（两条都要）",
        },
        "counts": {
            "total": len(modes),
            "by_verdict": counts,
            "by_batch": by_batch,
            "by_evidence_strength": dict(sorted(strength.items())),
            "with_ps_truth": sum(1 for m in modes if m["collapse_class"]),
            "without_ps_truth": sum(1 for m in modes if not m["collapse_class"]),
            "no_conclusion": 0,
        },
        "collapse": {
            "definition": "把有 PS 真值的模式按「6 组 alpha_sha256 元组」分类；同类 = 在这 6 组参数下 PS 出的是同一张像素",
            "modes_with_truth": sum(1 for m in modes if m["collapse_class"]),
            "distinct_classes": len(classes),
            "indistinguishable_modes": sum(len(c["orders"]) for c in classes if len(c["orders"]) > 1),
            "d10_only_classes": len(d10),
            "d10_only_indistinguishable_modes": sum(n for n in d10_classes if n > 1),
            "classes": [{"id": c["id"], "size": len(c["orders"]), "orders": c["orders"], "label": c["label"]}
                        for c in classes],
        },
        "modes": modes,
    }
    return data


# ── 渲染 ───────────────────────────────────────────────────────────────────────
BT = chr(96)  # 反引号：源码里不直接写，避免与工具链冲突


def _code(s):
    return BT + s + BT


def _cell(x):
    return str(x).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _ratio_cell(m):
    if m["diff_ratio"] is None:
        return "—"
    if m["diff_ratio"] == m["diff_ratio_best"]:
        return "%.2f%%" % (m["diff_ratio"] * 100)
    return "%.2f%%–%.2f%%" % (m["diff_ratio_best"] * 100, m["diff_ratio"] * 100)


def _kv_cell(m):
    if m["krita_value"] is None:
        return "无对应"
    return "%d %s" % (m["krita_value"], m["krita_member"] or "")


def render_section(data):
    c = data["counts"]
    col = data["collapse"]
    bb = c["by_batch"]
    out = [SECTION_HEAD, ""]
    out.append("> 本节的**数据真身是 " + _code("docs/数据/mode-table.json") + "**：由 "
               + _code("python tools/render_mode_table.py --build") + " 重算，由 "
               + _code("python tools/render_matrix_md.py") + " 写入。**禁止手改本节**。")
    out.append("> 本节把 v0.4 三批（任务 34/35/36 = A/B/C）的逐模式结论合成唯一真相，"
               "**不新增模式结论**，只统一三批口径并重算数字。")
    out.append("")
    out.append("**判据（三批一致，未放宽）**：差异比例 = " + _code("|Δalpha|>2") + " 的像素数 ÷ " + _code("W×H")
               + "，门槛 **≤2% 且 max_delta ≤2**；分母是全部像素，不是非零像素数。")
    out.append("")
    out.append("| verdict | 含义 | 本表条数 |")
    out.append("|---|---|---:|")
    out.append("| 可映射 | Krita 有承载字段（TexturingMode 成员），实测差异 ≤2%%/max≤2 | %d |" % c["by_verdict"]["可映射"])
    out.append("| 需校准 | Krita 有承载，但残差超阈且已定位（只有 hardMix 1 条） | %d |" % c["by_verdict"]["需校准"])
    out.append("| 不可映射 | Krita 枚举无等价算子，或 PS 侧产不出真值 | %d |" % c["by_verdict"]["不可映射"])
    out.append("| 不可判定 | 证据不足（原 A 批 4 个「真值饱和」按「枚举有无成员」改判，故本表 0 条） | %d |"
               % c["by_verdict"]["不可判定"])
    out.append("")
    out.append("**证据强度分档**（决定「能不能拿这条结论去改用户可见行为」）：")
    out.append("")
    out.append("| 档位 | 判据 | 条数 |")
    out.append("|---|---|---:|")
    out.append("| 强 | 自有 Krita 同模式真机渲染 + 两侧非饱和 + 6/6 组 ≤2%%/max≤2 | %d |"
               % c["by_evidence_strength"].get("强", 0))
    out.append("| 中 | 无同模式渲染，用跨模式真机候选；两侧非饱和 + 6/6 组达标 | %d |"
               % c["by_evidence_strength"].get("中", 0))
    out.append("| 弱-饱和 | 判据材料饱和（PS 真值 " + _code("alpha_unique==1") + "），0.00%% 只证明「不冲突」 | %d |"
               % c["by_evidence_strength"].get("弱-饱和", 0))
    out.append("| 弱-退化 | base=1 数学退化（多个算子同像素），0.00%% 不构成语义等价证据 | %d |"
               % c["by_evidence_strength"].get("弱-退化", 0))
    out.append("| 弱-塌缩 | PS 侧多个模式出同一张像素（模式塌缩），0.00%% 只证明本口径不可区分 | %d |"
               % c["by_evidence_strength"].get("弱-塌缩", 0))
    out.append("| 结构性（含 +饱和 / +退化 / +无真值） | 枚举无成员或 PS 侧产不出真值——结论不依赖像素门槛 | %d |"
               % (c["by_evidence_strength"].get("结构性", 0) + c["by_evidence_strength"].get("结构性+饱和", 0)
                  + c["by_evidence_strength"].get("结构性+退化", 0)
                  + c["by_evidence_strength"].get("结构性+无真值", 0)))
    out.append("| 需校准-边界残差 | 残差已定位到 Krita 算子边界，转换侧无参数可修 | %d |"
               % c["by_evidence_strength"].get("需校准-边界残差", 0))
    out.append("")
    out.append("**计数**：共 **%d** 个 PS 模式（A %d / B %d / C %d）；可映射 **%d** / 需校准 **%d** / "
               "不可映射 **%d** / 不可判定 **%d**；**无结论模式数 %d**。"
               % (c["total"], bb["A"]["合计"], bb["B"]["合计"], bb["C"]["合计"],
                  c["by_verdict"]["可映射"], c["by_verdict"]["需校准"], c["by_verdict"]["不可映射"],
                  c["by_verdict"]["不可判定"], c["no_conclusion"]))
    out.append("")
    out.append("**模式塌缩**：有 PS 真值的 **%d** 个模式里，按 6 组 " % c["with_ps_truth"]
               + _code("alpha_sha256") + " 元组严格分类只有 **%d** 类（%d 个模式与别的模式像素完全不可区分）；"
               "若只看 " % (col["distinct_classes"], col["indistinguishable_modes"])
               + _code("d10_b0_c0") + " 单张图则是 **%d** 类——任务 36 报告说的「7 张不同像素」用的是单组口径。"
               % col["d10_only_classes"])
    out.append("")
    out.append("⚠ 因此本表**禁止**把 0.00% 读成「语义等价」：PS 侧 13 个不同语义的模式出同一张图，"
               "0.00% 只能读成「当前口径下像素不可区分」。")
    out.append("")
    out.append("**复刻前提（soft texturing）**：" + _code("tools/texture_mask.py") + " 没有 "
               + _code("useSoftTexturing") + " 分支，只在强度 s=1 与真实 Krita 渲染逐像素一致"
               "（s<1 实测差 229/102）。本表全部判定用**真实渲染**比较；只有「前提」列标 **s=1 条件成立** 的行"
               "引用了复刻做交叉校验。")
    out.append("")
    out.append("### 逐模式总表（47 行，按 mode-batch.json 的 order）")
    out.append("")
    heads = ["#", "批", "PS 模式", "runtime_id", "Krita 值", "结论", "判别方式", "差异比例（最好–最差）",
             "max_delta", "证据强度", "可区分", "塌缩类", "前提 / 备注"]
    out.append("| " + " | ".join(heads) + " |")
    out.append("|" + "|".join(["---:"] + ["---"] * (len(heads) - 1)) + "|")
    for m in data["modes"]:
        member = _code(m["ps_member"])
        if m["ps_real_4cc"]:
            member += "（真名 " + _code(m["ps_real_4cc"]) + "）"
        premise = m["premise"]
        if m["s1_premise"] == "条件成立":
            premise += "；**s=1 条件成立**"
        dist = {True: "是", False: "否", None: "未测"}[m["distinguishable"]]
        cls = "%s（%d）" % (m["collapse_class"], m["collapse_class_size"]) if m["collapse_class"] else "—"
        cells = [str(m["order"]), m["batch"], member,
                 "—" if m["runtime_id"] is None else str(m["runtime_id"]), _kv_cell(m),
                 "**%s**" % m["verdict"], m["judge_kind"], _ratio_cell(m),
                 "—" if m["max_delta"] is None else str(m["max_delta"]),
                 m["evidence_strength"], dist, cls, _cell(premise) + "；" + _cell(m["note"])]
        out.append("| " + " | ".join(cells) + " |")
    out.append("")
    out.append("**三批原判与 37 定稿的逐条差异**（含原文与行号）见 " + _code("docs/验收报告/37-报告.md")
               + " §三/§四；本表 " + _code("batch_verdict") + " 字段保留三批原文结论。")
    out.append("")
    return "\n".join(out) + "\n"



# ── 数据修订：把 C 批「改名重出」的 5 个模式写回 mode-batch.json / manifest.json ────────────
RERENDER_ORDERS = (32, 33, 34, 35, 36)
RERENDER_TAG = "task36-rerender"


def _group_key(e):
    """按 6 组的固定顺序排序（d10_b0_c0, d10_b-50_c-50, d60… ），与任务 33 的 GROUPS 一致。"""
    tag = "d%d_b%d_c%d" % (e["depth"], e["brightness"], e["contrast"])
    return GROUPS.index(tag) if tag in GROUPS else 99


def amend_data():
    """按 模式-C.md §5.3 把 5 个模式的 PS 真值状态从「fail：值被 PS 忽略」更正为「已改名重出」。

    幂等：重复运行结果不变（manifest 里旧的本脚本条目会先剔除再重建）。
    只改数据，不改任何既有图、不改 33 的原始失败记录（保留在 ps_truth.orig_* 里）。
    """
    rer = load(RERENDER)["entries"]
    by_order = {}
    for e in rer:
        by_order.setdefault(e["order"], []).append(e)
    for e in rer:
        if e["order"] not in RERENDER_ORDERS:
            raise SystemExit("ps_rerender_result.json 出现未预期的 order=%r" % e["order"])

    # 1) mode-batch.json：ps_truth 改成重出结果（保留原失败状态）
    doc = load(BATCH_JSON)
    fixed = {}
    for entry in load(RERENDER).get("fixed", []):
        fixed[entry["order"]] = entry
    changed = 0
    for b in doc["batches"]:
        for mem in b["members"]:
            order = mem["order"]
            if order not in by_order:
                continue
            imgs = [{
                "file": "tests/baselines/mode-C/ps/" + e["file"],
                "ok": bool(e.get("ok")),
                "jsx_verify": bool(e.get("verify")),
                "mode_read": e.get("mode_read", ""),
                "mode_value": e.get("mode_value", ""),
                "error": e.get("error", ""),
                "alpha_mean": e.get("alpha_mean"),
                "alpha_unique": e.get("alpha_unique"),
                "alpha_sha256": e.get("alpha_sha256"),
            } for e in sorted(by_order[order], key=_group_key)]
            old = mem.get("ps_truth") or {}
            if "orig_status" not in old:
                old = dict(old)
                old["orig_status"] = old.get("status")
                old["orig_ok"] = old.get("ok")
                old["orig_fail"] = old.get("fail")
                old["orig_images"] = old.get("images")
            fx = fixed.get(order) or {}
            mem["ps_truth"] = {
                "组数": 6, "ok": sum(1 for e in by_order[order] if e.get("ok")), "fail": 0,
                "status": "ok（6/6）★ 33 记为 fail「值被 PS 忽略」，实为枚举写法错；C 批按真名重出成功",
                "images": imgs,
                "orig_status": old.get("orig_status"),
                "orig_ok": old.get("orig_ok"),
                "orig_fail": old.get("orig_fail"),
                "orig_images": old.get("orig_images"),
                "rerender": {
                    "task": "36",
                    "script": "tests/baselines/mode-C/ps_rerender_c.py",
                    "result": "tests/baselines/mode-C/ps_rerender_result.json",
                    "fixed_mode_kind": fx.get("mode_kind"),
                    "fixed_mode_value": fx.get("mode_value"),
                    "fixed_mode_key": fx.get("mode_key"),
                    "images_in_repo": False,
                    "basis": "docs/研究报告/模式-C.md §5.3（伪名是 Ghidra 小端反序渲染；喂真名四张表才动）",
                },
            }
            changed += 1

    ps_ok = 0
    for b in doc["batches"]:
        for mem in b["members"]:
            ps_ok += int((mem.get("ps_truth") or {}).get("ok") or 0)
    doc["counts"]["ps_ok"] = ps_ok
    doc.setdefault("counts", {})["ps_ok_before_rerender"] = 204
    doc["amendments"] = [{
        "task": "37",
        "what": "B/C 批的 PS 真值状态更正：order 32–36 从「fail：值被 PS 忽略」改为「改名重出后 ok（6/6）」",
        "basis": "docs/研究报告/模式-C.md §5.3；tests/baselines/mode-C/ps_rerender_result.json（30/30 ok+verify）",
        "evidence": "docs/数据/mode-ids.json 的 name_corrections（SnrB→BrnS、MnrB→BrnM、HnrB→BrnH、ttsD→Dstt、Str 保持）",
        "note": "重出的 30 张图在 tests/baselines/mode-C/ps/（.gitignore 已忽略，不入库）；"
                "本修订只改登记状态，不改任何既有图。重跑 python tools/render_mode_table.py --amend-data 可复现。",
    }]
    BATCH_JSON.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # 2) manifest.json：追加 30 条重出记录（kind=ps, source=task36-rerender），保留原失败记录
    man = load(MANIFEST)
    kept = [e for e in man["entries"] if e.get("source") != RERENDER_TAG]
    kr = [e for e in kept if e.get("kind") == "krita"]
    ps = [e for e in kept if e.get("kind") == "ps"]
    ps.sort(key=lambda e: (e.get("order") or 0, e.get("file") or ""))
    kr.sort(key=lambda e: (e.get("order") or 0, e.get("file") or ""))
    kvals = {}
    for b in doc["batches"]:
        for mem in b["members"]:
            kvals[mem["order"]] = mem.get("krita_value")
    amend = []
    for order in sorted(by_order):
        for e in sorted(by_order[order], key=_group_key):
            png = REPO / "tests/baselines/mode-C/ps" / e["file"]
            amend.append({
                "file": "../mode-C/ps/" + e["file"], "kind": "ps", "source": RERENDER_TAG,
                "supersedes": "ps/ps__mode__%02d_%s__%s.png" % (
                    order, e["ps_member"].strip().replace(" ", "_") or "unnamed",
                    "d%d_b%d_c%d" % (e["depth"], e["brightness"], e["contrast"])),
                "order": order, "ps_member": e["ps_member"], "krita_value": kvals.get(order),
                "depth": e["depth"], "brightness": e["brightness"], "contrast": e["contrast"],
                "ok": bool(e.get("ok")), "exists": png.is_file(),
                "jsx_verify": bool(e.get("verify")), "mode_read": e.get("mode_read", ""),
                "sha256": None, "idat_md5": None,
                "alpha_sha256": e.get("alpha_sha256"), "bytes": None,
                "alpha_mean": e.get("alpha_mean"), "alpha_unique": e.get("alpha_unique"),
                "alpha_nonzero_ratio": e.get("alpha_nonzero_ratio"), "error": "",
            })
    man["entries"] = ps + amend + kr
    man["counts"] = {
        "ps": sum(1 for e in man["entries"] if e["kind"] == "ps"),
        "ps_ok": sum(1 for e in man["entries"] if e["kind"] == "ps" and e["ok"]),
        "ps_base": len(ps),
        "ps_base_ok": sum(1 for e in ps if e["ok"]),
        "ps_rerender": len(amend),
        "krita": len(kr),
        "krita_ok": sum(1 for e in kr if e["ok"]),
    }
    man["amendments"] = [{
        "task": "37",
        "what": "追加 C 批改名重出的 30 条 PS 真值登记（source=%s），原 30 条失败记录保留不动" % RERENDER_TAG,
        "basis": "docs/研究报告/模式-C.md §5.3；tests/baselines/mode-C/ps_rerender_result.json",
        "note": "图片在 tests/baselines/mode-C/ps/（不入库）；file 字段用相对路径 ../mode-C/ps/… 标明出处。"
                "重跑 python tools/render_mode_table.py --amend-data 可复现。",
    }]
    _note_mark = "；任务 37 追加 amendments（C 批 5 模式重出，见 amendments 字段）"
    _note_base = str(man.get("note") or "").replace(_note_mark, "")
    man["note"] = _note_base + _note_mark
    MANIFEST.write_text(json.dumps(man, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("已修订 %s：%d 个模式 ps_truth 更正，counts.ps_ok=%d；%s：%d 条重出登记（counts.ps=%d/ps_ok=%d）"
          % (BATCH_JSON.relative_to(REPO).as_posix(), changed, ps_ok,
             MANIFEST.relative_to(REPO).as_posix(), len(amend), man["counts"]["ps"], man["counts"]["ps_ok"]))
    return 0

def _split_md(text):
    i = text.find(SECTION_HEAD)
    j = text.find(NEXT_HEAD)
    if i < 0 or j < 0 or j < i:
        return None
    return text[:i], text[i:j], text[j:]


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成 docs/数据/mode-table.json 与 docs/参数矩阵.md 的模式总表节")
    ap.add_argument("--build", action="store_true", help="重算 docs/数据/mode-table.json")
    ap.add_argument("--amend-data", action="store_true",
                    help="把 C 批改名重出的 5 个模式写回 mode-batch.json / manifest.json（幂等）")
    ap.add_argument("--check", action="store_true", help="只比对 docs/参数矩阵.md 的模式总表节")
    ap.add_argument("--stdout", action="store_true", help="打印模式总表节")
    args = ap.parse_args(argv)

    if args.amend_data:
        return amend_data()

    if args.build:
        data = build()
        TABLE_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        c = data["counts"]
        print("已写 %s：%d 行，可映射 %d / 需校准 %d / 不可映射 %d / 不可判定 %d（无结论 %d）"
              % (TABLE_JSON.relative_to(REPO).as_posix(), c["total"], c["by_verdict"]["可映射"],
                 c["by_verdict"]["需校准"], c["by_verdict"]["不可映射"], c["by_verdict"]["不可判定"],
                 c["no_conclusion"]))
        return 0

    data = load(TABLE_JSON)
    section = render_section(data)
    if args.stdout:
        sys.stdout.write(section)
        return 0
    md = MATRIX_MD.read_text(encoding="utf-8")
    parts = _split_md(md)
    if args.check:
        if parts is None:
            print("%s 里找不到模式总表节，请跑 python tools/render_matrix_md.py" % MATRIX_MD.name, file=sys.stderr)
            return 1
        if parts[1] != section:
            print("%s 的模式总表节与 mode-table.json 不同步，请重跑 python tools/render_matrix_md.py"
                  % MATRIX_MD.name, file=sys.stderr)
            return 1
        print("%s 的模式总表节与 mode-table.json 同步" % MATRIX_MD.name)
        return 0
    if parts is None:
        MATRIX_MD.write_text(md.rstrip("\n") + "\n\n" + section, encoding="utf-8")
    else:
        MATRIX_MD.write_text(parts[0] + section + parts[2], encoding="utf-8")
    print("已写 %s 的模式总表节（%d 行）" % (MATRIX_MD.name, len(data["modes"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
