# 任务 11A：逆向动态追踪调用链（Photoshop 纹理亮度 / 对比度 / 深度 / 混合模式）

> **降级说明（第一行按任务书硬性要求 4 写）**：本机**无 x64dbg**（§1.1 有原始命令与输出）。
> 但本任务**没有降级为「仅静态 + Ghidra 调试器尝试」**——改用 REA（rea-agents 6.9.0）内置的 **Frida 通路**，
> 对 `Photoshop.exe` 建立了真正的运行期插桩，拿到了「同一条指令在不同参数下写入的运行期对象值」。
> 因此本报告的结论置信度**高于**任务书降级预案里的「仅静态」。仍**未达成**的部分见 §5（像素消费端未定位）。

> 执行者：re-dyn-line｜执行日期：2026-11-18｜目标：`<LOCAL_APP_DIR>\PS2025\Adobe Photoshop 2025\Photoshop.exe`
> 全程**只读**：未修改 PS 安装目录任何文件、未写注册表、未改持久设置、未注入恶意代码；
> 所有插桩都是「Interceptor 挂接 + 内存读取」，**没有任何写目标内存或调用目标函数**的动作；
> 结束时 Frida 会话已显式关闭、PS 进程已终止（§1.6 有前后对照）。

---

## 一、环境探测结果

### 1.1 x64dbg：确认未安装（原始输出）

