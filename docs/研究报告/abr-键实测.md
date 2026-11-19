# ABR 描述符键实测（任务 22）

> 数据文件：`docs/数据/abr_keys.json`（由 `tools/scan_abr_keys.py` 生成，可一条命令重算）
> 扫描对象：仓库 `tests/*.abr` 3 个（只读）+ 外部公开样本 4 个（不入库，登记于 `docs/数据/abr-samples.json`）
> 生成时间：2026-11-19 ｜ 工作目录：仓库根
> 同名 JSON 字段含义：`key`/`count`/`type`/`samples`/`in_repo_parser`（必备） + `types`/`paths`/`count_files`/`value_range`/`value_counts`/`repo_refs`（扩展）

## 一、结论（先看这一段）

本任务三个数字（详见 §二 的复现命令）：

| 指标 | 数值 | 说明 |
|---|---:|---|
| **ABR 实测键总数** | **91** | desc 区段里出现过的不同键（含嵌套动态对象里的键，键名原样保留尾空格） |
| **其中仓库解析器已识别** | **39** | 判据：该键作为字符串字面量出现在 `src/brush_converter/**/*.py`（ast 精确匹配） |
| **Krita 侧能力表条目数** | **45** | 来自 `docs/数据/krita_caps.json` 的 `params` 数组 |

扫描规模：7 个 .abr（仓库 3 + 外部 4），解析成功 6，共 79 个预设，键共出现 5240 次。

**五条要点**：

1. **外部 4 支公开样本没有带来任何新键**（外部并集 ⊆ 仓库 3 支的键集；差集为空）。说明现有 3 支仓库样本已覆盖本样本集合里的全部 desc 键，后续要扩键空间必须换来源（例如 v10.x 新字段、湿媒体笔刷、颜色动态齐全的样本）。
2. **未识别的 52 个键集中在转换器「刻意不做」的域**：双笔刷、颜色动态、工具选项/平滑、笔刷姿态、笔刷组、混合/湿边。它们不是解析器坏了，而是任务 23 矩阵里「ABR 有 / 转换器不映射」那一路的原始证据。
3. **6 个键名带尾随空格**："Cnt "、"H   "、"Md  "、"Mnm "、"Nm  "、"Rpt "。扫描器不做 trim，否则与逆向侧、PS schema 对不上。
4. **纹理混合模式实测出现了 `Mltp`（38 次里 17 次）**，而 `mapping.py` 的 `_TEXTURING_MODE` 只登记了 `Mul `——功能上两者都落到 MULTIPLY(0)，但会多打一条「回退 Multiply」警告（任务 23/38 需处置，见 §七）。
5. **纹理缩放实测到 280%**，而 Krita 5.3.3 的缩放滑块范围是 0.0–2.0（见 `docs/研究报告/krita-能力表.md`）——这是「ABR 取值范围超出 Krita UI 范围」的第一例原始证据。

## 二、方法与可复现性

### 2.1 解析路径

扫描器 **只读**，复用仓库自己的 ABR 解析器，保证「扫到的键」与「转换器看到的键」同源：

1. `AbrFile.parse()`（`src/brush_converter/abr/reader.py`）读文件头与 8BIM 区段；
2. 取 `desc` 区段 → `descriptors.parse_descriptor_block()` → `iter_brush_presets()` 取 `Brsh` 列表里每个 `Objc`；
3. 递归遍历描述符（`Objc` / `VlLs` 里的 `Objc` 都会展开），记录 `(键, 路径模板, 深度, 值)`；
4. 按文件 × 出现次数聚合，并静态分析 `src/brush_converter` 的字符串字面量判断 `in_repo_parser`。

### 2.2 计数口径（避免「同名不同义」误读）

