$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

py -3 -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "依赖安装失败，退出码：$LASTEXITCODE"
}
py -3 -m PyInstaller --noconfirm --clean --windowed --name RSIWatch --paths src src\rsi_monitor\__main__.py
if ($LASTEXITCODE -ne 0) {
    throw "桌面版打包失败，退出码：$LASTEXITCODE"
}

New-Item -ItemType Directory -Force -Path dist\RSIWatch\config | Out-Null
Copy-Item -Force config\settings.ini dist\RSIWatch\config\settings.ini
if (Test-Path config\monitor_sessions.json) {
    Copy-Item -Force config\monitor_sessions.json dist\RSIWatch\config\monitor_sessions.json
}
Write-Host "已生成：dist\RSIWatch\RSIWatch.exe"