~~~
$ ls -d /d/program/x64dbg* /d/program/*dbg* 2>&1
ls: cannot access '/d/program/x64dbg*': No such file or directory
ls: cannot access '/d/program/*dbg*': No such file or directory

$ find /d/program -maxdepth 2 -iname 'x64dbg*' 2>/dev/null
(end)          # 无任何结果

$ which x64dbg x32dbg 2>&1
which: no x64dbg in (...)
which: no x32dbg in (...)
~~~

结论：**x64dbg 未安装**（与任务 11 报告一致）。因此任务书里的「硬件读断点 `ba r4 [rdi+0x18]`」在本机不可用，
必须用等效手段（见 1.2）。

### 1.2 REA 自带动态能力：Frida 可用（原始返回）

`mcp__rea__list_frida_devices`：

~~~json
{"devices":[
  {"device_id":"local","name":"Local System","type":"local"},
  {"device_id":"socket","name":"Local Socket","type":"remote"},
  {"device_id":"barebone","name":"GDB Remote Stub","type":"remote"}
],"cleanup_error":null}
~~~

即：**本机不需要额外安装 frida，REA 已带可用通路**。据此实际使用的动态工具链：

| 工具 | 实际作用 | 本次是否用上 |
|---|---|---|
| `list_frida_devices` / `list_frida_processes` | 发现本地进程 | ✅ |
| `start_frida_session`（attach + pid） | 把 Frida 附到已运行的 PS | ✅ 共 6 次 |
| `load_frida_script`（source_kind=file） | 载入自写插桩脚本 | ✅ |
| `frida_session_status` | 取回插桩脚本 `send()` 的消息（= 原始观测值） | ✅ |
| `close_frida_session` | 干净脱离 | ✅ |
| `instrument_with_frida` | 定长观测窗口（`duration_ms` 上限 6×10⁴ ms） | ❌ 未用（实验跨度 > 60 s，改用持久会话） |

**需要更正的预期**：任务下达时提到的 `trace_native_values` / `trace_feature` / `trace_call_path` / `trace_native_ui_action`
经查是**静态值流/静态调用图**类工具（描述里写明 "static value dependencies"、"static using nodes"），
它们**不是调试器**，抓不到运行期寄存器。任务下达说明里的 `run_bytes` 在本机工具表里**不存在**（只有 `read_bytes`）。
真正能「附身 + 取运行期寄存器」的只有 Frida 那一组，本报告走的就是这组。

### 1.3 REA 静态分析 provider（原始返回摘要）

`mcp__rea__binary_session` 的 `analysis_provider_candidates`：

| provider | availability | 说明原文 |
|---|---|---|
| ghidra | **available** | — |
| hopper | unavailable | `Hopper integration is not supported on win32.` |
| ida | unavailable | `REA configuration is invalid: Set REA_IDA_MCP_CONFIG …` |
| kuna | unavailable（后续项） | 同上为未配置 |

### 1.4 为什么没有走「Ghidra 自带调试器」

任务书降级预案写的是「仅用 Ghidra 自带调试器尝试」。本任务**没有**走这条路，理由有三条，都可复核：

1. 任务 11 已实测：对 `Photoshop.exe` 建库 + 自动分析耗时 **1,744.9 秒（29.1 分钟）**且在 1500 秒上限被强制中断
   （`docs/研究报告/ps-逆向-静态.md` §9.3），本机分析期间空闲内存 5.5～6.7 GB、页面文件余量 0.55 GB。
   再开 Ghidra 调试会话等于把 29 分钟的开销再付一次，且大概率 OOM。
2. Frida 通路**不需要任何静态分析前置**，2 分钟就能建立（本次实测：PS 冷启动 10 s + 附身 1 s + 下钩 < 1 s）。
3. REA 的 Ghidra provider 走 MCP 单次响应 10 MiB + 宿主 deadline 约束，对 198 MB PE 必然超时（任务 11 §9.4 已记录原始报错）。

### 1.5 建立动态通路时踩的 5 个坑（**原始报错原文**，这是本任务最耗时的部分，写下来给后续任务省时间）

| # | 现象（原始报错） | 根因 | 修法 |
|---|---|---|---|
| ① | `setOpts (executeAction setd) : 非法参数 line 25` | 自写 JSX 里把 `d.putObject(cid("T   "), sid("currentToolOptions"), o)` 误写成 `cid("currentToolOptions")`；`charIDToTypeID` 只接受 4 字符 ID，19 字符直接抛「非法参数」，而报错行号指向 `function cid(s)` 那一行，极难定位 | 改回 `sid(...)`；并加「逐条 charID/stringID 自检」日志（本次 33 个 ID 全部 `ok`，见附件 `11A-runtime-driver.log` 首段） |
| ② | `RangeError: invalid array index`（脚本第 130 / 144 / 157 行） | Frida 的 `onEnter(args, context)` 被我写成 `function (ctx)`，于是 `ctx` 其实是 **args 数组**，`ctx.rdi` 拿数组当下标 | 明确写成 `function (args, ctx)` |
| ③ | `TypeError: cannot read property 'rdi' of undefined` | 本机这版 Frida 的 `Interceptor.attach` 回调**只传了 1 个实参**（`argc: 1`，实测），第二形参恒为 `undefined` | 改用约定接口 `this.context`（实测 `hasThisCtx: true` 可用）；脚本里 `cpuOf(this, ctx)` 双路兼容 |
| ④ | Frida 侧读步骤标记文件恒为空（`step: null`） | `File` 在构造函数里就打开文件；`read()` 需要显式长度 | 去掉多余的 `open()`、改 `read(4096)`；**仍未能读通**（Windows 路径下 Frida File 读文本未成功）→ 连带后果见 §3.1 与 §5.2 |
| ⑤ | `MemoryAccessMonitor` 打开成功但 **0 次访问** | 见 §5.1（未定位，如实记录） | 未解决 |

### 1.6 收尾状态（干净脱离 + PS 安装目录未动）

~~~
# 前置（开工前，2026-11-18 23:03）
-rwxr-xr-x 1 <user> 197121 208456176 2026-01-30 14:13:26.918235300 +0800 .../Photoshop.exe

# 收尾（2026-11-18 之后）
-rwxr-xr-x 1 <user> 197121 208456176 2026-01-30 14:13:26.918235300 +0800 .../Photoshop.exe
05720af2cd207ead737b4c9ff422ffb7191f2d65cc0045b88735030cda76c953  .../Photoshop.exe
drwxr-xr-x 1 <user> 197121 0 2026-11-03 19:39:03.439630400 +0800  "<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025"

$ tasklist /FI "IMAGENAME eq Photoshop.exe"      # 收尾
信息: 没有运行的任务匹配指定标准
~~~

- **PS 安装目录 mtime 未变**（仍是 2026-01-30 14:13:26.918235300），大小 208,456,176 字节，SHA-256 与任务 11 记录的
  `05720af2…c953` **完全一致**。
- **进程列表无残留调试会话**：6 次 Frida 会话全部 `close_frida_session`；PS 进程已终止。
- 插桩脚本只做 `Interceptor.attach` + 内存读取；PS 进程结束后 hook 自动消失，**无持久化**（不写注册表、不改文件）。

---

## 二、实验序列（核心资产，必须可复现）

### 2.1 判据（先写死，避免结论倒推）

- 某候选在**改变亮度/对比度**时，其在**同一条指令**上产生的运行期值随之变化，且与界面/描述符请求值有**稳定的逐位对应关系** → 记 **证实**。
- 完全不随参数变化（或变化与本组参数无关） → 记 **排除**，并说明它处理的是别的路径。
- 抓不到 / 通路失败 → 记 **未判定**，附原始报错与已试过什么。

> 重要边界：本任务能证实的是**「参数以什么形态进入运行期对象」**（参数层）。
> **「像素公式用这个对象算什么」**（像素层）需要另外的证据 —— 见 §5.1，本任务**未判定**。

### 2.2 参数矩阵（14 组，覆盖单变量 + 极值 + clamp 边界 + 4 个混合模式）

| 步 | brightness | contrast | depth(%) | textureBlendMode | 意图 |
|---|---|---|---|---|---|
| 01 | 0 | 0 | 100 | Mltp（正片叠底） | 基线 |
| 02 | **+50** | 0 | 100 | Mltp | 只改亮度 |
| 03 | **−50** | 0 | 100 | Mltp | 只改亮度 |
| 04 | **+150** | 0 | 100 | Mltp | 亮度上极值 |
| 05 | **−150** | 0 | 100 | Mltp | 亮度下极值 |
| 06 | 0 | **+50** | 100 | Mltp | 只改对比度 |
| 07 | 0 | **−50** | 100 | Mltp | 只改对比度 |
| 08 | 0 | **+100** | 100 | Mltp | 对比度上极值 |
| 09 | 0 | **−100** | 100 | Mltp | 对比度下极值 |
| 10 | 0 | 0 | **60** | Mltp | 只改深度 |
| 11 | 0 | 0 | **101** | Mltp | 深度越界（静态预测被 clamp 到 100） |
| 12 | 0 | 0 | 100 | **linearHeight**（stringID） | 换混合模式 |
| 13 | 0 | 0 | 100 | **Hght**（charID） | 换混合模式 |
| 14 | 0 | 0 | 100 | **Sbtr**（charID） | 换混合模式 |

其余参数在 14 组里**保持恒定**（这是能横向比较的前提）：
画布 100×100 / 72dpi / 透明；纹理图案 = `tests/baselines/ps/_work/texture_noise64.png`（64×64 噪声，任务 05/10/12 同款）；
纹理缩放 100%；笔尖 直径 300px、硬度 100%、间距 1000%；不透明度 100%、流量 100%；
`useTipDynamics / useScatter / smoothing` 全关；前景色纯黑。

### 2.3 时序设计（「先插桩、后改参数」的握手）

这是本次能抓到值的关键。若先改参数再附身，只能抓到结论态、抓不到变化过程。

~~~
PS 启动（-r 驱动脚本）
   └─ 定义纹理图案 → 选中画笔工具 → 写 ready.txt
                                    │
外部：等 ready.txt 出现 ─────────────┘
外部：Frida attach(pid) → load 插桩脚本 → 四个钩子就位
外部：插桩脚本自己写 go.flag（**放行信号由插桩方发出**，保证钩子先就位）
PS ：轮询到 go.flag → 进入 14 步循环
       每步：写 step.txt(begin) → 写参数 → 读回 → 打两笔（触发像素合成）→ 写 step.txt(painted) → sleep 2500ms
   └─ 写 done.txt → app.quit()
~~~

- 每步之间 **2500 ms**，给插桩侧足够时间在同一参数下多次命中；
- 每步**打两笔**（两条 0.4px 的路径描边），确保「纹理 → 掩码 → 像素」链路真的跑起来，而不是只改参数不渲染；
- 驱动脚本每一步的子操作都单独 `try/catch` 并记录「消息 + 行号」，出错能一眼定位（§1.5-① 就是这么定位的）。

### 2.4 逐步界面/脚本操作（人工可对着 GUI 复演）

脚本走的是 **Action Manager（描述符）**通路，等价于界面上的下列操作（每一步都只改一个参数）：

1. 打开 PS → 打开 `texture_noise64.png` → 菜单「编辑 > 定义图案」，命名 `bc_texture_noise64_dyn` → 关闭纹理图（不保存）。
2. 工具栏选**画笔工具**（B）→ 前景色设为黑。
3. 打开「画笔设置」面板 → 勾选**纹理** → 图案选 `bc_texture_noise64_dyn` → 缩放 100%。
4. 打开「画笔」主面板 → 直径 300px、硬度 100%、间距 1000%、不透明度 100%、流量 100%。
5. 关闭「传递/散布/平滑」（脚本里是 `useTipDynamics=false / useScatter=false / smoothing=false`）。
6. 按 §2.2 的表逐步改「纹理」区的**亮度**/**对比度**/**深度**/**模式**；每改一次新建 100×100 画布打一笔。
   —— 这四步在界面上分别是「纹理 > 亮度」「纹理 > 对比度」「纹理 > 深度」「纹理 > 模式」四个输入框/下拉。
