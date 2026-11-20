# -*- coding: utf-8 -*-
<#
工具：tools/capture_ps_mode_dropdown.ps1
任务：brushConverter v1.4.0 / 任务 101-补充 —— 采集「画笔设置 > 纹理 > 模式」下拉的全部选项（只读操作）

做什么：
  1. 复用（或启动）Photoshop，找到「画笔设置」面板（OWL.ShadowView / OWL.Dock）；
  2. 按 capture_ps_ui.ps1 的同一套行坐标标定方法点选第 4 行「纹理」页签，并用像素亮度校验选中行；
  3. 用 UIA 在纹理页控件里定位「模式」ComboBox（宽 161、y 更小的那个），点击展开下拉；
  4. 展开后依次尝试三条采集路线并全部打印到 stdout：
       A) 顶层窗口差分：找出新出现/变大的可见窗口（含标准 ComboLBox），若为 ComboLBox 则用
          LB_GETCOUNT / LB_GETTEXT 直接读取全部条目文本；
       B) UIA 枚举：枚举 PS 顶层元素与「模式」ComboBox 的整棵子树，打印 ListItem/MenuItem/Text；
       C) 截图：CopyFromScreen 截「画笔设置」内容区（OWL.Dock）整窗，落 09-纹理模式下拉.png；
  5. 按 Esc 关闭下拉（不选中任何项，避免改变笔刷设置），并用像素比对确认下拉前后状态一致；
  6. 收尾：还原被点选页签时 PS 自动改掉的勾选状态；关闭画笔设置面板；
     若 PS 是本次脚本启动的则退出 PS，否则保留（只关面板）。

