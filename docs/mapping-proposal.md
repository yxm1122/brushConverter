# ABR → Krita 参数映射方案（历史方案，已被取代）

> **历史文档**：本文是 v1.1 之前的初版方案（2026-08-24 等待确认）。它当时提出的结构大部分已落地，
> 但其结论已被 [`parameter-mapping.md`](parameter-mapping.md) 取代——**当前逐字段映射清单以该文件为准，
> 不要按本文实现**。保留本文只为留存决策过程与当时的实测事实。
>
> 基于真实样本 `海怪笔刷…18支.abr`（v6.2，20 个预设：14 采样 + 6 计算）解析得出。
> 本方案供确认后再进入实现（**该「待确认」状态已结束**，确认与实现结果见 `texture-mapping-proposal.md` 与 `parameter-mapping.md`）。

## 0. 已确证的事实

- desc 区段可完整解析：名称 `Nm `、直径 `Dmtr`、硬度 `Hrdn`、角度 `Angl`、圆度 `Rndn`、间距 `Spcn`、笔尖 UUID `sampledData`。
- 采样笔刷 = 有 `sampledData`(UUID)；计算笔刷 = 无 UUID、有 `Hrdn`。
- 多个预设可共享同一笔尖（不同直径），如「勾线笔」1901px 与「大怪兽-勾线笔」26px 共用 `87465e27…`。
- 动态项统一结构：`{ bVTy 变量类型, fStp 渐隐步数, jitter 抖动, Mnm 最小值 }`，外加 `minimumDiameter/minimumRoundness/tiltScale`（`#Prc` 百分比）与 `useXXX` 布尔开关。

## 1. 映射分三档

### 第一档 · 精确映射（保真，全部实现）
| ABR 键 | 含义 | → Krita | 说明 |
|--------|------|---------|------|
| samp 位图 | 笔尖图 | GBR 笔尖 | 直接搬原始字节（0=墨），Krita 加载即得 |
| `Nm ` | 名称 | preset 名称 | 中文名原样保留 |
| `Spcn` | 间距 % | spacing | 同为直径百分比，直接 |
| `Angl` | 角度（度） | angle/rotation | 直接 |
| `Rndn` | 圆度 % | ratio | Krita ratio = 圆度，直接 |
| `Dmtr` | 直径 px | 尺寸缩放 | **见决策 1** |

### 第二档 · 近似映射（压感动态，建议实现）
| ABR 键 | → Krita | 映射方式 |
|--------|---------|----------|
| `minimumDiameter` (#Prc) | SizeSensor 压感曲线 | 压感=0 时尺寸 = 直径×最小值 |
| `opVr`（不透明度动态） | OpacitySensor 压感曲线 | 含 jitter/最小值 |
| `wtVr`（流量动态） | FlowSensor 压感曲线 | 含 jitter/最小值 |
| `szVr.jitter`（大小抖动） | SizeSensor random | 抖动值映射 |
| `angleDynamics` / `roundnessDynamics` | Rotation / ratio 动态 | 含 jitter |
| `prVr`（压力→？） | Pressure 传感器 | 按变量类型 bVTy 判断 |

### 第三档 · 跳过或标注（**历史判断：其中纹理与散布后来已实现，本档已过时**）
| ABR 键 | 跳过原因 |
|--------|----------|
| `useTexture` + `patt` 区 | ~~Krita 纹理引擎不同；patt 可另存为 Krita pattern，但纹理映射复杂~~ **已推翻：v1.1 起纹理已完整映射，见 `parameter-mapping.md` §7** |
| `dualBrush`（双笔刷） | Krita 是 masking brush 机制，语义不同 |
| `useColorDynamics`（H/Strt/Brgh/purity） | 颜色动态两软件模型差异大 |
| `useScatter`（散布） | ~~Krita 有基于 spacing 的散布，可近似但默认不做~~ **已推翻：散布早已映射，见 `parameter-mapping.md` §5** |
| 计算笔刷（无位图） | **见决策 3** |

## 2. 当时待确认的 4 个决策（**均已在后续版本落地**）

1. **直径 `Dmtr` 如何处理？**（影响笔刷在 Krita 里的实际渲染大小）
2. **动态映射做到哪一档？**
3. **计算笔刷（6 支：圆头虚/圆头实/针管笔/喷枪×3）怎么处理？**
4. **交付形态？**

（详见聊天里的选项）

> **后续处置（历史记录）**：4 项均已落地并写进 `parameter-mapping.md`——
> ① 直径 → `scale = Dmtr / max(笔尖宽, 笔尖高)`（§1）；② 动态 → 第一、二档全部实现（§3、§4）；
> ③ 计算笔刷 → Krita `auto_brush`（§2）；④ 交付形态 → `.kpp` + `.bundle` 双形态（README「转换说明」）。