7. 每步结束关闭文档（不保存），进入下一步。

> 脚本复用任务 05 已验证的写法：`putClass(sid("paintbrushTool"))` + `putObject(cid("T   "), sid("currentToolOptions"), o)` + `executeAction(cid("setd"))`；
> 纹理模式必须**分两阶段**写（先写亮度/对比度/深度，再写模式），否则 `textureBlendMode` 键还不存在。

### 2.5 启动与插桩命令（原文）

~~~bash
# 启动 PS + 驱动脚本（后台任务，避免 shell 等 PS 退出）
"<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe" \
  -r "<REPO>/tools/re_pipeline/ps_dyn_driver.jsx"
# 附身（REA）：start_frida_session{mode:"attach", device_id:"local", pid:<Photoshop.exe 的 pid>}
# 插桩（REA）：load_frida_script{session_id, source_kind:"file",
#               path:"<REPO>/tools/re_pipeline/ps_dyn_hook.js"}
# 取观测（REA）：frida_session_status{session_id}
# 脱离（REA）：close_frida_session{session_id}
~~~

### 2.6 复现步骤（3 条命令 + 1 次附身）

~~~bash
# 0) 清空工作目录（工作目录在仓库外，避免污染仓库）
rm -rf %USERPROFILE%/AppData/Local/Temp/bcdyn && mkdir -p %USERPROFILE%/AppData/Local/Temp/bcdyn

# 1) 启动 PS 跑实验驱动（后台）
"<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe" -r "<REPO>/tools/re_pipeline/ps_dyn_driver.jsx" &

# 2) 等 .../bcdyn/ready.txt 出现后，用 REA 附身 + 载入 ps_dyn_hook.js（它会自己写 go.flag 放行）
# 3) 等 .../bcdyn/done.txt 出现，用 frida_session_status 取回全部 send() 消息
~~~

观测点（RVA 相对模块基址；本次实测模块基址 `0x7ff672020000`，模块大小 218,800,128 字节）：

| 标签 | VA | RVA | 装的是什么 | 取到的是什么 |
|---|---|---|---|---|
| H1-entry | 0x141a939d5 | 0x1a939d5 | 描述符 → 对象（亮度/对比度组） | `rdi`=目标对象、`r14`=描述符、调用栈 |
| **H1 真入口** | **0x141a937e0** | 0x1a937e0 | 同上（**任务 11 报告写的 0x141a939d5 是内部地址**，见 §3.5） | 静态调用者 + 动态调用栈吻合 |
| H1-snapshot | 0x141a93a79 | 0x1a93a79 | 五个字段写完后（`add rsp,0x50` 之前） | `[rdi+0x10/+0x18/+0x1c/+0x20/+0x21]` |
| H3-entry | 0x141a935f0 | 0x1a935f0 | 描述符 → 对象（深度/模式组） | `rcx`=目标对象、`rdx`=描述符 |
| H3-snapshot | 0x141a9379b | 0x1a9379b | 深度/最小深度/模式写完后 | `[rbx+0x00/+0x01/+0x04/+0x08/+0x0c]` |

---

## 三、结论与原始观测表

### 3.1 结论总表（三档，逐候选）

| # | 候选 | 结论 | 依据（可复核） |
|---|---|---|---|
| H1 | 亮度/对比度组读取点（真入口 **0x141a937e0**，任务 11 报告写的 0x141a939d5 是其**内部地址**） | **证实** | 14 组参数下，对象 `+0x1c`(brightness) 观测值 = 请求值（0/+50/−50/+150/−150，**逐位相同、无比例无偏移无 clamp**）；`+0x18`(contrast) 观测值 = 请求值（0/+50/−50/+100/−100，同样逐位相同、无 clamp）；`+0x10`(textureScale) 恒为 double 100.0。表 A、表 C |
| H3 | 深度/混合模式组读取点 0x141a935f0 | **证实** | depth 观测 100 → **60** → **100**（请求 101 被存成 100，动态复现了静态预测的 clamp）；mode 观测 **7 / 41 / 40 / 39** 随 Mltp / linearHeight / height / Sbtr 变化且与 JSX 读回名一一对应。表 B |
| H2 | 写侧 0x141a96230 | **未判定** | 本轮未对其下钩（时间预算用于 H1/H3 + 消费端探路）。静态证据仍成立，但本任务没做动态复核。 |
| H4 | TransferMode 注册表构造 0x141b94750 | **未判定** | 本轮未下钩。任务 11 静态判断它只在启动期执行一次；本实验附身时 PS 已启动完毕，必然错过。**不能**据此写「排除」，只能说本方法抓不到。 |
| M1/M2/M3/M4/M5、L1～L6 | 其余 11 个候选 | **未判定** | 本轮未下钩，如实标注。 |
| **像素消费端**（真正拿 brightness/contrast 算像素的函数） | —— | **未判定** | §5.1：页级内存访问监控已成功打开但观测到 **0 次访问**，因此**既不能证实也不能排除**该对象被像素代码读取。 |
| 结构体偏移本身的成立 | 任务 11 静态结论 | **证实（动态复核）** | `+0x18`=contrast、`+0x1c`=brightness、`+0x10`=scale(double)、`+0x20`/`+0x21`=两个 bool；H3 侧 `+0x00`=useTexture、`+0x01`=coverage、`+0x04`=模式号、`+0x08`=depth、`+0x0c`=minimumDepth。表 A/B |

### 3.2 观测表 A —— H1 目标对象的亮度/对比度（**这是本任务最硬的一张表**）

- 观测方式：Frida `intercept` 到 H1 内部之后，对 `rdi` 指向对象做 **200 ms 轮询采样**（`sampleH1`）。
- 为什么用轮询而不是固定断点：contrast 的写入发生在 H1 末尾的**尾跳**（`0x141a93a81 jmp 0x142c4e820`）里，
  固定快照点 `0x141a93a79` **早于**该写入 8 字节，因此快照表 `snapH1` 里 contrast 恒为上一次的值；
  轮询在写入完成后采样，才能拿到 contrast 真值。（这一点本身也是「H1 的最后一条指令就是写 contrast」的旁证。）
