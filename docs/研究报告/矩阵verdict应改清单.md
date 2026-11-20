# 矩阵 verdict 应改清单（任务 108 收敛定稿）

> 执行者：v08验收员（v1.4.0 第三轮，任务 108）｜日期：2026-11-20｜工作区：仓库根
> 数据基准：HEAD = `4335bb5`（107 参数修正提交）；矩阵现状已用 `python tools/check_matrix.py` 复核（退出码 0，137 行、8 栏）。
> 收敛来源：`docs/验收报告/107-报告.md`、`docs/研究报告/参数偏差-2026-11-20.md`（106）、
> `docs/数据/偏差/results_{spacing,scatter,hardness,size,angle,scale}.json`、`docs/parameter-mapping.md` §10。
> 判据（不许改）：差异比例 ≤2% 且 max_delta ≤2；几何类相对误差 ≤2%（绝对差 ≤2px 亦算一致）；判像素只认 PNG IDAT 的 sha256。
> **任务 113 以此为准。** 本文件是 113 修改 `docs/参数矩阵.json` verdict 的唯一依据；不在本表的行一律不改。
> 本清单不改 `docs/数据/mode-table.json`、不改 `src/**`、不改 `tests/**`。

---

## 一、总表：7 行（1 改 + 6 维持）

| # | 矩阵键 | 现有 verdict | 应改成 | 依据（哪份证据哪一节） |
|---:|---|---|---|---|
| 1 | `Spcn` | 可映射 | **需校准** | 107 §2.B 表（改后 nonsquare 100% = 3.53%，1/8 可判读点超阈）；106 §3.2 表 / §4.2（PS 100% 点自身偏离短边模型 −3.4%，约 3.6 周期少周期估计）；`results_spacing.json` `conclusion_detail`「真偏…nonsquare v100=3.5%（共 1/8 点超阈）」；`parameter-mapping.md` §10.1 与第 13 行状态栏（已写「⚠️需校准」） |
| 2 | `Hrdn` | 需校准 | 维持 需校准 | 106 §3.4 表（h0 +15.60px/19.8%、h50 −7.33px/22.6%、h100 绝对差 1.07px 一致）；107 §2.C（h0 结构性不可达：`fade=0` 已是最软档 63.15px < PS 78.75px）；`parameter-mapping.md` §10.2；`results_hardness.json` 与 106 逐字节一致（blob `0e604d3d…`） |
| 3 | `scatter` | 需校准 | 维持 需校准 | 106 §3.5 表（带高增量 PS/KT 63/38 = 39.7%、274/134 = 51.1%）；107 §2.A（二次式实验已回退，`band_height` 复现性 ±7–13%，≤2% 不可证）；`parameter-mapping.md` §10.3；`results_scatter.json` 与 106 逐字节一致（blob `7a5f9647…`） |
| 4 | `scatterDynamics` | 需校准 | 维持 需校准 | 同第 3 行；该键是 ABR 实际键（`scatterDynamics.jitter`），与 `scatter` 必须同 verdict，避免 113 只改一个留下自相矛盾 |
| 5 | `textureScale` | 需校准 | 维持 需校准 | 106 §3.1 表（200% 差异比例 93.72%、max_delta 93、R 曲线相对残差 32.4%；50% 0.00%/max1、100% 0.00%/max0）；107 §2.D 与 `parameter-mapping.md` §10.4（Qt 重采样，转换侧无常数可修）；`results_scale.json` 与 106 逐字节一致（blob `0567ebe5…`） |
| 6 | `Dmtr` | 可映射 | 维持 可映射 | 106 §3.3 表（相对误差 0.09–2.45%，绝对差 0.25–0.51px 全部 ≤2px）；107 §1/§4「尺寸/角度零改动」；`results_size.json` 与 106 逐字节一致（blob `6d988977…`） |
| 7 | `Angl` | 可映射 | 维持 可映射 | 106 §3.6 表（三点主轴角 Δ=0.00°，判据 3.6°）；`results_angle.json` 与 106 逐字节一致（blob `92929601…`） |

**给 113 的一句话：矩阵只改 1 条（`Spcn` 可映射 → 需校准），其余 6 条维持；mode-table 一律不碰。**

---

## 二、唯一应改条目：`Spcn`（间距）

### 2.1 为什么不能维持「可映射」

按锁定判据，非方形 100% 点同时超两道线：

| 组 | 取值 | PS 步长 | 改后 Krita 步长 | 相对误差（PS 为分母） | 绝对差 | 判定 |
|---|---:|---:|---:|---:|---:|---|
| nonsquare | 100% | 84.51px | 87.49px | **3.53%**（>2%） | **2.98px**（>2px） | 超阈 |

- 其余 7 个可判读点全部达标：nonsquare 10%/50% = 0.11%/0.18%，square 10%/50%/100% = 0.68%/0.37%/1.18%，computed 50%/100% = 1.88%/0.55%（1 点 computed 10% 不可判读，同 106）。
- `tools/measure_deviation.py` 的脚本结论仍是「真偏」（`results_spacing.json` `conclusion_detail`：共 1/8 点超阈）。
- 若写「可映射」等于在验收里放宽判据（违反通用规范铁律 3）；且 `parameter-mapping.md` 第 13 行已把该行标成「✅ / ⚠️需校准（仅非方形 100% 点）」，矩阵与文档现状不一致。

### 2.2 为什么残差不是「换算错」，也不需要 113 之后再去调系数