- **count**：该键在全部样本里的总出现次数（每个预设的每个键算 1 次；嵌套对象里的键也各算 1 次）。所以 `bVTy` 出现 446 次，是"opVr"、"szVr"、"prVr" 等 6 类动态对象各自都有 bVTy 的结果。
- **count_files**：出现过该键的样本文件数（共 7 个文件，其中 1 个无 desc）。
- **paths**：路径模板（去掉数组下标），用来区分「顶层键」「预设内键」「动态对象内键」；例如 `Brsh[].Brsh.Angl` 的第一个 `Brsh` 是顶层笔刷列表、第二个 `Brsh` 是预设里的笔尖对象。
- **type / types**：Photoshop 描述符值类型（`long`/`doub`/`bool`/`TEXT`/`enum`/`UntF`/`Objc`/`VlLs`…）。`UntF` 是带单位的浮点（`#Prc` 百分比 / `#Pxl` 像素 / `#Ang` 角度）。
- **value_counts**：枚举/数值键在去重后 ≤25 个取值时的真实分布（给任务 23/31 核对「ABR 里真实出现过哪些模式号」）。
- **in_repo_parser**：判据写在 JSON 的 `in_repo_parser_rule` 字段里，命中位置在该键的 `repo_refs`（`文件:行`）。

### 2.3 一条命令复算

```bash
# 只扫仓库样本（与全量扫描的三个数字相同）
python tools/scan_abr_keys.py tests -o /tmp/abr_keys.json --krita-caps docs/数据/krita_caps.json

# 全量（仓库 + 外部样本；外部样本先按 docs/数据/abr-samples.json 的 URL/sha256 下载到 %TEMP%）
python tools/scan_abr_keys.py tests "$TEMP/brushconverter-team/abr-samples" \
    --ext-label "%TEMP%/brushconverter-team/abr-samples" \
    -o docs/数据/abr_keys.json --krita-caps docs/数据/krita_caps.json
```

两条命令末尾都会打印一行固定格式的摘要（本次实测）：

```text
=== 三个数字：ABR实测键总数=91；仓库解析器已识别=39；Krita能力表条目数=45 ===
```

> 备注：Windows 控制台若是 GBK，脚本已自行把 stdout 重配成 UTF-8；若在重定向文件里仍看到乱码，加 `PYTHONIOENCODING=utf-8`。

## 三、样本清单

| 来源 | 文件 | ABR 版本 | 字节 | 预设数 | desc 键数 | sha256 |
|---|---|---|---:|---:|---:|---|
| 仓库（只读） | tests/定量测试专用.abr | v6.2 | 11061688 | 1 | 64 | `8feb07d3b844f2c48470af1878f3ff18488e8462137e35d7b096195341be768b` |
| 仓库（只读） | tests/测试用.abr | v6.2 | 691524 | 6 | 76 | `781176f69d3567e14847adb743c2243699044a92db50b4a810bc4d7e00be6bdc` |
| 仓库（只读） | tests/海怪笔刷-详情展示的勾线笔套装组18支.abr | v6.2 | 42150208 | 20 | 89 | `24c05a0caacb75914d1ead2167f285ac31066f6e052c9d564a9ce35ce7445f5b` |
| 外部（不入库） | Dry Media Brushes.abr | v6.1 | 1092651 | 21 | 45 | `d8ccece6780a8c5ae2dacd6aa8094eb3d94001dbafe786c565475de850ec9f69` |
| 外部（不入库） | Special Effect Brushes.abr | v6.1 | 1974272 | 0 | 0 | `3b59dd196a1dbec3c502b42f153baad62328ab081f149f74355c23d7b8247329` |
| 外部（不入库） | Thick Heavy Brushes.abr | v6.1 | 231856 | 5 | 35 | `4c6faa5d791375bad27441770b5e146605145ea0c75e36d1ca38b32e0bc58310` |
| 外部（不入库） | Wet Media Brushes.abr | v6.1 | 1057987 | 26 | 50 | `f413eaa368560c8f453f8b17e4f8ca2995892ac4fe1ea62da9b6b260693a4e19` |

仓库 3 支样本是既有只读素材（`tests/` 下，不入库、未改动）；外部 4 支样本只登记 URL + sha256（`docs/数据/abr-samples.json`），文件本身不在仓库里。

**`Special Effect Brushes.abr` 解析失败说明**：该文件 v6.1 且**没有 `desc` 区段**（只有采样笔尖），不是解析器缺陷。它的正确处置是记入报告、不硬解析——这与任务书「常见问题」第一条一致。

## 四、键总表（91 个键）

### 4.1 仓库解析器已识别（39 个，按出现次数降序）

