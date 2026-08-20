$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "未找到 Python。请先安装 Python 3.12 或更高版本，并勾选加入 PATH。"
}

py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "当前 Python 版本低于 3.12，请升级后再运行。"
}

py -3 -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "依赖安装失败，退出码：$LASTEXITCODE"
}

py -3 -m pip install -e .
if ($LASTEXITCODE -ne 0) {
    throw "项目安装失败，退出码：$LASTEXITCODE"
}

py -3 -m rsi_monitor