- 对象地址（一次运行内稳定）：`0x2678f9641d0`。模块基址 `0x7ff672020000`。

| 序 | 观测到的 `+0x10` | `+0x18` contrast | `+0x1c` brightness | `+0x20` invert | `+0x21` protect | 对应步（§2.2） | 请求值 |
|---|---|---|---|---|---|---|---|
| 1 | 100.0 | 0 | **0** | 0 | 0 | 01 | b=0 c=0 |
| 2 | 100.0 | 0 | **+50** | 0 | 0 | 02 | b=+50 |
| 3 | 100.0 | 0 | **−50** | 0 | 0 | 03 | b=−50 |
| 4 | 100.0 | 0 | **+150** | 0 | 0 | 04 | b=+150 |
| 5 | 100.0 | 0 | **−150** | 0 | 0 | 05 | b=−150 |
| 6 | 100.0 | **+50** | 0 | 0 | 0 | 06 | c=+50 |
| 7 | 100.0 | 0 | 0 | 0 | 0 | 06→07 过渡（单次刷新的中间态） | — |
| 8 | 100.0 | **+50** | 0 | 0 | 0 | 06 稳定态复现 | c=+50 |
| 9 | 100.0 | **−50** | 0 | 0 | 0 | 07 | c=−50 |
| 10 | 100.0 | **+100** | 0 | 0 | 0 | 08 | c=+100 |
| 11 | 100.0 | **−100** | 0 | 0 | 0 | 09 | c=−100 |
| 12 | 100.0 | 0 | 0 | 0 | 0 | 10 起（深度/模式步，亮度对比度回到 0） | b=0 c=0 |

**判读（只写观测支持的）**：

1. brightness 的 5 个取值（0 / +50 / −50 / +150 / −150）与请求值**逐位相同**：int32、有符号、**没有**被映射成 0..100、
   **没有**被 clamp 到更窄区间（±150 原样进入对象）。
2. contrast 的 5 个取值（0 / +50 / −50 / +100 / −100）同样**逐位相同**、**无 clamp**（±100 原样进入对象）。
3. `+0x10` 恒为 double `100.0`，与请求的纹理缩放 100% 一致 → 该槽位的类型（double）与语义（百分比数值）被动态确认。
4. `+0x20` / `+0x21`（invertTexture / protectTexture）恒 0，与实验里从未开启反相/保护一致 → 偏移正确、没有串位。

> 与任务 11 §「六·补」的 4 条锚点对照：任务 10 的 JSX 实测「brightness 接受 −150..150、contrast 接受 −100..100，
> 原样不钳制」在本表里被**运行期对象**逐位复核 —— 不再只是「界面接受」，而是「PS 内部就是这么存的」。

### 3.3 观测表 B —— H3 目标对象的深度/模式（clamp 与模式号）

- 观测方式：Frida 钩 `0x141a9379b`（H3 内所有写入之后），读 `rbx` 指向对象（函数入口 `mov rbx, rcx`）。
- 对象地址（一次运行内稳定）：`0x2678f964770`。

观测到的 28 条快照（`snapH3`）**按发射顺序**完整列在下面，不做任何挑选或省略：

| 实测序号 | `+0x00` useTex | `+0x01` cov | `+0x04` 模式号 | `+0x08` depth | `+0x0c` minDepth | 对应步（§2.2） | 请求值 | JSX 读回的模式名 |
|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 1 | **40** | 100 | 0 | 附身前遗留态（不是本实验设置的） | — | — |
| 2～18（17 条） | 1 | 1 | **7** | 100 | 0 | 01～09 | Mltp | multiply |
| 19～20 | 1 | 1 | **7** | **60** | 0 | 10 | d=**60** | multiply |
| 21～23 | 1 | 1 | **7** | **100** | 0 | 11 | d=**101** | multiply |
| 24～25 | 1 | 1 | **41** | 100 | 0 | 12 | linearHeight | linearHeight |
| 26～27 | 1 | 1 | **40** | 100 | 0 | 13 | Hght（height） | height |
| 28 | 1 | 1 | **39** | 100 | 0 | 14 | Sbtr（subtract） | subtract |

**判读**：

1. **深度 clamp 被动态复现**：请求 101 → 对象 `+0x08` 观测为 **100**。这与 H3 指令序列完全对应：
   ```
   0x141a936b3 test eax, eax
   0x141a936b5 jns 0x141a936bb
   0x141a936b7 mov eax, esi          ; esi = 0  → 负数截到 0
   0x141a936bb cmp eax, r15d         ; r15d = 0x64 = 100
   0x141a936be cmovg eax, r15d       ; > 100 → 100
   0x141a936c2 mov dword ptr [rbx + 8], eax
   ```
   即 `depth = clamp(round(value), 0, 100)`。**证实**（静态指令 + 动态取值双向一致）。
2. **运行期模式号是第三套编号**：multiply=7、linearHeight=41(0x29)、height=40(0x28)、subtract=39(0x27)。
   这与任务 11 静态报告里的 TransferMode 注册表编号（`linearHeight=0x2A`、`height=0x29`、`hardMix=0x1E` …）
   **不是同一套**。原因在指令层就能看出来：H3 解析模式时走的是
   `0x141a93651 lea rdx,"textureBlendMode" ; call 0x141b94090` → `0x141b94090` 再查注册表，
   而 `[rbx+4]` 落的是**解析结果**（另一套编号）。**这一点直接影响 11B/13 的枚举表，见 §4 假设 5。**
3. `useTexture=1`、`coverage=1` 恒成立，与实验里「勾选纹理、覆盖率 100%」一致 → 偏移正确。
4. `minimumDepth` 恒 0，与实验里从未设置「最小深度」一致。

### 3.4 观测表 C —— 命中记录与调用栈（含「静态扫不到」的间接调用被补出）

原始消息（附件 `11A-frida-run5-sampling.json`，n=8/9/12 三条）：

~~~json
{"t":"diag","argc":1,"hasThisCtx":true,"rdi":"0x2678f9641d0","n":8}
{"t":"H1-hit","step":null,"rdi":"0x2678f9641d0","rax":"0x255","rbx":"0x255","r14":"0xf9dccfb220",
 "bt":["Photoshop.exe+0x1ef2218","Photoshop.exe+0x2c4f344","Photoshop.exe+0x2732fb9",
       "Photoshop.exe+0x27330ad","Photoshop.exe+0x164e627"],"n":9}
{"t":"H3-hit","step":null,"rcx_struct":"0x2678f964770","rdx_desc":"0xf9dccfb220","rbx":"0x7ff67d8a43f0",
 "bt":["Photoshop.exe+0x1ef2227","Photoshop.exe+0x2c4f344","Photoshop.exe+0x2732fb9",
       "Photoshop.exe+0x27330ad","Photoshop.exe+0x164e627"],"n":12}
