$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$python=(Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { $python=(Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $python) { throw '找不到 Python 3.12' }
& $python auto_repair.py
if ($LASTEXITCODE -ne 0) { throw '自主修復三輪仍未通過；最後有效資料與戰報已自動回復' }
Start-Process (Join-Path $PSScriptRoot 'site\index.html')
