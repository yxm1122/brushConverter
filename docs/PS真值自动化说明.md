# PS 真值自动化说明（任务 05）

> 说明：本文档里的本机实测路径已占位化（`<LOCAL_APP_DIR>` = 本机软件安装目录，`<REPO>` = 仓库根），复现时请改成你自己的安装路径与仓库路径。

> 适用版本：Photoshop 2025（26.9.0.15，`<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe`）
> 本机环境：Windows + MSYS bash；Python `<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe`（3.12.13）
> 相关脚本：`tools/ps_probe.py`（驱动 + 校验）、`tools/ps_export.jsx`（Photoshop 侧出图）、`tools/ps_ingest.py`（人工真值图入库）
> 相关产物：`tests/baselines/ps/`（基准图 + `manifest.json`）、`tests/baselines/ps/_work/`（任务清单、日志、纹理、探测脚本等取证材料）

---

## 一、结论：三条通路的实测结果

| 通路 | 结论 | 证据 |
|---|---|---|
| ① pywin32 COM | **能连上，但不能执行脚本 → 不能出图** | 见第二节原始报错 |
| ② ExtendScript JSX（`Photoshop.exe -r`） | **可用，本次采用** | 20/20 张自动出图成功，见第四节 |
| ③ 人工导出图入库降级 | **已实现并有实际数据**（158 张人工真值图入库登记） | `tools/ps_ingest.py`，见第八节 |

一句话：**自动出图走的是 JSX 通路；COM 只成功拿到版本号，脚本执行被 PS 2025 的已知问题挡住。**

---

## 二、通路① COM（pywin32）原始实测

前置：`pywin32` 未安装，按任务书先登记后安装：

| 包名 | 用途 | 大小 | 联网 | 命令 | 装后版本 |
|---|---|---|---|---|---|
| pywin32 | 用 COM 驱动 Photoshop | wheel 6.9 MB | 是（走本机代理 127.0.0.1:9190） | `python -m pip install pywin32 --proxy http://127.0.0.1:9190` | **312** |

复现命令：

```bash
"<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe" tools/ps_probe.py --com-check
```

关键原始返回值 / 报错（`--com-check` 的真实输出）：

```text
[COM] 尝试 Dispatch('Photoshop.Application') ...
  原始报错：com_error: (-2147221005, '无效的类字符串', None, None)
[COM] 尝试 Dispatch('Photoshop.Application.190') ...
  成功：Name='Adobe Photoshop' Version='26.9.0' Path='<LOCAL_APP_DIR>\\PS2025\\Adobe Photoshop 2025\\'
[COM] 尝试 app.DoJavaScript('1+1', [], 1) ...
  原始报错：com_error: (-2147352567, '发生意外。', (0, 'Adobe Photoshop', '非法参数 - 参数 1\n- 缺少必需值', None, 0, -2147220262), None)
[COM] 尝试 app.Open(某个真值 PNG) ...
  原始报错：com_error: (-2147352567, '发生意外。', (0, 'Adobe Photoshop', '非法参数 - 参数 1\n- 缺少必需值', None, 0, -2147220262), None)
```

- **必须用带版本号的 ProgID**：注册表里 PS 2025 只注册了 `Photoshop.Application.190`（`HKLM\SOFTWARE\Classes` 下 112 个 `Photoshop.*.190` 键），裸 `Photoshop.Application` 不存在，所以报「无效的类字符串」。
- 属性读取正常（`Name` / `Version` / `Path` / `Documents.Count`），但**任何执行脚本/打开文件的调用都报「非法参数 - 参数 1 - 缺少必需值」**；`CharIDToTypeID`、`StringIDToTypeID`、`ExecuteAction` 同样报错。这与社区记录的「Photoshop 2025 + pywin32：跑 .jsx 时抛 `Illegal argument - argument 1 - Required value is missing`，2024 不复现」（loonghao/photoshop-python-api issue #348）一致。
- 结论：**COM 只作取证，不用来出图**。

---

## 三、通路② JSX：怎么跑

