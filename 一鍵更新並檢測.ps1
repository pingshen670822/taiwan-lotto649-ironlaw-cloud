$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$python=(Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { $python=(Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $python) { throw '找不到 Python 3.12' }
& $python update.py
if ($LASTEXITCODE -ne 0) { throw '更新失敗，既有戰報未覆蓋' }
& $python verify.py
if ($LASTEXITCODE -ne 0) { throw '完整檢測失敗，禁止發布' }
Start-Process (Join-Path $PSScriptRoot 'site\index.html')
