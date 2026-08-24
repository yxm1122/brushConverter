# ABR → KPP 笔刷转换器 — 可行性报告与项目结构

> 阶段 1 交付物 | 调研日期：2026-08-24
> 结论：**可行**，针对采样笔尖（sampled tips）可做到高保真转换；参数化笔刷动态需近似映射。

---

## 1. 核心结论

| 目标 | 可行性 | 说明 |
|------|--------|------|
| 提取 ABR 中的采样笔尖位图 | ✅ 完全可行 | 格式已被逆向并有多份开源实现参考 |
| 生成 Krita 可用的 .kpp 预设 | ✅ 完全可行 | 已下载真实 .kpp 验证结构：PNG + zTXt 内嵌 XML |
| 打包为 Krita .bundle 资源包 | ✅ 完全可行 | ZIP 容器，Krita 官方导入格式 |
| 笔刷动态参数（压感曲线等）1:1 迁移 | ⚠️ 部分可行 | ABR 的 desc 描述符可解析，但两软件参数模型不同，只能近似映射 |
| 纹理（pattern）迁移 | ⚠️ 可行但后置 | 'patt' 块格式有公开文档，列为后期增强 |
| 图形界面 + 打包 exe | ✅ 完全可行 | PySide6 + PyInstaller 成熟方案 |

## 2. ABR 格式调研结果

文件头为 2 字节大端主版本 + 2 字节次版本，之后分两大类：

### 2.1 旧格式（v1/v2，Photoshop ≤ 7）
- Adobe 官方文档有记载（Photoshop File Formats Specification）
- 结构简单：`brush count` + 每支笔的（尺寸 + 8bit 灰度位图，可选 RLE 压缩）
- 解析难度：**低**

### 2.2 新格式（v6+，Photoshop 7 ~ CC）
从偏移 4 开始是一系列 8BIM 标签块（结构公开）：

```
[version(2B)][subversion(2B)]
[8BIM][samp][length][...]   ← 采样笔尖位图
[8BIM][patt][length][...]   ← 纹理图案
[8BIM][desc][length][...]   ← 描述符（笔刷参数、笔尖与参数的关联）
```

- **v6.1**：`samp` 内每项 = Pascal 字符串 ID + 矩形 + 深度 + 压缩模式 + 位图数据（RLE，算法与 PSD 相同）
- **v6.2**：`samp` 内每项 = ID + 虚拟内存数组列表（较复杂，但 GIMP 已有成熟解析实现可参考）
- `desc` 为 Photoshop 描述符结构（在 PSD 规范中有文档），存储新版笔刷的完整动态参数
- 解析难度：**中**（v6.1 低；v6.2 需要参考 GIMP 源码）

### 2.3 参考实现（开源，供学习格式用）
| 项目 | 语言 | 覆盖范围 |
|------|------|----------|
| GIMP `file-abr` 插件 | C | 最完整：v1/v2/v6.1/v6.2 全支持 |
| abrViewer | C# | v6.1 位图提取 |
| nijiGPen `file_formats.py` | Python + numpy | v1/v2 + v6 位图提取，代码量小，最值得借鉴的 Python 范例 |

## 3. KPP 格式调研结果（已实测验证）

下载了 Krita 官方仓库的真实预设 `AutoBrush_70px_rotated.kpp` 并解析成功：

```
.kpp = PNG 图像（预览缩略图，200×200 推荐）
     + zTXt 块（keyword="preset"，zlib 压缩的 XML）
     + tEXt 块（"version" 等）
```

解出的 XML 核心结构：

```xml
<Preset name="..." paintopid="paintbrush">
  <param name="brush_definition"><![CDATA[
    <Brush type="auto_brush" spacing="0.1" angle="0">
      <MaskGenerator radius="70" .../>
    </Brush>
  ]]></param>
  <param name="CurveOpacity"><![CDATA[0,0;1,1;]]></param>
  ...（压感曲线、传感器等参数）
</Preset>
```

**关键发现（决定架构）**：Krita 5.x（BrushVersion=2）中，图像笔尖**不再内嵌**在 kpp 里，而是以 `filename` + `md5sum` 属性引用资源系统中的独立笔尖文件（GBR/GIH）。因此转换器需要同时产出：

1. **GBR 笔尖文件**——GIMP 笔刷格式，Krita 原生加载。头部布局已从 Krita 源码 `kis_gbr_brush.cpp` 确认：`header_size, version(=2), width, height, bytes(=1 灰度), magic("GIMP"), spacing` 全大端 uint32 + null 结尾的名称 + 位图数据。
2. **.kpp 预设**——PNG(预览) + zTXt(XML)，XML 中引用上述 GBR。
3. **.bundle 资源包**——ZIP（含 `mimetype`、`META-INF/manifest.xml`、`meta.xml`、`paintoppresets/*.kpp`、笔尖文件），Krita 一步导入。这是最终交付给用户的形态。

