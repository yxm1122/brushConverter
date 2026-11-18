# Krita kritarunner 无头渲染可行性研究报告（任务 04 · 验收门）

> 执行代理：krita-line　|　执行时间：2026-11-18 21:58–22:15（Asia/Shanghai）
> 环境：Windows / MSYS bash；Krita 5.3.3（`<LOCAL_APP_DIR>\Krita (x64)`）；仓库 cwd = `<REPO>`
> 探测脚本：`tools/krita_probe/probe*.py`（只读探测，不改 Krita 安装目录、不改 `converted/`）

---

## 一、结论（一句话）

**结论：kritarunner 不能无头落笔；但「GUI 进程 + 启动插件」路线可行（2026-11-18 22:33 追加实验成功，见 §九）。**

具体：`kritarunner.exe` 能启动 Krita 内核（`Krita.instance()` 可用、能建文档、能读到 137 个预设），
但**无法建立画布视图（view）**：调用 `Window.addView()` 必崩（访问违例 `0xC0000005`，实测 3 次复现）；
而 Krita 的笔刷落笔接口 `Node.paintLine()` 在源码里硬依赖「当前主窗口 → 当前视图」（无视图即空指针崩溃）。
因此「不开界面就把 .kpp 笔刷画出来」在本机 Krita 5.3.3 上**不可实现**。

附带发现（同样影响任务 06/06A）：`kritarunner` 里 `Krita.instance().resources("preset")` 只能读到资源库里的预设，
**没有「按文件路径加载任意 .kpp」的 Python API**；实测 137 个预设中匹配本项目转换产物（大怪兽/勾线/圆头/测试用）的数量为 **0**，
说明 `converted/测试用.bundle` 也没有被装进 Krita 资源库。

---

## 二、探测现场（只读，未改任何安装目录文件）

| 项目 | 实测值 | 取数命令 |
|---|---|---|
| Krita 版本 | 5.3.3 (git 858d352) | 见 §3.2 JSON `krita_version` |
| kritarunner.exe 修改时间 | 2026-07-26 19:45:02 | `stat -c '%y %n' '/d/program/Krita (x64)/bin/kritarunner.exe'` |
| 内置 Python | 3.13.5 (MSC v.1943 64 bit) | 见 §3.2 JSON `python_version` |
| Krita 安装目录是否被改动 | **否**（文件时间 2026-07-26，早于本次） | 同上 |
| converted/ 是否被改动 | **否**（`测试用.bundle` 时间 2026-08-27 18:52:10） | `stat -c '%y %n' converted/测试用.bundle` |

---

## 三、证据（命令原文 + 原始输出）

### 3.1 kritarunner 的调用语义（`--help` 与参数传递）

命令（2026-11-18 21:58）：

```bash
cd '/d/program/Krita (x64)/bin' && timeout 40 ./kritarunner.com --help
```

原始输出（退出码 0）：

```text
用法：./kritarunner.com [选项] [argument(s)]
kritarunner executes one python script and then returns.

选项：
  -v, --version              显示版本信息。
  -?, -h, --help             在命令行选项上显示帮助。
  --help-all                 显示包括 Qt 特有选项的帮助。
  -s, --script <script>      The script to run. Do not append the .py
                             extension.
  -f, --function <function>  The function to call (by default __main__ is
                             called).

参数：
  [argument(s)]              The arguments for the script
```

**实测到的三条关键语义**（脚本 `tools/krita_probe/probe2.py`，模块级 `print`）：

1. `-s` 要的是**模块名**，不是文件路径：`-s probe2`（cwd=脚本目录）能执行；
   传绝对路径（`-s D:/.../probe2` 或带 `.py`）时进程**静默退出码 0、脚本一行都没跑**。
2. 脚本被当作模块加载：`__name__ = 'probe2'`（不是 `__main__`），所以
   `if __name__ == "__main__":` 守卫里的代码**不会执行**，脚本必须写在模块级。
3. `-s probe2 alpha beta` 时脚本里 `sys.argv = ['']`，**位置参数没有传给脚本**；
   因此外部参数只能通过环境变量或生成临时模块传入（任务 06 的外壳据此设计）。