```bash
cd /d/PycharmProjects/brushConverter

# 1) 批量自动出图（会先强杀残留的 Photoshop，再启动 PS 跑 JSX，完成后自动清理）
"<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe" tools/ps_probe.py --batch

# 2) 只跑前 N 个组合（调试）
".../python.exe" tools/ps_probe.py --batch --limit 4

# 3) 校验基准目录与清单
".../python.exe" tools/ps_probe.py --verify
```

内部流程（`tools/ps_probe.py` → `tools/ps_export.jsx`）：

1. Python 生成确定性的 64×64 灰度噪点纹理 `tests/baselines/ps/_work/texture_noise64.png`（`seed=20261118`）。
2. Python 把组合清单写进 `_work/job.json`（画布/画笔/纹理口径 + 每张的模式、深度、亮度、对比度）。
3. `taskkill -F -IM Photoshop.exe` 清场，然后 `Photoshop.exe -r tools/ps_export.jsx` 启动 PS。
4. PS 侧读 `job.json`：定义纹理图案 → 选中画笔工具 → 逐条建画布、写参数、打一个笔尖、导出 PNG → 写 `_work/done.json` → 自己退出。
5. Python 等 `done.json` 出现（默认 420 秒超时），兜底再 `taskkill`，然后做 Alpha 统计、把 RGB 清零、把条目合并进 `tests/baselines/ps/manifest.json`。

一次完整批量（20 张）实测耗时约 **2 分钟**，其中 PS 冷启动占大头。

---

## 四、每张图的设置（口径固定，改口径必须整批重出）

| 项 | 取值 | 说明 |
|---|---|---|
| 画布 | **100×100 px，72 ppi，RGB，透明背景** | 与 `research/测试结果`、`research/测试结果2` 的 100×100 口径一致 |
| 纹理图案 | `bc_texture_noise64`：64×64、全不透明、0..255 灰度噪点（seed=20261118） | 与历史人工真值图「自制 64×64 灰度纹理」同类 |
| 纹理缩放 `textureScale` | **100%** | 固定，不随图变化 |
| 纹理亮度/对比度 | 按组合：亮度 ∈ {-50, 0, 50}、对比度 ∈ {-50, 0, 50} | 直接写 `textureBrightness` / `textureContrast` |
| 纹理深度 `textureDepth` | 按组合：{10, 60, 100}% | 单位是 percentUnit |
| 纹理混合模式 | 减去 / 正片叠底 / 高度 / 线性高度 | 见第五节的枚举写法 |
| 画笔 | 直径 **300 px**、硬度 **100%**、间距 **1000%**、不透明度/流量 100%、圆度 100% | 300 px 直径 > 画布对角线 141 px，保证一个笔尖铺满画布；1000% 间距保证「整张图只有一个笔尖」 |
| 动态 | 笔尖动态/散布/平滑全部关闭 | 保证可复现 |
| 前景色 | 纯黑 (0,0,0) | 与历史真值图 RGB 全黑口径一致 |
| 落笔 | 画布中心 (50,50) 到 (50.4,50.4) 的 0.4 px 路径描边 | 用 `pathItem.strokePath(ToolType.BRUSH)` 触发；0.4 px ≪ 间距 3000 px，故只落一个章 |
| 导出 | PNG（RGBA），透明通道保留 | 导出后把 RGB 清零（见第八节口径），Alpha 保持 PS 原值 |

直接点击「画笔工具 → 画布」在脚本里无法模拟，本方案用「路径 + 超大画笔 + 超大步距」等价出单个笔尖，这也是为什么画笔直径与间距必须锁死。

---

## 五、Action Manager 关键写法（踩坑记录，改脚本前必读）

1. **选画笔工具用类名 `paintbrushTool`**，不是 `brushTool` / `Brsh`：
   ```jsx
   var d = new ActionDescriptor(); var r = new ActionReference();
   r.putClass(stringIDToTypeID("paintbrushTool"));
   d.putReference(charIDToTypeID("null"), r);
   executeAction(charIDToTypeID("slct"), d, DialogModes.NO);
   ```
   （`brushTool` 会新建一个无意义 ID 并报「命令"选择"当前不可用」；`moveTool` 等其它工具用同一写法可选中，可作对照。）
