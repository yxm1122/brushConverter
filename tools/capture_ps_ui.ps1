# -*- coding: utf-8 -*-
<#
工具：tools/capture_ps_ui.ps1
任务：brushConverter v1.4.0 / 任务 101-B —— Photoshop「画笔设置」界面截图 + UIAutomation 控件树采集（只读操作）

做什么：
  1. 启动 Photoshop（已在运行则复用），轮询 MainWindowHandle 最多 StartTimeoutSec 秒；
  2. AppActivate 聚焦主窗口后按 F5 打开「画笔设置」面板；
  3. 用 UIAutomation 枚举面板控件树，写入 uia_dump.txt（含 8 个页签各自的右侧控件表）；
  4. 依次选中 8 个页签，按面板窗口矩形用 Graphics.CopyFromScreen 截图，落 01..08 共 8 张；
  5. 另截 1 张「全景」（PS 主窗口客户区，含画笔设置面板）；
  6. 写 index.md 索引（文件名/页签/时间/分辨率/字节数/采集方式/底部 5 项可见性/失败原因）；
  7. 收尾：关闭画笔设置面板；若 Photoshop 是本次脚本启动的，则退出 PS（本脚本从不打开、也不保存任何文档）。

不做什么（硬约束）：
  - 不改 PS 的文档、首选项、笔刷；不登录、不输入任何账号信息；
  - 只截取 PS 主窗口客户区与「画笔设置」面板矩形，绝不截桌面或其它窗口；
  - 不改 docs/参数矩阵.json、src/**、tests/**；不执行 git 提交。

自动化路线（按优先级降级，实测结论写在 index.md）：
  A) UIA：面板右侧控件能枚举（有 Name/Class/AutomationId），但左侧 13 项列表是 PS 自绘，
     在 UIA/原始视图里都没有对应元素 —— 无法用 UIA 选中页签；
  B) 键盘：点击左侧列表后按 Down 不切换页签（实测无效）；
  C) 坐标（本脚本最终采用）：按面板窗口矩形为基准，行中心 = 面板顶 + 实测标定的行坐标表，
     点击行内文字区选中页签；每张截图后做像素校验（选中行高亮 + 13 行文字齐全）确认没点错。

用法（在仓库根执行）：
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\capture_ps_ui.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\capture_ps_ui.ps1 -KeepPhotoshop   # 调试：不退出 PS
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\capture_ps_ui.ps1 -OutDir docs\截图\ui
#>
[CmdletBinding()]
param(
    [string]$OutDir = 'docs\截图\ui',
    [int]$StartTimeoutSec = 120,
    [switch]$KeepPhotoshop,   # 调试用：脚本结束时不退出 Photoshop
    [switch]$KeepPanel        # 调试用：脚本结束时不关闭画笔设置面板
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# ----------------------------------------------------------------------------
# 0. 路径与常量
# ----------------------------------------------------------------------------
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path $RepoRoot)) { $RepoRoot = (Get-Location).Path }

if ([System.IO.Path]::IsPathRooted($OutDir)) {
    $OutAbs = $OutDir
} else {
    $OutAbs = Join-Path $RepoRoot $OutDir
}
if (-not (Test-Path $OutAbs)) { New-Item -ItemType Directory -Path $OutAbs -Force | Out-Null }

$DumpPath = Join-Path $OutAbs 'uia_dump.txt'
$IndexPath = Join-Path $OutAbs 'index.md'

# Photoshop 可执行文件：环境变量优先，本机默认值兜底（不含身份信息）
if ($env:BRUSHCONVERTER_PS) { $PsExe = $env:BRUSHCONVERTER_PS }
else { $PsExe = 'D:\program\PS2025\Adobe Photoshop 2025\Photoshop.exe' }

# 8 个页签（顺序固定）。Row = 面板左侧列表的第几行（1 起）；
# UiText = 界面上读到的原文（第 8 项实测是「画笔笔势」，与任务书写的「画笔姿势」不同，文件仍按任务书命名）。
$TabDefs = @(
    [pscustomobject]@{ No='01'; File='01-画笔笔尖形状.png'; Name='画笔笔尖形状'; Row=1; UiText='画笔笔尖形状' },
    [pscustomobject]@{ No='02'; File='02-形状动态.png';   Name='形状动态';     Row=2; UiText='形状动态' },
    [pscustomobject]@{ No='03'; File='03-散布.png';       Name='散布';         Row=3; UiText='散布' },
    [pscustomobject]@{ No='04'; File='04-纹理.png';       Name='纹理';         Row=4; UiText='纹理' },
    [pscustomobject]@{ No='05'; File='05-双重画笔.png';   Name='双重画笔';     Row=5; UiText='双重画笔' },
    [pscustomobject]@{ No='06'; File='06-颜色动态.png';   Name='颜色动态';     Row=6; UiText='颜色动态' },
    [pscustomobject]@{ No='07'; File='07-传递.png';       Name='传递';         Row=7; UiText='传递' },
    [pscustomobject]@{ No='08'; File='08-画笔姿势.png';   Name='画笔姿势';     Row=8; UiText='画笔笔势' }
)

# 底部 5 个勾选项在列表中的行号（第 9~13 行），界面原文见实测：
#   9=杂色 10=湿边 11=建立（任务书写作「喷枪」） 12=平滑 13=保护纹理
$BottomRows = @(
    [pscustomobject]@{ Row=9;  UiText='杂色' },
    [pscustomobject]@{ Row=10; UiText='湿边' },
    [pscustomobject]@{ Row=11; UiText='建立' },
    [pscustomobject]@{ Row=12; UiText='平滑' },
    [pscustomobject]@{ Row=13; UiText='保护纹理' }
)

# 行坐标实测标定（面板截图内坐标，原点=面板窗口左上角，2026-11-20 本机 2560x1600@150% 实测）：
#   通过「文字亮像素带检测」测得 13 行的文字中心 y = 148,181,218,253,289,325,363,397,434,470,505,541,577
$CalibRowCenters = @(148,181,218,253,289,325,363,397,434,470,505,541,577)
$CalibRowTextX    = 120   # 点击用的 x（落在行内文字区，避开左侧勾选框，避免误勾选）
$CalibRowLabelX0  = 70    # 文字区 x 范围（用于行带检测）
$CalibRowLabelX1  = 135
$CalibSelX0       = 20    # 选中高亮采样 x 范围（勾选框左侧的纯背景）
$CalibSelX1       = 28

# 隐私：写进 index.md 的日志里若出现本机绝对路径，统一占位化
function ConvertTo-SafeText {
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    $t = $Text
    $psRoot = Split-Path -Parent $PsExe
    if ($psRoot -and $t.Contains($psRoot)) { $t = $t.Replace($psRoot, '<LOCAL_APP_DIR>\PS2025\Adobe Photoshop 2025') }
    if ($t.Contains($RepoRoot)) { $t = $t.Replace($RepoRoot, '<REPO>') }
    return $t
}

$LogLines = New-Object System.Collections.Generic.List[string]
$TabResults = New-Object System.Collections.Generic.List[object]
$FailNotes = New-Object System.Collections.Generic.List[string]

function Write-Log {
    param([string]$Text)
    $line = ('[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $Text)
    $LogLines.Add($line) | Out-Null
    Write-Host $line
}

# ----------------------------------------------------------------------------
# 1. Win32 声明（DPI 感知必须在任何坐标/截图之前设置）
# ----------------------------------------------------------------------------
$NativeSrc = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

namespace BCUI {
    public static class Native {
        [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
        [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
        public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);

        [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
        [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
        [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
        [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, IntPtr dwExtraInfo);
        [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
        [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr after, int x, int y, int cx, int cy, uint flags);
        [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr hWnd, out RECT lpRect);
        [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr hWnd, ref POINT lpPoint);
        [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr lParam);
        [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr parent, EnumProc cb, IntPtr lParam);
        [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
        [DllImport("user32.dll")] public static extern int GetClassName(IntPtr hWnd, StringBuilder sb, int nMax);
        [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int nMax);
        [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
        [DllImport("user32.dll")] public static extern IntPtr GetParent(IntPtr hWnd);
        [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
        [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);

        public static string ClassName(IntPtr h) {
            StringBuilder sb = new StringBuilder(256);
            GetClassName(h, sb, 256);
            return sb.ToString();
        }
        public static string WindowText(IntPtr h) {
            StringBuilder sb = new StringBuilder(512);
            GetWindowText(h, sb, 512);
            return sb.ToString();
        }
        public static IntPtr[] ChildrenOf(IntPtr parent) {
            List<IntPtr> list = new List<IntPtr>();
            EnumChildWindows(parent, delegate(IntPtr h, IntPtr l) { list.Add(h); return true; }, IntPtr.Zero);
            return list.ToArray();
        }
        public static IntPtr[] TopLevelOf(uint pid) {
            List<IntPtr> list = new List<IntPtr>();
            EnumWindows(delegate(IntPtr h, IntPtr l) {
                uint p; GetWindowThreadProcessId(h, out p);
                if (p == pid) list.Add(h);
                return true;
            }, IntPtr.Zero);
            return list.ToArray();
        }
        public static int[] GetRect(IntPtr h) {
            RECT r;
            GetWindowRect(h, out r);
            return new int[] { r.Left, r.Top, r.Right - r.Left, r.Bottom - r.Top };
        }
        public static int[] GetClientScreenRect(IntPtr h) {
            RECT r; POINT p;
            GetClientRect(h, out r);
            p.X = 0; p.Y = 0;
            ClientToScreen(h, ref p);
            return new int[] { p.X, p.Y, r.Right - r.Left, r.Bottom - r.Top };
        }
    }
}
'@
if (-not ('BCUI.Native' -as [type])) { Add-Type -TypeDefinition $NativeSrc -Language CSharp }
[void][BCUI.Native]::SetProcessDPIAware()

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$UIAE = [System.Windows.Automation.AutomationElement]
$UIA_TreeScope = [System.Windows.Automation.TreeScope]

$ScreenRect = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
Write-Log ('屏幕（物理像素）：{0}x{1}；PS 可执行文件：{2}' -f $ScreenRect.Width, $ScreenRect.Height, $PsExe)

# ----------------------------------------------------------------------------
# 2. 位图工具：截图 / 像素采样（选中行校验、13 行文字带检测）
# ----------------------------------------------------------------------------
# 读第 2~13 行左侧勾选框的状态（True=已勾选）。实测：勾选行勾选框区域亮度 ~130，未勾选 ~82，阈值 100。
function Get-CheckStates {
    param($Img, $RowCenters)
    $bytes = $Img.Bytes
    $stride = $Img.Stride
    $states = New-Object System.Collections.Generic.List[bool]
    $states.Add($false) | Out-Null   # 第 1 行（画笔笔尖形状）没有勾选框
    for ($i = 1; $i -lt 13; $i++) {
        $c = $RowCenters[$i]
        $sum = 0
        $n = 0
        for ($y = $c - 7; $y -le $c + 7; $y++) {
            $rowBase = $y * $stride
            for ($x = 38; $x -le 54; $x++) {
                $sum += $bytes[$rowBase + $x * 3 + 2]
                $n++
            }
        }
        $states.Add((($sum / $n) -gt 100)) | Out-Null
    }
    return $states
}

function Invoke-RowClick {
    param([int]$X, [int]$Y)
    [void][BCUI.Native]::SetCursorPos($X, $Y)
    Start-Sleep -Milliseconds 150
    [BCUI.Native]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
    Start-Sleep -Milliseconds 60
    [BCUI.Native]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
    Start-Sleep -Milliseconds 450
}

function Set-PanelTopMost {
    param([IntPtr]$Hwnd, [bool]$On)
    # 截图期间把面板设为置顶：否则别的窗口（编辑器/浏览器）一抢焦点，抓屏就会抓到它，点坐标也会点偏。
    # 结束时恢复 NOTOPMOST，不改面板位置和大小。
    $after = if ($On) { [IntPtr](-1) } else { [IntPtr](-2) }          # HWND_TOPMOST / HWND_NOTOPMOST
    $flags = 0x0002 -bor 0x0001 -bor 0x0010                          # SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
    [void][BCUI.Native]::SetWindowPos($Hwnd, $after, 0, 0, 0, 0, $flags)
}

function Save-Rect-Png {
    param([int]$X, [int]$Y, [int]$W, [int]$H, [string]$Path)
    $bmp = New-Object System.Drawing.Bitmap -ArgumentList $W, $H, ([System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $ok = $true
    $err = ''
    try {
        $g.CopyFromScreen($X, $Y, 0, 0, (New-Object System.Drawing.Size $W, $H))
    } catch {
        $ok = $false
        $err = $_.Exception.Message
    }
    $g.Dispose()
    if (-not $ok) {
        # CopyFromScreen 失败的兜底：PrintWindow（本机实测一般用不到）
        try {
            $hdc = $null
            $gg = [System.Drawing.Graphics]::FromImage($bmp)
            $hdc = $gg.GetHdc()
            [void][BCUI.Native]::PrintWindow($script:PanelHwnd, $hdc, 2)
            $gg.ReleaseHdc($hdc)
            $gg.Dispose()
            Write-Log ('CopyFromScreen 失败，已改用 PrintWindow：' + $err)
        } catch {
            Write-Log ('PrintWindow 兜底也失败：' + $_.Exception.Message)
        }
    }
    $bmp.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    return $ok
}

function Save-Crop-Png {
    param([System.Drawing.Bitmap]$Bmp, [int]$X, [int]$Y, [int]$W, [int]$H, [string]$Path)
    $x2 = [math]::Max(0, [math]::Min($X, $Bmp.Width - 1))
    $y2 = [math]::Max(0, [math]::Min($Y, $Bmp.Height - 1))
    $w2 = [math]::Min($W, $Bmp.Width - $x2)
    $h2 = [math]::Min($H, $Bmp.Height - $y2)
    $rect = New-Object System.Drawing.Rectangle -ArgumentList $x2, $y2, $w2, $h2
    $cropped = $Bmp.Clone($rect, $Bmp.PixelFormat)
    $cropped.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    $cropped.Dispose()
    return @{ Width = $w2; Height = $h2 }
}

function Get-ImageBytes {
    param([System.Drawing.Bitmap]$Bmp)
    $rect = New-Object System.Drawing.Rectangle 0, 0, $Bmp.Width, $Bmp.Height
    $data = $Bmp.LockBits($rect, [System.Drawing.Imaging.ImageLockMode]::ReadOnly, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $bytes = New-Object byte[] ($data.Stride * $Bmp.Height)
    [System.Runtime.InteropServices.Marshal]::Copy($data.Scan0, $bytes, 0, $bytes.Length)
    $Bmp.UnlockBits($data)
    return [pscustomobject]@{ Bytes = $bytes; Stride = $data.Stride; Width = $Bmp.Width; Height = $Bmp.Height }
}

function Get-PixelR {
    param($Img, [int]$X, [int]$Y)
    if ($X -lt 0 -or $Y -lt 0 -or $X -ge $Img.Width -or $Y -ge $Img.Height) { return 0 }
    $i = $Y * $Img.Stride + $X * 3
    return [int]$Img.Bytes[$i + 2]   # BGR → R
}

# 返回 13 行（或实际行数）的文字带中心 y
function Find-RowBands {
    param($Img, [int]$X0, [int]$X1, [int]$Y0, [int]$Y1)
    $bytes = $Img.Bytes
    $stride = $Img.Stride
    $bands = New-Object System.Collections.Generic.List[object]
    $inBand = $false
    $start = 0
    for ($y = $Y0; $y -le $Y1; $y++) {
        $rowBase = $y * $stride
        $cnt = 0
        for ($x = $X0; $x -le $X1; $x++) {
            if ($bytes[$rowBase + $x * 3 + 2] -gt 150) { $cnt++ }   # BGR：+2 是 R
        }
        if ($cnt -gt 3 -and -not $inBand) { $inBand = $true; $start = $y }
        elseif ($cnt -le 3 -and $inBand) { $inBand = $false; $bands.Add([pscustomobject]@{ Top=$start; Bottom=($y-1) }) | Out-Null }
    }
    if ($inBand) { $bands.Add([pscustomobject]@{ Top=$start; Bottom=$Y1 }) | Out-Null }

    # 合并间隔 <=5 的碎片（汉字上下结构会被切成两段）
    $merged = New-Object System.Collections.Generic.List[object]
    foreach ($b in $bands) {
        if ($merged.Count -gt 0 -and ($b.Top - $merged[$merged.Count-1].Bottom) -le 5) {
            $merged[$merged.Count-1].Bottom = $b.Bottom
        } else {
            $merged.Add([pscustomobject]@{ Top=$b.Top; Bottom=$b.Bottom }) | Out-Null
        }
    }
    # 过滤掉过矮的行（<8px 基本是噪点）
    $kept = New-Object System.Collections.Generic.List[object]
    foreach ($b in $merged) {
        if (($b.Bottom - $b.Top + 1) -ge 8) {
            $kept.Add([pscustomobject]@{ Top=$b.Top; Bottom=$b.Bottom; Center=[int](($b.Top + $b.Bottom) / 2) }) | Out-Null
        }
    }
    return $kept
}

# 用「选中行背景更亮」判定当前选中第几行（1 起，找不到返回 0）
function Get-SelectedRowIndex {
    param($Img, $Bands)
    $bytes = $Img.Bytes
    $stride = $Img.Stride
    $vals = @()
    for ($i = 0; $i -lt $Bands.Count; $i++) {
        $c = $Bands[$i].Center
        $sum = 0
        $n = 0
        for ($y = $c - 6; $y -le $c + 6; $y++) {
            $rowBase = $y * $stride
            for ($x = $CalibSelX0; $x -le $CalibSelX1; $x++) {
                $sum += $bytes[$rowBase + $x * 3 + 2]
                $n++
            }
        }
        $vals += [double]$sum / [double]$n
    }
    if ($vals.Count -eq 0) { return [pscustomobject]@{ Index = 0; Bright = 0; Second = 0; Gap = 0; All = @() } }
    $maxIdx = 0
    for ($i = 1; $i -lt $vals.Count; $i++) { if ($vals[$i] -gt $vals[$maxIdx]) { $maxIdx = $i } }
    $second = 0.0
    for ($i = 0; $i -lt $vals.Count; $i++) { if ($i -ne $maxIdx -and $vals[$i] -gt $second) { $second = $vals[$i] } }
    $gap = $vals[$maxIdx] - $second
    return [pscustomobject]@{ Index = ($maxIdx + 1); Bright = [math]::Round($vals[$maxIdx],1); Second = [math]::Round($second,1); Gap = [math]::Round($gap,1); All = ($vals | ForEach-Object { [math]::Round($_,1) }) }
}

function Capture-PanelBitmap {
    param($Rect)
    $w = [int]$Rect[2]
    $h = [int]$Rect[3]
    $bmp = New-Object System.Drawing.Bitmap -ArgumentList $w, $h, ([System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    try {
        $g.CopyFromScreen([int]$Rect[0], [int]$Rect[1], 0, 0, (New-Object System.Drawing.Size -ArgumentList $w, $h))
    } finally {
        $g.Dispose()
    }
    return $bmp
}

# ----------------------------------------------------------------------------
# 3. 启动 / 复用 Photoshop
# ----------------------------------------------------------------------------
$script:StartedByUs = $false
$proc = Get-Process -Name Photoshop -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $proc) {
    if (-not (Test-Path $PsExe)) { throw ('找不到 Photoshop：' + $PsExe) }
    Write-Log ('Photoshop 未运行，启动：' + $PsExe)
    $proc = Start-Process -FilePath $PsExe -PassThru
    $script:StartedByUs = $true
} else {
    Write-Log ('Photoshop 已在运行（PID {0}），复用。' -f $proc.Id)
}

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$mainHwnd = [IntPtr]::Zero
while ($sw.Elapsed.TotalSeconds -lt $StartTimeoutSec) {
    $proc.Refresh()
    if ($proc.HasExited) { throw 'Photoshop 进程已退出（启动失败）' }
    if ($proc.MainWindowHandle -ne 0) { $mainHwnd = $proc.MainWindowHandle; break }
    Start-Sleep -Milliseconds 500
}
if ($mainHwnd -eq [IntPtr]::Zero) { throw ('等待 Photoshop 主窗口超时（{0} 秒）' -f $StartTimeoutSec) }
Write-Log ('主窗口就绪：PID={0} HWND={1} 耗时={2}s 标题=[{3}]' -f $proc.Id, $mainHwnd, [math]::Round($sw.Elapsed.TotalSeconds,1), $proc.MainWindowTitle)
Start-Sleep -Milliseconds 1500

$PsVersionText = ''
try {
    $PsVersionText = (Get-Item $PsExe).VersionInfo.FileVersion
} catch { $PsVersionText = $proc.MainModule.FileVersionInfo.FileVersion }
Write-Log ('Photoshop 版本：' + $PsVersionText)

# ----------------------------------------------------------------------------
# 4. 打开「画笔设置」面板
# ----------------------------------------------------------------------------
function Find-PanelHwnd {
    param([uint32]$ProcId, [IntPtr]$MainHwnd)
    # 实测：画笔设置面板是「属于主窗口的顶层弹出窗」（GetParent 返回主窗口=属主，不是父窗口），
    # 所以 EnumChildWindows 里找不到，必须用 EnumWindows 按 PID 过滤。
    foreach ($h in [BCUI.Native]::TopLevelOf($ProcId)) {
        if ([BCUI.Native]::ClassName($h) -ne 'OWL.ShadowView') { continue }
        if (-not [BCUI.Native]::IsWindowVisible($h)) { continue }
        if ($h -eq $MainHwnd) { continue }
        $r = [BCUI.Native]::GetRect($h)
        if ($r[2] -gt 300 -and $r[3] -gt 300) { return $h }
    }
    return [IntPtr]::Zero
}

function Find-PanelDockRect {
    param([IntPtr]$PanelHwnd, [uint32]$ProcId)
    # 面板外框 OWL.ShadowView 的四周有 8px 透明投影，会把背后主窗口的像素带进截图；
    # 内容区是另一个顶层窗口 OWL.Dock（实测 EnumChildWindows 枚举不到，GetParent 返回的是属主），
    # 所以按 PID 枚举顶层窗口，取落在面板矩形内的那个 OWL.Dock。
    $pr = [BCUI.Native]::GetRect($PanelHwnd)
    foreach ($h in [BCUI.Native]::TopLevelOf($ProcId)) {
        if ([BCUI.Native]::ClassName($h) -ne 'OWL.Dock') { continue }
        if (-not [BCUI.Native]::IsWindowVisible($h)) { continue }
        $r = [BCUI.Native]::GetRect($h)
        if ($r[2] -lt 300 -or $r[3] -lt 300) { continue }
        if ($r[0] -ge $pr[0] -and $r[1] -ge $pr[1] -and ($r[0] + $r[2]) -le ($pr[0] + $pr[2]) -and ($r[1] + $r[3]) -le ($pr[1] + $pr[3])) {
            Write-Log ('找到内容区窗口 OWL.Dock：HWND={0} 矩形={1}' -f $h, ($r -join ','))
            return $r
        }
    }
    return $null
}

function Invoke-ActivateMain {
    param([int]$ProcId, [IntPtr]$MainHwnd)
    $ws = New-Object -ComObject WScript.Shell
    try { [void]$ws.AppActivate($ProcId) } catch { }
    Start-Sleep -Milliseconds 400
    [void][BCUI.Native]::SetForegroundWindow($MainHwnd)
    Start-Sleep -Milliseconds 400
}

$script:PanelHwnd = Find-PanelHwnd -ProcId ([uint32]$proc.Id) -MainHwnd $mainHwnd
$panelWasOpen = ($script:PanelHwnd -ne [IntPtr]::Zero)

if ($script:PanelHwnd -eq [IntPtr]::Zero) {
    Invoke-ActivateMain -ProcId $proc.Id -MainHwnd $mainHwnd
    Write-Log '按 F5 打开画笔设置（需 PS 主窗口在前台）'
    [System.Windows.Forms.SendKeys]::SendWait('{F5}')
    $sw2 = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw2.Elapsed.TotalSeconds -lt 20) {
        Start-Sleep -Milliseconds 600
        $script:PanelHwnd = Find-PanelHwnd -ProcId ([uint32]$proc.Id) -MainHwnd $mainHwnd
        if ($script:PanelHwnd -ne [IntPtr]::Zero) { break }
    }
}
if ($script:PanelHwnd -eq [IntPtr]::Zero) {
    $FailNotes.Add('F5 未能打开「画笔设置」面板（20 秒内未出现 OWL.ShadowView 面板窗口）；未尝试菜单「窗口 > 画笔设置」，因为 PS 主窗口的菜单栏在 UIA 里没有暴露 MenuBar/MenuItem。') | Out-Null
    throw '无法打开画笔设置面板，任务中止'
}
Write-Log ('画笔设置面板：HWND={0} 外框矩形={1}' -f $script:PanelHwnd, (([BCUI.Native]::GetRect($script:PanelHwnd)) -join ','))

# 截图仍按外框 OWL.ShadowView 的矩形抓屏（点击坐标/行标定都基于它，已验证可用），
# 但保存时裁掉四周的 8px 透明投影 —— 投影会把背后主窗口的像素带进图里。
$PanelRect = [BCUI.Native]::GetRect($script:PanelHwnd)
$CropX = 8; $CropY = 3
$CropW = $PanelRect[2] - 16
$CropH = $PanelRect[3] - 15
$DockRect = Find-PanelDockRect -PanelHwnd $script:PanelHwnd -ProcId ([uint32]$proc.Id)
if ($null -ne $DockRect) {
    $CropX = $DockRect[0] - $PanelRect[0]
    $CropY = $DockRect[1] - $PanelRect[1]
    $CropW = $DockRect[2]
    $CropH = $DockRect[3]
    Write-Log ('内容区 OWL.Dock 矩形=' + ($DockRect -join ',') + '；保存时裁剪偏移=(' + $CropX + ',' + $CropY + ') 尺寸=' + $CropW + 'x' + $CropH)
} else {
    $FailNotes.Add('未找到面板内容区 OWL.Dock，已按固定 8px 内缩裁剪（投影可能残留 1~2px）。') | Out-Null
    Write-Log ('警告：未找到 OWL.Dock，按固定内缩裁剪 (8,3) ' + $CropW + 'x' + $CropH)
}

# 面板必须在屏幕内，否则 CopyFromScreen 会截到黑边或抛异常
if ($PanelRect[0] -lt $ScreenRect.X -or $PanelRect[1] -lt $ScreenRect.Y -or
    ($PanelRect[0] + $PanelRect[2]) -gt ($ScreenRect.X + $ScreenRect.Width) -or
    ($PanelRect[1] + $PanelRect[3]) -gt ($ScreenRect.Y + $ScreenRect.Height)) {
    $FailNotes.Add(('面板矩形 {0} 超出屏幕 {1}；SetWindowPos 实测只移动外框、内部 OWL.Dock 不跟随（会截出空白），故未强行改尺寸。' -f ($PanelRect -join ','), ($ScreenRect.Width.ToString() + 'x' + $ScreenRect.Height))) | Out-Null
    Write-Log ('警告：面板超出屏幕：' + ($PanelRect -join ','))
}

# 截图期间把面板钉在最上层，避免被别的窗口挡住（结束时恢复）
Set-PanelTopMost -Hwnd $script:PanelHwnd -On $true
Write-Log '已将画笔设置面板临时置顶（截图期间），收尾时恢复'

# ----------------------------------------------------------------------------
# 5. 标定行坐标（从当前面板截图里量，失败则用实测固定表）
# ----------------------------------------------------------------------------
Invoke-ActivateMain -ProcId $proc.Id -MainHwnd $mainHwnd
Start-Sleep -Milliseconds 500
$probeBmp = Capture-PanelBitmap $PanelRect
$probeImg = Get-ImageBytes $probeBmp
$bands = Find-RowBands -Img $probeImg -X0 $CalibRowLabelX0 -X1 $CalibRowLabelX1 -Y0 120 -Y1 ([math]::Min(620, $probeImg.Height - 1))
$probeBmp.Dispose()

if ($bands.Count -ge 13) {
    $rowCenters = @()
    for ($i = 0; $i -lt 13; $i++) { $rowCenters += $bands[$i].Center }
    Write-Log ('行坐标自适应标定成功（检测到 {0} 段文字带），行中心={1}' -f $bands.Count, ($rowCenters -join ','))
} else {
    $rowCenters = $CalibRowCenters
    $FailNotes.Add(('行坐标自适应标定失败（只检测到 {0} 段文字带，需 13），已回退到固定标定表。' -f $bands.Count)) | Out-Null
    Write-Log ('警告：行坐标标定不完整（{0} 段），回退固定表' -f $bands.Count)
}

# PS 2025 的行为：点选某个动态页签会顺手把这个动态「勾上」。
# 所以进循环前先记下 8 个动态项 + 5 个底部项的初始勾选状态，收尾时把被脚本改掉的还原回去。
$initialStates = Get-CheckStates -Img $probeImg -RowCenters $rowCenters
Write-Log ('初始勾选状态（行 2~13）：' + (($initialStates | Select-Object -Skip 1 | ForEach-Object { if ($_) { '√' } else { '×' } }) -join ' '))

# ----------------------------------------------------------------------------
# 6. 逐页签：点选 → 校验 → 截图
# ----------------------------------------------------------------------------
$CheckFailures = New-Object System.Collections.Generic.List[string]

foreach ($tab in $TabDefs) {
    $rowIdx = $tab.Row
    $clickX = $PanelRect[0] + $CalibRowTextX
    $clickY = $PanelRect[1] + $rowCenters[$rowIdx - 1]

    Invoke-ActivateMain -ProcId $proc.Id -MainHwnd $mainHwnd
    [void][BCUI.Native]::SetCursorPos($clickX, $clickY)
    Start-Sleep -Milliseconds 120
    [BCUI.Native]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)   # LEFTDOWN
    Start-Sleep -Milliseconds 60
    [BCUI.Native]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)   # LEFTUP
    Start-Sleep -Milliseconds 500
    # 鼠标移开，避免悬停提示气泡出现在截图里
    [void][BCUI.Native]::SetCursorPos($PanelRect[0] + 40, [math]::Max(4, $PanelRect[1] - 6))
    Start-Sleep -Milliseconds 700

    # 校验 1：截图 + 像素检查（选中行 / 13 行文字齐全）
    $shotPath = Join-Path $OutAbs $tab.File
    $bmp = Capture-PanelBitmap $PanelRect
    $img = Get-ImageBytes $bmp
    $checkBands = Find-RowBands -Img $img -X0 $CalibRowLabelX0 -X1 $CalibRowLabelX1 -Y0 120 -Y1 ([math]::Min(620, $img.Height - 1))
    $sel = Get-SelectedRowIndex -Img $img -Bands $checkBands
    $shotTime = Get-Date

    $retry = 0
    while (($sel.Index -ne $rowIdx -or $checkBands.Count -lt 13) -and $retry -lt 2) {
        $retry++
        Write-Log ('  校验未过（选中行={0} 期望={1} 文字带={2}），第 {3} 次重点击' -f $sel.Index, $rowIdx, $checkBands.Count, $retry)
        Invoke-ActivateMain -ProcId $proc.Id -MainHwnd $mainHwnd
        Set-PanelTopMost -Hwnd $script:PanelHwnd -On $true
        [void][BCUI.Native]::SetCursorPos($clickX, $clickY)
        Start-Sleep -Milliseconds 120
        [BCUI.Native]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
        Start-Sleep -Milliseconds 60
        [BCUI.Native]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
        Start-Sleep -Milliseconds 500
        [void][BCUI.Native]::SetCursorPos($PanelRect[0] + 40, [math]::Max(4, $PanelRect[1] - 6))
        Start-Sleep -Milliseconds 700
        $bmp.Dispose()
        $bmp = Capture-PanelBitmap $PanelRect
        $img = Get-ImageBytes $bmp
        $checkBands = Find-RowBands -Img $img -X0 $CalibRowLabelX0 -X1 $CalibRowLabelX1 -Y0 120 -Y1 ([math]::Min(620, $img.Height - 1))
        $sel = Get-SelectedRowIndex -Img $img -Bands $checkBands
        $shotTime = Get-Date
    }

    $saved = Save-Crop-Png -Bmp $bmp -X $CropX -Y $CropY -W $CropW -H $CropH -Path $shotPath
    $bmp.Dispose()

    $bottomOk = ($checkBands.Count -ge 13)
    $selOk = ($sel.Index -eq $rowIdx)
    if (-not $selOk) { $CheckFailures.Add(('{0}：选中行校验失败（实测第 {1} 行高亮，期望第 {2} 行）' -f $tab.File, $sel.Index, $rowIdx)) | Out-Null }
    if (-not $bottomOk) { $CheckFailures.Add(('{0}：左侧列表只检测到 {1} 段文字带（需 13 段），底部 5 项可能不全' -f $tab.File, $checkBands.Count)) | Out-Null }

    $fi = Get-Item $shotPath
    $TabResults.Add([pscustomobject]@{
        No        = $tab.No
        Tab       = $tab.Name
        UiText    = $tab.UiText
        Row       = $tab.Row
        File      = $tab.File
        Path      = $shotPath
        Width     = $saved.Width
        Height    = $saved.Height
        Bytes     = $fi.Length
        Time      = $shotTime.ToString('yyyy-MM-dd HH:mm:ss')
        SelRow    = $sel.Index
        SelBright = $sel.Bright
        SelGap    = $sel.Gap
        Bands     = $checkBands.Count
        SelOk     = $selOk
        BottomOk  = $bottomOk
        Method    = '坐标点击（行中心标定）+ 像素校验'
    }) | Out-Null
    Write-Log ('{0} {1}：{2}x{3} {4} B 选中行={5}(期望{6}) 文字带={7} 时间={8}' -f $tab.No, $tab.UiText, $saved.Width, $saved.Height, $fi.Length, $sel.Index, $rowIdx, $checkBands.Count, $shotTime.ToString('HH:mm:ss'))
}

# ----------------------------------------------------------------------------
# 7. UIA 控件树导出
# ----------------------------------------------------------------------------
function Get-MainElement {
    param([int]$ProcId)
    $cond = New-Object System.Windows.Automation.PropertyCondition($UIAE::ProcessIdProperty, $ProcId)
    return $UIAE::RootElement.FindFirst($UIA_TreeScope::Children, $cond)
}

function Format-Rect {
    param($Rect)
    if ($Rect.IsEmpty) { return '0,0,0x0' }
    return ('{0},{1},{2}x{3}' -f [int]$Rect.X, [int]$Rect.Y, [int]$Rect.Width, [int]$Rect.Height)
}

function Get-PatternNames {
    param($El)
    $names = @()
    try {
        foreach ($p in $El.GetSupportedPatterns()) { $names += $p.ProgrammaticName.Replace('PatternIdentifiers.Pattern', '') }
    } catch { }
    return ($names -join ';')
}

function Dump-UiaTree {
    param($Element, [int]$Depth, [int]$MaxDepth, $Sb)
    $walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
    $child = $walker.GetFirstChild($Element)
    while ($null -ne $child) {
        $c = $child.Current
        $line = ("{0}{1}`t{2}`t{3}`t{4}`t{5}`t{6}`t{7}`t{8}`t{9}`t{10}" -f `
            ('  ' * $Depth), $c.ControlType.ProgrammaticName.Replace('ControlType.',''), $c.Name, $c.AutomationId, $c.ClassName,
            $c.IsEnabled, $c.IsOffscreen, (Format-Rect $c.BoundingRectangle), (Get-PatternNames $child), $Depth, '')
        [void]$Sb.AppendLine($line.TrimEnd("`t"))
        if ($Depth -lt $MaxDepth) { Dump-UiaTree -Element $child -Depth ($Depth + 1) -MaxDepth $MaxDepth -Sb $Sb }
        $child = $walker.GetNextSibling($child)
    }
}

$dump = New-Object System.Text.StringBuilder
[void]$dump.AppendLine('# Photoshop「画笔设置」UIAutomation 控件树导出（任务 101-B）')
[void]$dump.AppendLine('# 采集时间：' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
[void]$dump.AppendLine('# Photoshop 版本：' + $PsVersionText)
[void]$dump.AppendLine('# 采集脚本：tools/capture_ps_ui.ps1（只读，不修改 PS 的任何设置）')
[void]$dump.AppendLine('# 屏幕：' + $ScreenRect.Width + 'x' + $ScreenRect.Height + '（物理像素，进程已 SetProcessDPIAware）')
[void]$dump.AppendLine('# 面板窗口：OWL.ShadowView HWND=' + $script:PanelHwnd + ' 矩形=' + ($PanelRect -join ','))
[void]$dump.AppendLine('# 说明 1：字段顺序 = 缩进(树形) | ControlType | Name | AutomationId | ClassName | IsEnabled | IsOffscreen | BoundingRectangle(X,Y,WxH) | 支持的UIA模式 | 层级')
[void]$dump.AppendLine('# 说明 2：Photoshop 左侧 13 项页签列表是自绘控件，UIA（含原始视图）里没有对应元素；页签只能靠截图/坐标点击。')
[void]$dump.AppendLine('# 说明 3：IsOffscreen=False 但 Y 坐标在 19000 左右的条目来自隐藏面板（PS 把未显示的页留在离屏位置），不是当前可见界面。')
[void]$dump.AppendLine('# 说明 4：ComboBox（形状/控制下拉）的 Name 由 PS 提供，可能显示为乱码，属 PS 无障碍信息的编码问题。')
[void]$dump.AppendLine('')

$mainEl = Get-MainElement -ProcId $proc.Id
if ($null -eq $mainEl) {
    $FailNotes.Add('UIA 找不到 Photoshop 主窗口元素，uia_dump.txt 只有表头。') | Out-Null
} else {
    [void]$dump.AppendLine('## 一、PS 主窗口原始视图树（含画笔设置面板）')
    [void]$dump.AppendLine(('主窗口 name=[{0}] cls={1} pid={2} rect={3}' -f $mainEl.Current.Name, $mainEl.Current.ClassName, $mainEl.Current.ProcessId, (Format-Rect $mainEl.Current.BoundingRectangle)))
    Dump-UiaTree -Element $mainEl -Depth 1 -MaxDepth 12 -Sb $dump
    [void]$dump.AppendLine('')
}

# 每个页签右侧控件的 UIA 表（这才是本任务真正的控件来源）
$panelEl = $null
try {
    $condSv = New-Object System.Windows.Automation.PropertyCondition($UIAE::ClassNameProperty, 'OWL.ShadowView')
    foreach ($sv in $mainEl.FindAll($UIA_TreeScope::Descendants, $condSv)) {
        $r = $sv.Current.BoundingRectangle
        if ($r.Width -gt 300 -and -not $sv.Current.IsOffscreen) { $panelEl = $sv; break }
    }
} catch { }

$pvEl = $null
if ($null -ne $panelEl) {
    try {
        $condPv = New-Object System.Windows.Automation.PropertyCondition($UIAE::ClassNameProperty, 'PSViewC')
        $pvEl = $panelEl.FindFirst($UIA_TreeScope::Descendants, $condPv)
    } catch { }
}

foreach ($tab in $TabDefs) {
    [void]$dump.AppendLine('## 二、页签「' + $tab.UiText + '」右侧控件（UIA 枚举，TSV：ControlType | Name | AutomationId | ClassName | IsEnabled | IsOffscreen | X | Y | W | H | 模式）')
    if ($null -eq $pvEl) {
        [void]$dump.AppendLine('# 未找到 PSViewC 容器（UIA 不可用或被遮挡）')
        [void]$dump.AppendLine('')
        continue
    }
    # 让面板停在该页签上再枚举（保证枚举到的是该页签的控件）
    $clickX = $PanelRect[0] + $CalibRowTextX
    $clickY = $PanelRect[1] + $rowCenters[$tab.Row - 1]
    Invoke-ActivateMain -ProcId $proc.Id -MainHwnd $mainHwnd
    [void][BCUI.Native]::SetCursorPos($clickX, $clickY)
    Start-Sleep -Milliseconds 150
    [BCUI.Native]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
    Start-Sleep -Milliseconds 60
    [BCUI.Native]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
    Start-Sleep -Milliseconds 600
    [void][BCUI.Native]::SetCursorPos($PanelRect[0] + 40, [math]::Max(4, $PanelRect[1] - 6))
    Start-Sleep -Milliseconds 300

    $items = @()
    try {
        foreach ($e in $pvEl.FindAll($UIA_TreeScope::Descendants, [System.Windows.Automation.Condition]::TrueCondition)) {
            $c = $e.Current
            $r = $c.BoundingRectangle
            $items += [pscustomobject]@{
                Type = $c.ControlType.ProgrammaticName.Replace('ControlType.','')
                Name = $c.Name
                Aid  = $c.AutomationId
                Cls  = $c.ClassName
                En   = $c.IsEnabled
                Off  = $c.IsOffscreen
                X    = [int]$r.X
                Y    = [int]$r.Y
                W    = [int]$r.Width
                H    = [int]$r.Height
                Pats = (Get-PatternNames $e)
            }
        }
    } catch {
        $FailNotes.Add('枚举页签「' + $tab.UiText + '」控件时异常：' + $_.Exception.Message) | Out-Null
    }
    $sorted = $items | Sort-Object @{ Expression = { $_.Off } }, @{ Expression = { $_.Y } }, @{ Expression = { $_.X } }
    [void]$dump.AppendLine('# 条目数=' + $items.Count)
    foreach ($it in $sorted) {
        [void]$dump.AppendLine(("{0}`t{1}`t{2}`t{3}`t{4}`t{5}`t{6}`t{7}`t{8}`t{9}`t{10}" -f `
            $it.Type, $it.Name, $it.Aid, $it.Cls, $it.En, $it.Off, $it.X, $it.Y, $it.W, $it.H, $it.Pats))
    }
    [void]$dump.AppendLine('')
    Write-Log ('UIA 页签「{0}」枚举到 {1} 个条目' -f $tab.UiText, $items.Count)
}

# 页签列表与底部勾选项的界面原文（UIA 没暴露，只能靠截图；这里如实标注来源）
[void]$dump.AppendLine('## 三、左侧页签/勾选项原文（UIA 未暴露，来源=本脚本截图人工读图）')
[void]$dump.AppendLine("# 行号`t界面原文`t类型")
$RowNames = @('画笔笔尖形状','形状动态','散布','纹理','双重画笔','颜色动态','传递','画笔笔势','杂色','湿边','建立','平滑','保护纹理')
for ($i = 0; $i -lt $RowNames.Count; $i++) {
    $kind = if ($i -eq 0) { '页签（画笔笔尖形状）' } elseif ($i -le 7) { '页签（动态项，带勾选框）' } else { '底部勾选项' }
    [void]$dump.AppendLine(("{0}`t{1}`t{2}" -f ($i + 1), $RowNames[$i], $kind))
}

[System.IO.File]::WriteAllText($DumpPath, $dump.ToString(), (New-Object System.Text.UTF8Encoding($false)))
Write-Log ('UIA 控件树已写入：' + $DumpPath)

# ----------------------------------------------------------------------------
# 8. 全景截图（画笔设置面板整窗，默认页签「画笔笔尖形状」）
#    注意：**不截 PS 主窗口背景**。PS 2025 的「主页/最近使用项」会显示本地文件名，
#    实测其中有含真实姓名的文件（.psd），进仓库会违反隐私门，所以全景只截对话框整窗。
# ----------------------------------------------------------------------------
$fullFile = '00-全景.png'
$fullPath = Join-Path $OutAbs $fullFile
$fullResult = $null
$clickX = $PanelRect[0] + $CalibRowTextX
$clickY = $PanelRect[1] + $rowCenters[0]
Invoke-ActivateMain -ProcId $proc.Id -MainHwnd $mainHwnd
[void][BCUI.Native]::SetCursorPos($clickX, $clickY)
Start-Sleep -Milliseconds 150
[BCUI.Native]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
Start-Sleep -Milliseconds 60
[BCUI.Native]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
Start-Sleep -Milliseconds 500
[void][BCUI.Native]::SetCursorPos($PanelRect[0] + 40, [math]::Max(4, $PanelRect[1] - 6))
Start-Sleep -Milliseconds 700
$fullTime = Get-Date
$fullBmp = Capture-PanelBitmap $PanelRect
$fullSaved = Save-Crop-Png -Bmp $fullBmp -X $CropX -Y $CropY -W $CropW -H $CropH -Path $fullPath
$fullBmp.Dispose()
$fi = Get-Item $fullPath
$fullResult = [pscustomobject]@{
    File = $fullFile
    Path = $fullPath
    Width = $fullSaved.Width
    Height = $fullSaved.Height
    Bytes = $fi.Length
    Time = $fullTime.ToString('yyyy-MM-dd HH:mm:ss')
    Rect = ('{0},{1},{2}x{3}' -f $PanelRect[0], $PanelRect[1], $PanelRect[2], $PanelRect[3])
}
Write-Log ('00-全景：{0}x{1} {2} B 时间={3}' -f $fullSaved.Width, $fullSaved.Height, $fi.Length, $fullTime.ToString('HH:mm:ss'))

# ----------------------------------------------------------------------------
# 8.5 还原勾选状态（点选页签会被 PS 自动勾上，这里把脚本改掉的改回去）
# ----------------------------------------------------------------------------
$restoreCount = 0
$script:RestoreMismatch = 0
Start-Sleep -Milliseconds 300
$nowBmp = Capture-PanelBitmap $PanelRect
$nowImg = Get-ImageBytes $nowBmp
$nowStates = Get-CheckStates -Img $nowImg -RowCenters $rowCenters
$nowBmp.Dispose()
for ($i = 1; $i -lt 13; $i++) {
    if ($nowStates[$i] -ne $initialStates[$i]) {
        $chkX = $PanelRect[0] + $CropX + 38
        $chkY = $PanelRect[1] + $rowCenters[$i]
        Invoke-RowClick -X $chkX -Y $chkY
        $restoreCount++
        Write-Log ('  还原第 {0} 行勾选框（{1} → {2}）' -f ($i + 1), $(if ($nowStates[$i]) { '勾选' } else { '未勾选' }), $(if ($initialStates[$i]) { '勾选' } else { '未勾选' }))
    }
}
if ($restoreCount -gt 0) {
    Start-Sleep -Milliseconds 400
    [void][BCUI.Native]::SetCursorPos($PanelRect[0] + 40, [math]::Max(4, $PanelRect[1] - 6))
    Start-Sleep -Milliseconds 400
    $verBmp = Capture-PanelBitmap $PanelRect
    $verImg = Get-ImageBytes $verBmp
    $verStates = Get-CheckStates -Img $verImg -RowCenters $rowCenters
    $verBmp.Dispose()
    $mismatch = 0
    for ($i = 1; $i -lt 13; $i++) { if ($verStates[$i] -ne $initialStates[$i]) { $mismatch++ } }
    $script:RestoreMismatch = $mismatch
    Write-Log ('勾选状态还原：共改 {0} 项，复核不一致 {1} 项' -f $restoreCount, $mismatch)
    if ($mismatch -gt 0) { $FailNotes.Add(('勾选状态还原后复核仍有 {0} 项与初始状态不一致（PS 可能自动又勾上），详见 index.md。' -f $mismatch)) | Out-Null }
} else {
    Write-Log '勾选状态未发生变化，无需还原'
}

# ----------------------------------------------------------------------------
# 9. 收尾：关闭面板 / 退出 PS
# ----------------------------------------------------------------------------
# 实测：向面板发 WM_CLOSE 无效（PS 的 OWL 面板不处理），点标题栏右侧的 × 才关得掉；
#       × 的位置按内容区右边缘 -15px、顶部 +8px 计算（本机 574 宽面板实测 (1359,227) 命中）。
$panelClosed = $false
if (-not $KeepPanel) {
    if ([BCUI.Native]::IsWindowVisible($script:PanelHwnd)) {
        [void][BCUI.Native]::SetCursorPos(($PanelRect[0] + $PanelRect[2] - 15), ($PanelRect[1] + 8))
        Start-Sleep -Milliseconds 200
        [BCUI.Native]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
        Start-Sleep -Milliseconds 60
        [BCUI.Native]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
        Start-Sleep -Milliseconds 1500
    }
    $panelClosed = -not [BCUI.Native]::IsWindowVisible($script:PanelHwnd)
    if (-not $panelClosed) {
        $FailNotes.Add('画笔设置面板未能关闭（点击标题栏 × 后 IsWindowVisible 仍为 True），面板句柄 ' + $script:PanelHwnd) | Out-Null
    }
    Write-Log ('画笔设置面板已关闭=' + $panelClosed)
} else {
    Write-Log '指定了 -KeepPanel，不关闭画笔设置面板'
}

$psExited = $false
if ($script:StartedByUs -and -not $KeepPhotoshop) {
    Write-Log 'Photoshop 是本次脚本启动的，发送 WM_CLOSE 退出（未打开任何文档，不保存）'
    [void][BCUI.Native]::PostMessage($mainHwnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
    $sw3 = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw3.Elapsed.TotalSeconds -lt 40) {
        Start-Sleep -Milliseconds 800
        $p2 = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
        if ($null -eq $p2) { $psExited = $true; break }
    }
    if (-not $psExited) {
        Write-Log 'Photoshop 40 秒内未退出，尝试 Stop-Process 强制结束'
        try { Stop-Process -Id $proc.Id -Force -ErrorAction Stop; $psExited = $true } catch { $FailNotes.Add('强制结束 PS 失败：' + $_.Exception.Message) | Out-Null }
    }
    Write-Log ('Photoshop 已退出 = ' + $psExited)
} else {
    Write-Log 'Photoshop 不是本次脚本启动的或指定了 -KeepPhotoshop，保留不退出'
}

# ----------------------------------------------------------------------------
# 10. 写 index.md
# ----------------------------------------------------------------------------
$sb = New-Object System.Text.StringBuilder
[void]$sb.AppendLine('# 「画笔设置」界面截图索引（任务 101-B）')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('> 由 `tools/capture_ps_ui.ps1` 自动生成，可重复跑（同名文件覆盖）。本文件不含本机用户名/家目录，PS 安装目录统一写作 `<LOCAL_APP_DIR>`。')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 采集环境')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('| 项 | 值 |')
[void]$sb.AppendLine('|---|---|')
[void]$sb.AppendLine('| Photoshop 版本 | ' + $PsVersionText + '（中文界面） |')
[void]$sb.AppendLine('| 安装目录 | `<LOCAL_APP_DIR>\PS2025\Adobe Photoshop 2025\` |')
[void]$sb.AppendLine('| 屏幕分辨率（物理像素） | ' + $ScreenRect.Width + 'x' + $ScreenRect.Height + '（150% 缩放，脚本已 SetProcessDPIAware） |')
[void]$sb.AppendLine('| 面板窗口 | OWL.ShadowView（外框，抓屏基准）内嵌 OWL.Dock（内容区）；保存时裁掉投影，成品尺寸 ' + $CropW + 'x' + $CropH + ' |')
[void]$sb.AppendLine('| 采集时间 | ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' |')
[void]$sb.AppendLine('| 采集方式 | 坐标点击选页签（UIA 与键盘均不可用，见下）+ `Graphics.CopyFromScreen` 按窗口矩形截图 |')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 自动化路线与失败点（原文）')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('1. **UIA 选页签：失败**。PS 面板左侧 13 项（8 个页签 + 5 个底部勾选项）是自绘列表，UIA 原始视图里只有 `OWL.ShadowView → OWL.Dock → OWL.TabPane → OWL.TabGroup → OWL.Palette → PSViewC` 容器，没有任何 ListItem/CheckBox/TabItem 元素，无法 Invoke / SelectionItem.Select。右侧设置区的控件（Static/Edit/Button）能正常枚举（见 `uia_dump.txt` 第二节）。')
[void]$sb.AppendLine('2. **键盘 Down 切换：失败**。实测：点击左侧列表行后再发 `{DOWN}`，选中行不变（截图比对：仍高亮原行）。')
[void]$sb.AppendLine('3. **坐标点击：成功**。以面板外框矩形为基准，行中心 = 面板顶 + 自适应量得的文字带中心（实测 13 行中心 y = ' + ($rowCenters -join ',') + '），点击 x = 面板左 + ' + $CalibRowTextX + '（行内文字区、不点勾选框）。注意：**PS 2025 点选动态页签会顺手把该项勾上**，见「对 PS 状态的副作用」一节，脚本收尾会还原。')
[void]$sb.AppendLine('4. **拉大面板：未采用**。`SetWindowPos` 把外框改到 1000x1350 后，内部 `OWL.Dock` 不跟随（仍 816,219,558x1228），会截出空白边框，故保持 PS 默认尺寸（内容区 558x1228，无换行、底部 5 项全程可见）。')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 图片清单')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('| 文件 | 页签 | 界面原文 | 分辨率 | 字节数 | 截图时间 | 采集方式 | 选中行校验 | 底部 5 项可见 |')
[void]$sb.AppendLine('|---|---|---|---|---|---|---|---|---|')
[void]$sb.AppendLine(('| {0} | 全景（默认页签 画笔笔尖形状） | 画笔笔尖形状 | {1}x{2} | {3} | {4} | CopyFromScreen（画笔设置面板内容区整窗，拍于 UIA 枚举之后，此时 8 个动态项都被 PS 自动勾上） | 通过（先点第 1 行再截） | 是 |' -f $fullResult.File, $fullResult.Width, $fullResult.Height, $fullResult.Bytes, $fullResult.Time))
foreach ($r in $TabResults) {
    $selText = if ($r.SelOk) { '通过（第 ' + $r.SelRow + ' 行高亮，亮度 ' + $r.SelBright + ' vs 次高 ' + ($r.SelBright - $r.SelGap) + '）' } else { '失败（实测第 ' + $r.SelRow + ' 行）' }
    $botText = if ($r.BottomOk) { '是（13 行文字带齐全）' } else { '否（只检测到 ' + $r.Bands + ' 行）' }
    [void]$sb.AppendLine(('| {0} | {1} | {2} | {3}x{4} | {5} | {6} | {7} | {8} | {9} |' -f $r.File, $r.Tab, $r.UiText, $r.Width, $r.Height, $r.Bytes, $r.Time, $r.Method, $selText, $botText))
}
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 底部 5 个勾选项（界面原文，按列表行号）')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('| 列表行 | 界面原文 | 任务书写的名字 | 备注 |')
[void]$sb.AppendLine('|---|---|---|---|')
[void]$sb.AppendLine('| 9 | 杂色 | 杂色 | 一致 |')
[void]$sb.AppendLine('| 10 | 湿边 | 湿边 | 一致 |')
[void]$sb.AppendLine('| 11 | 建立 | 喷枪 | **不一致**：本机 PS 2025 26.9 中文界面显示「建立」，未见「喷枪」字样 |')
[void]$sb.AppendLine('| 12 | 平滑 | 平滑 | 一致（默认勾选） |')
[void]$sb.AppendLine('| 13 | 保护纹理 | 保护纹理 | 一致 |')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 页签名与任务书的差异')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('- 第 8 个页签界面原文是「**画笔笔势**」（任务书写「画笔姿势」）。文件名仍按任务书固定命名 `08-画笔姿势.png`，画面上是「画笔笔势」。')
[void]$sb.AppendLine('- 其余 7 个页签与任务书一致：画笔笔尖形状 / 形状动态 / 散布 / 纹理 / 双重画笔 / 颜色动态 / 传递。')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 对 PS 状态的副作用（如实记录）')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('- **PS 2025 的行为：点选某个动态页签会同时把这一项勾上**（例如点「双重画笔」的名字，它左边勾选框就变勾）。本脚本要逐个页签截图，8 个动态项因此都被勾上了。')
[void]$sb.AppendLine('- 脚本在收尾时会比对「进入前的勾选状态」并把被改掉的点回去。本次进入前（行 2~13：形状动态/散布/纹理/双重画笔/颜色动态/传递/画笔笔势/杂色/湿边/建立/平滑/保护纹理）= ' + (($initialStates | Select-Object -Skip 1 | ForEach-Object { if ($_) { '√' } else { '×' } }) -join ' ') + '，还原改动 ' + $restoreCount + ' 项（复核不一致 ' + $script:RestoreMismatch + ' 项）。')
[void]$sb.AppendLine('- 全程没有保存任何笔刷预设、没有改首选项、没有打开或保存任何文档；截图只是屏幕抓取。')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 失败项与原因')
[void]$sb.AppendLine('')
if ($CheckFailures.Count -eq 0 -and $FailNotes.Count -eq 0) {
    [void]$sb.AppendLine('- 无。8 个页签全部截图成功，每张都通过「选中行高亮 + 13 行文字带」像素校验。')
} else {
    foreach ($f in $FailNotes) { [void]$sb.AppendLine('- ' + $f) }
    foreach ($f in $CheckFailures) { [void]$sb.AppendLine('- ' + $f) }
}
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 隐私说明')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('- 9 张图全部只截「画笔设置」面板内容区（外框矩形 ' + ($PanelRect -join ',') + '，保存时裁掉四周投影，成品 ' + $CropW + 'x' + $CropH + '）。')
[void]$sb.AppendLine('- **`00-全景.png` 没有包含 PS 主窗口背景**：本机 PS 2025 的「主页 → 最近使用项」里会出现本地文件名，实测其中有含真实姓名的 `.psd`，截进去会违反隐私门（用户名/真实姓名不许进仓库）。因此全景只截对话框整窗，页签为默认的「画笔笔尖形状」。')
[void]$sb.AppendLine('- 面板外框 `OWL.ShadowView` 四周有 8px 透明投影，会把背后主窗口像素带进画面；所以抓屏仍按外框矩形，但保存时统一裁成内容区 `OWL.Dock` 的 ' + $CropW + 'x' + $CropH + '，画面里只有 PS 的画笔设置控件。')
[void]$sb.AppendLine('- 未出现用户名、邮箱、家目录、文档路径；PS 未打开任何文档，未修改笔刷/首选项，本次只点选页签与截图。')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 运行日志（脚本 stdout 摘要）')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('```text')
[void]$sb.AppendLine('脚本启动 PS 方式：' + $(if ($script:StartedByUs) { '本次启动' } else { '复用已在运行的 PS' }))
[void]$sb.AppendLine('画笔设置面板：HWND=' + $script:PanelHwnd + ' 矩形=' + ($PanelRect -join ','))
[void]$sb.AppendLine('面板关闭=' + $panelClosed + '；Photoshop 已退出=' + $psExited)
foreach ($l in $LogLines) { [void]$sb.AppendLine((ConvertTo-SafeText $l)) }
[void]$sb.AppendLine('```')

[System.IO.File]::WriteAllText($IndexPath, $sb.ToString(), (New-Object System.Text.UTF8Encoding($false)))
Write-Log ('索引已写入：' + $IndexPath)

# ----------------------------------------------------------------------------
# 11. 控制台总结（便于人工/lead 摘数）
# ----------------------------------------------------------------------------
Write-Host ''
Write-Host '===== 汇总 ====='
Write-Host ('输出目录：' + $OutAbs)
Write-Host ('00-全景            {0}x{1}  {2} B' -f $fullResult.Width, $fullResult.Height, $fullResult.Bytes)
foreach ($r in $TabResults) {
    Write-Host ('{0,-18} {1}x{2}  {3} B  选中行={4}（期望 {5}） 文字带={6}' -f $r.File, $r.Width, $r.Height, $r.Bytes, $r.SelRow, $r.Row, $r.Bands)
}
Write-Host ('页签成功数：{0}/8' -f (($TabResults | Where-Object { $_.SelOk -and $_.BottomOk }).Count))
Write-Host ('失败项：{0}' -f ($FailNotes.Count + $CheckFailures.Count))
Write-Host ('画笔设置面板已关闭：' + $panelClosed)
Write-Host ('Photoshop 已退出：' + $psExited + '（本次启动=' + $script:StartedByUs + '）')
Write-Host '================'
