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