不做什么（硬约束）：
  - 不猜测、不补全下拉列表；脚本只负责「拿到什么打印什么」，中文名由人工读图转录；
  - 不改 PS 的文档、首选项、笔刷、预设；不登录、不输入任何账号信息；
  - 截图只截「画笔设置」面板内容区，绝不截桌面或 PS 主窗口的「最近使用文件」区域；
  - 不改 docs/参数矩阵.json、src/**、tests/**；不执行 git 提交。

用法（在仓库根执行）：
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\capture_ps_mode_dropdown.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\capture_ps_mode_dropdown.ps1 -KeepPanel   # 调试：不关面板
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\capture_ps_mode_dropdown.ps1 -ScrollCapture # 额外按 End 再截一张 09b
#>
[CmdletBinding()]
param(
    [string]$OutDir = 'docs\截图\ui',
    [string]$BaseName = '09-纹理模式下拉',
    [int]$StartTimeoutSec = 120,
    [switch]$KeepPanel,       # 调试用：脚本结束时不关闭画笔设置面板
    [switch]$KeepPhotoshop,   # 本次启动 PS 时也不退出
    [switch]$ScrollCapture,   # 打开下拉后按 End 再截一张（文件名追加 b）
    [int]$ShotMaxHeight = 1000 # 截图最大高度：本机右下角有桌面浮层（桌宠）会压住面板下缘，
                               # 统一裁到 1000 高避开；下拉列表完整区域在 y=418~674（内容区坐标），不受影响
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# ----------------------------------------------------------------------------
# 0. 路径与常量
# ----------------------------------------------------------------------------
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path $RepoRoot)) { $RepoRoot = (Get-Location).Path }

if ([System.IO.Path]::IsPathRooted($OutDir)) { $OutAbs = $OutDir }
else { $OutAbs = Join-Path $RepoRoot $OutDir }
if (-not (Test-Path $OutAbs)) { New-Item -ItemType Directory -Path $OutAbs -Force | Out-Null }

if ($env:BRUSHCONVERTER_PS) { $PsExe = $env:BRUSHCONVERTER_PS }
else { $PsExe = 'D:\program\PS2025\Adobe Photoshop 2025\Photoshop.exe' }

# 纹理页的「模式」ComboBox 上次实测位置（相对面板外框左上角，2026-11-20 19:01 采集）；
# 仅当 UIA 定位失败时作为点击兜底，并在日志中标注「使用了坐标兜底」。
$FallbackCombo = @(250, 376, 161, 32)
$TextureRow = 4   # 左侧列表第 4 行 = 纹理

$LogLines = New-Object System.Collections.Generic.List[string]
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

namespace BCMD {
    public static class Native {
        [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
        public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);

        [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
        [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
        [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
        [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, IntPtr dwExtraInfo);
        [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
        [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr lParam);
        [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
        [DllImport("user32.dll")] public static extern int GetClassName(IntPtr hWnd, StringBuilder sb, int nMax);
        [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int nMax);
        [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
        [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
        [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
        [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
        [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "SendMessageW", ExactSpelling = true)]
        public static extern IntPtr SendMessageString(IntPtr hWnd, uint msg, IntPtr wParam, StringBuilder lParam);

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
        public static int[] GetRect(IntPtr h) {
            RECT r;
            GetWindowRect(h, out r);
            return new int[] { r.Left, r.Top, r.Right - r.Left, r.Bottom - r.Top };
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
        // 标准列表框取条目文本（LB_GETTEXT=0x0189），失败返回空串
        public static string ListBoxText(IntPtr h, int index) {
            StringBuilder sb = new StringBuilder(1024);
            IntPtr ok = SendMessageString(h, 0x0189, (IntPtr)index, sb);
            if (ok == IntPtr.Zero) return string.Empty;
            return sb.ToString();
        }
        public static int ListBoxCount(IntPtr h) {
            return (int)SendMessage(h, 0x018B, IntPtr.Zero, IntPtr.Zero); // LB_GETCOUNT
        }
        public static int ListBoxItemHeight(IntPtr h) {
            return (int)SendMessage(h, 0x01A1, IntPtr.Zero, IntPtr.Zero); // LB_GETITEMHEIGHT
        }
    }
}
'@
if (-not ('BCMD.Native' -as [type])) { Add-Type -TypeDefinition $NativeSrc -Language CSharp }
[void][BCMD.Native]::SetProcessDPIAware()

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$UIAE = [System.Windows.Automation.AutomationElement]
$UIA_TreeScope = [System.Windows.Automation.TreeScope]
$UIA_CT = [System.Windows.Automation.ControlType]

$ScreenRect = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
Write-Log ('屏幕（物理像素）：{0}x{1}；PS 可执行文件：{2}' -f $ScreenRect.Width, $ScreenRect.Height, $PsExe)

# ----------------------------------------------------------------------------
# 2. 位图工具：截图 / 像素采样（沿用 capture_ps_ui.ps1 的实测参数）
# ----------------------------------------------------------------------------
function Capture-ScreenBitmap {
    param([int]$X, [int]$Y, [int]$W, [int]$H)
    $bmp = New-Object System.Drawing.Bitmap -ArgumentList $W, $H, ([System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    try {
        $g.CopyFromScreen($X, $Y, 0, 0, (New-Object System.Drawing.Size -ArgumentList $W, $H))
    } finally {
        $g.Dispose()
    }
    return $bmp
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

# 两幅同尺寸区域的平均绝对差（只比 R 通道，足够判定「下拉是否出现/是否还原」）
function Get-MeanAbsDiff {
    param($ImgA, $ImgB)
    if ($ImgA.Width -ne $ImgB.Width -or $ImgA.Height -ne $ImgB.Height) { return -1.0 }
    $sum = 0.0
    $n = 0
    for ($y = 0; $y -lt $ImgA.Height; $y++) {
        $base = $y * $ImgA.Stride
        for ($x = 0; $x -lt $ImgA.Width; $x++) {
            $sum += [math]::Abs([int]$ImgA.Bytes[$base + $x * 3 + 2] - [int]$ImgB.Bytes[$base + $x * 3 + 2])
            $n++
        }
    }
    if ($n -eq 0) { return -1.0 }
    return [math]::Round($sum / $n, 3)
}

function Invoke-ClickAt {
    param([int]$X, [int]$Y)
    [void][BCMD.Native]::SetCursorPos($X, $Y)
    Start-Sleep -Milliseconds 150
    [BCMD.Native]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)   # LEFTDOWN
    Start-Sleep -Milliseconds 60
    [BCMD.Native]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)   # LEFTUP
    Start-Sleep -Milliseconds 450
}

# 把鼠标移到面板外侧上方空白处（PS 画布），避免悬停高亮/提示气泡进入面板截图；
# 只移动鼠标、不点击，不会影响 PS 文档，也不会进入截图范围（截图只取面板内容区）。
function Move-CursorAway {
    param([int[]]$PanelRect)
    $x = [math]::Max(4, $PanelRect[0] - 80)
    $y = [math]::Max(4, $PanelRect[1] - 60)
    [void][BCMD.Native]::SetCursorPos($x, $y)
}

# 返回 13 行（或实际行数）的文字带中心 y（原点 = 面板外框左上角）
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
            if ($bytes[$rowBase + $x * 3 + 2] -gt 150) { $cnt++ }
        }
        if ($cnt -gt 3 -and -not $inBand) { $inBand = $true; $start = $y }
        elseif ($cnt -le 3 -and $inBand) { $inBand = $false; $bands.Add([pscustomobject]@{ Top=$start; Bottom=($y-1) }) | Out-Null }
    }
    if ($inBand) { $bands.Add([pscustomobject]@{ Top=$start; Bottom=$Y1 }) | Out-Null }

    $merged = New-Object System.Collections.Generic.List[object]
    foreach ($b in $bands) {
        if ($merged.Count -gt 0 -and ($b.Top - $merged[$merged.Count-1].Bottom) -le 5) {
            $merged[$merged.Count-1].Bottom = $b.Bottom
        } else {
            $merged.Add([pscustomobject]@{ Top=$b.Top; Bottom=$b.Bottom }) | Out-Null
        }
    }
    $kept = New-Object System.Collections.Generic.List[object]
    foreach ($b in $merged) {
        if (($b.Bottom - $b.Top + 1) -ge 8) {
            $kept.Add([pscustomobject]@{ Top=$b.Top; Bottom=$b.Bottom; Center=[int](($b.Top + $b.Bottom) / 2) }) | Out-Null
        }
    }
    return $kept
}

# 选中行背景更亮：采样 x=20..28（勾选框左侧纯背景），返回 1 起的行号
function Get-SelectedRowIndex {
    param($Img, $Bands)
    $bytes = $Img.Bytes
    $stride = $Img.Stride
    $vals = @()
    foreach ($b in $Bands) {
        $c = $b.Center
        $sum = 0
        $n = 0
        for ($y = $c - 6; $y -le $c + 6; $y++) {
            $rowBase = $y * $stride
            for ($x = 20; $x -le 28; $x++) {
                $sum += $bytes[$rowBase + $x * 3 + 2]
                $n++
            }
        }
        $vals += [double]$sum / [double]$n
    }
    if ($vals.Count -eq 0) { return [pscustomobject]@{ Index = 0; Bright = 0; Second = 0; All = @() } }
    $maxIdx = 0
    for ($i = 1; $i -lt $vals.Count; $i++) { if ($vals[$i] -gt $vals[$maxIdx]) { $maxIdx = $i } }
    $second = 0.0
    for ($i = 0; $i -lt $vals.Count; $i++) { if ($i -ne $maxIdx -and $vals[$i] -gt $second) { $second = $vals[$i] } }
    return [pscustomobject]@{
        Index = ($maxIdx + 1)
        Bright = [math]::Round($vals[$maxIdx], 1)
        Second = [math]::Round($second, 1)
        All = ($vals | ForEach-Object { [math]::Round($_, 1) })
    }
}

# 读第 2~13 行左侧勾选框状态（True=已勾选）。实测：勾选 ~130，未勾选 ~82，阈值 100。
function Get-CheckStates {
    param($Img, $RowCenters)
    $bytes = $Img.Bytes
    $stride = $Img.Stride
    $states = New-Object System.Collections.Generic.List[bool]
    $states.Add($false) | Out-Null
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

# ----------------------------------------------------------------------------
# 3. 启动 / 复用 Photoshop，找到画笔设置面板
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
Start-Sleep -Milliseconds 800

$PsVersionText = ''
try { $PsVersionText = (Get-Item $PsExe).VersionInfo.FileVersion }
catch { try { $PsVersionText = $proc.MainModule.FileVersionInfo.FileVersion } catch { } }
Write-Log ('Photoshop 版本：' + $PsVersionText)

function Find-PanelHwnd {
    param([uint32]$ProcId, [IntPtr]$MainHwnd)
    foreach ($h in [BCMD.Native]::TopLevelOf($ProcId)) {
        if ([BCMD.Native]::ClassName($h) -ne 'OWL.ShadowView') { continue }
        if (-not [BCMD.Native]::IsWindowVisible($h)) { continue }
        if ($h -eq $MainHwnd) { continue }
        $r = [BCMD.Native]::GetRect($h)
        if ($r[2] -gt 300 -and $r[3] -gt 300) { return $h }
    }
    return [IntPtr]::Zero
}

function Find-PanelDockRect {
    param([IntPtr]$PanelHwnd, [uint32]$ProcId)
    $pr = [BCMD.Native]::GetRect($PanelHwnd)
    foreach ($h in [BCMD.Native]::TopLevelOf($ProcId)) {
        if ([BCMD.Native]::ClassName($h) -ne 'OWL.Dock') { continue }
        if (-not [BCMD.Native]::IsWindowVisible($h)) { continue }
        $r = [BCMD.Native]::GetRect($h)
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
    Start-Sleep -Milliseconds 300
    [void][BCMD.Native]::SetForegroundWindow($MainHwnd)
    Start-Sleep -Milliseconds 300
}

$script:PanelHwnd = Find-PanelHwnd -ProcId ([uint32]$proc.Id) -MainHwnd $mainHwnd
if ($script:PanelHwnd -eq [IntPtr]::Zero) {
    # 面板未开：先还原主窗口（本机实测主窗口可能处于最小化），再按 F5
    [void][BCMD.Native]::ShowWindow($mainHwnd, 9)   # SW_RESTORE
    Start-Sleep -Milliseconds 600
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
    $FailNotes.Add('F5 未能打开「画笔设置」面板（20 秒内未出现 OWL.ShadowView 面板窗口）。') | Out-Null
    throw '无法打开画笔设置面板，任务中止（未采集到下拉，不写 JSON）'
}

$PanelRect = [BCMD.Native]::GetRect($script:PanelHwnd)
Write-Log ('画笔设置面板：HWND={0} 外框矩形={1}' -f $script:PanelHwnd, ($PanelRect -join ','))

$DockRect = Find-PanelDockRect -PanelHwnd $script:PanelHwnd -ProcId ([uint32]$proc.Id)
if ($null -ne $DockRect) {
    $CropX = $DockRect[0] - $PanelRect[0]
    $CropY = $DockRect[1] - $PanelRect[1]
    $CropW = $DockRect[2]
    $CropH = $DockRect[3]
    Write-Log ('内容区 OWL.Dock 矩形={0}；保存时裁剪偏移=({1},{2}) 尺寸={3}x{4}' -f ($DockRect -join ','), $CropX, $CropY, $CropW, $CropH)
} else {
    $CropX = 8; $CropY = 3
    $CropW = $PanelRect[2] - 16
    $CropH = $PanelRect[3] - 15
    $FailNotes.Add('未找到面板内容区 OWL.Dock，按固定 8px 内缩裁剪（投影可能残留）。') | Out-Null
    Write-Log ('警告：未找到 OWL.Dock，按固定内缩裁剪 (8,3) {0}x{1}' -f $CropW, $CropH)
}

if ($PanelRect[0] -lt 0 -or $PanelRect[1] -lt 0 -or ($PanelRect[0] + $PanelRect[2]) -gt $ScreenRect.Width -or ($PanelRect[1] + $PanelRect[3]) -gt $ScreenRect.Height) {
    $FailNotes.Add(('面板矩形 {0} 超出屏幕 {1}，截图可能截到黑边。' -f ($PanelRect -join ','), ($ScreenRect.Width.ToString() + 'x' + $ScreenRect.Height))) | Out-Null
    Write-Log ('警告：面板超出屏幕：' + ($PanelRect -join ','))
}

# 面板置前（当前实测面板已是前台窗口）
[void][BCMD.Native]::SetForegroundWindow($script:PanelHwnd)
Start-Sleep -Milliseconds 400
Write-Log ('当前前台窗口 HWND={0}（期望面板 {1}）' -f [BCMD.Native]::GetForegroundWindow(), $script:PanelHwnd)

# 实测（2026-11-20 19:06）：PS 主窗口最小化时，面板虽在前台，第一次点击只会激活应用、
# 不会传给控件（点击后前台变成主窗口、页签不变）。因此先点一下面板标签栏空白处做激活点击，
# 再执行真正的页签点击。该点位于「画笔设置/画笔」两个标签右侧的空白标签栏，不会改任何设置。
Invoke-ClickAt -X ($PanelRect[0] + 192) -Y ($PanelRect[1] + 14)
Start-Sleep -Milliseconds 800
Move-CursorAway -PanelRect $PanelRect
Start-Sleep -Milliseconds 300
Write-Log ('激活点击后前台窗口 HWND={0}' -f [BCMD.Native]::GetForegroundWindow())

# ----------------------------------------------------------------------------
# 4. 行坐标标定 + 点选第 4 行「纹理」并像素校验
# ----------------------------------------------------------------------------
$probeBmp = Capture-ScreenBitmap $PanelRect[0] $PanelRect[1] $PanelRect[2] $PanelRect[3]
$probeImg = Get-ImageBytes $probeBmp
$probeBmp.Dispose()
$bands = Find-RowBands -Img $probeImg -X0 70 -X1 135 -Y0 100 -Y1 ([math]::Min(640, $probeImg.Height - 1))
if ($bands.Count -ge 13) {
    $rowCenters = @()
    for ($i = 0; $i -lt 13; $i++) { $rowCenters += $bands[$i].Center }
    Write-Log ('行坐标自适应标定成功（检测到 {0} 段文字带），行中心={1}' -f $bands.Count, ($rowCenters -join ','))
} else {
    $rowCenters = @(147,180,216,252,288,324,360,396,432,468,505,540,576)
    $FailNotes.Add(('行坐标自适应标定失败（只检测到 {0} 段文字带，需 13），回退 2026-11-20 实测固定表。' -f $bands.Count)) | Out-Null
    Write-Log ('警告：行坐标标定不完整（{0} 段），回退固定表' -f $bands.Count)
}

$initialStates = Get-CheckStates -Img $probeImg -RowCenters $rowCenters
Write-Log ('进入时勾选状态（行 2~13）：' + (($initialStates | Select-Object -Skip 1 | ForEach-Object { if ($_) { '√' } else { '×' } }) -join ' '))

$clickX = $PanelRect[0] + 120
$clickY = $PanelRect[1] + $rowCenters[$TextureRow - 1]
$sel = $null
$checkBands = $null
for ($attempt = 1; $attempt -le 3; $attempt++) {
    Invoke-ClickAt -X $clickX -Y $clickY
    Move-CursorAway -PanelRect $PanelRect
    Start-Sleep -Milliseconds 900
    $bmp = Capture-ScreenBitmap $PanelRect[0] $PanelRect[1] $PanelRect[2] $PanelRect[3]
    $img = Get-ImageBytes $bmp
    $bmp.Dispose()
    $checkBands = Find-RowBands -Img $img -X0 70 -X1 135 -Y0 100 -Y1 ([math]::Min(640, $img.Height - 1))
    $sel = Get-SelectedRowIndex -Img $img -Bands $checkBands
    Write-Log ('点选第 4 行第 {0} 次：选中行={1}（期望 4）亮度={2} 次高={3} 文字带={4}' -f $attempt, $sel.Index, $sel.Bright, $sel.Second, $checkBands.Count)
    if ($sel.Index -eq $TextureRow -and $checkBands.Count -ge 13) { break }
}
if ($sel.Index -ne $TextureRow) {
    # 现场留图后再失败退出（文件名即约定证据名，内容为未成功的现场）
    $bmpFail = Capture-ScreenBitmap $PanelRect[0] $PanelRect[1] $PanelRect[2] $PanelRect[3]
    $failPath = Join-Path $OutAbs ($BaseName + '.png')
    $failSaved = Save-Crop-Png -Bmp $bmpFail -X $CropX -Y $CropY -W $CropW -H ([math]::Min($CropH, $ShotMaxHeight)) -Path $failPath
    $bmpFail.Dispose()
    Write-Log ('未能选中「纹理」页签，已保留现场截图：{0}（{1}x{2}）' -f $failPath, $failSaved.Width, $failSaved.Height)
    $FailNotes.Add(('点选「纹理」页签失败：实测选中第 {0} 行（期望第 4 行）。' -f $sel.Index)) | Out-Null
    throw '未能选中纹理页签，任务中止（不写 JSON）'
}
Write-Log '第 4 行「纹理」已选中并通过像素校验'

# ----------------------------------------------------------------------------
# 5. UIA 定位「模式」ComboBox（纹理页的 2 个 ComboBox 里宽 161 / y 更小者）
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

$mainEl = Get-MainElement -ProcId $proc.Id
$pvEl = $null
if ($null -ne $mainEl) {
    try {
        $condSv = New-Object System.Windows.Automation.PropertyCondition($UIAE::ClassNameProperty, 'OWL.ShadowView')
        foreach ($sv in $mainEl.FindAll($UIA_TreeScope::Descendants, $condSv)) {
            $r = $sv.Current.BoundingRectangle
            if ($r.Width -gt 300 -and -not $sv.Current.IsOffscreen) {
                $pvEl = $sv.FindFirst($UIA_TreeScope::Descendants, (New-Object System.Windows.Automation.PropertyCondition($UIAE::ClassNameProperty, 'PSViewC')))
                break
            }
        }
    } catch { Write-Log ('UIA 定位面板异常：' + $_.Exception.Message) }
}
if ($null -eq $pvEl) {
    Write-Log 'UIA 未找到 PSViewC 容器，后续只能使用坐标兜底'
    $FailNotes.Add('UIA 未枚举到面板 PSViewC 容器。') | Out-Null
}

$combos = New-Object System.Collections.Generic.List[object]
if ($null -ne $pvEl) {
    try {
        $condCombo = New-Object System.Windows.Automation.PropertyCondition($UIAE::ControlTypeProperty, $UIA_CT::ComboBox)
        foreach ($e in $pvEl.FindAll($UIA_TreeScope::Descendants, $condCombo)) {
            $c = $e.Current
            $r = $c.BoundingRectangle
            $combos.Add([pscustomobject]@{
                El = $e; Type = 'ComboBox'; Name = $c.Name; Aid = $c.AutomationId
                En = $c.IsEnabled; Off = $c.IsOffscreen
                X = [int]$r.X; Y = [int]$r.Y; W = [int]$r.Width; H = [int]$r.Height
            }) | Out-Null
            Write-Log ('UIA ComboBox：Name=[{0}] Aid={1} En={2} Off={3} 矩形={4},{5},{6}x{7}' -f $c.Name, $c.AutomationId, $c.IsEnabled, $c.IsOffscreen, [int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height)
        }
    } catch { Write-Log ('UIA 枚举 ComboBox 异常：' + $_.Exception.Message) }
}

$modeCombo = $null
foreach ($cb in $combos) {
    if (-not $cb.En -or $cb.Off) { continue }
    if ($cb.W -lt 150) { continue }
    if ($cb.X -lt $PanelRect[0] -or $cb.X -gt ($PanelRect[0] + $PanelRect[2])) { continue }
    if ($cb.Y -lt $PanelRect[1] -or $cb.Y -gt ($PanelRect[1] + $PanelRect[3])) { continue }
    if ($null -eq $modeCombo -or $cb.Y -lt $modeCombo.Y) { $modeCombo = $cb }
}

if ($null -ne $modeCombo) {
    $comboRect = @($modeCombo.X, $modeCombo.Y, $modeCombo.W, $modeCombo.H)
    $comboNameBefore = $modeCombo.Name
    Write-Log ('定位到「模式」ComboBox：矩形={0}（UIA 按宽>=150、y 最小筛选）' -f ($comboRect -join ','))
} else {
    $comboRect = @(($PanelRect[0] + $FallbackCombo[0]), ($PanelRect[1] + $FallbackCombo[1]), $FallbackCombo[2], $FallbackCombo[3])
    $comboNameBefore = ''
    $FailNotes.Add('UIA 未能按宽>=150 定位「模式」ComboBox，已回退 2026-11-20 实测相对坐标（面板左+250, 面板顶+376, 161x32）。') | Out-Null
    Write-Log ('警告：UIA 未定位到「模式」ComboBox，使用坐标兜底：矩形={0}' -f ($comboRect -join ','))
}

# 记录 UIA 文本标签（确认纹理页的 缩放/亮度/对比度/模式 都在）
if ($null -ne $pvEl) {
    try {
        $condText = New-Object System.Windows.Automation.PropertyCondition($UIAE::ControlTypeProperty, $UIA_CT::Text)
        $texts = New-Object System.Collections.Generic.List[string]
        foreach ($e in $pvEl.FindAll($UIA_TreeScope::Descendants, $condText)) {
            $t = $e.Current.Name
            if ($t) { $texts.Add($t) | Out-Null }
        }
        Write-Log ('纹理页 UIA 文本标签（共 {0} 条）：{1}' -f $texts.Count, ($texts -join ' / '))
    } catch { Write-Log ('UIA 枚举文本标签异常：' + $_.Exception.Message) }
}

# ----------------------------------------------------------------------------
# 6. 记录下拉前状态（组合框像素 + 下方区域像素 + PS 顶层窗口快照）
# ----------------------------------------------------------------------------
function Get-PSVisibleWindows {
    param([uint32]$ProcId)
    $list = New-Object System.Collections.Generic.List[object]
    foreach ($h in [BCMD.Native]::TopLevelOf($ProcId)) {
        if (-not [BCMD.Native]::IsWindowVisible($h)) { continue }
        $r = [BCMD.Native]::GetRect($h)
        if ($r[2] -le 0 -or $r[3] -le 0) { continue }
        if ($r[0] -lt -1000 -or $r[1] -lt -1000) { continue }
        $list.Add([pscustomobject]@{ H = [int64]$h; Cls = [BCMD.Native]::ClassName($h); X = $r[0]; Y = $r[1]; W = $r[2]; Hh = $r[3] }) | Out-Null
    }
    return $list
}

Move-CursorAway -PanelRect $PanelRect
Start-Sleep -Milliseconds 500

$comboBeforeBmp = Capture-ScreenBitmap ($comboRect[0] - 2) ($comboRect[1] - 2) ($comboRect[2] + 4) ($comboRect[3] + 4)
$comboBeforeImg = Get-ImageBytes $comboBeforeBmp
$comboBeforeBmp.Dispose()

$belowY = $comboRect[1] + $comboRect[3]
$belowW = [math]::Min($comboRect[2] + 140, ($PanelRect[0] + $PanelRect[2]) - $comboRect[0])
$belowH = [math]::Min(620, ($PanelRect[1] + $PanelRect[3]) - $belowY)
$belowBeforeBmp = Capture-ScreenBitmap $comboRect[0] $belowY $belowW $belowH
$belowBeforeImg = Get-ImageBytes $belowBeforeBmp
$belowBeforeBmp.Dispose()
Write-Log ('下拉前基线：组合框 {0}x{1}；下方区域 {2}x{3}（x={4} y={5}）' -f $comboBeforeImg.Width, $comboBeforeImg.Height, $belowW, $belowH, $comboRect[0], $belowY)

$winBefore = Get-PSVisibleWindows -ProcId ([uint32]$proc.Id)
$winBeforeKeys = @{}
foreach ($w in $winBefore) { $winBeforeKeys[[string]$w.H] = $w }
Write-Log ('下拉前 PS 可见顶层窗口 {0} 个（已跳过屏幕外/零尺寸）' -f $winBefore.Count)

# ----------------------------------------------------------------------------
# 7. 点击「模式」ComboBox 展开下拉
# ----------------------------------------------------------------------------
$clickComboX = [int]($comboRect[0] + $comboRect[2] / 2)
$clickComboY = [int]($comboRect[1] + $comboRect[3] / 2)
Write-Log ('点击「模式」ComboBox 中心：({0},{1})' -f $clickComboX, $clickComboY)
Invoke-ClickAt -X $clickComboX -Y $clickComboY
Move-CursorAway -PanelRect $PanelRect
Start-Sleep -Milliseconds 900

$belowAfterBmp = Capture-ScreenBitmap $comboRect[0] $belowY $belowW $belowH
$belowAfterImg = Get-ImageBytes $belowAfterBmp
$belowAfterBmp.Dispose()
$openDiff = Get-MeanAbsDiff -ImgA $belowBeforeImg -ImgB $belowAfterImg
Write-Log ('下拉展开判定：下方区域平均像素差={0}（>2 视为已展开）' -f $openDiff)

if ($openDiff -le 2) {
    # 兜底：用 UIA ExpandCollapsePattern 展开
    if ($null -ne $modeCombo) {
        try {
            $pat = $modeCombo.El.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
            Write-Log ('点击未展开下拉，尝试 UIA ExpandCollapsePattern.Expand()；展开前状态={0}' -f $pat.Current.ExpandCollapseState)
            $pat.Expand()
            Start-Sleep -Milliseconds 900
            $belowAfterBmp = Capture-ScreenBitmap $comboRect[0] $belowY $belowW $belowH
            $belowAfterImg = Get-ImageBytes $belowAfterBmp
            $belowAfterBmp.Dispose()
            $openDiff = Get-MeanAbsDiff -ImgA $belowBeforeImg -ImgB $belowAfterImg
            Write-Log ('UIA Expand 后下方区域平均像素差={0}' -f $openDiff)
        } catch { Write-Log ('UIA ExpandCollapsePattern 失败：' + $_.Exception.Message) }
    }
}
if ($openDiff -le 2) {
    $FailNotes.Add('点击「模式」ComboBox 后下方区域像素无变化，疑似下拉未展开。') | Out-Null
    Write-Log '警告：下拉疑似未展开，仍按现场截图与 UIA 结果记录'
}

# ----------------------------------------------------------------------------
# 8. 采集路线 A：PS 顶层窗口差分 + 标准 ComboLBox 直接读条目
# ----------------------------------------------------------------------------
$winAfter = Get-PSVisibleWindows -ProcId ([uint32]$proc.Id)
Write-Log ('下拉后 PS 可见顶层窗口 {0} 个：' -f $winAfter.Count)
$newWindows = New-Object System.Collections.Generic.List[object]
foreach ($w in $winAfter) {
    $key = [string]$w.H
    $old = $winBeforeKeys[$key]
    if ($null -eq $old) {
        $newWindows.Add($w) | Out-Null
        Write-Log ('  新增可见窗口 HWND={0} cls={1} 矩形={2},{3},{4}x{5}' -f $w.H, $w.Cls, $w.X, $w.Y, $w.W, $w.Hh)
    } elseif ($old.X -ne $w.X -or $old.Y -ne $w.Y -or $old.W -ne $w.W -or $old.Hh -ne $w.Hh) {
        $newWindows.Add($w) | Out-Null
        Write-Log ('  窗口变化 HWND={0} cls={1} 矩形 {2},{3},{4}x{5} → {6},{7},{8}x{9}' -f $w.H, $w.Cls, $old.X, $old.Y, $old.W, $old.Hh, $w.X, $w.Y, $w.W, $w.Hh)
    } else {
        Write-Log ('  未变化 HWND={0} cls={1} 矩形={2},{3},{4}x{5}' -f $w.H, $w.Cls, $w.X, $w.Y, $w.W, $w.Hh)
    }
}

$listboxCount = -1
$listboxItems = New-Object System.Collections.Generic.List[object]
foreach ($w in $newWindows) {
    if ($w.Cls -eq 'ComboLBox') {
        $cnt = [BCMD.Native]::ListBoxCount([IntPtr]$w.H)
        $ih = [BCMD.Native]::ListBoxItemHeight([IntPtr]$w.H)
        Write-Log ('  标准列表框 ComboLBox HWND={0} 矩形={1},{2},{3}x{4} 条目数={5} 条目高={6}（条目总高={7}）' -f $w.H, $w.X, $w.Y, $w.W, $w.Hh, $cnt, $ih, ($cnt * $ih))
        if ($cnt -gt 0) {
            $listboxCount = $cnt
            for ($i = 0; $i -lt $cnt; $i++) {
                try {
                    $txt = [BCMD.Native]::ListBoxText([IntPtr]$w.H, $i)
                    $listboxItems.Add([pscustomobject]@{ Index = $i; Text = $txt }) | Out-Null
                    Write-Log ('    LB[{0}] = [{1}]' -f $i, $txt)
                } catch {
                    Write-Log ('    LB[{0}] 读取失败：{1}' -f $i, $_.Exception.Message)
                    $FailNotes.Add(('标准列表框第 {0} 项文本读取失败：{1}' -f $i, $_.Exception.Message)) | Out-Null
                }
            }
        }
    }
}

# ----------------------------------------------------------------------------
# 9. 采集路线 B：UIA 枚举（PS 顶层元素 + 模式 ComboBox 子树）
# ----------------------------------------------------------------------------
function Write-UiaTree {
    param($Element, [int]$Depth, [int]$MaxDepth, [int[]]$RefRect)
    $walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
    $child = $walker.GetFirstChild($Element)
    while ($null -ne $child) {
        try {
            $c = $child.Current
            $r = $c.BoundingRectangle
            $inRef = $true
            if ($null -ne $RefRect) {
                $inRef = (-not $r.IsEmpty) -and ($r.Right -ge $RefRect[0]) -and ($r.Left -le ($RefRect[0] + $RefRect[2])) -and ($r.Bottom -ge $RefRect[1]) -and ($r.Top -le ($RefRect[1] + $RefRect[3]))
            }
            if ($inRef) {
                $type = $c.ControlType.ProgrammaticName.Replace('ControlType.','')
                Write-Host ('UIA|{0}{1}|{2}|{3}|{4}|En={5}|Off={6}|{7}' -f ('  ' * $Depth), $type, $c.Name, $c.AutomationId, $c.ClassName, $c.IsEnabled, $c.IsOffscreen, (Format-Rect $r))
                if ($Depth -lt $MaxDepth) { Write-UiaTree -Element $child -Depth ($Depth + 1) -MaxDepth $MaxDepth -RefRect $RefRect }
            } else {
                if ($Depth -lt $MaxDepth) { Write-UiaTree -Element $child -Depth ($Depth + 1) -MaxDepth $MaxDepth -RefRect $RefRect }
            }
        } catch { }
        $child = $walker.GetNextSibling($child)
    }
}

$refRect = @(($PanelRect[0] - 40), ($PanelRect[1] - 40), ($PanelRect[2] + 80), ($PanelRect[3] + 80))
Write-Host '----- UIA 树（下拉展开后，仅打印与面板区域相交的元素）-----'
if ($null -ne $mainEl) {
    try {
        $topCond = New-Object System.Windows.Automation.PropertyCondition($UIAE::ProcessIdProperty, $proc.Id)
        foreach ($top in $UIAE::RootElement.FindAll($UIA_TreeScope::Children, $topCond)) {
            $tr = $top.Current.BoundingRectangle
            if ($tr.IsEmpty) { continue }
            if ($tr.Right -lt $refRect[0] -or $tr.Left -gt ($refRect[0] + $refRect[2])) { continue }
            if ($tr.Bottom -lt $refRect[1] -or $tr.Top -gt ($refRect[1] + $refRect[3])) { continue }
            Write-Host ('TOP|{0}|{1}|{2}|{3}' -f $top.Current.ControlType.ProgrammaticName.Replace('ControlType.',''), $top.Current.Name, $top.Current.ClassName, (Format-Rect $tr))
            Write-UiaTree -Element $top -Depth 1 -MaxDepth 10 -RefRect $refRect
        }
    } catch { Write-Log ('UIA 顶层枚举异常：' + $_.Exception.Message) }
} else {
    Write-Host 'UIA 主窗口元素缺失，跳过 UIA 树枚举'
}

Write-Host '----- 模式 ComboBox 子树（下拉展开后）-----'
if ($null -ne $modeCombo) {
    try {
        Write-Host ('COMBO|{0}|{1}|{2}' -f $modeCombo.Name, $modeCombo.Aid, (Format-Rect (New-Object System.Windows.Rect $modeCombo.X, $modeCombo.Y, $modeCombo.W, $modeCombo.H)))
        Write-UiaTree -Element $modeCombo.El -Depth 1 -MaxDepth 8 -RefRect $null
        try {
            $pat = $modeCombo.El.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
            Write-Host ('COMBO ExpandCollapseState={0}' -f $pat.Current.ExpandCollapseState)
        } catch { Write-Host 'COMBO 不支持 ExpandCollapsePattern' }
        try {
            $pat2 = $modeCombo.El.GetCurrentPattern([System.Windows.Automation.SelectionPattern]::Pattern)
            Write-Host ('COMBO SelectionPattern 当前选中 {0} 项' -f $pat2.Current.GetSelection().Count)
        } catch { Write-Host 'COMBO 不支持 SelectionPattern' }
    } catch { Write-Log ('UIA 打印 ComboBox 子树异常：' + $_.Exception.Message) }
} else {
    Write-Host 'UIA 未定位到模式 ComboBox，跳过其子树枚举'
}

# ----------------------------------------------------------------------------
# 10. 采集路线 C：截图（面板内容区整窗，包含完整下拉）
# ----------------------------------------------------------------------------
$SaveH = [math]::Min($CropH, $ShotMaxHeight)
if ($SaveH -lt $CropH) {
    Write-Log ('截图高度上限 {0}px（面板内容区高 {1}px）：裁掉下缘以避开本机桌面浮层（桌宠）' -f $ShotMaxHeight, $CropH)
}
$shotPath = Join-Path $OutAbs ($BaseName + '.png')
$shotBmp = Capture-ScreenBitmap $PanelRect[0] $PanelRect[1] $PanelRect[2] $PanelRect[3]
$shotSaved = Save-Crop-Png -Bmp $shotBmp -X $CropX -Y $CropY -W $CropW -H $SaveH -Path $shotPath
$shotBmp.Dispose()
$shotFi = Get-Item $shotPath
Write-Log ('下拉截图：{0}  {1}x{2}  {3} B' -f $shotPath, $shotSaved.Width, $shotSaved.Height, $shotFi.Length)

$scrollPath = ''
if ($ScrollCapture) {
    # 键盘 End 把列表滚到底（只移动高亮，不确认选择；随后 Esc 取消）
    [System.Windows.Forms.SendKeys]::SendWait('{END}')
    Start-Sleep -Milliseconds 800
    $scrollPath = Join-Path $OutAbs ($BaseName + 'b.png')
    $scrollBmp = Capture-ScreenBitmap $PanelRect[0] $PanelRect[1] $PanelRect[2] $PanelRect[3]
    $scrollSaved = Save-Crop-Png -Bmp $scrollBmp -X $CropX -Y $CropY -W $CropW -H $SaveH -Path $scrollPath
    $scrollBmp.Dispose()
    $scrollFi = Get-Item $scrollPath
    Write-Log ('滚动后截图：{0}  {1}x{2}  {3} B' -f $scrollPath, $scrollSaved.Width, $scrollSaved.Height, $scrollFi.Length)
}

# ----------------------------------------------------------------------------
# 11. 按 Esc 关闭下拉，验证「未改变」组合框状态
# ----------------------------------------------------------------------------
[void][BCMD.Native]::SetForegroundWindow($script:PanelHwnd)
Start-Sleep -Milliseconds 250
[System.Windows.Forms.SendKeys]::SendWait('{ESC}')
Start-Sleep -Milliseconds 800
# 若标准列表框仍可见（Esc 未被接收），补发一次 Esc
$lbStill = @(Get-PSVisibleWindows -ProcId ([uint32]$proc.Id) | Where-Object { $_.Cls -eq 'ComboLBox' })
if ($lbStill.Count -gt 0) {
    Write-Log ('第一次 Esc 后仍有可见 ComboLBox {0} 个，补发 Esc' -f $lbStill.Count)
    [void][BCMD.Native]::SetForegroundWindow($script:PanelHwnd)
    Start-Sleep -Milliseconds 250
    [System.Windows.Forms.SendKeys]::SendWait('{ESC}')
    Start-Sleep -Milliseconds 800
}
Move-CursorAway -PanelRect $PanelRect
Start-Sleep -Milliseconds 300

$comboAfterBmp = Capture-ScreenBitmap ($comboRect[0] - 2) ($comboRect[1] - 2) ($comboRect[2] + 4) ($comboRect[3] + 4)
$comboAfterImg = Get-ImageBytes $comboAfterBmp
$comboAfterBmp.Dispose()
$escDiff = Get-MeanAbsDiff -ImgA $comboBeforeImg -ImgB $comboAfterImg
Write-Log ('Esc 后组合框区域与下拉前平均像素差={0}（0~2 视为未改变）' -f $escDiff)

$belowClosedBmp = Capture-ScreenBitmap $comboRect[0] $belowY $belowW $belowH
$belowClosedImg = Get-ImageBytes $belowClosedBmp
$belowClosedBmp.Dispose()
$closedDiff = Get-MeanAbsDiff -ImgA $belowBeforeImg -ImgB $belowClosedImg
Write-Log ('Esc 后下方区域与下拉前平均像素差={0}（0~2 表示下拉已收起且内容未变）' -f $closedDiff)

$winClosed = Get-PSVisibleWindows -ProcId ([uint32]$proc.Id)
$stillNew = 0
foreach ($w in $winClosed) {
    if ($null -eq $winBeforeKeys[[string]$w.H]) {
        if ($w.W -gt 40 -and $w.Hh -gt 40) { $stillNew++; Write-Log ('  Esc 后仍存在新增可见窗口 HWND={0} cls={1} 矩形={2},{3},{4}x{5}' -f $w.H, $w.Cls, $w.X, $w.Y, $w.W, $w.Hh) }
    }
}
if ($stillNew -gt 0) {
    $FailNotes.Add(('Esc 后仍有 {0} 个新增可见窗口（下拉可能未关闭）。' -f $stillNew)) | Out-Null
} else {
    Write-Log 'Esc 后无新增可见窗口（下拉已关闭）'
}

$comboNameAfter = ''
if ($null -ne $modeCombo) {
    try { $comboNameAfter = $modeCombo.El.Current.Name } catch { }
    Write-Log ('组合框 UIA Name：下拉前=[{0}] Esc 后=[{1}] 一致={2}' -f $comboNameBefore, $comboNameAfter, ($comboNameBefore -eq $comboNameAfter))
}
if ($escDiff -gt 2) {
    $FailNotes.Add(('Esc 后组合框区域像素差 {0}（>2），可能误选了下拉项，需人工核对当前模式是否仍为「线性高度」。' -f $escDiff)) | Out-Null
    Write-Log '警告：组合框显示可能已变化，请人工核对！'
}

# ----------------------------------------------------------------------------
# 12. 还原勾选状态（点选页签可能被 PS 自动勾上）
# ----------------------------------------------------------------------------
$nowBmp = Capture-ScreenBitmap $PanelRect[0] $PanelRect[1] $PanelRect[2] $PanelRect[3]
$nowImg = Get-ImageBytes $nowBmp
$nowBmp.Dispose()
$nowStates = Get-CheckStates -Img $nowImg -RowCenters $rowCenters
$restoreCount = 0
for ($i = 1; $i -lt 13; $i++) {
    if ($nowStates[$i] -ne $initialStates[$i]) {
        Invoke-ClickAt -X ($PanelRect[0] + $CropX + 38) -Y ($PanelRect[1] + $rowCenters[$i])
        $restoreCount++
        Write-Log ('  还原第 {0} 行勾选框（{1} → {2}）' -f ($i + 1), $(if ($nowStates[$i]) { '勾选' } else { '未勾选' }), $(if ($initialStates[$i]) { '勾选' } else { '未勾选' }))
    }
}
if ($restoreCount -gt 0) {
    Start-Sleep -Milliseconds 500
    Move-CursorAway -PanelRect $PanelRect
    Start-Sleep -Milliseconds 400
    $verBmp = Capture-ScreenBitmap $PanelRect[0] $PanelRect[1] $PanelRect[2] $PanelRect[3]
    $verImg = Get-ImageBytes $verBmp
    $verBmp.Dispose()
    $verStates = Get-CheckStates -Img $verImg -RowCenters $rowCenters
    $mismatch = 0
    for ($i = 1; $i -lt 13; $i++) { if ($verStates[$i] -ne $initialStates[$i]) { $mismatch++ } }
    Write-Log ('勾选状态还原：共改 {0} 项，复核不一致 {1} 项' -f $restoreCount, $mismatch)
    if ($mismatch -gt 0) { $FailNotes.Add(('勾选状态还原后复核仍有 {0} 项不一致。' -f $mismatch)) | Out-Null }
} else {
    Write-Log '勾选状态未发生变化，无需还原'
}

# ----------------------------------------------------------------------------
# 13. 收尾：关闭画笔设置面板 / 退出 PS（若本次启动）
# ----------------------------------------------------------------------------
$panelClosed = $false
if (-not $KeepPanel) {
    if ([BCMD.Native]::IsWindowVisible($script:PanelHwnd)) {
        Invoke-ClickAt -X ($PanelRect[0] + $PanelRect[2] - 15) -Y ($PanelRect[1] + 8)
        Start-Sleep -Milliseconds 1500
    }
    $panelClosed = -not [BCMD.Native]::IsWindowVisible($script:PanelHwnd)
    if (-not $panelClosed) { $FailNotes.Add('画笔设置面板未能关闭（点击标题栏 × 后 IsWindowVisible 仍为 True）。') | Out-Null }
    Write-Log ('画笔设置面板已关闭=' + $panelClosed)
} else {
    Write-Log '指定了 -KeepPanel，不关闭画笔设置面板'
}

$psExited = $false
if ($script:StartedByUs -and -not $KeepPhotoshop) {
    Write-Log 'Photoshop 是本次脚本启动的，发送 WM_CLOSE 退出（未打开任何文档，不保存）'
    [void][BCMD.Native]::PostMessage($mainHwnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
    $sw3 = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw3.Elapsed.TotalSeconds -lt 40) {
        Start-Sleep -Milliseconds 800
        $p2 = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
        if ($null -eq $p2) { $psExited = $true; break }
    }
    if (-not $psExited) {
        try { Stop-Process -Id $proc.Id -Force -ErrorAction Stop; $psExited = $true } catch { $FailNotes.Add('强制结束 PS 失败：' + $_.Exception.Message) | Out-Null }
    }
    Write-Log ('Photoshop 已退出 = ' + $psExited)
} else {
    Write-Log 'Photoshop 不是本次脚本启动的或指定了 -KeepPhotoshop，保留不退出'
}

# ----------------------------------------------------------------------------
# 14. 控制台总结（便于 lead 摘数）
# ----------------------------------------------------------------------------
Write-Host ''
Write-Host '===== 模式下拉采集汇总 ====='
Write-Host ('面板外框：' + ($PanelRect -join ','))
Write-Host ('内容区裁剪：' + $CropW + 'x' + $CropH + ' 偏移=' + $CropX + ',' + $CropY)
Write-Host ('纹理页选中：第 4 行 亮度=' + $sel.Bright + ' 次高=' + $sel.Second + ' 文字带=' + $checkBands.Count)
Write-Host ('模式 ComboBox：' + ($comboRect -join ',') + ' UIA_Name=[' + $comboNameBefore + ']')
Write-Host ('下拉展开像素差：' + $openDiff + '；Esc 后组合框差：' + $escDiff + '；Esc 后下方差：' + $closedDiff)
Write-Host ('标准 ComboLBox 条目数：' + $listboxCount)
if ($listboxItems.Count -gt 0) {
    Write-Host ('标准 ComboLBox 条目文本（按索引，原文）：')
    foreach ($it in $listboxItems) { Write-Host ('  [{0}] {1}' -f $it.Index, $it.Text) }
}
Write-Host ('截图：' + $shotPath + '  ' + $shotSaved.Width + 'x' + $shotSaved.Height + '  ' + (Get-Item $shotPath).Length + ' B')
if ($scrollPath) { Write-Host ('滚动截图：' + $scrollPath + '  ' + (Get-Item $scrollPath).Length + ' B') }
Write-Host ('面板已关闭：' + $panelClosed + '；PS 已退出：' + $psExited + '（本次启动=' + $script:StartedByUs + '）')
Write-Host ('失败项：' + $FailNotes.Count)
foreach ($f in $FailNotes) { Write-Host ('  - ' + $f) }
Write-Host '============================='
