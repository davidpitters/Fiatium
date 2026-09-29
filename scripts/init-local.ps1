param([string]$Python = '.\.venv\Scripts\python.exe')
$ErrorActionPreference = 'Stop'
if (-not (Test-Path '.env')) {
    $token = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    $tokens = @{ $token = @{ tenant = 'demo'; role = 'operator' } } | ConvertTo-Json -Compress
    "FIATIUM_TOKENS='$tokens'" | Set-Content -LiteralPath '.env' -Encoding utf8
    Write-Output 'Created .env with a random local demo token. Read it locally to sign in.'
}
sqlcmd -S localhost -E -C -b -Q "IF DB_ID('FiatiumDev') IS NULL CREATE DATABASE FiatiumDev;"
if ($LASTEXITCODE -ne 0) { throw 'Database creation failed' }
& $Python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Migration failed' }
& $Python -m fiatium.seed
if ($LASTEXITCODE -ne 0) { throw 'Seed failed' }