原始输出（`cd tools/krita_probe && "<LOCAL_APP_DIR>/Krita (x64)/bin/kritarunner.exe" -s probe2`，退出码 0）：

```text
Fontconfig error: Cannot load default config file: No such file: (null)
Fontconfig error: Cannot load default config file: No such file: (null)
Could not find platform independent libraries <prefix>
<LOCAL_APP_DIR>\Krita (x64)\lib\krita-python-libs\krita added to PYTHONPATH
HELLO_FROM_SCRIPT
STDERR_FROM_SCRIPT
NAME='probe2' ARGV=[''] CWD='<REPO>\\tools\\krita_probe'
```

### 3.2 Krita 内核在 kritarunner 里是可用的（`probe_env.py`）

命令（2026-11-18 22:00）：

```bash
cd tools/krita_probe && timeout 120 '/d/program/Krita (x64)/bin/kritarunner.exe' -s probe_env
```

原始输出（退出码 0，节选）：

```text
PROBE_JSON_BEGIN
{
  "python_version": "3.13.5 (tags/v3.13.5:6cb20a2, Jun 11 2025, 16:15:46) [MSC v.1943 64 bit (AMD64)]",
  "executable": "<LOCAL_APP_DIR>\\Krita (x64)\\bin\\kritarunner.exe",
  "krita_module": "<LOCAL_APP_DIR>\\Krita (x64)\\lib\\krita-python-libs\\krita\\__init__.py",
  "instance_repr": "<PyKrita.krita.Krita object at 0x000002201B8159A0>",
  "krita_version": "5.3.3 (git 858d352)",
  "documents": 0,
  "windows": 0,
  "activeWindow": "None",
  "activeDocument": "None",
  "batchmode": false,
  "resources_preset": 137,
  "createDocument": "<PyKrita.krita.Document object at 0x000002201B817F50>",
  "doc_width": 100,
  "doc_height": 100,
  "rootNode": "<PyKrita.krita.GroupLayer object at 0x000002201B817EC0>",
  "activeNode": "None"
}
PROBE_JSON_END
```

**读法**：`Krita.instance()` 非空、`createDocument` 成功、资源库有 137 个预设；
但 `windows=0`、`activeWindow=None`——**没有任何主窗口/视图**，这正是后面崩溃的根。

### 3.3 崩溃证据一：`Window.addView()` 必崩（`probe7_crash.py`）

命令（2026-11-18 22:05）：

```bash
cd tools/krita_probe && timeout 150 '/d/program/Krita (x64)/bin/kritarunner.exe' -s probe7_crash
```

原始输出（进程退出码 = **-1073741819**，即 `0xC0000005` 访问违例）：

```text
[STEP] openWindow
[STEP] win=<PyKrita.krita.Window object at 0x000001E2FF035EB0>
[STEP] createDocument
[STEP] doc=<PyKrita.krita.Document object at 0x000001E2FF035D90>
[STEP] 调用 addView（预期在此崩溃）
Windows fatal exception: access violation

Current thread 0x00007488 (most recent call first):
  File "<REPO>\tools\krita_probe\probe7_crash.py", line 24 in <module>
  ...
```

### 3.4 崩溃证据二：预热 2.5 秒 + batchmode 后仍然崩（`probe11_addview.py`）

命令（2026-11-18 22:09）：

```bash
cd tools/krita_probe && timeout 120 '/d/program/Krita (x64)/bin/kritarunner.exe' -s probe11_addview
```

原始输出（退出码 -1073741819）：

```text
[STEP] 预热完成
[STEP] batchmode=True
[STEP] win=<PyKrita.krita.Window object at 0x0000020902F560F0>
[STEP] doc=<PyKrita.krita.Document object at 0x0000020902F55D00> topLevel=[<PyKrita.krita.Node object at 0x0000020902F56330>]
[STEP] 准备 addView
Windows fatal exception: access violation

Current thread 0x000079b4 (most recent call first):
  File "<REPO>\tools\krita_probe\probe11_addview.py", line 34 in <module>
  ...
```

另有一次（`probe4_steps.py`）与 `win.qwindow().show()` 也分别在 `addView` / `show` 处崩，
共 **3 次独立复现**（probe4、probe7、probe11），不是偶发。

