# 纹理模式补强：RE 记录与两个问题的回答（任务 104 · 2026-11-20）

> 执行者：纹理补强员（v1.4.0 第三轮）｜工作区：仓库根
> 目标二进制：Adobe Photoshop 2025 / 26.9.0.15（`Photoshop.exe`，208,456,176 字节，
> SHA-256 `05720af2…c953`）；Krita 5.3.3。
> 本文只写「要回答的问题、方法、命中的函数/区域标识、判定依据、命令与原始输出」。
> 含地址的内容只留本地（通用规范 §七.11）；本文不写本机绝对路径/用户名/家目录，
> 命令里的 PS 主程序路径统一写成 `<PS_EXE>`。
> 只读：PS 安装目录、`src/**`、`tests/**`、`docs/参数矩阵.json` 全程未改。

---

## 一、一句话结论

**REA 本会话「服务在、但打不开这个 208 MB 目标」：rizin provider 未配置（原始报错见 §2.1），
Ghidra 对本 exe 的全量分析在任务 11 实测 1,744.9 秒仍在 1500 秒上限被中断（§2.2），
因此本轮没有用 REA 打开目标；改用它同一判定口径下的仓库既有单区域反汇编工具
`tools/re_pipeline/native_xref.py`（`.pdata` 函数边界 + capstone 5.0.7）
做了 3 个单函数/单区域核对 + 1 个静态表解析。**
**Q1 的答案是：能证明 6 个目标模式在 PS 参数层各自有独立 runtime id、没有「多模式并成一个 id」的归并；
但像素分派函数未定位，所以不能写「PS 判定等价/同一算子」。真值重跑（depth 25/50/100）显示
5 个目标模式 3/3 深度恒 255、linearBurn 与 Mltp 3/3 深同 IDAT 哈希——
塌缩不是采样点不够，而是判据材料 base=1 导致的饱和/退化，正确口径是「本口径下不可区分」。**
**Q2 的答案是：8 个无真值行全部不在 PS「纹理 > 模式」下拉里，按 103 的 UI 门一行都不投入；
其中 `Lght/Grdn/HdMxS` 是 PS 拒绝枚举（跑不出来），`Add`/`Rvrt` 是 PS 报错/忽略（跑不出来），
`SgdD/MgdD/HgdD` 是 33 批喂了反序伪名（没跑过，正名可用但 UI 不可见）——本任务对它们 0 次重跑、0 次逆向。**

---

## 二、REA 可用性与「为什么没用 REA 打开目标」

### 2.1 rizin provider：未配置（原始报错，逐字）

REA 服务本身在（`rea-agents 6.9.0`，`binary_session` 报 `ghidra: available`），
但 `execute_rizin_command` 直接失败：

```json
{"error":{"code":"capability_unavailable","category":"unsupported_provider",
 "message":"Could not launch the selected rizin executable \"rizin\" (ERR_REA_WINDOWS_NATIVE).",
 "retryable":false,
 "remediation":{"action":"Set REA_RIZIN_COMMAND to an installed executable Rizin command, or make that command available on PATH. ..."},
 "details":{"provider_id":"rizin","operation":"execute_rizin_command",
 "reason":"Unable to run configured command (ERR_REA_WINDOWS_NATIVE)",
 "diagnostics":{"boundary":"provider-launch","command":"rizin","system_code":"ERR_REA_WINDOWS_NATIVE"}}}}
```

### 2.2 Ghidra provider：可用，但对本目标「打开分析太慢」

- 任务 11 已实测：对 `Photoshop.exe` 建库 + 自动分析总耗时 **1,744.9 秒（29.1 分钟）**，
  自动分析在 **1500 秒**上限被强制中断，12 个目标地址的交叉引用只拿到 1 条
  （`docs/研究报告/ps-逆向-静态.md` §9.3，原始日志 `附件/ghidra_photoshop.log`）。
  本机空闲物理内存 5.5–6.7 GB、页面文件可用 0.55 GB。
- 本轮时间预算内再开一次 Ghidra 只会重复 29 分钟开销，故**不打开目标**。
- 因此按任务书口径如实记录：**REA 本会话未对目标做逆向**；下述单区域核对使用仓库既有工具，
  每条都给了可复跑命令与地址，不凭想象写结论。

### 2.3 实际使用的单区域静态工具

`tools/re_pipeline/native_xref.py`（任务 11 建立，只读、确定性）：

- 函数边界来自 PE `.pdata`（编译器写的 RUNTIME_FUNCTION 表，438,177 条记录），误差为 0；
- 反汇编用 capstone 5.0.7，字符串引用带 `; "xxx" @VA` 注释；
- 只反汇编指定区间（`--start/--end`），**没有**全量反编译。