## 4. 转换管线设计

```
.abr ──► 解析器 ──► [笔尖位图] ──► 灰度整理/边缘清理 ──► GBR 笔尖
              │                                            │
              └─► [desc 参数] ──► 参数映射表 ──► 预设 XML ──┤
                                                           ▼
                                          .kpp (PNG+zTXt) ──► .bundle ──► Krita 导入
```

参数映射策略（初始版本）：
- 笔尖位图 → `paintbrush` 引擎 + 图像笔尖，spacing 取 ABR 值或默认 0.25
- 尺寸/间距/角度 → 直接映射
- 压感不透明度/大小 → 映射到 Krita 的 OpacitySensor/SizeSensor 曲线
- 无法映射的动态（散布、双笔尖、湿边等）→ 使用合理默认值，并在 GUI 中标注"部分转换"

## 5. 已验证的环境

- conda 环境：`D:\program\anaconda3\envs\brushConverter`，Python **3.12.13** ✅
- 依赖规划（均成熟、纯 pip 安装）：
  - `numpy`（位图数组运算）
  - `Pillow`（PNG 读写、预览图生成；PNG 文本块可自写以控制 zTXt 压缩格式）
  - `PySide6`（GUI）
  - `PyInstaller`（打包 exe）
  - `pytest`（测试）
- 无任何需要 C 扩展编译或闭源组件的环节

## 6. 推荐项目结构

```
brushConverter/
├── docs/                        # 文档
│   ├── feasibility.md           # 本报告
│   └── format_notes/            # 格式笔记（ABR/GBR/KPP 字段表）
├── research/                    # 调研材料（真实样本、Krita 源码参考）
├── src/brush_converter/         # 核心包（纯逻辑，不依赖 GUI）
│   ├── abr/
│   │   ├── reader.py            # 头/8BIM section 分发
│   │   ├── samples.py           # v1/v2/v6.1/v6.2 位图提取
│   │   ├── descriptors.py       # desc 参数解析
│   │   └── patterns.py          # patt 提取（后期）
│   ├── kpp/
│   │   ├── gbr_writer.py        # 灰度位图 → GBR
│   │   ├── preset_xml.py        # 预设 XML 生成（模板+参数映射）
│   │   ├── kpp_writer.py        # PNG + zTXt 组装
│   │   └── bundle.py            # .bundle ZIP 打包
│   ├── mapping.py               # ABR 参数 → Krita 参数映射表
│   └── convert.py               # 管线编排（单文件→bundle）
├── gui/
│   ├── __main__.py              # 入口
│   ├── main_window.py           # 主窗口（拖入 abr、笔尖预览、批量转换）
│   └── workers.py               # QThread 后台转换
├── tests/                       # pytest + 小型真实样本 fixtures
├── cli.py                       # 命令行入口（先于 GUI 实现，便于调试）
├── requirements.txt
└── pyproject.toml
```

设计原则：**核心转换逻辑与 GUI 完全解耦**（gui/ 和 cli.py 都是薄壳），方便测试与后续扩展。

## 7. 分阶段实施计划

| 阶段 | 内容 | 交付物 | 验收标准 |
|------|------|--------|----------|
| 1 ✅ | 格式调研、结构设计 | 本报告 + 目录骨架 | — |
| 2 | ABR 解析器（v1/v2 → v6.1 → v6.2）+ cli.py | 能导出笔尖 PNG 的命令行工具 | 对真实 .abr 提取出全部笔尖位图 |
| 3 | KPP/GBR/bundle 生成器 | 完整转换管线 | 生成的 .bundle 在 Krita 5.x 中导入成功并可用 |
| 4 | GUI（PySide6） | 图形界面 | 拖拽/批量转换、笔尖预览、进度与错误提示 |
| 5 | 打包发布 | PyInstaller exe | 无 Python 环境的机器可运行 |

每个阶段结束用真实笔刷文件做回归测试（收集 Photoshop 各年代导出的样本放入 `tests/fixtures/`）。

## 8. 风险与对策

| 风险 | 等级 | 对策 |
|------|------|------|
| v6.2 虚拟内存数组解析复杂 | 中 | 参考 GIMP `file-abr` C 实现；先支持 v6.1，v6.2 迭代补充 |
| 现代 PS 笔刷大量参数化动态无法 1:1 映射 | 中 | 接受近似映射；GUI 中明确标注转换保真度 |
| Krita 各版本对 kpp/XML 字段兼容差异 | 低 | 以 Krita 5.x 为基准（BrushVersion=2），实测验证 |
| PNG zTXt 写入格式细节 | 低 | 已验证可手工构造 PNG 块；用二进制比对官方 kpp 校验 |