2. **写工具选项的引用必须是工具类**：
   ```jsx
   var d = new ActionDescriptor(); var r = new ActionReference();
   r.putClass(stringIDToTypeID("paintbrushTool"));
   d.putReference(charIDToTypeID("null"), r);
   d.putObject(charIDToTypeID("T   "), stringIDToTypeID("currentToolOptions"), optsDesc);
   executeAction(charIDToTypeID("setd"), d, DialogModes.NO);
   ```
   用 `capp`（应用）引用会报「命令"设置"当前不可用」；用 `Brsh` 引用会返回成功但**什么都不改**。
3. **`textureBlendMode` / `textureDepth` 只在 `useTexture=true` 之后才出现在 `currentToolOptions` 里**，所以必须分两阶段：先写 `useTexture` + 图案 + 缩放 + 亮度/对比度 + 深度，再读回选项写混合模式。
4. **枚举值写法不统一**：
   - 减去 = `charIDToTypeID("Sbtr")`，正片叠底 = `"Mltp"`，高度 = `"Hght"`，变暗 = `"Drkn"`，叠加 = `"Ovrl"`，柔光 = `"SftL"`，强光 = `"HrdL"`；
   - **线性高度只能用字符串 `stringIDToTypeID("linearHeight")`**；四字符候选 `LnHt/LnHg/LinH/LHgt/HgtL/LnH ` 全部被忽略（回读仍是 `height`）。
5. `currentToolOptions` 描述符的 `toSource()` 在 PS 2025 里返回 `({})`，**不能靠 toSource 看内容**；要逐键展开：`desc.count`、`desc.getKey(i)`、`desc.getType(k)`、`typeIDToStringID` / `typeIDToCharID`。
6. `app.brushes` 在 PS 2025 里不存在（`undefined`）；`executeActionGet` 对 `pattern` 类的引用一律报「命令"获取"当前不可用」，所以纹理图案**按名字引用**即可（`putString(sid("name"), "bc_texture_noise64")`），不需要图案 ID。
7. `app.quit()` 有时停在退出流程不返回，驱动脚本在拿到 `done.json` 后统一 `taskkill -F -IM Photoshop.exe` 兜底。

---

## 六、本批自动出图清单（20 张，`tests/baselines/ps/`）

命名规则：`<来源>__<标识>__<模式>__<关键参数>.png`，本批 `来源=ps`、`标识=自动纹理A`、参数 `d<深度>_b<亮度>_c<对比度>`。

| 文件 | PS 回读模式 | Alpha 均值 | Alpha 最小/最大 | 取值种数 |
|---|---|---|---|---|
| ps__自动纹理A__减去__d100_b0_c0.png | subtract | 127.87 | 0 / 255 | 256 |
| ps__自动纹理A__减去__d100_b50_c0.png | subtract | 82.67 | 0 / 205 | 206 |
| ps__自动纹理A__减去__d100_b-50_c0.png | subtract | 172.95 | 50 / 255 | 206 |
| ps__自动纹理A__减去__d100_b0_c50.png | subtract | 127.70 | 0 / 255 | 129 |
| ps__自动纹理A__减去__d100_b0_c-50.png | subtract | 127.94 | 64 / 192 | 129 |
| ps__自动纹理A__减去__d60_b0_c0.png | subtract | 178.73 | 102 / 255 | 154 |
| ps__自动纹理A__减去__d10_b0_c0.png | subtract | 242.03 | 229 / 255 | 27 |
| ps__自动纹理A__正片叠底__d100_b0_c0.png | multiply | 127.13 | 0 / 255 | 256 |
| ps__自动纹理A__正片叠底__d100_b50_c0.png | multiply | 172.33 | 50 / 255 | 206 |
| ps__自动纹理A__正片叠底__d60_b0_c0.png | multiply | 178.27 | 102 / 255 | 154 |
| ps__自动纹理A__正片叠底__d10_b0_c0.png | multiply | 241.97 | 229 / 255 | 27 |
| ps__自动纹理A__高度__d100_b0_c0.png | height | 255.00 | 255 / 255 | 1（**饱和**） |
| ps__自动纹理A__高度__d60_b0_c0.png | height | 255.00 | 255 / 255 | 1（**饱和**） |
| ps__自动纹理A__高度__d10_b0_c0.png | height | 178.52 | 57 / 255 | 199 |
| ps__自动纹理A__高度__d10_b0_c50.png | height | 167.59 | 57 / 255 | 100 |
| ps__自动纹理A__高度__d10_b0_c-50.png | height | 184.94 | 121 / 249 | 129 |
| ps__自动纹理A__线性高度__d100_b0_c0.png | linearHeight | 255.00 | 255 / 255 | 1（**饱和**） |
| ps__自动纹理A__线性高度__d60_b0_c0.png | linearHeight | 255.00 | 255 / 255 | 1（**饱和**） |
| ps__自动纹理A__线性高度__d10_b0_c0.png | linearHeight | 178.52 | 57 / 255 | 199 |
| ps__自动纹理A__线性高度__d10_b0_c50.png | linearHeight | 167.59 | 57 / 255 | 100 |