| 键（原样，含尾空格） | 次数 | 文件数 | 类型 | 首个路径模板 | 含义 |
|---|---:|---:|---|---|---|
| `bVTy` | 446 | 6 | long | Brsh[].dualBrush.countDynamics.bVTy (×44) | 控制源（bVTy） |
| `jitter` | 446 | 6 | UntF | Brsh[].dualBrush.countDynamics.jitter (×44) | 抖动（%） |
| `Nm  ` | 204 | 6 | TEXT | Brsh[].Nm   (×79) | 名称 |
| `Mnm ` | 183 | 3 | UntF | Brsh[].angleDynamics.Mnm  (×20) | 动态最小值（%） |
| `Spcn` | 176 | 6 | UntF | Brsh[].Brsh.Spcn (×79) | 间距（%） |
| `Brsh` | 129 | 6 | Objc / VlLs | Brsh[].Brsh (×79) | 笔刷（顶层列表 / 笔尖对象） |
| `Angl` | 123 | 6 | UntF | Brsh[].Brsh.Angl (×79) | 角度（度） |
| `Dmtr` | 123 | 6 | UntF | Brsh[].Brsh.Dmtr (×79) | 直径（px） |
| `Rndn` | 123 | 6 | UntF | Brsh[].Brsh.Rndn (×79) | 圆度（%） |
| `useScatter` | 123 | 6 | bool | Brsh[].useScatter (×79) | 启用散布 |
| `sampledData` | 87 | 5 | TEXT | Brsh[].Brsh.sampledData (×48) | 采样笔尖 UUID |
| `dualBrush` | 79 | 6 | Objc | Brsh[].dualBrush (×79) | 双笔刷 |
| `Nose` | 79 | 6 | bool | Brsh[].Nose (×79) | 喷嘴 |
| `useColorDynamics` | 79 | 6 | bool | Brsh[].useColorDynamics (×79) | 启用颜色动态 |
| `useDualBrush` | 79 | 6 | bool | Brsh[].dualBrush.useDualBrush (×79) | 启用双笔刷 |
| `useTexture` | 79 | 6 | bool | Brsh[].useTexture (×79) | 启用纹理 |
| `Wtdg` | 79 | 6 | bool | Brsh[].Wtdg (×79) | 湿边 |
| `bothAxes` | 77 | 5 | bool | Brsh[].dualBrush.bothAxes (×44) | 两轴散布 |
| `scatterDynamics` | 77 | 5 | Objc | Brsh[].dualBrush.scatterDynamics (×44) | 散布动态 |
| `opVr` | 47 | 5 | Objc | Brsh[].opVr (×43) | 不透明度动态 |
| `prVr` | 47 | 5 | Objc | Brsh[].prVr (×43) | 流量动态 |
| `szVr` | 46 | 6 | Objc | Brsh[].szVr (×42) | 大小动态 |
| `angleDynamics` | 42 | 6 | Objc | Brsh[].angleDynamics (×42) | 角度动态 |
| `minimumDiameter` | 42 | 6 | UntF | Brsh[].minimumDiameter (×42) | 最小直径（%） |
| `minimumRoundness` | 42 | 6 | UntF | Brsh[].minimumRoundness (×42) | 最小圆度（%） |
| `roundnessDynamics` | 42 | 6 | Objc | Brsh[].roundnessDynamics (×42) | 圆度动态 |
| `Idnt` | 38 | 5 | TEXT | Brsh[].Txtr.Idnt (×38) | 图案 UUID |
| `InvT` | 38 | 5 | bool | Brsh[].InvT (×38) | — |
| `minimumDepth` | 38 | 5 | UntF | Brsh[].minimumDepth (×38) | 纹理最小深度（%） |
| `textureBlendMode` | 38 | 5 | enum | Brsh[].textureBlendMode (×38) | 纹理混合模式（BlnM 枚举） |
| `textureDepth` | 38 | 5 | UntF | Brsh[].textureDepth (×38) | 纹理深度（%） |
| `textureDepthDynamics` | 38 | 5 | Objc | Brsh[].textureDepthDynamics (×38) | 纹理深度动态 |
| `textureScale` | 38 | 5 | UntF | Brsh[].textureScale (×38) | 纹理缩放（%） |
| `Txtr` | 38 | 5 | Objc | Brsh[].Txtr (×38) | 纹理对象 |
| `Hrdn` | 36 | 4 | UntF | Brsh[].Brsh.Hrdn (×31) | 硬度（%） |
| `useBrushPose` | 27 | 3 | bool | Brsh[].useBrushPose (×27) | 启用笔刷姿态 |
| `protectTexture` | 16 | 3 | bool | Brsh[].protectTexture (×16) | 保护纹理 |
| `textureBrightness` | 16 | 3 | long | Brsh[].textureBrightness (×16) | 纹理亮度（PS -150..150） |
| `textureContrast` | 16 | 3 | long | Brsh[].textureContrast (×16) | 纹理对比度（PS -50..100） |

