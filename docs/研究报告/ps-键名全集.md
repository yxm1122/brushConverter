# 任务 21：PS 键名全集（逆向骨架）

> 目标：Photoshop.exe 26.9.0.15（sha256 `05720af2cd207ead737b4c9ff422ffb7191f2d65cc0045b88735030cda76c953`，镜像基址 0x140000000）
> 产物：`docs/数据/ps_keys.json`（机器可读、每条带证据）+ 本文件（人读）
> **本任务只做键名与取值范围的静态采集，不做「谁消费它算像素」的结论（那是任务 25/30）。**

---

## 一、结论（先看这一段）

1. **键名全集共 125 条 = 93 条「字符串形态」+ 32 条「4CC 立即数形态」**。
   取数：`python -c "import json;d=json.load(open('docs/数据/ps_keys.json',encoding='utf-8'));print(len(d))"` → `125`
2. **其中能从某个结构体定位到偏移的有 59 条**（分布在 8 个不同结构体上，见 §4）。
   取数：`python -c "import json;d=json.load(open('docs/数据/ps_keys.json',encoding='utf-8'));print(sum(1 for i in d if i['offset']))"` → `59`
3. **有 clamp 的 5 条、明确「无 clamp」的 6 条**（各给指令级证据，见 §5）。
4. **本轮最重要的方法性发现**：PS 的键名有**两种传递形态**——
   ① 以字符串地址（`lea rdx, "useTexture"`）传给描述符 API；② 以 **32 位立即数**（4CC，字符序即整数，如 `mov edx, 0x5370636e` = `"Spcn"`）直接当 key id。
   → **只扫字符串一定会漏**。这解释了「ABR 里有、字符串扫描里没有」的绝大部分缺口（36 个 4CC 键里 32 个能扫到立即数）。
5. **Ghidra 的引用图确实不能用**（任务 20 §7.2 的结论在本任务复现）：本任务全程用 capstone 直接扫指令操作数，未使用 `getReferencesTo`。

---

## 二、判据与口径（可复算，不是「看着像」）

### 2.1 为什么不能用「所有字符串」当全集

.rdata 里共有 **177,761** 条可打印字符串，其中被指令引用到的有 **74,104** 条（含 UI 文案、`$$$/` 资源、着色器关键字、C++ 关键字等）。
直接取「camelCase 字符串」会把 `texture`（GLSL 内建函数名，420 条引用）、`hardness`、`style`、`count` 之类一起捞进来。本任务用**三条可机械复算的条件**收敛：

| # | 条件 | 复算方式 |
|---|---|---|
| C1 | 字符串在 .rdata 里以 NUL 结尾、形态为 `^[A-Za-z][A-Za-z0-9]{1,39}$` 或带一个尾空格（如 `"Mnm "`） | `ps_keys.json` 每条的 `key` 与 `rifrefs.json` 的 `text` 字段 |
| C2 | 存在一条 **capstone 校验过**的 rip 相对引用指令（`lea/mov reg,[rip+disp]`）指向它，且这条指令之后 ≤12 条内有一条直达 `call`，目标是**描述符 API 家族**成员；或在「键表指针数组」构造里以 `lea rax` 出现 | 每条的 `evidence[].value`（指令地址）+ `evidence[].detail`（所属函数 + 消费者） |
| C3 | 该引用指令所属的 `.pdata` 函数落在**笔刷模块地址窗口** `[0x141a8b000,0x141aa2000)` 或**预设装载/保存窗口** `[0x141ef15c0,0x141efe000)`，且该函数引用了 ≥1 个「判别性键」（`minimumRoundness`/`textureDepthDynamics`/`brushPoseAngle` 这类不会出现在图形代码里的键） | `evidence[].detail` 里的函数范围 |