### 3.5 崩溃证据三：`Node.paintLine()` 无视图调用（`probe8_paintline.py`）

命令（2026-11-18 22:05）：

```bash
cd tools/krita_probe && timeout 150 '/d/program/Krita (x64)/bin/kritarunner.exe' -s probe8_paintline
```

原始输出（退出码 -1073741819）：

```text
[STEP] doc=<PyKrita.krita.Document object at 0x000001A531205EB0> activeNode=None
[STEP] topLevelNodes=[<PyKrita.krita.Node object at 0x000001A531205D00>]
Windows fatal exception: access violation
...
```

### 3.6 崩溃证据四：无视图时导出也崩（`probe10_warm.py`）

命令（2026-11-18 22:06，先处理事件 2 秒再操作）：

```bash
cd tools/krita_probe && timeout 150 '/d/program/Krita (x64)/bin/kritarunner.exe' -s probe10_warm
```

原始输出（退出码 -1073741819）：

```text
[STEP] 预热完成 用时=2.01s
[STEP] version=5.3.3 (git 858d352)
[STEP] doc=<PyKrita.krita.Document object at 0x000001FC6ABE5D90>
[STEP] activeNode=None
[STEP] pixelData 字节=16384 alpha非零=4096
Windows fatal exception: code 0x8001010d

Current thread 0x000037d8 (most recent call first):
  File "<REPO>\tools\krita_probe\probe10_warm.py", line 46 in <module>
  ...
```

（第 46 行是 `doc.exportImage(...)`；异常码 `0x8001010d` 是 COM 的「输入同步调用中不允许外调」，
说明导出流程同样依赖 GUI 侧尚未建立起来的组件。）

### 3.7 预设资源：137 个，但没有本项目产物（`probe13_presets.py`）

命令（2026-11-18 22:11）：

```bash
cd tools/krita_probe && timeout 120 '/d/program/Krita (x64)/bin/kritarunner.exe' -s probe13_presets
```

原始输出（退出码 0）：

```text
[STEP] 预设总数=137
[STEP] 匹配大怪兽/勾线/圆头/测试用 的预设数=0
```

### 3.8 源码根因（Krita 主分支，libkis）

1. `libs/libkis/PaintingResources.cpp` 的 `createHelper()` 第一行就是：
   ```cpp
   KisView *activeView = KisPart::instance()->currentMainwindow()->activeView();
   KoCanvasResourceProvider *resourceManager = activeView->viewManager()->canvasResourceProvider()->resourceManager();
   ```
   → `kritarunner` 里 `currentMainwindow()` 为空（§3.2 `windows=0`），`->activeView()` 即空指针解引用。
2. `libs/libkis/Node.cpp`：`Node::paintLine()` → `PaintingResources::createHelper(d->image, node(), strokeStyle)`，
   走的正是上面这条路径 → **没有视图就一定崩**。
3. `libs/libkis/Window.cpp`：`Window::addView()` → `d->window->newView(document->document())`，
   `newView` 在 `kritarunner`（普通 QApplication，未完成 Krita GUI 初始化）里触发访问违例（§3.3/§3.4 实测）。
4. `libs/libkis/Resource.cpp`：`Resource::resource()` 用 `KisResourceLocator::instance()->resourceForId(d->id)`
   → Python 侧只能拿到**资源库里的**预设，没有「按 .kpp 文件路径构造 Resource」的 API。

### 3.9 其他命令行能力（排除项）

`krita.com --help`（退出码 0）里与批量出图有关的只有
`--export` / `--export-filename` / `--export-sequence` / `--resource-location`，
**没有任何「执行 Python 脚本」或「无界面画一笔」的选项**；`--export` 只能把已有文档导出图片，不能落笔。

---

## 四、能力边界（能 / 不能）

