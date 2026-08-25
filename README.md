# brushConverter

Convert Photoshop brush sets (`.abr`) into Krita brushes (`.kpp` presets / `.bundle` resource packs), with automatic brush-parameter mapping. Ships as both a command-line tool and a PySide6 GUI.

Photoshop 笔刷（`.abr`）转 Krita 笔刷（`.kpp` / `.bundle`）工具，支持参数自动映射，提供命令行与图形界面两种方式。

## 阶段进度

- [x] 阶段 1：格式调研 + 可行性分析（`docs/feasibility.md`）
- [x] 阶段 2：ABR 解析器 + CLI（`src/brush_converter/abr/`，`cli.py`）
- [x] 阶段 3：desc 解析 + KPP/bundle 生成（`src/brush_converter/kpp/`，`mapping.py`，`convert.py`）
- [x] 阶段 4：GUI（`gui/`，PySide6）
- [x] 阶段 5：PyInstaller 打包（`brushconverter-cli.spec` / `brushconverter-gui.spec`）

## 环境

```
conda activate brushConverter      # Python 3.12, 含 numpy + pillow + pyside6
pip install -r requirements.txt    # 补充依赖（含 pyinstaller）
```

## 用法

```bash
python cli.py gui                                                # 启动图形界面
python cli.py info <file.abr>                                    # 打印版本/区段/笔尖列表
python cli.py extract <file.abr> -o out/ [--contact-sheet]       # 导出 PNG + info.json
python cli.py convert <file.abr> -o out/                         # 命令行批量转换
```

## 打包

项目根目录的 `*.spec` 是 PyInstaller 配置，`--onedir` 模式输出两个目录：

```bash
pyinstaller brushconverter-cli.spec    # → dist/brushconverter-cli/brushconverter-cli.exe  (62 MB, 无 GUI 依赖)
pyinstaller brushconverter-gui.spec    # → dist/brushConverter/brushConverter.exe  (155 MB, 含 PySide6)
```

- 应用图标：`assets/brushConverter.ico`（自绘蓝紫渐变 + 转换箭头 + 笔刷墨点）。
- CLI 入口：`cli.py`（console 模式）；GUI 入口：`brushConverter_gui_entry.py`（避免 `gui/__main__.py` 的开发期 sys.path hack）。
- 重新打包前可加 `--clean` 清理 `build/` 与 `dist/`。

## GUI 功能

- 打开 .abr，预览每支笔刷的笔尖缩略图与名称；
- 勾选要转换的笔刷（支持全选/全不选）；
- 产物可选 `.kpp`（每支一个文件）或 `.bundle`（Krita 资源包）；
- 含纹理/双笔刷/颜色动态/湿边等无法映射参数的笔刷会弹出提醒。

## 转换说明

- 支持 v1/v2 旧格式 + v6.1/v6.2 新格式；采样笔尖转为内嵌 PNG 笔尖，计算笔刷映射为 Krita auto_brush。
- 参数映射见 `docs/parameter-mapping.md`（完整清单）。
- 交付物：Krita 5.x 资源包 `.bundle`（一键导入）+ 自包含 `.kpp`（笔尖 base64 内嵌）。
- 纹理（patt）、双笔刷、颜色动态、湿边等暂不映射（引擎差异大）。
- 想了解实现原理与开发中踩过的坑，见 `docs/developer-guide.md`。

## 在 Krita 中验证

1. 设置 → 管理资源 → 导入资源包，选择生成的 `.bundle`；
2. 或把 `converted/kpp/*.kpp` 复制到 Krita 资源目录的 `paintoppresets/` 下，重启 Krita。
