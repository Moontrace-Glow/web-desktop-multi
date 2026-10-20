# 在桌面创建 WebDesktop 快捷方式
# 用法：powershell -ExecutionPolicy Bypass -File create_shortcut.ps1
# 参数：-ExePath 指定 exe 路径（默认 dist\WebDesktop.exe）；-ShortcutName 快捷方式名称（默认 WebDesktop）

param(
    [string]$ExePath = (Join-Path $PSScriptRoot 'dist\WebDesktop.exe'),
    [string]$ShortcutName = 'WebDesktop'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $ExePath)) {
    Write-Error "未找到程序：$ExePath，请先运行 build.ps1 打包。"
    exit 1
}

# 获取桌面目录（兼容 OneDrive 重定向）
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
if (-not $desktop) { $desktop = [Environment]::GetFolderPath('Desktop') }

$shortcutPath = Join-Path $desktop "$ShortcutName.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $ExePath
# 工作目录设为 exe 所在目录（配置文件位于用户主目录 ~/.WebDesktop 下，与工作目录无关）
$shortcut.WorkingDirectory = Split-Path -Parent $ExePath
# 快捷方式图标：显式使用 exe 第 0 号内嵌图标（打包时嵌入的 app.ico）
$shortcut.IconLocation = "$ExePath,0"
$shortcut.Description = 'Web 桌面启动器'
$shortcut.Save()

Write-Host "桌面快捷方式已创建：$shortcutPath"
