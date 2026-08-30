$ErrorActionPreference = "Stop"
$launcher = Join-Path $PSScriptRoot "local.py"
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $launcher web @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python $launcher web @args
} else {
    Write-Error "未找到 Python 3.10+。请先安装 Python，并启用 py launcher 或加入 PATH。"
}
exit $LASTEXITCODE