### 4.2 仓库解析器未识别（52 个，按出现次数降序）

| 键（原样，含尾空格） | 次数 | 文件数 | 类型 | 首个路径模板 | 含义 |
|---|---:|---:|---|---|---|
| `fStp` | 446 | 6 | long | Brsh[].dualBrush.countDynamics.fStp (×44) | 抖动步长（fStp） |
| `Intr` | 123 | 6 | bool | Brsh[].Brsh.Intr (×79) | 含义未在仓库文档给出 |
| `Rpt ` | 79 | 6 | bool | Brsh[].Rpt  (×79) | 重复 |
| `usePaintDynamics` | 79 | 6 | bool | Brsh[].usePaintDynamics (×79) | 启用绘制动态 |
| `useTipDynamics` | 79 | 6 | bool | Brsh[].useTipDynamics (×79) | 启用笔尖动态 |
| `Cnt ` | 77 | 5 | doub | Brsh[].dualBrush.Cnt  (×44) | （双笔刷）数量 |
| `countDynamics` | 77 | 5 | Objc | Brsh[].dualBrush.countDynamics (×44) | 数量动态 |
| `smoothing` | 72 | 6 | bool | Brsh[].smoothing (×52) | 平滑 |
| `flipX` | 52 | 3 | bool | Brsh[].Brsh.flipX (×27) | 笔尖 X 翻转 |
| `flipY` | 52 | 3 | bool | Brsh[].Brsh.flipY (×27) | 笔尖 Y 翻转 |
| `BlnM` | 44 | 5 | enum | Brsh[].dualBrush.BlnM (×44) | （双笔刷）混合模式 |
| `tiltScale` | 42 | 6 | UntF | Brsh[].tiltScale (×42) | 倾斜缩放 |
| `TxtC` | 38 | 5 | bool | Brsh[].TxtC (×38) | Texture Each Tip |
| `brushGroup` | 27 | 3 | Objc | Brsh[].brushGroup (×27) | 笔刷组 |
| `useBrushGroup` | 27 | 3 | bool | Brsh[].brushGroup.useBrushGroup (×27) | 启用笔刷组 |
| `useBrushSize` | 27 | 3 | bool | Brsh[].useBrushSize (×27) | 笔刷组-使用大小 |
| `brushPreset` | 20 | 3 | bool | Brsh[].toolOptions.brushPreset (×20) | 工具选项-笔刷预设标记 |
| `brushProjection` | 20 | 3 | bool | Brsh[].brushProjection (×20) | 笔刷投影 |
| `flow` | 20 | 3 | long | Brsh[].toolOptions.flow (×20) | 工具选项-流量 |
| `Opct` | 20 | 3 | long | Brsh[].toolOptions.Opct (×20) | 工具选项-不透明度 |
| `toolOptions` | 20 | 3 | Objc | Brsh[].toolOptions (×20) | 工具选项对象 |
| `useLegacy` | 20 | 3 | bool | Brsh[].toolOptions.useLegacy (×20) | 工具选项-旧模式 |
| `usePressureOverridesOpacity` | 20 | 3 | bool | Brsh[].toolOptions.usePressureOverridesOpacity (×20) | 压感覆盖不透明度 |
| `usePressureOverridesSize` | 20 | 3 | bool | Brsh[].toolOptions.usePressureOverridesSize (×20) | 压感覆盖大小 |
| `Md  ` | 19 | 3 | enum | Brsh[].toolOptions.Md   (×19) | 工具选项-混合模式 |
| `interpretation` | 16 | 3 | bool | Brsh[].interpretation (×16) | 纹理解释标记 |
| `pressureSmoothing` | 16 | 3 | bool | Brsh[].toolOptions.pressureSmoothing (×16) | 压感平滑 |
| `Smoo` | 16 | 3 | long | Brsh[].toolOptions.Smoo (×16) | 工具选项-平滑 |
| `smoothingCatchup` | 16 | 3 | bool | Brsh[].toolOptions.smoothingCatchup (×16) | 平滑追赶 |
| `smoothingCatchupAtEnd` | 16 | 3 | bool | Brsh[].toolOptions.smoothingCatchupAtEnd (×16) | 平滑终点追赶 |
| `smoothingRadiusMode` | 16 | 3 | bool | Brsh[].toolOptions.smoothingRadiusMode (×16) | 平滑半径模式 |
| `smoothingValue` | 16 | 3 | doub | Brsh[].toolOptions.smoothingValue (×16) | 平滑强度 |
| `smoothingZoomCompensation` | 16 | 3 | bool | Brsh[].toolOptions.smoothingZoomCompensation (×16) | 平滑缩放补偿 |
| `mxVr` | 14 | 2 | Objc | Brsh[].mxVr (×14) | 混合动态 |
| `wtVr` | 14 | 2 | Objc | Brsh[].wtVr (×14) | 湿边动态 |
| `Flip` | 5 | 2 | bool | Brsh[].dualBrush.Flip (×5) | （双笔刷）翻转 |
| `brushPoseAngle` | 4 | 1 | long | Brsh[].brushPoseAngle (×4) | 笔刷姿态-角度 |
| `brushPosePressure` | 4 | 1 | UntF | Brsh[].brushPosePressure (×4) | 笔刷姿态-压感 |
| `brushPoseTiltX` | 4 | 1 | long | Brsh[].brushPoseTiltX (×4) | 笔刷姿态-倾角X |
| `brushPoseTiltY` | 4 | 1 | long | Brsh[].brushPoseTiltY (×4) | 笔刷姿态-倾角Y |
| `overridePoseAngle` | 4 | 1 | bool | Brsh[].overridePoseAngle (×4) | 姿态覆盖-角度 |
| `overridePosePressure` | 4 | 1 | bool | Brsh[].overridePosePressure (×4) | 姿态覆盖-压感 |
| `overridePoseTiltX` | 4 | 1 | bool | Brsh[].overridePoseTiltX (×4) | 姿态覆盖-倾角X |
| `overridePoseTiltY` | 4 | 1 | bool | Brsh[].overridePoseTiltY (×4) | 姿态覆盖-倾角Y |
| `Brgh` | 2 | 2 | UntF | Brsh[].Brgh (×2) | 颜色动态-亮度 |
| `clVr` | 2 | 2 | Objc | Brsh[].clVr (×2) | 颜色动态-颜色变化 |
| `H   ` | 2 | 2 | UntF | Brsh[].H    (×2) | 颜色动态-色相 |
| `purity` | 2 | 2 | UntF | Brsh[].purity (×2) | 颜色动态-纯度 |
| `Strt` | 2 | 2 | UntF | Brsh[].Strt (×2) | 颜色动态-起始/饱和度 |
| `colorDynamicsPerTip` | 1 | 1 | bool | Brsh[].colorDynamicsPerTip (×1) | 每笔尖颜色动态 |
| `ErsB` | 1 | 1 | long | Brsh[].toolOptions.ErsB (×1) | 工具选项-橡皮擦标记 |
| `MgcE` | 1 | 1 | bool | Brsh[].toolOptions.MgcE (×1) | 工具选项-魔术橡皮擦 |