~~~

| 观测项 | H1 | H3 | 说明 |
|---|---|---|---|
| 目标对象 | `rdi = 0x2678f9641d0` | `rcx = 0x2678f964770` | **两个不同的堆对象**（本次运行相差 +0x5A0，另一次运行相差 +0x460 → 不是固定子偏移，是两个独立分配） |
| 输入描述符 | `r14 = 0xf9dccfb220` | `rdx = 0xf9dccfb220` | **同一个描述符对象**先喂 H1 再喂 H3 → 两者读的是同一份笔刷预设（这就是「同一调用簇」的动态证据） |
| 返回地址 | `Photoshop.exe+0x1ef2218` | `Photoshop.exe+0x1ef2227` | 两条路径的上一级完全相同 |
| 上层调用栈 | +0x2c4f344 → +0x2732fb9 → +0x27330ad → +0x164e627 | 同左 | 说明处在同一条装载/渲染链路 |

把这几个返回地址回代到静态反汇编，得到一条**静态与动态互相印证**的结论（§3.5）。

### 3.5 对任务 11 报告的一处**勘误**（动态追踪的直接收获）

任务 11 把 `0x141a939d5` 记为 H1 的「函数地址」，但它**不是函数入口**，而是函数内部的一条指令。证据链三条：

1. **函数体自相矛盾**：从 0x141a939d5 开始反汇编，函数里既有 `add rsp,0x50 / pop r14 / pop rdi / pop rbx`，
   却没有任何对应的 `push rdi/r14/rbx` 与 `sub rsp,0x50` —— 说明它是半截。
2. **动态返回地址**：H1 返回地址 = `0x1ef2218`，回代 `0x141ef2218` 处的指令是 `mov rdx, rdi`，
   其**前一条** `0x141ef2213` 才是 `call`。
3. **静态调用者**：`native_xref.py callers --funcs 0x141a937e0` 找到 4 个直接调用者，
   而 `--funcs 0x141a939d5` 一个都没有（这正是任务 11 报告里「H1 无调用者」的原因）。

**真实入口 0x141a937e0 的序言与签名**：

~~~
0x141a937e0 push rbx
0x141a937e2 push rdi
0x141a937e3 push r14
0x141a937e5 sub rsp, 0x50
0x141a937e9 mov rdi, rcx        ; 第 1 实参 = 目标对象（H1 往这里写）
0x141a937ec mov r14, rdx        ; 第 2 实参 = 描述符（H1 从这里读键值）
0x141a937ef mov rcx, qword ptr [rdx]
~~~

**真实入口的 4 个直接调用者**（`native_xref.py callers --funcs 0x141a937e0`）：

~~~
0x141a8f983 → caller 0x141a8f520 (2286)     0x141ef2213 → caller 0x141ef1f90 (2183)   ← 动态栈命中
0x141a90750 → caller 0x141a90410 (1781)     0x141ef93e9 → caller 0x141ef92e0 (2502)   ← 动态栈命中
~~~

与 H3（0x141a935f0）的 4 个调用者**完全同址同函数**（0x141a8f520 / 0x141a90410 / 0x141ef1f90 / 0x141ef92e0），
其中 `0x141ef2213`（call H1）与 `0x141ef2222`（call H3）只隔 15 字节：

~~~
0x141ef220c mov rcx, qword ptr [rbx + 0x370]
0x141ef2213 call 0x141a937e0     ; ← H1 真入口（装填 亮度/对比度/缩放/反相/保护）
0x141ef2218 mov rdx, rdi
0x141ef221b mov rcx, qword ptr [rbx + 0x378]
0x141ef2222 call 0x141a935f0     ; ← H3（装填 纹理开关/覆盖率/模式/深度/最小深度）
~~~

**结论：H1 与 H3 是同一条装载链上背靠背的一对**（动、静双重证据），
它们把同一份笔刷预设描述符装进运行期对象的两个子结构。这条「同一调用簇」的判定此前只有静态推测，
本任务用运行期返回地址把它钉死了。

### 3.6 顺带确证的另一个公式片段（H3 内的最小深度归一化）

H3 在「描述符里**没有** `interpretation` 键 + `coverage != 0`」时，会改写 `minimumDepth`：

~~~
0x141a93758 mov ecx, dword ptr [rbx + 8]     ; ecx = depth
0x141a9375b mov eax, ecx
0x141a9375d mov edx, dword ptr [rbx + 0xc]   ; edx = minimumDepth
0x141a93760 cmp edx, ecx
0x141a93762 jle 0x141a9376e                  ; minDepth <= depth 则不交换
0x141a93764 mov eax, edx                     ; 否则交换，使 [rbx+8] = 较大者、[rbx+0xc] = 较小者
0x141a9376e test eax, eax
0x141a93770 je 0x141a93798                   ; 较大者为 0 → 跳过
0x141a93772 movd xmm1, edx ; cvtdq2ps        ; xmm1 = (float)较小值
0x141a93779 movd xmm0, eax ; cvtdq2ps        ; xmm0 = (float)较大值
0x141a93780 divss xmm1, xmm0                 ; 较小 / 较大
0x141a93784 mulss xmm1, [0x148405d68]        ; × K1
0x141a9378c addss xmm1, [0x14842004c]        ; + K2
0x141a93794 cvttss2si esi, xmm1              ; 截断取整
0x141a93798 mov dword ptr [rbx + 0xc], esi   ; 写回 minimumDepth
~~~

两个常量用 `tools/re_pipeline/ps_read_const.py` 从文件字节里解出来（可离线复算）：

~~~
$ python tools/re_pipeline/ps_read_const.py --exe "<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe"
H3 minimumDepth 缩放系数 K1 (mulss)   VA=0x148405d68 file_off=138429800 bytes=0000c84200000000 float32=100
H3 minimumDepth 偏移量 K2 (addss)     VA=0x14842004c file_off=138537036 bytes=0000003f65732d38 float32=0.5
H1/H3 共用的 double 100.0             VA=0x1483d7978 file_off=138240376 bytes=0000000000005940 double=100
~~~

