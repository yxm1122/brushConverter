# Krita 渲染说明（任务 06 · 路线 A：GUI 启动插件 · 真实渲染）

> 说明：本文档里的本机实测路径已占位化（`<LOCAL_APP_DIR>` = 本机软件安装目录，`<REPO>` = 仓库根），复现时请改成你自己的安装路径与仓库路径。

> 对应代码：
> - `tools/krita_render_gui_plugin.py`（PyKrita 启动插件，运行时临时装到 Krita 用户插件目录）
> - `tools/krita_render_runner.py`（conda 侧外壳：装/卸插件、启动 Krita、超时控制、结果解析、配置还原）
> - `tools/krita_render.py`（kritarunner 无头路线的脚本，保留作失败证据与回退）
> - 单测：`tests/test_krita_render.py`（`python -m pytest -m verify` 自动收集）

## 0. 当前状态（2026-11-18 22:33 实测）

| 路线 | 状态 | 说明 |
|---|---|---|
| A：GUI 进程 + PyKrita 启动插件 | **可用（真实渲染）** | 单支约 11–14 秒（含 Krita 启动），实测 6 支全部产出 200×200、Alpha 非零的 PNG |
| kritarunner 无头 | **不可用（保留证据）** | `Window.addView` 触发 `0xC0000005`，`Node.paintLine` 依赖视图 → 无头落笔不可能；见 `docs/研究报告/krita-runner-可行性.md` |

**结论口径**：本项目的 Krita 出图**不是无头的**，而是「无人值守的 GUI 会话」——Krita 真实主窗口会闪一下，
插件在真实视图里落笔、导出、关文档退出；全程无人工点击。kritarunner 路线仅用于复现失败证据。

## 1. 参数含义

| 参数 | 含义 | 默认/约束 |
|---|---|---|
| `--preset` | .kpp 预设路径（必填） | 相对路径会被外壳转绝对路径 |
| `--out` | 输出 PNG 路径（必填） | 目录不存在时自动创建 |
| `--size` | 画布尺寸，形如 `200x200` | 必填；默认 `200x200` |
| `--stroke` | 笔划长度（像素） | 默认 120；横向一笔 |
| `--pressure` | 压感 0–1 | 默认 1.0；起止同值 |
| `--route` | `gui`（默认）/ `headless`（证据）/ `auto`（先 gui 再 headless） | — |
| `--timeout` | 单次超时秒数 | GUI 默认 180，headless 默认 120 |

## 2. 运行方式

```bash
# 单笔真实渲染（约 11–15 秒）
PYTHONIOENCODING=utf-8 "<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe" \
    tools/krita_render_runner.py \
    --preset "converted/kpp/003_大怪兽-圆头虚_用数位板.kpp" \
    --out "tests/baselines/krita/one.png"

# 复现 kritarunner 无头失败（门结论证据）
... tools/krita_render_runner.py --route headless --preset "converted/kpp/003_大怪兽-圆头虚_用数位板.kpp" --out /tmp/x.png
```

外壳退出码：0=成功，2=失败（失败原因在 JSON 的 `reason`，完整过程在 `log_tail`）。

## 3. GUI 路线怎么工作（以及临时改动怎么还原）

1. 备份 `%LOCALAPPDATA%/kritarc` 与 `%APPDATA%/krita/resourcecache.sqlite` 到临时目录；
2. 临时把 `bc_auto_render` 插件装进 `%APPDATA%/krita/pykrita/`（`__init__.py` + `.desktop`），
   并在 kritarc 的 `[python]` 段临时写 `enable_bc_auto_render=true`、把 `sessionOnStartup` 临时改成 2
   （否则会先弹「会话管理器」对话框，主窗口不创建——实测 60 次轮询 `windows=0`）；
3. 把目标 `.kpp` 临时复制进 `%APPDATA%/krita/paintoppresets/`，让 Krita 启动时把它导入资源库
   （Krita 的 Python API **没有**按路径加载 `.kpp` 的入口，必须先入库）；
4. `subprocess` 启动 `krita.com --nosplash`（超时 180 秒）；插件轮询到主窗口后：
   建文档 → `Window.addView` 建视图 → **删除默认白底图层、新建空 paintlayer**（保证 Alpha 真实反映笔迹）
   → 资源库里按 `.kpp` 文件名匹配预设 → `View.setCurrentBrushPreset` → `Node.paintLine` →
   `Document.projection()` 存 PNG → 统计 Alpha 非零像素 → 写结果 JSON → 关文档（不保存）→ 退出；