> 完整字段（`samples`、`value_range`、`value_counts`、`repo_refs`、全部路径模板）见 `docs/数据/abr_keys.json`。

## 五、尾随空格与 4CC 命名

带尾随空格的键共 6 个："Cnt "、"H   "、"Md  "、"Mnm "、"Nm  "、"Rpt "。

原因：Photoshop 这些键是 **4 字符四字码（4CC）**，短于 4 字符的名字用空格补齐（`Nm  `、`Cnt `、`Rpt `），`Mnm ` 是 3 字符 + 1 空格，`H   ` 是 1 字符 + 3 空格，`Md  ` 是 2 + 2。

**扫描器一律原样保留，不 trim**。若下游做键名比对，必须按原始长度匹配；`Mnm `（带空格）与 `Mnm`（不带）是两个不同的键。

## 六、未识别键的分组与含义

52 个未识别键按主题分组（分组基于键名与 `docs/parameter-mapping.md` 的既有分类，仅为阅读方便）：

| 主题 | 数量 | 键 |
|---|---:|---|
| 双笔刷 | 5 | `BlnM`、`Cnt `、`Flip`、`countDynamics`、`fStp` |
| 颜色动态 | 6 | `Brgh`、`H   `、`Strt`、`purity`、`clVr`、`colorDynamicsPerTip` |
| 工具选项/平滑 | 17 | `ErsB`、`Md  `、`MgcE`、`Opct`、`Smoo`、`brushPreset`、`flow`、`pressureSmoothing`、`smoothing`、`smoothingCatchup`、`smoothingCatchupAtEnd`、`smoothingRadiusMode`、`smoothingValue`、`smoothingZoomCompensation`、`useLegacy`、`usePressureOverridesOpacity`、`usePressureOverridesSize` |
| 笔刷姿态 | 8 | `brushPoseAngle`、`brushPosePressure`、`brushPoseTiltX`、`brushPoseTiltY`、`overridePoseAngle`、`overridePosePressure`、`overridePoseTiltX`、`overridePoseTiltY` |
| 笔刷组 | 3 | `brushGroup`、`useBrushGroup`、`useBrushSize` |
| 混合/湿边 | 2 | `mxVr`、`wtVr` |
| 其它 | 11 | `Intr`、`Rpt `、`TxtC`、`brushProjection`、`flipX`、`flipY`、`interpretation`、`tiltScale`、`toolOptions`、`usePaintDynamics`、`useTipDynamics` |