即 `minimumDepth = (int)(100.0f × min/max + 0.5f) = round(100 × min/max)`。
这条**不是亮度/对比度公式**，但它说明 PS 在「纹理深度」这条链上有**百分比归一化**的写法，
对 11B 理解「d<100 与 Height 系列」的行为有参考价值（是否影响最终像素 → 未验证，见 §4 假设 4）。

---

## 四、给任务 11B 的「可拟合关系假设」（**全部标「假设」，不得直接写进 `src/`**）

> 任务书要求：公式必须先在 11B 变成可执行的参考实现并通过比对，才允许进 `src/`（任务 13）。
> 下面每条都写清「哪部分是**实证**、哪部分是**假设**」，不要把假设当结论用。

**假设 1（亮度语义）**：brightness 以 **int32** 存放在运行期对象 `+0x1c`，取值 **−150..+150**，
进入像素运算时按「**有符号整数偏移量**」使用，即形如 `out = clip(base ± brightness)`。
- 实证支撑：§3.2 表 A 第 2～5 行（±50/±150 逐位原样进对象，无缩放、无 clamp）；
  任务 12 在 d=100 的位移型模式上拟合到 **0.00% 差异、最大差值 0**。
- 仍是假设的部分：**对象里的整数如何变成 8-bit 域的偏移**（直接加？还是先 ×255/100？）本任务没有观测到。

**假设 2（对比度语义）**：contrast 以 **int32** 存放在 `+0x18`，取值 **−100..+100**，
使用时换算成倍率 `k` 作用于**以 127.5 为中点**的 8-bit 域。
- 实证支撑：§3.2 表 A 第 6～11 行（±50/±100 逐位原样进对象，无 clamp）。
- 仍是假设的部分：`k` 的具体形式。任务 12 的拟合值是
  c=−50→0.5029、−25→0.7533、0→1.0000、+25→**1.3287**、+50→2.0000、+75→4.0000，
  与经验式 `k=(100+c)/100`（0.5/0.75/1/1.3333/2/4）只在 **c=+25** 处有 0.0046 的偏差
  → **假设**这是因为 `k` 由**整数域运算 + 截断**得到（不是浮点 `100+c` 除 100）。

**假设 3（深度）**：depth 在进对象前被 `clamp(round(v), 0, 100)`。
- 这条其实是**实证**（§3.3 第 1 条：指令 + 动态 101→100 双向一致），列在这里是为了让 11B 直接用。
- 仍是假设的部分：`d<100` 时**像素层面的插值方式**（线性？查表？）本任务未观测。

**假设 4（最小深度归一化）**：`minimumDepth = round(100 × min/max)`，仅在描述符**没有** `interpretation` 键
且 `coverage != 0` 时执行（§3.6，常量 K1=100.0f、K2=0.5f 为字节级实证）。
- 仍是假设的部分：这段改写**是否影响最终像素**（可能只影响 UI 显示/预设回写）——本任务未验证。

**假设 5（模式编号，**最容易踩的坑**）**：运行期对象 `+0x04` 里的模式号是**第三套编号**，
本任务动态实测到 4 个：`multiply=7`、`linearHeight=41(0x29)`、`height=40(0x28)`、`subtract=39(0x27)`。
它与任务 11 静态报告里的 TransferMode 注册表（`linearHeight=0x2A`、`height=0x29`、`hardMix=0x1E`、`linearDodge=0x1F`）
**不是同一套**，与 Krita 的 `linearHeight=15` 更不是同一套。
- 仍是假设的部分：**ABR 的模式名 → 这套运行期编号**的完整映射（只测了 4 个；`hardMix`、`linearDodge` 本轮未测）。
  11B/13 落表时**必须**用「动态这套」去对齐 ABR 模式名，**不要**把静态注册表编号直接写进代码。

**假设 6（"参数层 ≠ 像素层"的边界，写进 11B 的注意事项）**：
本任务证实的是「PS 把亮度/对比度**原样存进运行期对象**」，**没有**证实「像素公式就是读这个对象算的」。
若 11B 的参考实现与真值图比对不达标，**优先怀疑是像素层公式问题，而不是参数语义问题**（参数语义已被逐位钉死）。

---

## 五、降级 / 未达成说明（如实，不粉饰）

### 5.1 像素消费端**未判定**（本任务最大的未达成项）

任务 11 给的「建议 0」（对 `struct+0x18`/`+0x1c` 下硬件读断点，第一次命中的代码就是消费端）
在本机用 Frida 的 `MemoryAccessMonitor`（页级守卫，效果等价于硬件读断点）实现，**打开了但观测到 0 次访问**：

~~~json
{"t":"armed","base":"0x7ff672020000","hooks":["H1-entry@0x1a939d5"],"mode":"MemoryAccessMonitor"}
{"t":"H1-first","struct":"0x21b764ccdc0","page":"0x21b764cc000"}
{"t":"watch-armed","page":"0x21b764cc000","window_ms":3000}
{"t":"watch-result","why":"timeout","struct":"0x21b764ccdc0","totalAccesses":0,"distinctSites":0,"sites":[]}
~~~

- 已排除「PS 崩了」：整轮实验 14 步全部成功，`done.txt` 正常写出，PS 正常退出。
- 已排除「窗口期没有像素活动」**部分成立但不足以解释 0 次**：同一窗口内 JSX 正在建文档 + 打两笔，
  而且**监控是在 H1 的 onEnter 里打开的**，H1 自己随后就要写 `+0x10/+0x18/+0x1c`
  —— 这些写操作本身就应当触发守卫页。**0 次访问**说明监控大概率没真正生效
  （Frida 在 Windows 上的 `MemoryAccessMonitor` 依赖 VEH/页保护，可能与 PS 自己的异常处理链冲突）。
- 因为「监控未生效」与「该对象真的没人读」两种解释无法区分，按判据只能记 **未判定**，
  **不能**写成「该对象无人读取」，更不能据此写「H1 与像素无关」。
- 原始消息：附件 `11A-frida-run6-watchpoint.json`；脚本：`tools/re_pipeline/ps_dyn_watch.js`。

**建议的替代路径**（给 lead 决策，见 §9 决策 3）：
1. 换用 **x64dbg 的真实硬件断点**（`ba r4 [rdi+0x18]`）——需要人工先把 x64dbg 装上，这是最稳的一条；
2. 或改走「**静态搜索 + 动态验证**」：在 `.text` 里搜「同一基址寄存器上同时读 `+0x18`、`+0x1c`、`+0x10`(double)、`+0x20`/`+0x21`」的函数，
   得到候选消费端短名单，再逐个下钩验证；