| 能力 | 结论 | 证据 |
|---|---|---|
| 在 kritarunner 里拿到 Krita 内核 | 能 | §3.2 `instance_repr` 非空 |
| 无头新建文档 | 能 | §3.2 `createDocument` + `doc_width=100` |
| 无头读写像素（`pixelData`/`setPixelData`） | 未验证成功落笔，但 `pixelData` 可读 | §3.6 `pixelData 字节=16384 alpha非零=4096` |
| 无头建立画布视图 | **不能** | §3.3/§3.4 崩溃（3 次复现） |
| 无头用笔刷预设落笔（`Node.paintLine`） | **不能** | §3.5 崩溃 + §3.8 源码依赖视图 |
| 无头导出 PNG | **不能（`exportImage` 崩）** | §3.6 异常码 0x8001010d |
| 按文件路径加载 `converted/kpp/*.kpp` | **不能（无 API）** | §3.7 + §3.8-4 |
| 无头加载「资源库里已有的」预设 | 理论可以，但本机资源库里没有本项目产物 | §3.7 匹配数 0 |

> 注意：`pixelData` 读到的 `alpha非零=4096` 是 `createDocument` 默认白底图层，**不是落笔结果**；
> 本任务**没有任何一张图可以称为「落笔成功」**，也没有产出 `tests/baselines/krita/` 下的渲染图。

---

## 五、对任务 06 / 06A 的具体影响

1. 任务 06 原验收标准（「`kritarunner.exe tools/krita_render.py ...` 能产出一张 200×200、Alpha 非零的 PNG」）
   **在当前环境无法达成**，不能把崩溃包装成通过。
2. 渲染器外壳（`tools/krita_render_runner.py`）仍有价值：它能把「kritarunner 崩溃/超时/无输出」如实转成结构化失败结果，
   为将来换成 Scripter 路线或新版 Krita 时保留同一接口。
3. 任务 06A 的批量清单不能来自本工具渲染，只能**登记历史真值图**（`research/krita结果` 23 张，Krita GUI 人工生成），
   并在清单里标明 `status="historical"`、`renderer="krita-gui-historical"`，不许写成「本工具渲染成功」。

---

## 六、两条降级路线与各自代价

### 路线 A：Scripter 半自动（在 Krita 界面里跑脚本）

做法：打开 Krita → 启用 Python 插件 → Scripter 里执行渲染脚本（此时有主窗口/视图，`Node.paintLine` 可用）
→ 导出 PNG 到 `tests/baselines/krita/`。可选进阶：写一个 pykrita 插件，Krita 启动时自动执行并退出（把「人工点 Scripter」也省掉，但仍是 GUI 进程）。

- 优点：真正用 Krita 笔刷引擎出图，结果可作基准；改动量大头在脚本本身（渲染脚本已有骨架）。
- 代价：**不是无头**，每轮渲染需要 GUI 进程；自动化插件版还要额外处理「插件安装位置」——Krita 用户资源目录（`%APPDATA%\krita`）或 `--resource-location`，
  属于仓库外写入，需要人工/提权确认；GUI 进程在无人值守时稳定性（弹窗、崩溃）需另做看护。
- 估算：脚本 + 单笔跑通 ≈ 1–2 小时；批量 19 支 + 稳定性看护再 ≈ 1–2 小时。

### 路线 B：只做算法复刻（不依赖 Krita 出图）

做法：用 `research/` 里的 Krita 源码（如 `KisTextureMaskInfo.cpp`）复刻纹理掩码算法（任务 07 已在进行），
像素级验证改用「历史真值图（23 张）+ 参数级比对」，不再新增 Krita 渲染图。

- 优点：全自动、零 GUI 依赖，CI 可跑；与任务 07/08 的现有计划吻合。
- 代价：放弃「Krita 自己画一笔」这条验证链，手感/笔刷引擎差异无法自动覆盖，最终仍需人工目检一次；
  任务 06/06A 退化为「接口占位 + 历史真值图登记」。

---

## 七、复现步骤（人工可照做）

```bash
cd <REPO>/tools/krita_probe
# 1) 环境探测（应退出码 0，打印 PROBE_JSON_BEGIN/END）
"<LOCAL_APP_DIR>/Krita (x64)/bin/kritarunner.exe" -s probe_env
# 2) 崩溃复现（应退出码 -1073741819 = 0xC0000005，stderr 有 "Windows fatal exception: access violation"）
"<LOCAL_APP_DIR>/Krita (x64)/bin/kritarunner.exe" -s probe7_crash
# 3) 预设清单（应退出码 0，预设总数=137、匹配本项目产物=0）
"<LOCAL_APP_DIR>/Krita (x64)/bin/kritarunner.exe" -s probe13_presets
```