---

## 三、Q1：塌缩组（多个 PS 模式出同一张像素）是「PS 判定等价」还是「采样点不够」？

### 3.1 问题拆成两半

| 子问题 | 判定标准 | 本轮做法 |
|---|---|---|
| Q1-a 参数层：PS 是否把多个模式归并成同一个 runtime mode id？ | 能证明同 id/同分支 → 可写「PS 判定等价」 | 单函数/单区域静态核对（§3.2） |
| Q1-b 材料层：换更多深度点能否把塌缩组拆开？ | 换深度后仍同哈希/饱和 → 「本口径下不可区分」 | 真值重跑 depth 25/50/100（§3.3） |

### 3.2 Q1-a：单区域静态核对（3 个区域 + 1 张静态表）

**要回答的问题**：6 个目标模式（Ovrl/CDdg/CBrn/linearBurn/height/linearHeight）在 PS 里
是不是各自登记为不同的 runtime mode id？H3 把模式值写进运行期对象时有没有「多值合一」的归一化？

**区域选择依据**（为什么是这三处、不是 H1/H2/K60）：
H1 `0x141a937e0` 读的是 brightness/contrast/invert/protect，H2 `0x141a96230` 是它们的写回，
K60 `0x141ef62b3` 是纹理结构体拷贝层（读 `+0x18/+0x1c`）——这三处都**不碰** `textureBlendMode`。
真正把模式值落到运行期对象 `+4` 的是 H3 `0x141a935f0`（`lea r8,[rbx+4]; lea rdx,"textureBlendMode"; call 0x141b94090`），
而 `0x141b94090` 查的表由 H4 `0x141b94750` 构造、4CC 条目在静态表 `0x14b219bc0`。
所以单区域核对选 H3 + H4 + 翻译器 + 静态表，四处都在「模式 id」这条链上。

**命令（仓库根执行，`<PS_EXE>` 为 PS 主程序路径）**：

```bash
python tools/re_pipeline/native_xref.py disasm --exe "<PS_EXE>" --start 0x141a935f0 --end 0x141a937d6
python tools/re_pipeline/native_xref.py disasm --exe "<PS_EXE>" --start 0x141b94750 --end 0x141b949ee
python tools/re_pipeline/native_xref.py disasm --exe "<PS_EXE>" --start 0x141b94090 --end 0x141b94136
# 静态表 0x14b219bc0（.data，文件偏移 0xb217bc0）176 字节，按 {dword 值, dword 4CC} 8 字节记录解析
python -c "import struct;d=open(r'<PS_EXE>','rb').read();o=0xb217bc0;[print(hex(0x14b219bc0+8*i),*struct.unpack('<II',d[o+8*i:o+8*i+8])) for i in range(22)]"
```

**命中 1：H3 `0x141a935f0`（读 useTexture/coverage/模式/深度）**

```asm
0x141a9363a  lea r8, [rbx + 1]                       ; textureCoverage -> obj+1
0x141a9364a  lea r8, [rbx + 4]                       ; 模式号输出槽 = obj+4
0x141a93651  lea rdx, [rip + 0x6afd1f0]  ; "textureBlendMode"
0x141a93658  call 0x141b94090                        ; 翻译器：描述符值 -> 模式号 -> [r8]
0x141a9365d  lea rcx, [rip + 0x6afd1fc]  ; "textureDepth"
```

判定依据：模式号由 `0x141b94090` **直接写进 `[rbx+4]`**，H3 内没有对多个模式值做比较/合并/等价归一化。

**命中 2：翻译器 `0x141b94090`（166 字节）**

```asm
0x141b940d2  call 0x142c4cbd0        ; 描述符里取整数键值 -> ebx
0x141b940d9  call 0x141b94750        ; 取表基址（0x14b219bc0）
0x141b940de  cmp dword ptr [rax], 0x36       ; 终止哨兵
0x141b940e3  cmp dword ptr [rax + 4], ebx    ; 键匹配
0x141b940e8  add rax, 8                      ; 记录跨度固定 8 字节
0x141b94103  mov ecx, dword ptr [rax]        ; 值 = [0]
0x141b9410c  mov dword ptr [rsi], ecx        ; *out = 值（rsi = H3 的 rbx+4）
```

判定依据：一个键匹配一条记录、返回该记录的**一个** dword 值；没有「多个模式命中同一条记录」的写法。

**命中 3：H4 `0x141b94750`（注册表构造，启动期一次性）**