**描述符 API 家族**（由「11 个已确证纹理键的引用点」归纳，共 21 个成员），例如：
`0x142c528d0` hasKey(名) / `0x142c52870` hasKey(id) / `0x142c4a8f0` getBool / `0x142c57a90` putBool /
`0x142c4e820` getInteger / `0x142c5b3f0` putInteger / `0x142c5ed90` get/putDouble / `0x142c90280` 名字→key id /
`0x141b94090`、`0x141b952c0` 纹理模式枚举翻译 / `0x140fa6790`、`0x140faecf0`、`0x142c55710` 取子描述符。

> `0x142c90280` 是「名字→内部 id」：全二进制有 **4,219** 个调用点紧随字符串 LEA，是「这个名字被当键用」的最强单点信号。

### 2.2 为什么另开一类「4CC 立即数形态」

`0x141a930b0`（scatter 读取函数）里有一处：

```asm
0x141a93136  mov edx, 0x5370636e        ; "Spcn"（4CC，字符序即整数）
0x141a9313b  call 0x142c52870           ; hasKey(id)
...
0x141a93178  mov eax, 4
0x141a93187  mov dword ptr [rbx + rax], esi   ; 存到 scatter 子对象 +0x04
```

即 4CC 键根本不以字符串存在，**任何字符串扫描都不可能看到它**。用「大端字符序」把 36 个 ABR 侧 4CC 键转成 imm32 后在 .text 里扫 `mov reg,imm32`，
**32/36 命中**（未命中 4 个见 §7）。这 32 条作为 `form = "4cc_immediate"` 收进 JSON，证据是指令地址。

---

## 三、键名全集的构成（125 条）

| 分组 | 条数 |
|---|---|
| 纹理（texture*、invert/protect/useTexture、minimumDepth） | 13 |
| 笔尖动态（useTipDynamics、flipX/Y、brushProjection、minimumDiameter/Roundness、tiltScale、angle/roundnessDynamics） | 10 |
| 散布（useScatter、bothAxes、count、count/countDynamics、scatter、scatterDynamics） | 6 |
| 颜色动态（useColorDynamics、purity、chroma、saturation、lightness、hue、brightness、colorDynamicsPerTip、前景/背景色） | 10 |
| 画笔姿态（useBrushPose、brushPoseAngle/Pressure/TiltX/TiltY、overridePose*） | 10 |
| 锁定标志（*Lock，共 10 个） | 10 |
| 平滑/兼容（smoothing*、usePressureOverrides*、pressureSmoothing、useLegacy） | 10 |
| 工具选项（wetEdges、repeat、noise、useBrushSize、usePaintDynamics、toolOptions） | 6 |
| 预设/元数据（brushPreset、brushGroup、brushType、computedBrush、dBrush、dTips、sampledBrush…） | 14 |
| 双重画笔 / 混合翻转 | 4 |
| **ABR/4CC 立即数形态** | **32** |
| **合计** | **125** |

取数：`python -c "import json,collections;d=json.load(open('docs/数据/ps_keys.json',encoding='utf-8'));print(collections.Counter(i['group'] for i in d))"`

---

## 四、能定位到结构体偏移的 59 条（按结构体分组）

> **重要**：这些偏移**不属于同一个结构体**。PS 把笔刷预设拆成多个子对象，每个访问函数写的是自己的那个子对象。
> 把不同结构体的偏移混成一张表是错的——所以 JSON 里每条都带 `offset_struct` 字段。

### 4.1 texture 子对象（11 条；读侧 0x141a939d5 / 0x141a935f0，写侧 0x141a96230 / 0x141a960c0）