---

## 八、未完成 / 无法验证

- 「Krita 资源库里已有预设 + 无头落笔」：无法验证，因为第一步建视图就崩（§3.3）。
- 换新版 Krita（>=5.4/6.x）是否修复：未验证（本机只装 5.3.3，不得改动安装目录）。
- `krita.exe --export` 路径：未实测（只能导出已有文档，不能落笔，与本任务目标无关）。
---

## 九、追加实验（2026-11-18 22:20–22:45）：路线 A「GUI 启动插件」**成功**

> 由 lead 追加指令执行：不再用 kritarunner，而是启动**真实 Krita GUI 进程**，用一个临时的
> PyKrita 启动插件在主窗口就绪后自动建视图、落笔、导出、关文档退出。结论：**成功**。

### 9.1 做法

1. 临时把 `bc_auto_render` 插件装进 `%APPDATA%/krita/pykrita/`，在 `%LOCALAPPDATA%/kritarc` 的
   `[python]` 段写 `enable_bc_auto_render=true`，并把 `sessionOnStartup` 临时改成 2
   （否则启动先弹「会话管理器」对话框，主窗口不创建：首次实验轮询 60 次 `windows=0`，
   诊断到可见控件为 `QDialog:Sessions` 与 `ExceptHookDialog:Script error`）。
2. 把目标 `.kpp` 临时复制进 `%APPDATA%/krita/paintoppresets/`（Krita Python API 不能按路径加载预设，必须先入库），
   启动 Krita 时导入资源库。
3. 插件轮询到主窗口后：`createDocument` → `Window.addView`（**在 GUI 里成功**）→ 删除默认白底图层、
   新建空 paintlayer（透明画布，Alpha 才反映笔迹）→ 资源库按 `.kpp` 文件名匹配预设 →
   `View.setCurrentBrushPreset` → `Node.paintLine` → `Document.projection()` 存 PNG → 关文档 → 退出。
4. 会话结束 `finally`：还原 kritarc、还原 resourcecache.sqlite、删除临时 .kpp、删除插件、清理残留 krita.exe。

### 9.2 原始证据（退出码 0，约 11 秒/支）

```text
[22:33:01] 插件模块加载，注册 Extension
[22:33:04] BEGIN size=200x200 stroke=120 pressure=1.00 preset_file='003_大怪兽-圆头虚_用数位板.kpp' hint=''
[22:33:05] addView=<PyKrita.krita.View object at 0x...> activeView=<PyKrita.krita.View object at 0x...>
[22:33:05] 移除默认图层 name='Background' type=paintlayer
[22:33:05] 落笔前 alpha_nonzero=0
[22:33:05] 资源库预设数=342
[22:33:05] 选用预设 name='大怪兽-圆头虚【用数位板】' filename='003_大怪兽-圆头虚_用数位板.kpp'
[22:33:05] 导出 saved=True alpha_nonzero=3348
[22:33:05] 文档已关闭（不保存）
```

外壳 JSON（节选）：`"ok": true, "route": "gui", "elapsed_s": 10.995, "alpha_nonzero": 3348`。

### 9.3 清理核对（实测）

```text
插件已清理
临时kpp已清理
0                      # kritarc 中 enable_bc_auto_render 出现次数
sessionOnStartup=0     # 已还原为原值
```

### 9.4 结论与代价

- `addView`/`paintLine` 在**真实 GUI 会话**里完全可用 → 路线 A 把「Krita 出图」从「不可做」变成「可做」；
- 代价：不是无头（Krita 会建真实窗口，需要交互式桌面会话）；单支 11–14 秒，六支批量约 1–2 分钟；
  运行期间临时改 kritarc/资源缓存，正常路径由 `finally` 还原（9.3 已核对）。
- 失败路径（本轮实验中出现过）：上一轮 `krita.exe` 没退干净时，新实例可能秒退/不加载插件 →
  外壳已加「等残留退出 + 失败重试一次」；另有会话管理器对话框需自动关闭。