两点必须记住：

- **高度 / 线性高度在 d60、d100 饱和成全 255**（1 种取值）。这是 PS 真实行为（降低深度才有信息量），但饱和图对拟合没有信息量，做拟合时应按 `alpha_stats.alpha_unique==1` 过滤掉。
- **高度 与 线性高度在 d10 像素完全一致**（diff_pixels=0）。这不是 bug：历史人工真值图 `research/测试结果2/高度_d10/b0_c0.png` 与 `线性高度_d10/b0_c0.png` 也是逐像素一致（diff_pixels=0, max_delta=0）；两者要到 d5/d2 才分叉（分别 mean 48.20/78.73、6.76/30.27）。

---

## 七、排错

| 现象 / 原始报错 | 原因与处置 |
|---|---|
| `-2147221005, '无效的类字符串'` | 用了裸 ProgID；PS 2025 只注册 `Photoshop.Application.190` |
| `非法参数 - 参数 1 - 缺少必需值` | PS 2025 下 COM 执行脚本/打开文件被拦；改用 JSX 通路 |
| `命令"选择"当前不可用`（选工具时） | 工具类名错（`brushTool` / `Brsh`）；改用 `paintbrushTool` |
| `命令"设置"当前不可用`（写工具选项时） | 引用用了 `capp`；改用 `putClass(paintbrushTool)` |
| 写模式返回成功但读回没变 | `textureBlendMode` 键在 `useTexture=false` 时不存在；先启用纹理再写 |
| 线性高度读回还是 `height` | 用了 charID；改用 `stringIDToTypeID("linearHeight")` |
| 导出的 PNG 没有 Alpha（`im.mode == "RGB"`） | `PNGSaveOptions.transparency=true` 没有生效时，改存会得到 RGB；本脚本已显式设置并校验（导出图都是 RGBA） |
| 脚本跑完 Photoshop 不退出 / 下一次 `-r` 直接退出（`PS_EXIT=1`） | `app.quit()` 卡住或已有实例；驱动脚本每次启动前/拿到 `done.json` 后都 `taskkill -F -IM Photoshop.exe` |
| `done.json` 迟迟不出现 | 看 `tests/baselines/ps/_work/batch.log`；PS 可能弹了对话框（首次启动的登录/更新提示），先手动启动 PS 处理掉再跑 |
| 出图全白/全 255 | 深度太大（高度系列 d≥60 会饱和）或纹理没启用；先跑 `--limit 1` 用 减去 d100 b0 c0 验证（应得到 256 种取值、均值约 128） |

---

## 八、可信度边界（引用这批图前先看这里）