5. `finally` 中一律：还原 kritarc、还原 resourcecache.sqlite、删除临时 `.kpp`、删除插件与 desktop、
   杀掉本次新起的残留 `krita.exe`。**不留任何持久改动**（已实测核对：插件目录无残留、kritarc 无
   `enable_bc_auto_render`、`sessionOnStartup` 恢复为 0）。

## 4. 超时与判定口径

- **超时**：GUI 路线 180 秒（实测 11–14 秒即可完成）；超时即失败，外壳会杀掉「本次新起的」krita.exe，
  不会误杀用户自己开着的 Krita。
- **判定成功**必须同时满足：插件写的结果 JSON `ok=true` + PNG 存在 + Alpha 非零像素数 > 0。
  **不看进程退出码**（kritarunner 实测不透传退出码；GUI 进程退出路径也可能被兜底强杀）。
- 失败时 `reason` 已翻译成人话；`log_tail` 是插件日志尾部，能定位到「卡在哪一步」。

## 5. 幂等性（实测）

同一预设（`003_大怪兽-圆头虚`）、同一参数连跑两次：

- PNG 尺寸一致（200×200），Alpha 非零像素数一致（3348）；
- 逐像素比较：**RGB 通道完全一致（差 0）**；Alpha 通道最大差 **4–6**（255 级中约 2%），
  差异只出现在笔迹边缘——来源是 Krita 笔刷动力学的随机种子/亚像素抗锯齿，应用层关不掉；
- 单测按 **tol=10** 判定「确定性」（`tests/test_krita_render.py::test_two_runs_alpha_identical_within_tolerance`），
  并同时断言 RGB 完全一致，不做「零差异」的假承诺。

## 6. 失败排查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| `插件未写结果文件` + log 为空 | 上一轮 Krita 没退干净 / kritarc 未生效 | 外壳已加「等上一轮退出 + 失败重试一次」；仍失败时看 `%APPDATA%/krita/pykrita` 是否有残留 |
| 结果 `stage=window` | 会话管理器/异常对话框挡住主窗口 | 插件会自动关闭 `Sessions`/`ExceptHookDialog` 对话框 |
| 结果 `stage=preset` | 临时 .kpp 没被资源库导入 | 确认文件在 `paintoppresets` 且 Krita 启动扫描完成 |
| `alpha_nonzero=0` | 画布没清背景/笔刷没落笔 | 插件已强制清空默认背景层；仍为 0 视为失败 |
| 进程超时 | 桌面会话不可用或弹窗阻塞 | 外壳强杀并报 timeout；检查是否有交互式桌面 |

## 7. 为什么不能只用 kritarunner

见任务 04 报告与研究报告：kritarunner 里 `windows=0 / activeWindow=None`，
`Window.addView()` 访问违例（`0xC0000005`，3 次复现），而 `Node.paintLine()` 在 Krita 源码里
硬依赖 `KisPart::instance()->currentMainwindow()->activeView()`。所以「无头落笔」在本机 Krita 5.3.3 上不成立，
路线 A 用真实 GUI 会话绕开这一限制。

## 8. 人工验收步骤

```bash
# 1) 单笔渲染（应 ok=true，alpha_nonzero>0，约 12 秒）
PYTHONIOENCODING=utf-8 "<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe" \
    tools/krita_render_runner.py --preset "converted/kpp/003_大怪兽-圆头虚_用数位板.kpp" --out tests/baselines/krita/_check.png
# 2) 单测（真实断言 + 门结论证据）
"<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe" -m pytest tests/test_krita_render.py -v
# 3) 清理检查（应无输出/0）
ls "$APPDATA/krita/pykrita" | grep bc_auto_render
grep -c enable_bc_auto_render "$LOCALAPPDATA/kritarc"
```

## 9. 已知限制

1. 需要**交互式桌面会话**（GUI 进程会创建真实窗口；无人值守服务器上不适用）。
2. 同一时刻只能跑一个渲染（共用 kritarc/插件目录），批量工具是串行的。
3. 运行期间会短暂修改 kritarc 与资源缓存，异常断电等极端情况下可能残留——正常路径由 `finally` 还原，
   报告里给出了清理核对命令。
4. 预设必须先进 Krita 资源库：外壳用「临时复制 .kpp + 启动导入 + 结束后删除」实现，不污染用户长期配置。