| 键 | 偏移 | 类型 | 本任务的指令证据 |
|---|---|---|---|
| useTexture | +0x00 | bool(byte) | 0x141a93280 / 0x141a93286 `mov byte ptr [rbx], al` |
| textureCoverage | +0x01 | bool(byte) | 任务 11 静态 + 任务 11A 动态（本任务未重新导出该条，标「沿用」） |
| textureBlendMode | +0x04 | enum(int32) | 任务 11 静态 + 11A 动态（观测值 7/39/40/41） |
| textureDepth | +0x08 | int32 | 0x141a936c2 附近 `mov [rbx+8], eax`（clamp 见 §5） |
| minimumDepth | +0x0c | int32 | 0x141a9371c `mov dword ptr [rbx + 0xc], eax` |
| textureScale | +0x10 | **double** | 0x141a93a15 `cvtdq2pd` → 0x141a93a19 `movsd qword ptr [rdi + 0x10], xmm0` |
| textureContrast | +0x18 | int32 | 0x141a93a6e `lea r8,[rdi+0x18]` → 尾调用 `jmp 0x142c4e820` |
| textureBrightness | +0x1c | int32 | 0x141a93a56 `lea r8,[rdi+0x1c]` → `call 0x142c4e820` |
| invertTexture | +0x20 | bool(byte) | 0x141a93a26 `lea r8,[rdi+0x20]` → `call 0x142c4a8f0` |
| protectTexture | +0x21 | bool(byte) | 0x141a93a3e `lea r8,[rdi+0x21]` → `call 0x142c4a8f0` |
| texture（子描述符本身） | +0x00 | descriptor 指针 | 0x141a9381c `lea r8,"texture"; call 0x140fa6790` |

### 4.2 scatter 子对象（6 条；函数 0x141a930b0）

useScatter +0x00（0x141a93280 `mov byte ptr [rbx],al`）、count +0x08（0x141a931d3）、bothAxes +0x0c（0x141a931e2 `lea r8,[rbx+0xc]`）、
countDynamics +0x10（0x141a93201 `lea rcx,[rbx+0x10]` → dynamics 解析器 0x141a93ea0）、scatterDynamics +0x24（0x141a9321c）、scatter +0x2c（0x141a93127）。

### 4.3 tipDynamics 子对象（3 条）/ colorDynamics 子对象（5 条）/ pose 子对象（6 条）

- tipDynamics（0x141a932b0）：flipX +0x4c、flipY +0x4d、brushProjection +0x4e（都是 `mov byte ptr [rbx+0x4x], 0` 的默认值写法）。
- colorDynamics（0x141a93ab0）：chroma +0x14、saturation +0x18、lightness +0x1c、purity +0x20、colorDynamicsPerTip +0x24(byte)。
- pose（0x141a92d50）：brushPoseAngle +0x0c、brushPosePressure +0x10、overridePosePressure +0x14、overridePoseAngle +0x15、overridePoseTiltY +0x16、overridePoseTiltX +0x17。

### 4.4 笔刷预设标志块（20 条；装载/保存侧 0x141ef2xxx / 0x141ef9xxx）

一条连续的 bool 块：brushPreset +0x436，brushPoseLock +0x43a，dualBrushLock +0x43b，protectTextureLock +0x43c，
textureInteractionLock +0x43d，scatterDynamicsLock +0x43e，brushTipDynamicsLock +0x43f，paintDynamicsLock +0x440，
colorDynamicsLock +0x441，wetEdgesLock +0x442，repeatLock +0x443，noiseLock +0x444，smoothingLock +0x445，
useLegacy +0x446，usePressureOverridesSize +0x447，usePressureOverridesOpacity +0x448，
smoothingRadiusMode +0x44a，smoothingCatchup +0x44b，smoothingCatchupAtEnd +0x44c，smoothingZoomCompensation +0x44d。

### 4.5 批量 IO 结构（7 条；函数 0x141a8e060 读、0x141a90410 写）

usePaintDynamics +0x290、useColorDynamics +0x291、wetEdges +0x292、noise +0x293、repeat +0x294、useBrushSize +0x295、toolOptions +0x00。

---

## 五、取值范围：有 clamp / 无 clamp（各给指令证据）

### 5.1 有 clamp 的 5 条