```asm
0x141b947d0  lea rcx, [rip + 0x6a11b79]  ; "linearBurn" @0x1485a6350
0x141b947dc  mov dword ptr [rip + 0x968549a], eax ; 标识 = interned id -> 0x14b219c7c
0x141b947e2  mov dword ptr [rip + 0x9685494], 0x20 ; 字面枚举 32 -> 0x14b219c80
...
0x141b94924  lea rcx, [rip + 0x683ba3d]  ; "height" @0x1483d0368
0x141b94930  mov dword ptr [rip + 0x96853be], eax ; -> 0x14b219cf4
0x141b94936  mov dword ptr [rip + 0x96853b8], 0x29 ; 字面枚举 41 -> 0x14b219cf8
0x141b94940  lea rcx, [rip + 0x6a11a59]  ; "linearHeight" @0x1485a63a0
0x141b9494c  mov dword ptr [rip + 0x96853aa], eax ; -> 0x14b219cfc
0x141b94952  mov dword ptr [rip + 0x96853a4], 0x2a ; 字面枚举 42 -> 0x14b219d00
```

**命中 4：静态 4CC 表 `0x14b219bc0`（21 条 + 终止记录，逐条解析；名字按 4CC 正序读）**

```text
0x14b219be0 id=7   Mltp      0x14b219bf0 id=17  Ovrl
0x14b219bf8 id=19  SftL      0x14b219c00 id=18  HrdL
0x14b219c48 id=25  CDdg      0x14b219c50 id=26  CBrn
0x14b219c60 id=39  Sbtr      0x14b219c68 id=35  （终止记录，第 2 个 dword 被 H4 复用）
```

判定依据：`Ovrl=17`、`CDdg=25`、`CBrn=26` 是三条独立记录；结合 H4 的 `linearBurn=0x20`、
`height=0x29`、`linearHeight=0x2a`，以及任务 31 动态实测的对象值
（Ovrl=17、CDdg=25、CBrn=26、linearBurn=31、height=40、linearHeight=41），
**6 个目标模式有 6 个不同的 runtime id，参数层不存在归并**。

**未命中（如实写）**：**像素分派函数没有定位**——即「拿到 id=X 后跳去哪个混合算子」的那段代码，
本轮没有找到。因此不能证明两个模式「走同一分支/同一算子」，只能证明「参数层 id 不同」。

### 3.3 Q1-b：真值重跑（depth 25/50/100，scale=100，反相关闭）

口径与落盘见 `docs/数据/真值/manifest.json`；每张图都有 IDAT sha256 与跑出时间。
目标 6 行 + 1 个对照行（Mltp，UI 可见、非目标，用于判断 linearBurn 是否仍退化）各 3 个深度 = 21 张 PS 真值，
Krita 同口径 21 张，逐张比对（判据 ≤2% 且 max_delta≤2）。

| 模式（runtime id） | d25 PS u / Krita u | d50 | d100 | PS 侧塌缩 |
|---|---|---|---|---|
| Ovrl (17) | 1 / 1 | 1 / 1 | 1 / 1 | 与 CDdg/CBrn/height/linearHeight 同 IDAT 哈希（恒 255） |
| CDdg (25) | 1 / 1 | 1 / 1 | 1 / 1 | 同上 |
| CBrn (26) | 1 / 1 | 1 / 1 | 1 / 1 | 同上 |
| linearBurn (31) | 65 / 65 | 127 / 127 | 221 / 221 | 与对照行 Mltp **3/3 深度同 IDAT 哈希**（u 完全相同） |
| height (40) | 1 / 1 | 1 / 1 | 1 / 1 | 与 linearHeight 3/3 深同哈希 |
| linearHeight (41) | 1 / 1 | 1 / 1 | 1 / 1 | 与 height 3/3 深同哈希 |
| Mltp (7，对照) | 65 / 65 | 127 / 127 | 221 / 221 | 与 linearBurn 3/3 深同哈希 |

21/21 条比对全部 0.00%/max_delta=0（数据见 manifest 的 `comparison`），但其中 15 条两侧饱和
（u=1，无判别力），6 条非饱和（linearBurn 与 Mltp 各 3 条）。

**判读**：
1. 加了 depth 25/50 两个采样点后，5 个目标模式仍在 3/3 深度恒 255——**塌缩不是「采样点不够」**。
2. `linearBurn` 在 3/3 深度都与对照行 `Mltp` 同 IDAT 哈希：base=1（满覆盖笔尖）时
   `linear burn(1,t) = t = multiply(1,t)`，像素无法区分 9（LINEAR_BURN）与 0（MULTIPLY）——
   这是**判据材料的数学退化**，不是 PS 把它们归并了（§3.2 已证 id 不同）。
3. 因此结论口径只能是「**本口径下不可区分**」；不能写成「PS 判定等价/同一算子」。

### 3.4 Q1 结论（按任务书口径逐字）