3. 或承认「像素层交给任务 12 的黑盒拟合」，11B 直接采用拟合公式（任务 12 已达 LUT 0.00%、参数化 0.01%）。

### 5.2 步骤标签缺失（影响可追溯性，已用「取值一一对应」补偿）

Frida 侧读取 `step.txt` 恒为空（§1.5-④，两种写法都试过），所以所有观测消息的 `step` 字段都是 `null`。
补偿办法与可靠性说明：

- §3.2 表 A：每个观测值（brightness 0/+50/−50/+150/−150、contrast 0/+50/−50/+100/−100）**在 14 步里都是唯一的**，
  因此「值 ↔ 步」的对应是**由值本身唯一确定**的，不依赖文件标签；只有 `(b=0,c=0)` 出现在 01/10/11/12/13/14 多步，
  这几行用「发射顺序单调递增」+ §3.3 的 depth 交叉确认来定位。
- §3.3 表 B：depth=60 唯一对应步 10；depth=100 且紧随其后的对应步 11（101 被 clamp）；
  末 6 条 mode 41/40/39 唯一对应步 12/13/14（其余步的 mode 恒为 7）。
- 结论：**不影响任何一条结论的成立**，但确实降低了「一眼可读性」。已在 §8 遗留里登记。

### 5.3 只做了 2 个候选的正面验证（H1、H3）

时间预算（约 2 小时）优先给了「把动态通路建起来 + 钉死参数层」，
H2/H4 与 M/L 档 11 个候选**没有下钩**，一律记 **未判定**。
特别是 **H4（TransferMode 注册表）**：它只在启动期执行一次，本实验的附身时机必然错过，
要抓它必须改成「spawn 模式 + 在进程入口就挂好钩」——本轮未做。

---

## 六、关键数据（供 lead 回写台账）

- 动态通路：**REA 内置 Frida**，`list_frida_devices` 返回 3 个设备（local/socket/barebone），**无需安装 frida**；共建立 **6 次** attach 会话，全部干净关闭。
- 实验矩阵：**14 组参数**（§2.2），覆盖 ±50/±150 亮度、±50/±100 对比度、depth 60/101、4 种纹理混合模式。
- 观测产出（run5 一次运行）：**12 条**对象采样（亮度/对比度）+ **28 条** H1 结构体快照 + **28 条** H3 结构体快照 + 命中/调用栈记录，共 **78 条** send 消息，**无截断**。
- 逐位一致率：brightness **5/5** 个取值逐位相同；contrast **5/5** 个取值逐位相同；scale **12/12** 次恒为 double 100.0。
- clamp 动态复现：depth 请求 101 → 观测 **100**（1/1）。
- 运行期模式号（新发现）：multiply=**7**、linearHeight=**41**、height=**40**、subtract=**39**。
- H3 最小深度常量：K1=**100.0f**、K2=**0.5f**（文件偏移 138429800 / 138537036）。
- 单次实验耗时：PS 冷启动 **10 s** + 14 步 **62 s**（附身与下钩 < 1 s）。
- PS 主程序：大小 208,456,176、mtime `2026-01-30 14:13:26.918235300`、SHA-256 `05720af2…c953`，**开工前后完全一致**。
- **下一个任务（11B）预计耗时：60–90 分钟**（依据：任务书给的区间；本任务已把「参数语义/量纲/clamp」钉死，
  11B 只剩「把公式写成 numpy 参考实现 + 用现有真值图核验」；但如果 11B 需要**像素消费端代码**才能定公式，
  需再加 30–45 分钟，或改用任务 12 的拟合结果）。

---

## 七、改动清单（全部为新增；未修改任何既有文件）

| 文件 | 说明 |
|---|---|
| `tools/re_pipeline/ps_dyn_driver.jsx` | **新增**。PS 侧「等待-执行」实验驱动：14 步参数矩阵、ready/go 握手、逐子操作 try/catch 诊断、每步打两笔。 |
| `tools/re_pipeline/ps_dyn_hook.js` | **新增**。Frida 插桩脚本：H1 入口/末尾、H3 入口/末尾四个钩子 + 对象轮询采样；只读，无写内存。 |
| `tools/re_pipeline/ps_dyn_watch.js` | **新增**。页级内存访问监控（等效硬件读断点）尝试脚本；本轮 0 命中，保留供后续用。 |
| `tools/re_pipeline/ps_read_const.py` | **新增**。把候选函数引用的浮点常量从文件字节里解出来（可离线复算）。 |
| `docs/研究报告/ps-逆向-动态.md` | **新增**。本报告。 |
| `docs/研究报告/附件/11A-frida-run5-sampling.json` | **新增**。插桩原始消息（78 条，含全部观测值）。 |
| `docs/研究报告/附件/11A-frida-run6-watchpoint.json` | **新增**。内存监控尝试的原始消息（4 条）。 |
| `docs/研究报告/附件/11A-runtime-driver.log` | **新增**。PS 侧驱动日志（33 条 ID 自检 + 14 步读回值）。 |
| `docs/研究报告/附件/11A-runtime-step.txt` / `-ready.txt` / `-done.txt` / `-go.flag` / `-ps_stdout.log` | **新增**。运行期握手与 PS stdout 原始文件。 |
| `docs/验收报告/11A-报告.md` | **新增**。任务验收报告。 |

未改动：`src/`、`gui/`、`cli.py`、`research/`、`tests/*.abr`、`docs/优化提示词/**`、`docs/工作流程/**`、PS 安装目录。

---

## 八、遗留问题（如实列出，不做补全）

1. **像素消费端未定位**（§5.1）。这是任务 11 遗留 1 的延续，本轮没能解决。
2. **步骤标签缺失**（§5.2）：Frida 侧读 `step.txt` 不成功，观测消息的 `step` 恒为 `null`，靠取值对应重建。
3. **H2 / H4 / M1～M5 / L1～L6 共 13 个候选未下钩**（§5.3），全部未判定。
4. **H1 的地址勘误影响任务 11 报告的后续引用**：任务 11 报告里 H1 = `0x141a939d5` 应更正为
   **函数入口 `0x141a937e0`**；且「H1 无直接调用者」的说法应更正为「有 4 个直接调用者」。
   本报告只做记录，**没有**去改任务 11 的报告文件（不在本任务写入范围）。
5. **`hardMix` / `linearDodge` 的运行期模式号未测**（本轮只测了 multiply/linearHeight/height/subtract 4 个）。
6. **`app.quit()` 在 PS 2025 上不总能真正退出**：本次多次出现「脚本已 `done.txt` 但 PS 进程仍在」，
   收尾靠 `taskkill /F /IM Photoshop.exe`。已在此记录，后续任务收尾务必显式检查进程列表。