说明：

- 「未识别」= 转换器代码里没有以字符串字面量出现该键，**不等于该键无意义**。例如 `toolOptions` 是 PS 画笔设置面板的一整棵子树（含不透明度/流量/平滑），`brushPreset`/`brushPose*` 是 PS 2023+ 的新特性。
- `docs/parameter-mapping.md` 已把其中多数标为 ⛔（不映射），本次实测补上了「这些键在真实样本里确实存在、各出现多少次」这一层证据（见 §八）。
- 任务 23 应把「ABR 有 / 转换器不映射」的行按上表与其 `count`/`count_files` 填进矩阵，而不是只写「不映射」。

## 七、枚举与动态控制源的实测取值

### 7.1 纹理混合模式 `textureBlendMode`（enum，类 id = `BlnM`）

| 实测取值 | 次数 | 是否登记在 mapping.py 的 `_TEXTURING_MODE` |
|---|---:|---|
| `Mltp` | 17 | ❌ **未登记**（会回退 0=MULTIPLY 并产生一条警告） |
| `linearHeight` | 10 | ✅ → 15 (LINEAR_HEIGHT_PHOTOSHOP) |
| `Hght` | 4 | ✅ → 14 (HEIGHT_PHOTOSHOP) |
| `CBrn` | 3 | ✅ → 7 (COLOR_BURN) |
| `Sbtr` | 2 | ✅ → 1 (SUBTRACT) |
| `CDdg` | 1 | ✅ → 6 (COLOR_DODGE) |
| `hardMix` | 1 | ✅ → 10 (HARD_MIX_PHOTOSHOP) |

**结论**：样本里 7 种纹理模式，其中 `Mltp` 占 17/38（最多），当前会被当成「未知模式回退 Multiply」并打警告。**语义上 0=MULTIPLY 是正确的**，但映射表应补 `"Mltp": 0`，否则 GUI/日志会持续报「未知模式」。这条要交给任务 23/38 处置（本任务不改 `src/`）。

### 7.2 双笔刷混合模式 `dualBrush.BlnM`（enum，同 `BlnM` 类）

| 实测取值 | 次数 |
|---|---:|
| `Mltp` | 26 |
| `CBrn` | 9 |
| `Drkn` | 4 |
| `hardMix` | 2 |
| `CDdg` | 1 |
| `Ovrl` | 1 |
| `linearBurn` | 1 |