1. **版本锁定**：所有结论只对 **Photoshop 2025 / 26.9.0.15** 有效；换 PS 版本必须整批重出（与 `docs/优化提示词/01-通用规范.md` 的「基准环境锁定」一致）。
2. **纹理不同 = 不可与旧人工真值图逐像素比对**。本批用的是 seed=20261118 的 64×64 噪点纹理，旧图用的是另一张自制 64×64 纹理，所以逐像素差异没有意义；可比的是一维映射关系（排序后的 Alpha 分布：以 `减去_d100/b0_c0` 为例，与 `research/测试结果2` 对应图比，排序后最大差 5、平均差 1.54；`高度_d10/b0_c0` 排序后最大差 5、平均差 1.21）。这说明本通路复现了 PS 的同一套变换，**但不是同一张图**。
3. **单笔尖假设**：本批是「一个笔尖铺满画布」，没有笔划搭接、没有压力/动态。它测的是「纹理 → 亮度/对比度 → 模式合成」这一层，不是真实笔划手感；手感比对属于任务 14。
4. **RGB 清零是后处理**：PS 导出的透明像素 RGB 是白色，历史真值图 RGB 全黑，为让新旧口径一致，Python 侧把 RGB 全部清零、**Alpha 保持 PS 原值**（清单 note 里逐张记录了是否发生清零）。本项目比对只看 Alpha（任务 03 约定），所以不影响判据。
5. **饱和图要过滤**：`高度/线性高度` 的 d60、d100 为全 255，属于真实但无信息量的输出，拟合前应按 `alpha_unique==1` 剔除。
6. **画布/缩放口径已锁死**：100×100、纹理 scale=100%、画笔 300px/间距 1000%。改任何一项都要整批重出并在清单里改口径说明，否则新旧图不可比。
7. **PS 必须能被本机启动**：脚本会自动强杀并重启 Photoshop；运行期间不要手动操作 PS，否则会让画笔状态被改掉。
8. **出图数量**：本批 20 张（目标 ≥12）。任务书设想的完整网格是 4 模式 × 3 深度 × 3 亮度 × 3 对比度 = 108 张，本批是代表性子集，**未覆盖全网格**；扩充只需改 `tools/ps_probe.py` 的 `DEFAULT_COMBOS` 再跑一次（约 2 分钟/20 张）。

---

## 九、人工真值图入库（通路③，`tools/ps_ingest.py`）

即使自动通路已经打通，历史人工真值图仍然是拟合与交叉校验的重要数据，因此按命名规范入库 + 登记：

```bash
".../python.exe" tools/ps_ingest.py --dry-run   # 看计划
".../python.exe" tools/ps_ingest.py --apply     # 复制 + 写清单
".../python.exe" tools/ps_ingest.py --verify    # 校验
```

- 来源：`research/测试结果`（48 张，d60/d6）→ 标识 `定量测试v1`；`research/测试结果2`（110 张，d100/d10/d5/d2）→ 标识 `定量测试v2`，共 **158 张**（任务书里写的「127 张」与实际文件数不符，以实际 110 张为准）。
- `research/` 只读：只复制、不改原文件；原路径记录在清单 `origin` 字段。
- 命名：`ps__定量测试v2__减去__d100_b0_c0.png`（深度也写进关键参数，避免不同深度混在一起）。
- 历史数据里 `b0_-25.png` 这种非常规名（对比度写在 `b` 后）也能正确解析成 `c-25`。
- 清单字段：`file/source/size/note`（任务要求的四项）+ `mode/depth/brightness/contrast/alpha_stats/origin/provenance`。`provenance=human_manual` 表示人工导出、`auto_jsx` 表示本次自动化产出。

当前 `tests/baselines/ps/manifest.json`：**共 178 条 = 自动 20 + 人工 158**。

---

## 十、当前状态与后续

- `tools/ps_probe.py --verify` 通过：命名合规、文件齐全、Alpha 非全 0、目录无未登记图片。
- 待人工拍板（已写进 `docs/验收报告/05-报告.md` 的「决策记录」）：组合规模（20 张是否扩到 108 张）、画布/缩放口径是否沿用 100×100，以及是否把「COM 不通」当作长期事实写进文档。
- 复现入口只要三行：`--batch`（出图）、`--verify`（校验）、`--com-check`（COM 取证）。