7. **`MemoryAccessMonitor` 在 Windows + PS 上疑似不生效**（0 次访问），未定位根因。

---

## 九、决策记录（lead 代为决策）

> 团队规则禁止子代理调用 `ask_user_question`；任务书第六节的三个人工决策据此改写为下表，由 lead 复核后代为拍板。

| # | 决策点（对应任务书「六、收尾交接」） | 选项 | 选择 | 一句话理由 |
|---|---|---|---|---|
| 1 | **决策 1：证实程度够不够** | (a) 够，进 11B（推荐）(b) 不够，先去任务 12 黑盒拟合 (c) 停下来讨论 | **(a)+(b) 并行**：11B 用本报告钉死的参数语义，公式系数同时采纳任务 12 的拟合结果 | 参数层已**逐位**证实（brightness 5/5、contrast 5/5、scale 12/12、depth clamp 1/1），足够支撑 11B 的参数部分；但**像素层未定位**，所以系数仍需任务 12 的拟合值兜底，不能只靠逆向。 |
| 2 | **决策 2：实验参数范围要不要扩大** | (a) 保持 ±50 起（本任务已到 ±150/±100 极值）(b) 扩到 ±150 全量采样 (c) 加负对比度边界 | **(a) 已在极值上，暂不扩** | 本任务已把 brightness 打到 **±150**（界面上限）、contrast 打到 **±100**（上限），且都**原样无 clamp** 进对象；再扩只会得到「界面拒绝设置」而不是「内部 clamp」，信息量为 0。真正缺的是**中段细网格**（±10/±25/±75）用于定 `k(c)` 的取整方式 → 建议放到 11B 用出图拟合做，而不是再做一轮插桩。 |
| 3 | **决策 3：动态追踪要不要继续追更深** | (a) 追到被调用函数/像素消费端 (b) 到此为止，像素层交给任务 12 | **(b) 到此为止（本轮）；但要给 (a) 留好接口** | 页级监控实测 0 命中（§5.1），继续在同一工具上纠缠大概率是重复投入；而任务 12 的参数化公式已达 **0.01%** 差异，工程上够用。若后续必须要消费端代码，**先装 x64dbg 用真硬件断点**（1 小时内可完成），或走 §5.1 的静态短名单路线。**不建议**在 Frida 页监控上再试第 4 次。 |
| 4 | 观测脚本放在哪（本任务临时决策） | (a) 进 `tools/re_pipeline/`（推荐）(b) 只留在会话里 | **(a) 进仓库** | 四个脚本都可复跑、无版权风险、不含大段机器码；任务 12/11B 若要做同类实验可直接复用。 |
| 5 | 实验临时文件放哪（本任务临时决策） | (a) `tests/baselines/ps/_work`（任务 05/12 用过）(b) 仓库外临时目录（推荐） | **(b) `%USERPROFILE%/AppData/Local/Temp/bcdyn`** | `tests/` 不在本任务写入范围；放仓库外天然不进 git，也不与并行任务抢目录。代价：每次运行前要清空该目录（已写进 §2.6 复现步骤）。 |
| 6 | H1 地址勘误要不要直接改任务 11 的报告 | (a) 直接改（推荐）(b) 只在本报告记录 | **(b) 只记录** | `docs/研究报告/ps-逆向-静态.md` 是任务 11 的产物，不在本任务写入范围；越界修改会破坏「一人一份产物」的边界。建议 lead 在收尾时统一改，或指派任务 11 的 owner 改。 |

---

## 十、人工如何验收（给使用者，不需要编程基础）

1. **看结论**：打开本文件 §3.1 的表格，看每个候选是「证实 / 排除 / 未判定」。本任务 **证实 1 个（H1）**、**证实 1 个（H3）**、
   **未判定 13 个候选 + 1 个像素消费端**。没有把未判定写成证实。
2. **看最硬的一张表**：§3.2 表 A。「观测到 +0x1c」那一列从 `0 → +50 → −50 → +150 → −150` 一路跟着你请求的亮度走，
   一位不差；「+0x18」那一列跟着对比度走。这就是「PS 内部把亮度/对比度当整数原样存」的直接证据。
3. **复核原始数据**（不需要装任何工具）：
   用记事本打开 `docs/研究报告/附件/11A-frida-run5-sampling.json`，搜 `"sampleH1"`，能看到 12 条原始记录，
   每条都是 `{"scale":100,"contrast":…,"bright":…}` —— 与表 A 一一对应。搜 `"snapH3"` 能看到 depth 从 100 跳到 60 再回 100。
4. **复核 PS 没被改动**：在仓库根执行
   ```
   "<LOCAL_APP_DIR>/anaconda3/envs/brushConverter/python.exe" tools/re_pipeline/ps_read_const.py --exe "<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe"
   ```
   会打印三个常量的文件偏移（138429800 / 138537036 / 138240376），说明脚本能只读打开 PS 主程序。
   再执行 `sha256sum "<LOCAL_APP_DIR>/PS2025/Adobe Photoshop 2025/Photoshop.exe"`，
   应得到 `05720af2cd207ead737b4c9ff422ffb7191f2d65cc0045b88735030cda76c953`（与任务 11 记录一致）。
5. **想自己重跑一遍实验**：按 §2.6 的 3 条命令 + REP 附身步骤；约 2 分钟一轮，PS 会自己退出（若没退出就 `taskkill`）。

---

## 十一、附件清单（原始输出，均在 `docs/研究报告/附件/`）

| 文件 | 内容 |
|---|---|
| `11A-frida-run5-sampling.json` | **主证据**。14 步实验的全部 78 条插桩消息：hook 就位、诊断、H1/H3 命中与调用栈、12 条对象采样、28+28 条结构体快照。 |
| `11A-frida-run6-watchpoint.json` | 页级内存监控尝试的 4 条消息（含 `totalAccesses: 0` 的原始结论）。 |
| `11A-runtime-driver.log` | PS 侧驱动日志：33 条 charID/stringID 自检（全 `ok`）+ 14 步的请求值与 PS 读回值。 |
| `11A-runtime-step.txt` / `11A-runtime-ready.txt` / `11A-runtime-done.txt` / `11A-runtime-go.flag` | 运行期握手文件原文。 |
| `11A-runtime-ps_stdout.log` | PS 进程 stdout（含 7 条 `Error in locale file` —— PS 自带的本地化文件提示，与本实验无关）。 |