`BlnM` 是「双笔刷」子对象的混合模式，与纹理模式**共用同一个枚举类**但语义不同（一个作用于笔尖叠加、一个作用于纹理）。转换器当前完全不读它（`in_repo_parser=false`），符合「双笔刷不映射」的既定口径。

### 7.3 动态控制源 `bVTy`（long）

| 实测取值 | 次数 | 转换器 `_bvty_sensor` 的行为 |
|---|---:|---|
| 0（关） | 327 | 无传感器（按「无控制源」处理） |
| 2（压力） | 106 | `pressure` ✅ |
| 5（旋转随机） | 1 | 返回 None（未映射），但 `jitter>0` 时会补一条警告 ✅ |
| 6（方向） | 12 | `drawingangle` ✅ |

样本中**没有出现** 1（渐隐）、3（倾斜）、4（转轮）、7（初始方向）——这与 `parameter-mapping.md` §8 的「1/3/4/7 未映射/预留」不冲突；任务 23 需要注明「ABR 样本未覆盖 ≠ PS 没有」。

## 八、与 `docs/parameter-mapping.md`「不映射清单」的交叉验证

| 键（按映射表的写法） | 映射表状态 | 实测出现次数 / 文件数 | 结论 |
|---|---|---:|---|
| `dualBrush` | §6 不映射 | 79 / 6 | ABR 里真实存在，⛔/⚠️ 属「有键但不映射」，非「键不存在」 |
| `useColorDynamics`（`H`/`Strt`/`Brgh`/`purity`/`clVr`） | §6 不映射 | 79 / 6 | ABR 里真实存在，⛔/⚠️ 属「有键但不映射」，非「键不存在」 |
| `Wtdg`（湿边） | §6 不映射 | 79 / 6 | ABR 里真实存在，⛔/⚠️ 属「有键但不映射」，非「键不存在」 |
| `Nose` / `Rpt ` | §6 不映射 | 79 / 6 | ABR 里真实存在，⛔/⚠️ 属「有键但不映射」，非「键不存在」 |
| `brushProjection` / `useBrushPose` | §6 不映射 | 20 / 3 | ABR 里真实存在，⛔/⚠️ 属「有键但不映射」，非「键不存在」 |
| `wtVr` / `mxVr` | §4 不映射 | 14 / 2 | ABR 里真实存在，⛔/⚠️ 属「有键但不映射」，非「键不存在」 |
| `Cnt ` / `countDynamics` | §5 不映射 | 77 / 5 | ABR 里真实存在，⛔/⚠️ 属「有键但不映射」，非「键不存在」 |
| `TxtC` / `protectTexture` / `interpretation` | §7 ⚠️ | 38 / 5 | ABR 里真实存在，⛔/⚠️ 属「有键但不映射」，非「键不存在」 |

**一致项**：映射表标 ⛔ 的 11 类键全部在样本里出现（`dualBrush`/`useColorDynamics`/`Wtdg`/`Nose`/`Rpt `/`brushProjection`/`useBrushPose`/`wtVr`/`mxVr`/`Cnt `/`countDynamics`），说明不映射清单没有「凭空多写」。

**无冲突项**：本次没有发现「映射表标 ❌ 但样本里根本不存在」的键，也没有发现「映射表标 ✅ 但样本里不存在」的键（39 个已识别键全部在样本里出现过）。

**新增证据**：`TxtC`/`protectTexture`/`interpretation` 三个纹理标记键在映射表里是 ⚠️（有键但无对应），实测 `TxtC` 38 次、`protectTexture` 16 次、`interpretation` 16 次——可把「⚠️」的说明从定性升级到「实测 16–38 次」。

## 九、关键参数的实测取值范围

只列与纹理/基础属性相关、且样本里取值多于一种的键（完整 `value_range` 见 JSON）：

