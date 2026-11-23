# brushConverter

Convert Photoshop brush sets (`.abr`) into Krita brushes (`.kpp` presets / `.bundle` resource packs), with automatic brush-parameter mapping. Ships as both a command-line tool and a PySide6 GUI. **v1.5.0**

Photoshop 笔刷（`.abr`）转 Krita 笔刷（`.kpp` / `.bundle`）工具，支持参数自动映射，提供命令行与图形界面两种方式。

- 采样笔尖 → 内嵌 PNG 笔尖；计算笔刷 → Krita `png_brush`（v1.5.0 起按 PS 硬度剖面生成位图）；支持 v1/v2 旧格式与 v6.1/v6.2 新格式。
- 自动映射尺寸/间距/角度/压感动态/旋转抖动/散布/纹理（PS UI 可选的 10 个纹理混合模式全部支持）。
- 纹理 PNG 内嵌 + 缩放/深度（可随压感）/反相/混合模式/亮度/对比度映射；写出后回读校验、覆盖防呆。
- 双笔刷/颜色动态/湿边等引擎差异大的参数暂不映射，转换前弹窗提醒。
- 逐字段映射权威清单见 [`docs/parameter-mapping.md`](docs/parameter-mapping.md)；实现原理与踩坑见 [`docs/developer-guide.md`](docs/developer-guide.md)；版本变更见 [`releases/RELEASE_NOTES.md`](releases/RELEASE_NOTES.md)。

## 使用指南

### 图形界面

![GUI 截图](docs/screenshot-gui.png)

打开 .abr → 预览每支笔尖缩略图与名称 → 勾选要转换的笔刷（全选/全不选）→ 选产物格式（`.kpp` 每支一个，或 `.bundle` 资源包）→ 选输出目录 → 「开始转换」。含未映射参数的笔刷有 ⚠ 标注并弹窗确认；使用纹理的笔刷会说明亮度/对比度已按 Photoshop 公式校准（v1.5.0 起唯一公式，legacy 开关已删除）与模式映射情况。右上角可切换中文 / English（选择会记住）。