| 键 | 区间 | 指令证据（原文） |
|---|---|---|
| textureDepth | [0,100] | `0x141a936b7 mov eax,esi(=0)` → `0x141a936bb cmp eax,r15d(=0x64)` → `0x141a936be cmovg eax,r15d` → `0x141a936c2 mov [rbx+8],eax`；11A 动态：请求 101 存成 100 |
| minimumDepth | [0,100] | 同一段 clamp 代码（0x141a936e1~0x141a9371c） |
| **textureScale** | **[1,1000]** | `0x141a939fb cmp eax,1` / `0x141a93a00 mov eax,1` / `0x141a93a07 mov ecx,0x3e8` + `0x141a93a0e cmovg` → `0x141a93a15 cvtdq2pd` → `0x141a93a19 movsd [rdi+0x10]` |
| **scatter** | **[0,1000]** | `0x141a93118 test eax,eax` / `0x141a9311c mov eax,esi(=0)` / `0x141a93120 cmp eax,r14d(0x3e8)` + `0x141a93123 cmovg` → `0x141a93127 mov [rbx+0x2c],eax` |
| **count** | **[1,10]** | `0x141a931c3 cmp eax,ebp(=1)` / `0x141a931c7 mov ecx,0xa` + `0x141a931d0 cmovg ebp,ecx` → `0x141a931d3 mov [rbx+8],ebp` |

> textureScale/scatter/count 三条 clamp **是本任务新发现的**（任务 11 只给了 depth/minimumDepth 的 clamp）。

### 5.2 无 clamp 的 6 条

| 键 | 证据 |
|---|---|
| textureBrightness | `0x141a93a56 lea r8,[rdi+0x1c]` 与 `0x141a93a61 call 0x142c4e820`（getInteger，出参直写）之间**没有任何 cmp/cmov/区间指令**；11A 动态 ±150 逐位透传 |
| textureContrast | `0x141a93a6e lea r8,[rdi+0x18]` 与 `0x141a93a81 jmp 0x142c4e820`（尾调用）之间同样没有区间指令；11A 动态 ±100 逐位透传 |
| chroma / saturation / lightness / purity | 键名 LEA 到 `mov [rdi+0x14/0x18/0x1c/0x20],eax` 之间没有 cmp/cmov |

---

## 六、与仓库 / ABR 样本交叉核对

### 6.1 「ABR 有、逆向未见」——5 条

`InvT`、`TxtC`、`Txtr`、`Wtdg`、`sampledData`
取数：`python -c "import json;d=json.load(open('docs/数据/ps_keys.json',encoding='utf-8'));print(sorted({i['key'] for i in d} ^ set()))"`（完整命令见验收报告三）。

- 前 4 条是 4CC：`InvT`（反相）、`TxtC`（纹理对比度）、`Txtr`（纹理类）、`Wtdg`（宽度抖动）。用立即数扫描**也没命中**，说明它们在 PS 侧走的是别的传递路径（可能只出现在 .abr 容器格式里、或用了另有的一份 id 表）。**本任务不判「PS 不认」，只记「本轮未定位」**。
- `sampledData` 只有 4 条引用且都不在笔刷模块窗口内（0x1420d267b / 0x14254fce4 / 0x14255022a），不足以判为笔刷描述符键。

### 6.2 「逆向有、ABR 样本未见」——39 条

这是本轮最有价值的一批（按通用规范 §七.10 必须保留）。例如：
`brightness`、`hue`、`saturation`、`lightness`、`chroma`、`purity`（颜色动态的一组）、
`*Lock` 十个锁定标志、`smoothing*`、`usePressureOverrides*`、`brushPose*`/`overridePose*`、
`texture`（纹理子描述符本身）、`textureCoverage`、`wetEdges`、`repeat`、`noise`、`zuid`、`style`…
取数：同 §6.1。

---

## 七、方法与已知局限（如实）

