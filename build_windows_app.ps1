$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$releaseDirectory = Join-Path $projectRoot "dist\RSIWatch"
$settingsPath = Join-Path $releaseDirectory "config\settings.ini"
$stagingRoot = Join-Path $projectRoot "dist-staging"
$stagedReleaseDirectory = Join-Path $stagingRoot "RSIWatch"
$previousReleaseDirectory = Join-Path $projectRoot "dist\RSIWatch-previous"

$runningApp = Get-CimInstance Win32_Process -Filter "Name = 'RSIWatch.exe'" |
    Where-Object { $_.ExecutablePath -eq (Join-Path $releaseDirectory "RSIWatch.exe") }
if ($runningApp) {
    throw "RSIWatch 正在运行，请从系统托盘退出程序后重新打包。"
}

py -3 -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "依赖安装失败，退出码：$LASTEXITCODE"
}

Remove-Item -Recurse -Force $stagingRoot -ErrorAction SilentlyContinue
py -3 -m PyInstaller --noconfirm --clean --windowed --name RSIWatch --paths src `
    --distpath $stagingRoot src\rsi_monitor\__main__.py
if ($LASTEXITCODE -ne 0) {
    throw "桌面版打包失败，退出码：$LASTEXITCODE"
}

New-Item -ItemType Directory -Force -Path (Join-Path $stagedReleaseDirectory "config") | Out-Null
Copy-Item -Force config\settings.example.ini (Join-Path $stagedReleaseDirectory "config\settings.example.ini")
if (Test-Path $settingsPath) {
    Copy-Item -Force $settingsPath (Join-Path $stagedReleaseDirectory "config\settings.ini")
} elseif (Test-Path config\settings.ini) {
    Copy-Item -Force config\settings.ini (Join-Path $stagedReleaseDirectory "config\settings.ini")
}

Remove-Item -Recurse -Force $previousReleaseDirectory -ErrorAction SilentlyContinue
if (Test-Path $releaseDirectory) {
    Move-Item $releaseDirectory $previousReleaseDirectory
}
try {
    Move-Item $stagedReleaseDirectory $releaseDirectory
} catch {
    if ((Test-Path $previousReleaseDirectory) -and -not (Test-Path $releaseDirectory)) {
        Move-Item $previousReleaseDirectory $releaseDirectory
    }
    throw
}
Remove-Item -Recurse -Force $previousReleaseDirectory -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $stagingRoot -ErrorAction SilentlyContinue
Write-Host "已生成：dist\RSIWatch\RSIWatch.exe"