| 键 | 单位 | 实测最小 | 实测最大 | 出现次数 | 备注 |
|---|---|---:|---:|---:|---|
| `textureScale` | % | 16 | 280 | 38 | **超过 Krita UI 滑块上限 2.0（=200%）**，实测到 2.80 |
| `textureDepth` | % | 4 | 100 | 38 |  |
| `minimumDepth` | % | 0 | 100 | 38 |  |
| `textureBrightness` | PS 单位 | -55 | 53 | 16 | Krita 亮度范围 [-1,1]；PS ±150 里只有一部分可表达 |
| `textureContrast` | PS 单位 | -50 | 84 | 16 | PS 允许到 100；Krita 对比度上限 2 对应 PS 50，>50 结构性不可表达 |
| `Spcn` | % | 1 | 1000 | 176 |  |
| `Dmtr` | px | 1 | 1920 | 123 |  |
| `jitter` | % | 0 | 533 | 446 |  |
| `Mnm ` | % | 0 | 100 | 183 |  |
| `Hrdn` | % | 0 | 100 | 36 |  |
| `Rndn` | % | 20 | 100 | 123 |  |
| `Angl` | 度 | -33 | 90 | 123 |  |

**读数注意**：`textureScale`/`textureDepth`/`Spcn`/`jitter`/`Mnm ` 是 `UntF` 带 `#Prc` 单位，JSON 的 `value_range` 是**去掉单位后的数值**；`Dmtr` 是 `#Pxl`。

## 十、外部样本的获取与合规

| 文件 | URL | sha256 | 版本 | 预设 | desc 键 |
|---|---|---|---:|---:|---:|
| Dry Media Brushes.abr | https://loony-archivist.com/temp/stuff/Brushes/Adobe%20Photoshop%20Only/Dry%20Media%20Brushes.abr | `d8ccece6780a8c5ae2dacd6aa8094eb3d94001dbafe786c565475de850ec9f69` | v6.1 | 21 | 45 |
| Special Effect Brushes.abr | https://loony-archivist.com/temp/stuff/Brushes/Adobe%20Photoshop%20Only/Special%20Effect%20Brushes.abr | `3b59dd196a1dbec3c502b42f153baad62328ab081f149f74355c23d7b8247329` | v6.1 | 0 | 0 |
| Thick Heavy Brushes.abr | https://loony-archivist.com/temp/stuff/Brushes/Adobe%20Photoshop%20Only/Thick%20Heavy%20Brushes.abr | `4c6faa5d791375bad27441770b5e146605145ea0c75e36d1ca38b32e0bc58310` | v6.1 | 5 | 35 |
| Wet Media Brushes.abr | https://loony-archivist.com/temp/stuff/Brushes/Adobe%20Photoshop%20Only/Wet%20Media%20Brushes.abr | `f413eaa368560c8f453f8b17e4f8ca2995892ac4fe1ea62da9b6b260693a4e19` | v6.1 | 26 | 50 |

**合规**：外部 `.abr` **不入库**，仓库里只有 `docs/数据/abr-samples.json` 的 URL + sha256；下载到 `%TEMP%/brushconverter-team/abr-samples/`，用完可删。

**取样本时试过但没成功的路径（如实记录）**：

- `github.com/jlai/brush-viewer`、`github.com/scurest/abrupng`、`github.com/alex-kabin/abr-viewer`、`github.com/pmwoz/brushkit` 的仓库树里**没有** `.abr` 测试夹具；
- `archive.org` 的 advancedsearch HTTP 请求在本机网络下失败（`fetch failed`），未取到；
- `myphotoshopbrushes.com` 的下载链接是前端渲染，未从中取到直链。

## 十一、局限与后续

1. **键空间仍未封闭**：7 个文件 / 79 个预设，全部是 ABR v6.x；没有 v1/v2 旧格式、也没有 v10.x 样本。任务 23 不能把本表当「PS 键全集」——PS 键全集以逆向（任务 21）为准（铁律 9）。
2. **未扫 `patt` / `samp` 区**：本任务只要求 desc 键；纹理图案/采样笔尖的字节结构与键无关。
3. **未做键值语义判定**：例如 `Mltp` 到底等价 PS 的哪种混合，靠任务 23/31 的模式号复核。
4. **`Special Effect Brushes.abr` 无 desc**：它只贡献「文件版本 v6.1、无预设」这条事实，不贡献键。
5. **`in_repo_parser` 是静态判据**：只证明「代码里提到过这个键」，不证明「该键被正确使用」；使用正确性由任务 23 的四路对账负责。