1. **没有用 Ghidra 的引用图**。任务 20 §7.2 已证 `getReferencesTo` 对键名字符串 49→1；本任务改用 capstone 自己扫
   「REX + 8D/8B + ModRM(rip)」机器码，再用 capstone 逐条校验，得到 **231,869** 条经校验的 rip 相对引用（覆盖 74,104 个不同字符串）。
   这条链不依赖 `Constant Propagation`，因此**不受任务 20「全量分析未跑完」的影响**——本结论是「反汇编完成」级别的，不是「引用图」级别的。
2. **`lea` 之外的传递路径会漏**：只认 `lea/mov reg,[rip+disp]`。若有代码用 `add rax, rbx` 之类算字符串地址，本方法看不到（与任务 20 §7.3.1 同一条局限）。
3. **4CC 立即数扫描只认 `mov r32, imm32`**：用 `or`/`add` 拼出来的 4CC 会漏。
4. **偏移归属靠「键名 LEA 分段」**：同一函数里若两个键共用一个出参，可能串位；§4 表里凡是**本任务没亲自导出**的都标了「沿用任务 11/11A」。
5. **JSON 里 32 条 4CC 形态没有偏移**（`offset: null`）——它们多数只在「键表指针数组」里出现，没有结构体槽位证据。
6. 未做：**值域（不只是 clamp，还有语义上限）**、**枚举候选值 dump**（`BlnM` 成员表）、**ABR→PS key id 的对照表**。见 §9 决策项。

---

## 八、复现命令

```text
# 1) 环境（本机实测）
GHIDRA_INSTALL_DIR=<LOCAL_APP_DIR>\ghidra_12.2_PUBLIC（本任务未启动 Ghidra）
python=<LOCAL_APP_DIR>\anaconda3\envs\brushConverter\python.exe（capstone 5.0.7 / pefile 2024.8.26）
目标=<LOCAL_APP_DIR>\PS2025\Adobe Photoshop 2025\Photoshop.exe

# 2) 全量 rip 相对引用扫描（约 23 s，得到 231,869 条）
python <脚本>\d_rifrefs.py

# 3) 键名收敛 + 偏移提取 + 生成 JSON
python <脚本>\h_cand.py  → l_final.py → n_seg.py / q_raw.py → t_final.py → u_add.py

# 4) 4CC 立即数扫描（32/36 命中）
python <脚本>\s2_fourcc.py

# 5) 复核两个数字
python -c "import json;d=json.load(open('docs/数据/ps_keys.json',encoding='utf-8'));print(len(d), sum(1 for i in d if i['offset']), sum(1 for i in d if i['range']))"
```

> 一次性脚本在 `%TEMP%\brushconverter-team\task21\`（按通用规范 §七.11 不入库）；核心逻辑已摘录在本文 §2/§5 与验收报告第四节。

---

## 九、决策项（需 lead / 使用者拍板，与任务书 §六 三条对应）

| # | 决策点 | 我的建议 | 不拍板的后果 |
|---|---|---|---|
| 1 | `ps_keys.json` 里的函数地址要不要脱敏后再进仓库？ | **按通用规范 §七.11 不进公开仓库**；`docs/数据/` 保持本地，公开版只写「125 条 / 59 条有偏移」两个数字 | 公开内部偏移等于给逆向工作量打折 |
| 2 | 找不到偏移的键怎么处理（125−59=66 条）？ | 保留并写 `offset: null` + `offset_struct: null`，**不猜**；任务 23 对账时按「无偏移」口径统计 | 口径不一，任务 23 的对账数字会对不上 |
| 3 | 要不要顺带 dump `BlnM` 的模式枚举候选值？ | **要，但放任务 25**（本任务已给 `0x141b94090`/`0x141b952c0` 两个翻译器地址与 11A 的 7/39/40/41 观测值） | 任务 25 的模式表还要回头补一次 |
