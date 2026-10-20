# WebDesktop 打包脚本：使用 PyInstaller 将应用打包为独立 exe
# 用法：powershell -ExecutionPolicy Bypass -File build.ps1 [-OneDir] [-Release <目录>]
#   -OneDir   产出目录形态（dist\WebDesktopDir\，无自解压步骤，启动更快）
#   -Release  成品复制到该目录（默认桌面）：工作区内的产物带 Low Mandatory Label，
#             在工作区内直接运行会被降级、无法写用户配置目录，必须放到工作区之外

param(
    [switch]$OneDir,
    [string]$Release = (Join-Path $env:USERPROFILE 'Desktop\WebDesktop')
)

$ErrorActionPreference = 'Stop'

# 切换到脚本所在目录（项目根）
Set-Location -Path $PSScriptRoot

# 沙箱兼容：将临时目录指向工作区，并通过 sitecustomize 修复 mkdtemp 权限问题
$tmpDir = Join-Path $PSScriptRoot '.tmp'
$sitecustomizeDir = Join-Path $PSScriptRoot '.sitecustomize'
$siteDir = Join-Path $PSScriptRoot '.site'
$extraPaths = @()
if (Test-Path $sitecustomizeDir) { $extraPaths += $sitecustomizeDir }
if (Test-Path $siteDir) { $extraPaths += $siteDir }
if ($extraPaths.Count -gt 0) {
    $env:PYTHONPATH = ($extraPaths -join ';') + ';' + $env:PYTHONPATH
}
if (Test-Path $tmpDir) {
    $env:TMP = $tmpDir
    $env:TEMP = $tmpDir
}

Write-Host '=== WebDesktop 打包开始 ==='

# 开发模式下第三方依赖安装在 .site 目录（沙箱环境下系统 site-packages 不可写）
$siteDir = Join-Path $PSScriptRoot '.site'
$pathsArgs = @()
if (Test-Path $siteDir) {
    $pathsArgs = @('--paths', $siteDir)
    Write-Host "检测到本地依赖目录：$siteDir"
}

# 应用默认图标：根目录 app.ico（打包嵌入 exe，同时作为运行时窗口/任务栏图标来源）
$iconFile = Join-Path $PSScriptRoot 'app.ico'
if (-not (Test-Path $iconFile)) {
    Write-Error "未找到应用图标：$iconFile，请在项目根目录放置 app.ico 后重试。"
    exit 1
}

# 清理旧的打包产物：只清本次形态的目标（PyInstaller --noconfirm 会重建自己的输出），
# 不要整个删掉 dist —— 否则单文件与目录两种形态会互相挤掉
if (Test-Path 'build') { Remove-Item -Recurse -Force 'build' }

# 解释器选择：优先环境变量 WEBDESKTOP_PYTHON，其次 py 启动器的 3.12，最后 python
# （本机 python 默认指向 3.14 且未安装 pip/PyInstaller，直接调用会失败）
$pythonCmd = 'python'
$pythonPrefix = @()
if ($env:WEBDESKTOP_PYTHON) {
    $pythonCmd = $env:WEBDESKTOP_PYTHON
    Write-Host "使用指定解释器：$pythonCmd"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCmd = 'py'
    $pythonPrefix = @('-3.12')
    Write-Host '使用解释器：py -3.12'
}

# PyInstaller 打包：单文件、无控制台窗口、收集 webview 相关模块，使用 app.ico 作为 exe 图标
#
# 注意（Windows PowerShell 5.1 的坑）：本脚本开头设了 $ErrorActionPreference='Stop'，
# 此时原生程序向 stderr 写任何内容都会被当成终止性错误 —— PyInstaller 光是打印一行
# INFO 就会让脚本中断。故本次调用临时放宽为 Continue，并把输出重定向到日志文件。
$pyInstallerLog = Join-Path $PSScriptRoot 'build.log'
$savedErrorAction = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
if ($OneDir) {
    Write-Host '产物形态：目录（--onedir，无自解压）'
    $modeArgs = @('--onedir')
    $appName = 'WebDesktopDir'
} else {
    Write-Host '产物形态：单文件（--onefile）'
    $modeArgs = @('--onefile')
    $appName = 'WebDesktop'
}
& $pythonCmd @pythonPrefix -m PyInstaller --noconfirm --clean --windowed @modeArgs `
    --name $appName `
    --icon $iconFile `
    --add-data "app\web;app\web" `
    --collect-all webview `
    --collect-all clr_loader `
    --collect-all pythonnet `
    --hidden-import bottle `
    --hidden-import proxy_tools `
    --hidden-import clr `
    @pathsArgs `
    main.py *> $pyInstallerLog
$pyInstallerExit = $LASTEXITCODE
$ErrorActionPreference = $savedErrorAction
Get-Content $pyInstallerLog -Tail 15 | Write-Host
if ($pyInstallerExit -ne 0) {
    Write-Error "PyInstaller 打包失败（退出码 $pyInstallerExit），完整日志：$pyInstallerLog"
    exit $pyInstallerExit
}

# 配置文件不再随包分发：首次运行时程序自动在用户主目录 ~/.WebDesktop 下创建
Write-Host ''
Write-Host '=== 打包完成 ==='
if ($OneDir) {
    Write-Host ('生成目录：' + (Join-Path 'dist' 'WebDesktopDir') + '（运行 WebDesktopDir.exe）')
    Write-Host '该形态无自解压步骤：不依赖 TEMP 可写，启动也更快。'
} else {
    Write-Host ('生成文件：' + (Join-Path 'dist' 'WebDesktop.exe'))
    Write-Host '若提示「Could not create temporary directory!」，说明当前环境不允许写临时目录：'
    Write-Host '  请改用目录形态（build.ps1 -OneDir），或直接从资源管理器双击运行（不要经受限终端启动）。'
}
# 发布：把成品复制到工作区之外（默认桌面）
# 原因：工作区内的文件带 Low Mandatory Label，在工作区内直接运行会被降级，
# 程序将无法写入 ~/.WebDesktop，表现为 WinError 5 / Errno 13「拒绝访问」。
if ($Release) {
    New-Item -ItemType Directory -Force -Path $Release | Out-Null
    if ($OneDir) {
        Copy-Item (Join-Path 'dist' 'WebDesktopDir\*') $Release -Recurse -Force
        Write-Host ('已复制到发布目录：' + $Release + '（运行 WebDesktopDir.exe）')
    } else {
        Copy-Item (Join-Path 'dist' 'WebDesktop.exe') $Release -Force
        Write-Host ('已复制到发布目录：' + $Release + '（运行 WebDesktop.exe）')
    }
    Write-Host '注意：不要直接运行工作区 dist 里的那份 —— 它会被降级，写不了用户配置目录。'
}
Write-Host '运行 create_shortcut.ps1 可在桌面创建快捷方式。'