- 短边基准补偿本身被 10%/50% 两点证明：25.28%→0.11%、28.09%→0.18%，改善 200 倍以上；Krita 改后步长恒等于 112×143/183 = 87.49px（与标称 1:1）。
- 100% 点的 3.53% 来自 PS 侧量测：PS 实测 84.51px 比「短边模型应得 87.5px」低 −3.4%，该点只有约 3.6 个周期（106 §4.2 已标注少周期估计偏差；PS 步长复现性 ±1%）。
- 结论：**写「需校准」并在 note 写明残差性质（PS 少周期量测偏差，不再调系数）**，不是要求 113 改代码。

### 2.3 已考虑过「保留 可映射」的选法（不采纳）

- 支持方理由：残差是 PS 量测噪声而非换算错，公式在 10%/50% 已对齐到 0.1–0.2%。
- 不采纳原因：①机器判据在 100% 点 3.53%/2.98px 双超，脚本仍判「真偏」；②写「可映射」会让矩阵与 `parameter-mapping.md` §10.1 的「⚠️需校准」冲突；③「需校准」+ note 说明残差来源，既能如实反映机器结论，又不会误导下轮去调系数。三条理由同向，故定 **需校准**。

### 2.4 连带文字建议（不属于 verdict，但同一次落库更省事）

- 该行 `krita_target` 现写「spacing 属性 = Spcn/100」，107 后采样笔刷实际是「Spcn/100 × 原始笔尖短边/长边」；建议 113 同步改成后者（严格正方形比值 1，计算笔刷不补偿）。
- `note` 建议写「仅非方形 100% 点残差 3.53%（PS 自身 −3.4%，少周期量测）；10%/50% 0.11%/0.18%，不再调系数」。

---

## 三、维持条目的逐条要点（113 不得改动）

- **`Hrdn`（需校准）**：h0 不可达是结构性（Krita `hfade=0` 已最软，63.15px < PS 78.75px），h50 可单点修（fade 0.60 → 0.03%）但整参数过不了 2%，107 按口径回退未改代码。残差写 `parameter-mapping.md` §10.2。维持。
- **`scatter`（需校准）**：现行代码仍是 `ScatterValue = jitter/400`（`preset_xml.py:484-487`，107 只加注释）；二次式 `s=0.34j′+0.07j′²` 实测后已回退。`band_height` 极值统计复现性 ±7–13%，2% 判据在本统计量下不可证。维持。
- **`scatterDynamics`（需校准）**：与 `scatter` 同参数的另一矩阵键；只改一个会造成同一参数两个 verdict。
- **`textureScale`（需校准）**：50%/100% 达标、200% 结构性残差 32.4%（Qt 重采样），转换侧无常数可修。**注意：lead 交办稿写「现 可映射」，与仓库现状不符——HEAD 矩阵自 103 起就是「需校准」，本轮 104/105/107 均未改过它；以文件为准，本行无需改动。** 另：`docs/parameter-mapping.md` 第 110 行仍写「✅（用户已确认一致）」，与本 verdict 冲突，属历史遗留（该行来自 v1.2.1 时期提交 `9147787`），建议 113「五处文档对齐」时改成「⚠️需校准（200% 残差 32.4%，见 §10.4）」。
- **`Dmtr` / `Angl`（可映射）**：106 判不偏（绝对差 ≤0.51px / Δ=0.00°），107 一行未动，结果 JSON 与 106 逐字节一致，维持。

---

## 四、边界：113 不做的事

1. **模式映射应改清单（104/105）是 0 条映射改动**：6 行 Ovrl/CDdg/CBrn/linearBurn/height/linearHeight 全部「不变」（`docs/研究报告/模式映射应改清单.md` §一/§二），且它们是 `docs/数据/mode-table.json` 的行，**不是矩阵 verdict**；113 不改 mode-table，任务 109 只做回归确认。
2. **HdMxS（order 46）的文档待办属 mode-table**：其 `recheck_cmd` 现值仍是「暂无（待任务 104）」（本次只读复核 `mode-table.json` 未变）。**113 不做，留 116/下一轮**。
3. **107 的「UI 有但无对应」筛查 44 行未实现任何一条**（107 §5）：这些行不在本轮 verdict 改动范围。
4. 本清单不涉及 `docs/参数矩阵.json` 的 `ui_visibility`/`sources`/`range` 等其它栏位；113 只按上表第 1 行改 verdict（连带可改该行 `krita_target`/`note` 文字）。

---

## 五、113 执行口径与自检命令

- 一次全改，不分批（本次实际只动 1 行 verdict，见 108 报告 §8 决策 1；使用者可否决）。
- 改完必须：
  ```bash
  PYTHONIOENCODING=utf-8 <LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe tools/check_matrix.py    # 退出码 0
  PYTHONIOENCODING=utf-8 <LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe tools/render_matrix_md.py  # 重生成 md
  ```
- 抽检命令（复核 Spcn 残差）：
  ```bash
  PYTHONIOENCODING=utf-8 <LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe tools/measure_deviation.py --param spacing --values 10,50,100 --analyze
  ```

---

## 六、一句话

**任务 113 以此为准：矩阵 verdict 只改 1 条（`Spcn`：可映射 → 需校准），`Hrdn`/`scatter`/`scatterDynamics`/`textureScale` 维持需校准，`Dmtr`/`Angl` 维持可映射；mode-table（含 HdMxS 待办）113 不碰。**
