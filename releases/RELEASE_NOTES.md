# brushConverter v1.6.0（候选 · 待发布，未发布）

This v1.6.0 candidate is not released and records changes after v1.5.0, including out-of-range texture brightness pre-baking and the single random source correction for rotation jitter. Existing outputs must be reconverted as described below; this is not a download or release announcement.

> **状态：待发布（v1.6.0 候选，未发布）。** 本段登记 v1.5.0 之后工作区里的待发布变更；发布日期、tag 与 Release 均未确定，正式发布前以仓库现状及本文件最终修订为准。

**升级必读：本轮有两处产物语义变化，旧 `.kpp` / `.bundle` 必须重转**：① 纹理亮度越界区改为预烘焙（命中区纹理字段失去 PS 可编辑语义）；② 旋转抖动 j<50 幅度修正（旧产物幅度约为 PS 的 2 倍）。

## 主要变化

- **纹理亮度越界区改为预烘焙（任务 153）**：命中判据 = calibrated 反解的未截断亮度 `|b_k|>1`（「C=−50 且 PS 亮度绝对值 >127」仅是旧实验切片，不是任意对比度的等价条件）；PS 亮度/对比度/反相按 PS 8bit 链（C<0 先对比度后亮度 / C≥0 先亮度后对比度）烘进内嵌纹理 PNG，Krita 三字段先写中性值 B=0、C=1、Invert=false；hardMix 满覆盖且无深度压感的专门分支随后令 B=−1/255。边界与代价统一见[映射参考§7.1](<../docs/parameter-mapping.md#texture-bc-current>)。端到端实渲 **12/12 = 0.000%/max0–1**（改前同批 8/12 超阈、最大 61.125%/max23）；非命中区行为不变。
- **代价（使用者已接受）**：命中区笔刷的 Krita「纹理-亮度/对比度/反相」不再对应 PS 语义（再调会二次叠加）；每 (b,c,invert) 组合一份纹理资源（本组约 1.6–12 KB/张），资源名/内容哈希/MD5 按新字节派生。
- **旋转抖动 j<50 幅度修正（任务 149A）**：`RotationSensor` 只保留 fuzzy 一个加法随机源（删除 fuzzystroke；两者都被 Krita 求和，旧写法使随机跨度=2×360°×R，PS 为 360°×R）。改后两批独立实机 j=25/50/100 相对 PS 的最大相对差 **4.06%**（≤10%）、两批复现 ≤4.73%（≤5%），对照改前 j=25 为 **+38.20%**；j≥50 两侧都铺满整圈、视觉等价，但随机序列不同；`bVTy=6/7/8` 且 `jitter>0` 同机制减半（未单独实机重测）。
- **矩阵 14 → 12（任务 156）**：`Mnm`（146：双端 0.00%/max_delta 1）与 `Rndn`（150：椭圆轴比误差 0.37%–1.34%）由「需校准」改「可映射」；其余 12 行维持「需校准」并在矩阵写清构造性理由与归属；`jitter` 的 UI 可见性由「未定」改「有」（`形状动态/角度抖动`）。矩阵总行数仍 137、八栏结构不变。
- **其余定论（任务 143–152/154）**：B9 旧数字 76.680%/57.570% 作废（128A 真值文件名不带纹理后缀、t2 覆盖 t1）——按纹理正确配对后 9/10 模式两纹理 0.000%/max≤1，仅 hardMix 在 b×C 双非零时真残差 56.87%/38.50%（修法候选 0.000%/max0 未落地）；B7 散布 j=1000% 端点生产实现超阈 18.21%（补偿变体 0.01%，未落地）；B8 旋转 j≥50 饱和升 A 级（四档相对差 1.40%）；B12 hardMix 满覆盖 + 压感双端 0.00%/max0；B1 大直径硬边失效边界 D≈453px；A5 缩放 s=75 现行 88.72%/max94（预缩放修法 0.000%/max1，未落地）。

## 升级注意

1. **含纹理亮度越界区的旧 `.kpp` / `.bundle` 必须重转**：命中区纹理字节、资源名、MD5 都会变化；不重转的旧产物仍是旧的截断行为。
2. **命中区纹理亮度/对比度/反相不可再在 Krita 里调出 PS 语义**（已烘焙进像素）。
3. **所有 `angleDynamics.jitter>0` 的旧 `.kpp` 必须重转**：j<50 的旋转抖动幅度减半（回到 PS 口径）；j≥50 视觉等价但随机序列不同。
4. **未命中区行为不变**；本轮无运行时开关，整体回退 = `git revert` + 重转。

## 已知限制

见 README「已知限制（摘要）」与 `docs/parameter-mapping.md` §10（第五轮四档判据、12 行需校准逐条数字与复跑命令）。本段为候选说明，发布前会按最终发布包复核。

## 验证

- 153：`pytest -q` 344 → **381 passed / 0 failed / 0 skipped**；端到端改后 12/12 PASS、改前 4/12。
- 149A：`pytest -q` 381 → **396 passed / 0 failed / 0 skipped**（改后首跑 1 个 Krita 非确定性假失败，单跑与全量重跑均绿，已登记）。
- 156：`render_matrix_md.py --check` / `check_matrix.py` / `render_mode_table.py --check` / `check_ui_controls.py` 全部 exit 0；需校准行数复算 14 → **12**。
- 全量回归与 verify_all 的发布前最后一跑在发布流程中完成（本段不代填最终数字）。

## 回退方式

本轮不提供功能开关；整体回退 = `git revert`（或 checkout v1.5.0）后重转产物。149A 的回退 = 恢复各处 fuzzystroke XML 行，但会恢复 j<50 幅度翻倍的已知缺陷。

---

# brushConverter v1.5.0（2026-11-23）

第四轮校准落地版（v0.12–v0.15）的产物语义收口与文档定稿。
**升级必读：旧 `.kpp` / `.bundle` 必须用 v1.5.0 重新转换**（计算笔刷类型、散布/旋转换算、纹理缩放三处改变产物语义，见「升级注意」）。

## 主要变化

- **计算笔刷 `auto_brush` → `png_brush`（任务 133）**：按 PS 硬度剖面生成采样位图（正方形 + 边框 255，铁律 17 不变），尺寸=PS 实际足印（PS 标称 200 → Krita 290/250/204）。硬度几何判据：h0/h50/h100 过渡带 Δ0.858/0.058/0.485 px（几何级 3/3 达标；旧 `auto_brush` 为 +15.60px / −7.33px / 1.07px）。代价：Krita 里 fade/硬度不可再调、每支 +3–12 KB。
- **散布 ÷400 → ÷200、旋转 j/100 → `min(1, j/50)`（任务 134）**：散布 a/D 相对差 0.65%（j=100）/ 1.69%（j=300），随机档达标（≤10%、复现性 0.08%/0.24%）；旋转 j=50 跨度 200.01°→309.90°（复渲 1.51%）。bVTy 枚举修正为「5 初始方向 / 6 笔划方向 / 7 速度 / 8 旋转」（旧文档 5/6/7 错位），bVTy=5 新增「未映射」告警。
- **纹理（任务 135）**：缩放 >100% 改「PS 同核预缩放内嵌 + `Scale=1.0`」（200% 由差异比例 93.72%/max93 → **0.00%/max1**；单预设 +0.8 KB~+434 KB）；对比度解除 `min(2.0,k)` 截断（C=84→6.25、C=100→1e6，UI 滑块仍只显示 0..2）；minimumDepth 曲线 0 端点改比值 `s_m/s_d`；hardMix + 满覆盖笔尖写 Strength=1.0 + 亮度 ±1/255 偏置（3/3 = 0.00%/max0）。
- **删除 legacy 公式开关**：`BRUSHCONVERTER_TEXTURE_FORMULA` 与 legacy 分支全部移除（引用 13→0），只剩 calibrated 公式；运行时无回退。
- **矩阵与文档定稿（任务 137）**：参数矩阵 137 行 × 8 栏按 v0.15 总账同步 16 处字段文本（verdict 分布保持 22/61/40/14）；`docs/参数矩阵.md` 由脚本生成；`docs/数据/mode-table.json` 47 行补逐行 `ui_visibility`（有 10 / 无 37）并为 HdMxS 补可跑 `recheck_cmd`；README / parameter-mapping / developer-guide / 本文件 / GUI 中英文案同步。

## 升级注意

1. **旧 `.kpp` / `.bundle` 必须重转**：v1.5.0 三批改动都改变产物字节与语义。
2. **计算笔刷在 Krita 里 fade/硬度不可再调**（已烘焙进位图）；尺寸显示值 = PS 实际足印，不是 PS 标称直径。
3. **散布数值 ×2、旋转抖动 j<50 ×2**：旧产物散布偏小一半、小角度抖动偏小约 2 倍。
4. **纹理缩放 >100% 的预设体积会变大**，资源名/内容哈希随之变化；缩放与相位不可再在 Krita 里调。
5. **没有运行时回退开关**：legacy 公式已删除，整体回退 = `git revert` + 重转。

## 已知限制

见 README「已知限制（摘要）」与 `docs/parameter-mapping.md` §10（v1.5.0 口径：四档判据，逐条给残差数字与复跑命令）；完整 22 条残差登记（结构性 3 / 有条件 3 / 未量测 12 / 待下轮 4）见 `docs/验收报告/136-报告.md` §四。

## 验证

- 三条结构校验 `check_matrix.py` / `render_matrix_md.py --check` / `check_ui_controls.py` 均 **exit 0**；`docs/参数矩阵.md` 由脚本生成（137 行）。
- 回归与三层验收沿用任务 136 收口数字（v1.5.0 未改转换行为，137 只改文档/矩阵与 Pillow 弃用 API）：`pytest -q` **344 passed / 0 failed / 0 skipped**（69.23s）；`tools/verify_all.py` 三层 **107/0/0**。
- 9 支端到端抽检 9/9 达标（133 硬度几何级 3/3、134 散布/旋转随机档、135 纹理像素级 0.00%/max≤1）——详见 `docs/验收报告/136-报告.md` §二。
- 旋转抖动复现性按 n≥120 dab 极差跨度判定（两两 ≤5%，实测 0.13–0.61%）；n=40 极差统计量方差大，非映射问题（依据 138 报告 §2.4 / §五 决策；同代码同 `.kpp`，非回归）。

## 回退方式

本轮不提供功能开关；整体回退 = `git revert` / `git checkout v1.4.0`（tag `v1.4.0`），回退后必须重转产物。legacy 纹理公式已在 v1.5.0 物理删除，回退到旧公式只能 checkout v1.4.0。

---

# brushConverter v1.4.0（2026-11-21）

参数修正与验证基建版：界面可见性门、证据分级、参数实测修正、测试 `.abr` 生成。
**升级必读：非方形采样笔刷的间距与所有笔刷的传感器 XML 写法会变，旧产物必须重新转换**（见下文「升级注意」）。
2026-11-21 修正：`.kpp` 所有 `*Sensor` 改为合法 `pressure` 占位，**禁止空 `<params/>`**。

## 主要变化

- **参数矩阵扩为 8 栏（UI 可见性门）**：`docs/参数矩阵.json` 137 行 × 8 栏，新增 `ui_visibility` / `ui_control`；只有 `ui_visibility=有` 的行才允许投入优化。PS「纹理 > 模式」下拉实测只有 10 项（正片叠底 / 减去 / 变暗 / 叠加 / 颜色减淡 / 颜色加深 / 线性加深 / 实色混合 / 线性高度 / 高度）。
- **证据分级 A/B/C**：47 个纹理混合模式与参数证据统一分级（A=可重跑实测或源码/逆向原文；B=单点/单类证据且写明依赖假设；C=语义推断或该模式在 PS 界面「模式」下拉里不存在）。**用户视角：PS UI 仅 10 项可选（A/B 级），全部支持——8 可映射 / 1 需校准 hardMix / 1 不可映射 Drkn；其余 37 项（C 级）是 UI 选不到的内部枚举值，不计为转换限制。** 完整 47 行枚举归档 verdict：可映射 22 / 需校准 1 / 不可映射 24 / 不可判定 0。
- **间距短边补偿（参数修正）**：采样笔刷 `spacing = Spcn/100 × 原始笔尖短边/长边`（`BrushPreset.spacing_basis`）。非方形三点实测 25.28%→**0.11%**、28.09%→**0.18%**、32.46%→**3.53%**；严格正方形=1、近方形按实际短/长边、计算笔刷不补偿；补白代码一行未改（铁律 17）。
- **三参数判「需校准」（不再硬调系数）**：散布 100%/300% 增量差 **39.7% / 51.1%**（带高统计复现性 ±7–13%）；硬度 h0 **+15.6px/19.8%**（结构性不可达）、h50 **−7.3px/22.6%**、h100 一致；纹理缩放 200% 差异比例 **93.72%**、max_delta **93**、相对残差 **32.4%**（Krita Qt 重采样）。尺寸 / 角度不偏、零改动。
- **传感器 XML 缺陷修复（`7073abb` 默认压感修复 + `5e4ad39` 格式修正）**：基础模板 17 组 `*Sensor` 写合法 `pressure` 占位（`<!DOCTYPE params> <params id="pressure"/>`），未映射动态用 `Pressure*=false` + `*UseCurve=false` 关闭；**禁止空 `<params/>`**——空 XML 会被 Krita 当成有定义、又因 `sensorId` 为空触发 `!sensorId.isEmpty()` 断言并早退，曲线/散布等都不读。`7073abb` 只修了「无动态默认接压感」，空 XML 写法本身仍错误；最终修复见 `tests/test_sensor_defaults_v140.py`。
- **测试笔刷集（20 支）**：`tests/abr-generated/` 六参数 × 3 取值 + 全默认对照 + 旧样本；逐支预期与不合格判据在 `docs/测试笔刷验证清单.md`；解析回读 `python tools/make_test_abr.py --check-suite`。
- **文档与 GUI 对齐**：README 新增「已知需校准参数」与「已知不可映射模式（v1.4.0 定稿）」；`docs/parameter-mapping.md` 修正 textureScale 状态、补传感器默认说明；`docs/developer-guide.md` 新增 §7.8（8 字节尾块 / 传感器合法占位 / 间距补偿 / 界面异常先查 sensor id / 公开与本地地址分隔）；GUI 中英文提示同步为 v1.4.0 事实（含已知残差与传感器格式修复）。

## 升级注意

1. **非方形采样笔刷的间距会变**：重新转换后中心距会变小、更接近 PS；这是修正，不是回归（已知残差见 README「已知需校准参数」）。
2. **旧 `.kpp` 必须重新转换**：旧产物把未映射 `*Sensor` 写成空 `<params/>`，Krita 会因 `sensorId` 为空早退，曲线/散布显示可能异常；v1.4.0 改为合法 `pressure` 占位 + `Pressure*=false` / `UseCurve=false`（`7073abb` 只先修了默认压感，`5e4ad39` 修最终格式）。
3. 产物格式、纹理亮度/对比度公式与模式映射表未变，但上述两点会改变产物内容与字节。

## 已知限制

见 README 的「已知不可映射模式」「已知需校准参数」两节与 `docs/parameter-mapping.md` §10（每个残差数字都有命令可复算）。

## 验证

- `python tools/check_matrix.py` 退出码 **0**（137 行 × 8 栏，逆向独有 39 行）；`python tools/render_matrix_md.py --check` 输出「同步」。
- `python tools/verify_all.py`：**58 / 0 / 0**；`python -m pytest -q`：**278 passed / 0 failed / 0 skipped**。
- 人工三层：优先 7 支全部完成并合格（scatter-300 按已知残差，只判方向与量级）；114 端到端按使用者 2026-11-21 决定放行——12 支明确合格 + §5.1 四支 + GUI 口头确认，其余 8 项以现有证据记录为边界，不写成人工合格。
- **隐私门**：全历史 `block=0`（warn 882，凭据 0；20 个自产测试 `.abr` 按使用者批准白名单记 warn）；发布前未推送/历史路径重写已完成，备份 bundle 留在校验目录外。
- 矩阵 verdict 本轮只改 1 行（`Spcn`：可映射 → 需校准），其余 6 行按 108 清单维持；逐条证据见 `docs/验收报告/113-报告.md`（该报告已按 2026-11-21「恢复与定稿说明」定稿；三项校验 2026-11-21 重跑仍为 0 / 0 / 0）。
- 发行包：GUI `brushConverter-v1.4.0-win64.zip`（65,269,647 B，sha256 `4d7c0b64…7af5b7a`）；CLI `brushconverter-cli-v1.4.0-win64.zip`（27,247,237 B，sha256 `91e859a2…683b213`）。

## 回退方式

本轮不做功能开关；整体回退 = `git revert` / `git checkout v1.3.1`（tag `v1.3.1`）。回退后非方形间距与传感器写法恢复旧行为（旧空 `<params/>` 会让 Krita 早退，不建议回退后继续用旧格式产物）。

---

# brushConverter v1.3.1（2026-11-20）

「Krita 里笔刷不可见 / 纹理消失」缺陷的**文档与工具加固版**（PATCH）。
**转换格式与参数映射未变：v1.3.0 的产物可以直接继续用，不需要重新转换。**

## 主要变化

- **产物命名带版本号 + 逻辑内容短哈希（只改 `.bundle`）**：
  `<ABR 名去扩展名>_v<版本号>_<8 位内容短哈希>.bundle`，例如 `测试用_v1.3.1_656f9dc8.bundle`。
  短哈希是确定性**逻辑内容**哈希（bundle 内 `paintoppresets/` 条目按名字排序，逐个喂
  「条目名 + 0x00 字节 + sha256(条目字节)」，整体再取 sha256 前 8 位；**不是** zip 文件字节哈希——
  zip 头带时间戳会破坏确定性）。同一输入重复转换结果字节相同、文件名相同。
  `.kpp`（standalone）命名**不变**，仍是 3 位序号 + 清洗后的笔刷名。
- **写出后回读校验**：`verify_bundle()` 回读产物并逐条校验（zip 可打开 / `testzip()` 无损坏条目 /
  mimetype 是首个条目且不压缩 / manifest 预设条目数等于 zip 里 `paintoppresets/` 条目数且 md5 一致 /
  每个 `.kpp` 都是合法 PNG + iTXt 预设 XML 且块 CRC 正确）。通过时 CLI 打印
  `输出校验：OK（N 个预设，sha256=…，大小=…B）`；失败打印原因并以**退出码 3** 结束（不再静默返回 0），
  GUI 在完成对话框追加「产物回读校验失败：<原因>」。
- **覆盖防呆（绝不静默覆盖）**：目标不存在 → 直接写；已存在且逻辑内容哈希相同 → 复用不重写（提示
  「已存在同内容产物，未重复写入」）；已存在但内容不同 → 自动加 `_2`/`_3` 后缀、原文件一字不动
  （提示「目标同名文件内容不同，已重命名为 …（原文件未覆盖）」）；旧版命名 `<stem>.bundle` 仍在同目录时
  只提示不覆盖不删除。
- **GUI / CLI 显示带真实纹理的预设数**：口径是 `.kpp` 里 `Texture/Pattern/Enabled=true`
  **且**内嵌 patterns 资源不是 1×1 占位图；CLI 完成行「转换完成：N 个预设（含真实纹理 M 个）」，
  GUI 摘要「预设 N 个（含真实纹理 M 个）」+「资源包：<文件名>（短哈希 <hash8>）」。
- **新增只读诊断工具 `tools/diagnose_profile.py`**：一条命令体检 `resourcecache.sqlite` 完整性、storages、
  **ABR 直读库（Adobe Brush Library）**、跨 storage 同名预设、悬空 storage、bundle 完整性与注册状态、
  逐预设纹理解析预测。只读：先把 cache 复制到临时目录再读，不启动 Krita、不写用户 profile。
- **README 新增 FAQ「Krita 里笔刷不可见 / 纹理消失怎么办」**：按「代价从小到大」给出排查顺序，
  第一条就是「不要把原始 `.abr` 直接导入 Krita」。

## 根因说明（重要）

本轮缺陷的实测真因**不是转换格式**：使用者把 **42 MB 的原始 `.abr` 直接导入了 Krita**。
Krita 5.3 会把 ABR 当 **Adobe Brush Library** 直读（资源管理器里显示为 Adobe Brush Library，
资源缓存里是 `storage_type_id=4` 的一行），生成一套**没有经过本工具转换**的同款笔刷
（无纹理映射、参数与本工具产物不同）。它和转换产物的 `.bundle` 混在同一笔刷列表里，
看起来就像「纹理全丢 / 笔刷不可见」。**实测：删掉 `%APPDATA%\krita` 下的 `.abr` 后即恢复。**

`resourcecache.sqlite` 的 `integrity_check` 确有轻微异常（`Page 30779 is never used` + 两处索引条目数不符），
但**重建资源缓存不是修复本缺陷的必要步骤**，只是两步排查都无效后的可选兜底；
重建会丢失**自定义预设标签与分组**（笔刷/图案等资源文件本身不丢）。

## 已知限制（与 v1.3.0 相同）

1. **24 个纹理混合模式不可映射**（16 个 Krita 无对应算子 / 8 个 PS 侧产不出可比真值），未登记模式回退 Multiply 并保留警告。
2. **0.00% 不等于语义等价**：39 个有 PS 真值的模式在 6 组参数下只出 12 类像素，31 个模式与其他模式像素完全不可区分。
3. 亮度/对比度是按实测校准的公式（不是源码复刻）；`height`/`linearHeight` 两侧逐像素不可区分。
4. 双笔刷、颜色动态、湿边仍未映射。

## 回退方式

把工作区 `git revert` 回 **v1.3.0**（tag `v1.3.0`，commit `bf52dab`），或直接 `git checkout bf52dab`。
本版本不改转换格式、映射表与纹理公式，回退后转换结果与 v1.3.0 一致。

## 验证

- **转换格式与映射未变**：v1.3.1 未改 `src/brush_converter/mapping.py` 的 `_TEXTURING_MODE` 表与纹理
  brightness/contrast 公式；v1.3.0 产物无需重转（对照见 `docs/验收报告/缺陷-纹理丢失-v1.3.1-代码加固.md`）。
- **命名确定性与覆盖防呆（本地实测）**：`tests/测试用.abr`（6 预设）→
  `测试用_v1.3.1_656f9dc8.bundle`，sha256 `cffcf027cbf52377862a0df3c06abd6f73ef70c21b5f4674bc04aca055054e6f`
  / 651,812 B；同目录再转一次 → 同一文件名、同 sha256，提示「已存在同内容产物，未重复写入」；
  把该文件截断成 1,000 B 后再转 → 生成 `..._656f9dc8_2.bundle`（sha256 `8d48cd17…` / 651,814 B，
  差别来自 meta.xml 的 `dc:title` 跟随文件名），截断的旧文件原样保留（1,000 B）。
- **全量回归**（本机 conda 环境、单进程串行实跑，2026-11-20 12:43）：`python -m pytest -q` →
  **266 passed / 0 failed / 0 skipped**（66.42 s；251 项基线 + 15 项新增 `tests/test_bundle_hardening_v131.py`）。
  **发布前回归请串行跑**：并发跑两套全量会挂 2 个 Krita 渲染用例（`tests/test_krita_render.py`，Krita 渲染属共享
  单实例资源，团队规则要求先抢锁），不是产品回归。
- **诊断工具自检**：`python tools/diagnose_profile.py --help` 全中文；`--krita-dir` 支持 `%VAR%`/`~`；
  代码内零本机绝对路径（`grep -nE "C:[\\/]Users|D:[\\/]program" tools/diagnose_profile.py` 0 命中）。
- **真机现场复跑**（只读副本）：缺陷现场快照 → `[3/8]` 报告 1 个 ABR 直读库并给出处理建议，退出码 1；
  使用者移除 `.abr` 后的当前 profile → `[3/8]` 报告 0 个 ABR 直读库，`[6/8]` 产物 manifest md5 19/19
  且「DB 注册状态：已注册（storage id=275，19 预设 active）」。逐条输出见
  `docs/验收报告/缺陷-纹理丢失-v1.3.1-文档加固.md` §三。

---

# brushConverter v1.3.0（2026-11-20）

纹理混合模式逐模式定论与转正：**PS UI 可选的 10 项模式全部支持**（8 可映射 / 1 需校准 hardMix / 1 不可映射 Drkn）；另对 37 个 UI 选不到的内部枚举值做了完整枚举研究归档（不计为用户限制）。完整 47 行结论：可映射 22 / 需校准 1 / 不可映射 24 / 不可判定 0，转换器为其中 22 个模式登记了明确映射。

## 主要变化

- **纹理混合模式逐模式转正**：`src/brush_converter/mapping.py` 的 `_TEXTURING_MODE` 由
  **19 键扩到 34 键（新增 15 个）**，其中三处是实质修复：
  - `Dfrn`（Difference）实测 PS 侧像素 ≡ 减去 → 映射 `SUBTRACT(1)`；
    旧行为静默回退 Multiply，实测差 **69.04%~99.29%**；
  - `blendSubtraction` 同样 → `SUBTRACT(1)`；旧行为回退 Multiply 属「把减去当正片叠底」的映射错误；
  - `linearDodge` → `8 LINEAR_DODGE`、`linearBurn` → `9 LINEAR_BURN` 显式登记；
    旧行为静默回退 Multiply，实测差 **96.11%~100%**。
  其余新增键（`Mltp`、`Nrml`、`Bhnd`、`Clar`、`H   `、`Strt`、`Clr `、`Lmns`、`passThrough`、
  `lighterColor`、`darkerColor`）让已实测达标的模式不再落进「回退 Multiply + 警告」分支。
  逐模式结论与证据强度见 `docs/数据/mode-table.json`（唯一真相）与 `docs/parameter-mapping.md` §7.2。
- **模式号依据**：以 Krita `TexturingMode` 的 16 个成员（0..15）为准取枚举值；
  Photoshop 专用模式优先用 Krita 的 (Photoshop) 项（`height`→14、`linearHeight`→15、`hardMix`→10）。
  Difference / blendSubtraction 这种「名字对不上但像素对得上」的，按逐像素实测校准，不按名字硬套。
- **文档定稿**：README 新增「已知不可映射模式」一节（24 个模式逐个给非技术语言理由，分
  「Krita 无对应能力 16 个」与「Photoshop 侧产不出可比真值 8 个」两类）；
  `docs/parameter-mapping.md` 新增 §7.2 逐模式结论；`docs/developer-guide.md` 新增数据文件索引；
  GUI 中英文提示同步改为事实陈述。
- **参数矩阵定稿**：`docs/参数矩阵.json`（130 行）修正 `textureBlendMode` 的过时说明、
  补齐 `H   `/`Strt` 两行的来源栏；`python tools/check_matrix.py` 退出码 **0**
  （此前 2 项 C6c 恒红）。

## 已知限制（如实记录）

1. **24 个纹理混合模式不可映射**：16 个是 Krita 侧没有对应算子（结构性限制），
   8 个是 Photoshop 侧产不出可比真值（`Add`、`Rvrt`、`SgdD`、`MgdD`、`HgdD`、`Lght`、`Grdn`、`HdMxS`）。
   未登记的模式会回退 Multiply(0) 并保留警告——是近似，不是等价转换。
2. **0.00% 不等于语义等价**：39 个有 PS 真值的模式在 6 组参数下只出 **12 类**像素，
   **31 个模式与别的模式像素完全不可区分**；本版本的说法统一是「当前口径下像素不可区分」。
3. **`height` 与 `linearHeight` 两侧都逐像素不可区分**（Krita 14/15 渲染 max Δ=0），
   不能说哪一个更准，也不能写「linearHeight 已精确复现」。
4. **亮度/对比度按实测校准，不是按源码复刻**：这两个值已证实是像素通路的真实输入
   （改值会改像素：multiply 97.45%、linearHeight 67.31% 的改值差异），
   但**具体哪段代码消费它们算像素尚未定位**，所以 calibrated 公式是数据拟合的结果。
5. **hardMix 需校准**：6 组里只有 1 组差 **1.03% / max 255**，是 PS 与 Krita 在 `src==0` 边界上的判定不同，
   转换侧没有参数可修。
6. v1.2.x 的既有结构性限制不变：Krita 对比度上限 2（PS 对比度 > 50 无法表达）、
   PS 深度 > 83% 时纹理强度封顶 1.0、亮度/对比度保留 3 位小数。

## 回退方式

本轮**不做功能开关**（不新增环境变量）。整体回退 = 把工作区 `git revert` 回 **v1.2.1**
（tag `v1.2.1`，commit `85aae49`），或直接 `git checkout v1.2.1`。

## 验证

- **参数矩阵自检**：`python tools/check_matrix.py` → `=== 参数矩阵校验通过 ===`，
  矩阵 130 行、逆向独有 39 行，退出码 0。
- **模式总表确定性**：`python tools/render_mode_table.py --build` 连跑两次哈希相同；
  `--check` 与 `python tools/render_matrix_md.py --check` 均输出「同步」。
- **全量回归**：`python -m pytest -q` → **251 passed / 0 failed / 0 skipped**（v0.4 合流验收，
  见 `docs/验收报告/37-报告.md` §三.5；v1.3.0 文档定稿后在最终工作区复跑，80.38 s，结果相同）。
- **端到端抽检**：Mltp / Sbtr / blendSubtraction 三个模式用当前转换链重新出 `.kpp` 后
  Krita 5.3.3 真机渲染，与 PS 真值逐像素比 **0.00% / max 0**（`docs/验收报告/37-报告.md` §三.6）。

---

# brushConverter v1.2.1（2026-11-19）

两处实验已实测证实的修复：高度系纹理强度 ×1.2（PS W=12 vs Krita W=10）
与纹理资源命名/内嵌字节防撞车（Krita 5.3.3 两个静默坑）。

## 主要变化

- **高度系纹理强度 ×1.2（修准深度）**：Photoshop 的「高度 / 线性高度」系纹理掩码按
  `12·q` 加权（`q=round(2.55·depth)`），Krita 同名 Photoshop 算子写 `10·s`
  （`s = Texture/Strength/Value`），归一化后固定差 `2q`。v1.2.1 起高度系写
  `Texture/Strength/Value = min(1.0, 1.2 × depth/100)`，压感曲线路径的 0 端点同样用
  `min(1.0, 1.2 × depth_min/100)`。**非高度系（multiply / subtract / hardMix）一律不动**。
  常数集中在 `src/brush_converter/convert.py`（`HEIGHT_STRENGTH_GAIN`、`_HEIGHT_BLEND_MODES`、
  `_texture_strength()` / `_texture_strength_min()`），不散落魔法数。
- **纹理资源名由内容哈希派生**：`tex_<sha256(内嵌PNG)前12>.png`（旧版 `tex_<uuid前8>.png`
  与内容无关，会与用户图案库同名不同内容撞车 → 串用别人的图案）。同一输入两次转换得到同一名字
  与同一字节，保证「重名 ⇒ 同内容」。
- **内嵌纹理 PNG 追加 tEXt 元数据标记**：keyword=`brushconverter`，
  内容=`sha256=<像素数组 sha256 hex>`，**像素数据逐字节不变**（IDAT 与源 PNG 相同，
  有逐像素断言）。作用是避开 Krita 5.3.3 的「内嵌纹理与图案库同 md5 → 纹理被静默禁用
  （Alpha 恒 255，无报错）」坑；`PatternMD5Sum` 仍是内嵌字节的 md5。
- **版本号**：`src/brush_converter/__init__.py` → `1.2.1`。

## 验证

- **fixA 实测（`docs/验收报告/实验-Krita侧-报告.md` §17，PS 真值对照 d10/scale=100）**：
  mean\|Δ\| **43.31 → 1.39**、max **52 → 2**、超阈像素 **95.36% → 0.00%**；
  d60/d100 两侧本就饱和，加增益后不变（d100 被 1.0 封顶）。
- **纹理撞车实测（同报告 §8.2）**：源文件逐字节内嵌（md5 与图案库同）→ 静默禁用、Alpha 恒 255；
  追加 tEXt 元数据块改变 md5 后恢复正常（与掩码预测逐像素一致）；旧命名 `tex_66e2987f.png`
  与库内同名图案撞车 → 不同字节渲染出同一张错图（Alpha 均值 211.6）。
- **新增回归用例 `tests/test_texture_v121_fixes.py`（挂 verify 标记）**：高度系 6 个别名 ×
  depth∈{0,10,60,83,100} 逐值断言实际 XML 的 `Texture/Strength/Value`；非高度系 2 个模式
  同表断言「不变」；压感曲线 0 端点逐值断言；内嵌 PNG 逐像素相等 + tEXt 值 = 像素 sha256 +
  内嵌 md5 ≠ 源文件 md5；资源名正则 `^tex_[0-9a-f]{12}\.png$` + 确定性 + 不同内容不同名。
- `pytest -q`：**251 项通过 / 0 失败 / 0 跳过**（v1.2.1 施工实测；含新增
  `tests/test_texture_v121_fixes.py` 55 项）。

## 与 v1.2.0 的行为差异

1. **只有高度系、且 depth < 约 83% 的预设**的 `Texture/Strength/Value` 变大（×1.2）：
   d<83% 变化、d≥83% 因 1.0 封顶与旧行为相同；
   非高度系预设（multiply / subtract / hardMix）的渲染参数**完全不变**。
2. **所有带纹理的预设**：内嵌 PNG 字节多一个 tEXt 块（像素不变）、图案资源名从
   `tex_<uuid8>.png` 变为 `tex_<sha256(内嵌PNG)前12>.png`、`PatternMD5Sum` 随内嵌字节变化。
3. 笔尖资源、非纹理参数、.bundle 结构均不变。

---

# brushConverter v1.2.0（2026-11-18）

自动验证体系 + PS/Krita 双端自动出图 + 纹理亮度/对比度 calibrated 转正 + 开源前隐私门。

## 主要变化

- **纹理亮度/对比度公式转正**：默认公式已从 v1.1.2 的经验公式切换为按任务 12 拟合公式反解的
  `calibrated`（掩码空间差异 70.75% → 0.00%）。公式开关仍保留：环境变量
  `BRUSHCONVERTER_TEXTURE_FORMULA`，取值 `calibrated`（默认）或 `legacy`（v1.1.2 历史行为）；
  非法取值回退**默认 calibrated** 并打印一次警告。读取集中在
  `src/brush_converter/kpp/preset_xml.py` 的 `_texture_formula()`，旧公式完整保留，可一键回退。
- **新增 calibrated 公式**：按任务 12 黑盒拟合的 PS 纹理前置变换（对比度中心 127、因子表、
  顺序随对比度符号翻转）反解出 Krita 亮度/对比度的闭式映射
  （`b_k = (1-k)/(510k) - b/255`（C≥0）或 `- b/(255k)`（C<0），`c_k = k`）。
  推导与局限见 `docs/parameter-mapping.md` 第 7.1 节。
- **回退路径不变**：显式 `BRUSHCONVERTER_TEXTURE_FORMULA=legacy` 时输出与 v1.1.2 逐字节一致
  （25 个 .kpp 与上一提交 sha256 全同）；不设变量时输出按 calibrated 变化（预期行为改变）。
- **新增自动验证体系（参数 / 像素 / 手感三层）**：`python tools/verify_all.py --archive` 一次串行跑完三层
  （参数 3 项 + 像素 34 项 + 手感 9 项 = **46 项**），输出与既定契约一致的总览行
  `通过 46 项 ｜ 失败 0 项 ｜ 跳过 0 项`；`--archive` 写带时间戳的归档
  （`docs/验收报告/归档/verify-*.md`，含总览表、失败明细、跳过原因、自动采集的 Python/Krita/Photoshop 三行环境）；
  `--selftest-failure` 可演示「失败明细指到具体用例 + 退出码 1」。像素层基线清单合并手感来源后为
  **341 条四源**（298 PS + 29 Krita + 2 手工 + 12 手感，对账 341 = 298 + 29 + 2 + 12），合并幂等（第二次新增 0）。
- **PS / Krita 双端自动出图**：Photoshop 侧用 ExtendScript（JSX）自动出图 **20/20 成功**，入库
  `tests/baselines/ps/`（另有人工真值 158 张，清单共 178 条）；Krita 侧 kritarunner 无头路线实测不可用
  （证据保留），改走**真实 Krita GUI 进程 + PyKrita 启动插件**（路线 A），单笔 200×200 渲染 **11–14 秒**、
  Alpha 非零像素 3348，同参数两次渲染 RGB 逐像素一致（Alpha 最大差 4–6）。整图 PS↔Krita 比对仍不可达，
  见「已知限制 5」。
- **文档与文案对齐**：GUI 的纹理提示改为 calibrated 说明（含两处结构性限制与 `legacy` 回退命令，中英文），
  `docs/parameter-mapping.md` §7 与 `docs/developer-guide.md` 同步更新；`README.md` 的版本号与文档链接
  留待发布任务统一改。
- **开源前隐私门**：`python tools/privacy_scan.py --include-untracked --fail-on-block` → **block = 0**
  （warn 551 条，逐条给出保留理由）；扫描范围为工作区受跟踪文件 + 未跟踪新文件 + 全部提交历史（双向），
  五类（凭据 / 身份 / 路径 / 元数据 / 二进制图片）；361 张受跟踪图片经 Pillow 检查 **0 张**带内嵌文本或 EXIF。
  发布前已重写本地未推送历史，工作区与全部提交历史双向扫描 **block = 0**（warn 551 条，逐条给出保留理由）。

## 验证

- A/B（掩码空间，纹理 0..255 全取值，tol=2，达标线 ≤2%）：
  61 组可表达参数组上 legacy 差异比例 **70.75%** → calibrated **0.00%**（最大差 ≤1）；
  真实 15 支纹理笔刷中 14 支 calibrated **0.00%**（最大差 ≤1），剩余 1 支（PS 对比度 84）属结构性限制；
  真实 PS 真值图 29 张（可表达参数）legacy 74.53% / 最大差 57 → calibrated **0.00% / 最大差 1**。
- **三层自动验收**：`python tools/verify_all.py --archive` 实跑 **通过 46 项 ｜ 失败 0 项 ｜ 跳过 0 项**，
  退出码 0（归档 `docs/验收报告/归档/verify-20261118-232059.md`）。
- `pytest -q`：**196 项通过 / 0 失败 / 0 跳过**（含新增 `tests/test_texture_formula_switch.py` 19 项、
  `tests/test_full_verify.py` 7 项等）。
- **打包自检（v1.2.0 发行包）**：打包后的 CLI 通过 `info` 与 `convert` 两个子命令（转出 6 个 `.kpp` +
  1 个 `.bundle`）；其对纹理亮度/对比度的输出与开发版 calibrated 路径逐字段一致（例：`测试用.abr` 000 号
  `-0.001 / 1.786`，同参数 legacy 为 `0.1 / 1.79`）；GUI 以 `QT_QPA_PLATFORM=offscreen` 离屏启动 13 秒
  存活、无 stderr、无崩溃转储（**双击启动由人工验证**）。

## 已知限制（7 条，逐条如实记录）

1. **Krita 纹理强度上限 1.0 与 PS 深度 > 约 83% 的冲突**：PS 高度/线性高度用 `12·q` 加权，
   Krita 同一算子写 `10·s`，换算约 `strength ≈ 1.2·depth/100`；PS 深度 > 约 83% 时 Krita
   会提前饱和、无法精确表达（本版未改深度映射）。
2. **Krita 对比度硬上限 2**：PS 对比度 > 50 时 `k > 2` 无法表达（结构性失真；真实样本
   「KYLE 的橡皮擦 - 自然边缘」C=84 即属此类），建议 PS 侧对比度控制在 ≤ 50。
3. **Krita 亮度范围 [-1,1]**：C<0 且 PS 亮度绝对值很大（约 `|b|>127 且 C=-50`，如 b=±150）时
   反解值越界，按边界截断。
4. **calibrated 保留 3 位小数**：Krita 用 `getDouble` 原样读取预设值；若用户在 Krita 里打开
   「笔刷设置」编辑器，控件可能把值舍入到两位小数（潜在 ≤3 灰度级偏差）。
5. **PS↔Krita 整图比对仍不可达**（73.13%~100%，任务 09/10 已定）——本版只校准亮度/对比度，
   模式/深度算子差异另属一项。
6. **C=+100 理论二值化极限**允许 ±128 的单像素翻转（任务 12 已说明）。
7. **GUI 提示文案**在任务 13 时仍是「经验映射」；任务 16 已完成文案对齐，本版 GUI 提示已说明 calibrated、
   两处结构性限制与 `legacy` 回退方式（本条为任务 13 时的记录，保留以便对照）。

## 用法

```bash
# 默认：calibrated（v1.2.0 起）
python cli.py convert tests/测试用.abr -o converted

# 回退到 v1.1.2 历史公式
BRUSHCONVERTER_TEXTURE_FORMULA=legacy python cli.py convert tests/测试用.abr -o converted
```

---

# brushConverter v1.1.2

代码审查修复版本：解析健壮性、纹理/预览参数正确性、GUI 稳定性与打包入口修复。

## 主要变化

- **修复 ABR 区段对齐**：8BIM 区段的 4 字节补足量算错（写成 `length % 4`），区段长度不是 4 的倍数时，其后的区段会被整段丢弃。实测 `测试用.abr` 的 `desc` 之后 `phry` 区段此前丢失，现已正确解析。
- **修复纹理/间距的合法 0 被默认值覆盖**：纹理深度、纹理缩放为 0 时会被改成 100，笔刷间距为 0 时会被改成 25%（`or` 把 0 当缺失）。现在「纹理深度 0（关闭）」会如实输出强度 0。
- **修复计算笔刷预览变成小点**：高硬度笔刷的预览蒙版公式写反，硬边被压成半径约 7% 的点；现按硬度生成正确的边缘过渡。
- **修复仅含纹理的计算笔刷不显示纹理**：这类预设没有声明 `embedded_resources="2"`，Krita 不会读取内嵌纹理资源，现已声明。
- **修复 `.bundle` 元数据不可解析**：`meta.xml` 缺失 `office` 命名空间声明（命名空间感知的解析器直接报 unbound prefix），并修正 Dublin Core URI 与 manifest 属性转义。
- **解析健壮性**：描述符 enum 字段顺序修正（typeID 与值曾互换）、对象引用按规范逐类型跳过、嵌套深度与长度校验、RLE 行长度表/图案通道/笔尖头部越界校验。损坏文件现在提示「ABR 结构损坏」，而不是抛未捕获异常；纹理图案解析失败只降级为「图案缺失」警告，不再让整个文件不可用。v1/v2 旧格式也不再误报「不是已知的 6/10」。
- **修复 `python -m gui` 无法启动**：`src` 的 `sys.path` 引导放在 `__main__.py` 太晚（包导入时已失败），已移到 `gui/__init__.py`。
- **GUI 稳定性**：关窗时等待后台线程（消除 QThread 在运行中被销毁的风险）；解析失败清空上一个文件残留的列表与预设；切换语言保留用户勾选；全选不再 O(N²) 卡顿；转换期间禁用「打开」并保证按钮状态与当前文件一致。
- **CLI**：导出笔尖时清洗 Windows 保留字符（`\ : * ? " < > |`）与结尾点号/空格，避免 `OSError`。

## 验证

- 测试套件：63 项通过（新增 16 项回归测试 `tests/test_review_fixes.py`，覆盖上述每一项修复）。
- 真实样本回归：`测试用.abr`（6 支）与「海怪…18支.abr」（19 支）的全部映射参数与 v1.1.1 逐字段一致，仅修复了丢失的区段与误报诊断。
- CLI `info/convert/extract`、`--no-bundle --no-standalone` 错误路径、`.kpp`（PNG 预览 + Preset XML）、`.bundle`（meta/manifest）均已验证。
- 本版本由 open-code-review 审查（50 条意见）驱动，逐条复核后修复确认项。

## 已知限制

- 纹理亮度/对比度仍为经验映射（同 v1.1.1），不保证与 Photoshop 完全等价。
- 双笔刷、颜色动态、湿边和喷嘴仍未映射。

---

# brushConverter v1.1.1

纹理映射统一、效果强度/旋转开关修复与 GUI 中英文支持版本。

## 主要变化

- **纹理亮度/对比度统一映射**：取消 Linear Height (Photoshop) 的特殊映射（0.30 基线 + 对比度倒数），所有纹理模式统一使用普通映射（亮度 `round(0.10-PS/250,2)`、对比度 PS 中心因子）。特殊映射实测效果不好，已按实测反馈取消。
- **修复纹理「效果强度」开关**：PS 纹理深度无传感器（`bVTy==0`）时，Krita「纹理-效果强度」保持开启、由 `textureDepth/100` 提供恒定强度（`UseCurve=false`），不再被整体禁用。
- **修复无传感器随机旋转**：PS 角度抖动控制=关（`bVTy==0`）但 jitter>0 时为纯随机旋转，Krita「旋转」选项现会开启并使用 fuzzy-only 传感器（不随笔迹方向），`RotationValue=jitter/100`。
- **未映射控制源提示**：旋转控制源 bVTy=1/3/4/5 且 jitter>0 时，GUI 会提示「旋转控制源未映射」，不再静默丢失。
- **GUI 中英文切换**：右上角新增语言下拉框（中文 / English），选择持久化（QSettings），切换时笔刷卡片与警告同步翻译。
- **GUI 纹理提示**：使用纹理的笔刷在转换前提示亮度/对比度为经验映射，建议转换后在 Krita 手动微调。

## 验证

- 测试套件：38 项通过（含纹理强度开关、无传感器随机旋转、纹理映射公式回归）。
- 纹理亮度/对比度统一映射、效果强度与旋转修复均已在 Krita 中实测确认。
- GUI 语言切换与纹理提示通过离屏冒烟/集成测试。

---

# brushConverter v1.1.0

纹理映射、笔尖尺寸和旋转映射校准版本。

## 主要变化

- 新增 ABR `patt` 纹理区段解析，按 UUID 关联纹理。
- 将纹理 PNG 以 `type="patterns"` 资源内嵌到 Krita `.kpp`。
- 支持 Krita Photoshop 专用纹理模式：Height、Linear Height、Hard Mix。
- 根据 Photoshop/Krita 定量对照更新亮度和对比度映射。
- 适配 Krita UI 的参数精度：亮度和对比度按两位小数写入，并限制到 Krita 实际范围。
- 非正方形采样笔尖在内嵌前使用白色边框补齐为正方形。
- 笔尖缩放基于宽高较大值，修复 Krita 预设编辑器与绘图区尺寸不一致。
- Photoshop 方向抖动映射到 Krita `RotationValue`；随机度曲线使用 XML 归一化坐标 `0,0;1,1;`。

## 验证

- 真实 ABR 纹理解析、RLE、KPP XML、模式映射、尺寸和旋转回归测试通过。
- 测试套件：35 项通过。
- 已生成 19 个预设的新示例 `.bundle`。

## 已知限制

- Photoshop 与 Krita 的纹理模式算法不同，亮度/对比度映射使用定量样本校准常数，不保证所有笔尖和深度完全等价。
- Photoshop 对比度高于约 +50 时会快速饱和，Krita 对比度范围为 0~2，无法一一表示极端值。
- 双笔刷、颜色动态、湿边和喷嘴仍未映射。