- 能证明：6 个目标模式在 PS **参数层**各自独立 runtime id，H3/翻译器/注册表没有归并逻辑。
- 不能证明：像素层走同一分支（分派函数未定位）。
- 重跑结论：depth 25/50/100 三点仍饱和/退化 → **塌缩属「本口径下不可区分」，
  不是采样点不够；要拆开必须换 base<1 的材料（半透明 dab / 压感边缘），不在本任务范围。**

---

## 四、Q2：无真值的 8 行是「没跑过」还是「跑不出来」？

先过 UI 门：这 8 行都不在 `docs/数据/ps_texture_modes_ui.json` 的 10 项下拉里
（下拉成员：Mltp/Sbtr/Drkn/Ovrl/CDdg/CBrn/linearBurn/hardMix/linearHeight/height），
按 103 的 UI 门**一行都不投入**（不重跑、不逆向）。下表只用 `docs/数据/mode-ids.json` 说明各自原因：

| order | 成员 | UI | runtime_id（mode-ids） | 现状 | 判定 |
|---:|---|---|---:|---|---|
| 19 | `Add ` | 无 | 38 | 33 批 PS 落笔/保存报错（原始错误：`发生了常规 Photoshop 错误。该功能可能无法在这个版本的 Photoshop 中使用。 - 不能完成命令，因为程序错误。 line 238`） | **跑不出来**；UI 不可见 → 不投入 |
| 28 | `Rvrt` | 无 | 22 | PS 忽略该枚举值（回读停在上一模式 `height`） | **跑不出来**；UI 不可见 → 不投入 |
| 29 | `SgdD` | 无 | 10 | 33 批喂的是 Ghidra 小端反序伪名，PS 忽略；任务 31 已证明正名 `DdgS` 可用 | **没跑过（名字错）**；无真值但不是 PS 产不出；UI 不可见 → 不投入 |
| 30 | `MgdD` | 无 | 11 | 同上（正名 `DdgM`） | 同上 |
| 31 | `HgdD` | 无 | 12 | 同上（正名 `DdgH`） | 同上 |
| 44 | `Lght` | 无 | None | PS 拒绝该枚举值（charID/stringID 都不改变模式，回读停在 `height`）+ 静态未定位 | **跑不出来**（PS 没有这个纹理模式入口）；UI 不可见 → 不投入 |
| 45 | `Grdn` | 无 | None | 同上 | 同上 |
| 46 | `HdMxS` | 无 | None | 同上；且 5 字符名被 charID 拒绝（charID 只收 4 字符） | 同上 |

**结论**：Q2 不是「本任务该跑没跑」，而是「按 UI 门不允许投入」；
`Lght/Grdn/HdMxS` 属 PS 拒绝枚举（跑不出来），`Add`/`Rvrt` 属 PS 报错/忽略（跑不出来），
`SgdD/MgdD/HgdD` 属名字错导致没跑过（正名可用，但 UI 不可见，按门仍不投入）。
本任务对这 8 行：**重跑 0 张、逆向 0 次**。

---

## 五、失败 / 未判定清单（如实）

| # | 项 | 状态 | 原始原因 / 影响 |
|---|---|---|---|
| 1 | REA rizin 通路 | **不可用** | `ERR_REA_WINDOWS_NATIVE`（§2.1 原文）；未做任何 rizin 反汇编 |
| 2 | REA Ghidra 打开本目标 | **未做（太慢）** | 任务 11 实测 1,744.9 s 仍在 1500 s 中断（§2.2）；本轮改用既有 capstone 单区域工具 |
| 3 | 像素分派函数（id → 算子） | **未定位** | 只证明参数层 id 不同；不能写「同一算子」 |
| 4 | 动态逆向（Frida） | **本轮未做** | 任务 31/30 的动态观测已存在（runtime id、K60 拷贝层），本轮引用不重跑 |
| 5 | 半透明/压感材料 | **未做** | 超出本任务口径；是拆开塌缩组的后续建议 |

---

## 六、遗留（给后续任务）

1. 若必须证明「哪些 id 走同一像素分支」，需要定位分派点：建议在 H3 写入 `[obj+4]` 后
   对对象 `+4` 下硬件读断点（x64dbg），第一次命中的代码即分派消费者；或用 spawn 模式
   在 `H4` 构造完成后遍历运行期表。本任务不做。
2. 拆开塌缩组的唯一有效方向是换 **base<1** 的材料（半透明 dab / 压感边缘），
   深度与缩放都改变不了 base=1 的饱和/退化。
3. `mode-table.json` 的 `counts.by_evidence_grade` / `by_batch_evidence_grade` 未随本任务重算
   （写入范围只含 6 个目标行；本轮 6 行都是 B，实际计数与旧值一致，无需变更）。
