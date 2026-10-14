$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$releasePath = Join-Path $projectRoot "RSIWatch.exe"
$configDirectory = Join-Path $projectRoot "config"
$stagingRoot = Join-Path $projectRoot "dist-staging"
$buildRoot = Join-Path $projectRoot "build-staging"
$stagedReleasePath = Join-Path $stagingRoot "RSIWatch.exe"

$runningApp = Get-CimInstance Win32_Process -Filter "Name = 'RSIWatch.exe'" |
    Where-Object { $_.ExecutablePath -eq $releasePath }
if ($runningApp) {
    throw "$releasePath 正在运行，请从系统托盘退出程序后重新打包。"
}

py -3 -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "依赖安装失败，退出码：$LASTEXITCODE"
}
$pySideDirectory = (py -3 -c "import pathlib, PySide6; print(pathlib.Path(PySide6.__file__).parent)").Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $pySideDirectory)) {
    throw "无法定位 PySide6 运行库目录。"
}
$icuLibrary = Join-Path $env:WINDIR "System32\icuuc.dll"
if (-not (Test-Path $icuLibrary)) {
    throw "无法定位 Qt 所需的 ICU 运行库：$icuLibrary"
}

Remove-Item -Recurse -Force $stagingRoot, $buildRoot -ErrorAction SilentlyContinue
$buildPathEntries = @(
    $env:PATH -split [IO.Path]::PathSeparator | Where-Object {
        $entry = $_.Trim()
        if (-not $entry -or $entry.Contains('%')) {
            return $false
        }
        try {
            -not (Test-Path (Join-Path $entry "icudt78.dll"))
        } catch [ArgumentException] {
            return $false
        }
    }
)
$originalPath = $env:PATH
try {
    $env:PATH = $buildPathEntries -join [IO.Path]::PathSeparator
    py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name RSIWatch --paths src `
        --distpath $stagingRoot --workpath $buildRoot --specpath $stagingRoot `
        --add-data "$projectRoot\config\settings.example.ini;config" `
        --add-binary "$pySideDirectory\concrt140.dll;PySide6" `
        --add-binary "$pySideDirectory\msvcp140_codecvt_ids.dll;PySide6" `
        --add-binary "$icuLibrary;PySide6" `
        src\rsi_monitor\__main__.py
    $pyInstallerExitCode = $LASTEXITCODE
} finally {
    $env:PATH = $originalPath
}
if ($pyInstallerExitCode -ne 0) {
    throw "桌面版打包失败，退出码：$pyInstallerExitCode"
}

New-Item -ItemType Directory -Force -Path $configDirectory | Out-Null

if (Test-Path $releasePath) {
    Remove-Item -Force $releasePath
}
Move-Item -Force $stagedReleasePath $releasePath
Remove-Item -Recurse -Force $stagingRoot -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $buildRoot -ErrorAction SilentlyContinue
Write-Host "已生成：$releasePath"
