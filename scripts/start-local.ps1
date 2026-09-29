param(
    [string]$Python = '.\.venv\Scripts\python.exe',
    [string]$Node = 'node',
    [int]$WebPort = 8088
)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
Set-Location $project
if (-not (Test-Path '.env')) { throw 'Run scripts/init-local.ps1 first.' }
if (-not (Test-Path 'apps/web/dist/index.html')) { throw 'Build apps/web first.' }
foreach ($port in @(8000,$WebPort)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is in use. Stop your previous Fiatium instance or select another web port."
    }
}
$logs = Join-Path $project 'artifacts'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$api = Start-Process -FilePath $Python -ArgumentList '-m','uvicorn','fiatium.api:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $project -WindowStyle Hidden -PassThru -RedirectStandardOutput "$logs/api.log" -RedirectStandardError "$logs/api-error.log"
$env:API_URL = 'http://127.0.0.1:8000'
$env:PORT = "$WebPort"
$env:HOST = '127.0.0.1'
$web = Start-Process -FilePath $Node -ArgumentList 'server.mjs' -WorkingDirectory "$project/apps/web" -WindowStyle Hidden -PassThru -RedirectStandardOutput "$logs/web.log" -RedirectStandardError "$logs/web-error.log"
Start-Sleep -Seconds 2
$apiProcess = (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue).OwningProcess
$webProcess = (Get-NetTCPConnection -LocalPort $WebPort -State Listen -ErrorAction SilentlyContinue).OwningProcess
if (-not $apiProcess -or -not $webProcess) { throw 'A service failed to start; inspect artifacts logs.' }
@{api=$apiProcess;web=$webProcess} | ConvertTo-Json | Set-Content "$logs/local-processes.json"
Write-Output "Started API PID $apiProcess, web PID $webProcess. Open http://127.0.0.1:$WebPort."
Write-Output 'After submitting payments, run: .venv/Scripts/python.exe -m fiatium.worker local-once'
Write-Output 'This local path uses the direct development transport. Compose enables Kafka.'