下载 `brushConverter-*-win64.zip`（见 [releases](https://github.com/yxm1122/brushConverter/releases)），解压后双击 `brushConverter.exe`。

### 命令行

下载 `brushconverter-cli-*-win64.zip` 解压后用 `brushconverter-cli.exe`：

```bash
brushconverter-cli.exe convert <file.abr> -o 输出目录                # 批量转换为 Krita 预设/资源包
brushconverter-cli.exe info <file.abr>                              # 查看笔刷概况（版本/区段/笔尖）
brushconverter-cli.exe extract <file.abr> -o out/ [--contact-sheet] # 导出笔尖 PNG + 总览图
```

## 环境

```
conda activate brushConverter      # Python 3.12, 含 numpy + pillow + pyside6
pip install -r requirements.txt    # 补充依赖（含 pyinstaller）
pip install -e .                   # 可选：可安装化，便于 import brush_converter / gui
pip install -e ".[dev]"            # 可选：开发工具（ruff / mypy / pyinstaller）
```

代码质量门：`ruff check src/ gui/ cli.py`、`mypy src/brush_converter`、`python -m pytest -q`。

## 用法

```bash
python cli.py gui                                                # 启动图形界面
python cli.py info <file.abr>                                    # 打印版本/区段/笔尖列表
python cli.py extract <file.abr> -o out/ [--contact-sheet]       # 导出 PNG + info.json
python cli.py convert <file.abr> -o out/                         # 命令行批量转换
```

## 打包

根目录 `*.spec` 是 PyInstaller 配置（`--onedir` 模式）：

```bash
pyinstaller brushconverter-cli.spec    # → dist/brushconverter-cli/brushconverter-cli.exe  (62 MB, 无 GUI 依赖)
pyinstaller brushconverter-gui.spec    # → dist/brushConverter/brushConverter.exe          (155 MB, 含 PySide6)
```

图标 `assets/brushConverter.ico`；CLI 入口 `cli.py`（console），GUI 入口 `brushConverter_gui_entry.py`。重打包前可加 `--clean` 清理 `build/` 与 `dist/`。

## 转换说明

- 交付物：Krita 5.x 资源包 `.bundle`（一键导入）+ 自包含 `.kpp`（笔尖 base64 内嵌）。
- 参数映射逐项清单见 [`docs/parameter-mapping.md`](docs/parameter-mapping.md)；纹理混合模式逐模式结论见其 §7.2（唯一真相 `docs/数据/mode-table.json`）。
- 纹理亮度/对比度按 calibrated 公式（v1.2.0 起转正，同引擎掩码空间差异 0.00%，见 parameter-mapping §7.1）；**v1.5.0 起 legacy 开关已删除**（无运行时回退，只能 `git revert` + 重转）。
- 双笔刷、颜色动态、湿边等暂不映射（引擎差异大）。

### v1.5.0 升级必读

- **旧 `.kpp` / `.bundle` 必须重转**：v1.5.0 三批改动（计算笔刷类型、散布/旋转换算、纹理预缩放）都改变产物字节与语义。
- **计算笔刷类型变了**：`auto_brush` → `png_brush`（按 PS 硬度剖面生成位图；尺寸=PS 实际足印——PS 标称 200 的笔在 Krita 显示 290/250/204）。代价：Krita 里 **fade/硬度不可再调**，每支多 3–12 KB。
- **散布数值 ×2、旋转抖动 j<50 ×2**：`ScatterValue = jitter/200`（旧 ÷400）、`RotationValue = min(1, jitter/50)`（旧 ÷100）。旧产物的散布偏小一半、小角度抖动偏小约 2 倍。
- **纹理缩放 >100% 改为预缩放内嵌**：先按 PS 同核放大再内嵌、`Scale` 写 1.0；单预设体积 +0.8 KB~+434 KB，资源名/内容哈希随之变化；缩放/相位不可再在 Krita 里调。
- **legacy 开关已删除**：`BRUSHCONVERTER_TEXTURE_FORMULA` 与旧公式分支已移除，无法回退到 v1.1.2 公式；整体回退只能 `git revert` + 重转。
- 传感器 XML 写法与 v1.4.0 相同（17 组 `*Sensor` 写合法 `pressure` 占位，禁止空 `<params/>`）。

### v1.4.0 升级必读

- **非方形采样笔尖间距会变**：`spacing = Spcn/100 × 短边/长边`（补偿「补白成正方形」的基准差）。非方形笔刷重转后中心距更小、更接近 PS；严格正方形与计算笔刷不变（见 parameter-mapping §1、§10.1）。
- **旧 `.kpp` 必须重转**：基础模板 17 组 `*Sensor` 写合法 `pressure` 占位，未映射动态用 `Pressure*=false` + `*UseCurve=false` 关闭，**禁止空 `<params/>`**（空 XML 会被 Krita 当成有定义又因 sensorId 为空早退，曲线/散布不读；2026-11-21 修正）。旧产物重转后再导入（见 parameter-mapping §4 传感器默认注）。

## 常见问题（FAQ）

### Krita 里笔刷不可见 / 纹理消失

按代价从小到大排查，前两步覆盖绝大多数情况：

1. **别把原始 `.abr` 直接导入 Krita**：Krita 5.3 会把它当 Adobe Brush Library 直读，生成一套**未经本工具转换**的笔刷，与 `.bundle` 产物混在同一列表，看起来像「纹理全丢/笔刷不可见」。在 `设置 → 管理资源` 移除该 ABR 库，或彻底退出 Krita 后把 `%APPDATA%\krita` 下的 `.abr` 移走备份再重启。原始 `.abr` 只交给本工具转换，要导入的是 `.bundle`（`.kpp` 放 `paintoppresets/`）。
2. **校验产物完整且已注册**：`python tools/diagnose_profile.py --krita-dir "%APPDATA%\krita" --bundle 你的.bundle --predict`（只读，不启动 Krita、不写 profile）。看 ZIP 条目数（正式产物 22）、manifest md5（19 通过 0 失败）、DB 注册状态。v1.3.1 起转换末尾应打印 `输出校验：OK（…）`，否则产物未过校验（退出码 3）。
3. **（可选，非必需）重建资源缓存**：前两步无效时才考虑——彻底退出 Krita、**整份备份 `%APPDATA%\krita`**、把 `resourcecache.sqlite` 改名、重启等重建（1–3 分钟）。**代价：自定义预设标签/分组会丢**（笔刷/图案等资源文件本身不丢）。
4. **更新产物先彻底退出 Krita**：运行中替换 `.bundle` 会「文件换了、Krita 还是旧的」甚至静默失败。顺序：退出 Krita → 替换 `.bundle` → 重启。v1.3.1 起产物名为 `<名>_v<版本>_<8位内容哈希>.bundle`：同内容复用不重写、同名不同内容自动加 `_2/_3`（原文件不覆盖）、旧版命名只提示不覆盖。
5. **有些预设本就没有可见纹理**：18 支里 10 支纹理关闭、001/002 是 1×1 纹理（PS 里同样无纹理感），只有 005–011 带真实 1920×1920 纹理。判断纹理是否恢复请用 005–011 落笔对照（推荐 006_大怪兽-碳铅），别当缺陷。

### Krita 5.3.3 传感器 / 曲线显示异常

若用的是 2026-11-21 传感器修复**之前**的产物，可能看到不透明度/流量被开压感、曲线变默认、散布 500%、强度 9% 等。**根因是旧产物写了空 `<params/>`**，触发 Krita `KisKritaSensorPack::read()` 的 `!sensorId.isEmpty()` 断言早退（此前归因为 Krita UI bug 的结论已撤回）。处理：彻底退出 Krita → 删旧测试预设 → 用当前版本重转重导 → 重启再看；仍异常查 `%LOCALAPPDATA%\krita.log`（复核见 `docs/验收报告/112-报告.md` §5.5）。

## 已知限制（摘要）

> 下列为当前工作区（**v1.6.0 候选，未发布**；已发布版仍为 v1.5.0）里用户可感知的残差与代价。逐条数字、归属与复跑命令见 [`parameter-mapping.md`](docs/parameter-mapping.md) §10 与 [`docs/参数矩阵.json`](docs/参数矩阵.json)；下一版变更见 [`releases/RELEASE_NOTES.md`](releases/RELEASE_NOTES.md) 的待发布段。

- **大直径硬边计算笔刷**：直径 ≳453px 且硬度≈100 时，Krita 的过渡带比 PS 宽（D=600/800 实测 +3.15/+4.78px）；h=95 仍贴边在 2px 判据内。属已登记几何级残差，未修。
- **中间硬度（与椭圆要分开看）**：计算笔刷硬度不在 {0,50,100} 时过渡带偏宽——D=200 时 h25 主轴 +19.9px、h75 +8.2px（根因=三档剖面插值）；椭圆轴比本身准确（0.37%–1.34%，已判「可映射」）。
- **小直径的尺寸显示**：D<50 的计算笔刷在 Krita 里显示的尺寸比 PS 实际足印大 2–4px（+9.65/+6.48/+2.78%）；渲染几何不受影响（3/3 通过），无像素后果。
- **旋转抖动**：j<50 的幅度在 v1.6.0 候选已修正（旧产物约为 PS 的 2 倍；改后实机三点 PASS）；j≥50 跨度已饱和（A 级），但 PS 是「U(−3.6j,+3.6j) 环绕」、Krita 是整圈均匀，**分布形状不等价**；Krita 旋转动态的中性基线是 180°。所有 `angleDynamics.jitter>0` 的旧 `.kpp` 必须重转。
- **bVTy=5/1/3/4 无承载**：5（初始方向）在 Krita 只能固定基准角 0°（VH/DD 有 90°/45° 残差）；1（渐隐）/3（倾斜）/4（转轮）无等价控制源。7/8（速度/旋转）在无真实输入时两侧一致，真实数位板输入的动态响应未测。
- **散布 1000% 端点**：计算笔刷的散布量按足印尺寸放大，j=1000% 端点实测 a/D 偏大 **18.21%**（补偿变体 0.01% 已实渲但未落地）；常规 100%/300% 档 a/D 0.65%/1.69% 达标。
- **hardMix 纹理**：满覆盖笔尖（含压感）0.00% 达标；base<1 时 Krita 无等价承载（现行残差 **10.65%–96.81%**，结构性）；b×C 双非零仍有真残差 **56.87%/38.50%**（满覆盖修法候选实渲 0.000%/max0，未落地）。PS「纹理 > 模式」UI 的 10 项全部支持（8 可映射 / 1 需校准 hardMix / 1 不可映射 Drkn）；其余 37 个内部枚举不计限制。
- **亮度越界区已改为预烘焙（v1.6.0 候选）**：C=−50 且 |PS 亮度|>127 的命中区 12/12 达标（改前 8/12 超阈），但命中区笔刷的纹理亮度/对比度/反相在 Krita 里**失去 PS 语义**（已烘进像素）、每 (b,c,invert) 组合一份纹理资源、旧 `.kpp` 必须重转；非命中区仍用 calibrated 公式（经验系数）。
- **纹理缩放与高对比度组合**：缩放 s<100 的非整数倍（如 s=75）现行路径 **88.72%/max94** 超阈（源对齐预缩放修法 0.000%/max1，未落地；s=50 与 200% 已达标）；预缩放 + C=80 组合仍 **5.65%/max5**。
- **参数域上限（无像素后果）**：PS 纹理深度 >83% 时 Krita `Strength` 封顶 1.0（饱和段同像素）；PS 对比度 >50 写出的 k>2，Krita UI 滑块仍只显示 0..2（属预期）。

## 在 Krita 中验证

1. 设置 → 管理资源 → 导入资源包，选生成的 `.bundle`；
2. 或把 `converted/kpp/*.kpp` 复制到 Krita 资源目录的 `paintoppresets/`，重启 Krita。

## 数据与自检入口

- **参数矩阵**：[`docs/参数矩阵.json`](docs/参数矩阵.json)（137 行 × 8 栏，参数结论唯一真相；渲染版 `参数矩阵.md` 脚本生成、禁止手改）。自检 `python tools/check_matrix.py`、`python tools/render_matrix_md.py --check`。
- **测试笔刷集**：[`tests/abr-generated/`](tests/abr-generated/) 20 支（六参数 × 3 取值 + 对照），预期与判据见 [`docs/测试笔刷验证清单.md`](docs/测试笔刷验证清单.md)。回读 `python tools/make_test_abr.py --check-suite`。
- **报告索引**：验收/研究报告为证据归档（非上手必读），清单见 `docs/验收报告/INDEX.md`、`docs/研究报告/INDEX.md`（`python tools/render_archive_index.py --check` 校验同步）。

## 相关文档

- [`docs/developer-guide.md`](docs/developer-guide.md) — 项目结构、ABR 解析原理、KPP/bundle 生成、开发踩坑（**§0 文档索引先看这里**）。
- [`docs/parameter-mapping.md`](docs/parameter-mapping.md) — 逐字段映射权威清单（§7 纹理、§7.2 模式、§10 残差）。
- [`docs/验证体系使用说明.md`](docs/验证体系使用说明.md) — 三层验证（回归/参数级/像素级）入口与口径。
- [`releases/RELEASE_NOTES.md`](releases/RELEASE_NOTES.md) — 版本变更、升级注意与已知限制。