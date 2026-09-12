$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot '.local'
$dataDir = Join-Path $runtimeDir 'postgres'
$pgBin = 'C:\Program Files\PostgreSQL\18\bin'
if (-not (Test-Path -LiteralPath (Join-Path $pgBin 'initdb.exe'))) { throw 'Install PostgreSQL 18 or adjust pgBin to your installed PostgreSQL binaries.' }
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$envPath = Join-Path $projectRoot '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    $dbPassword = [guid]::NewGuid().ToString('N')
    $sessionSecret = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    @("APP_ENV=development", "DATABASE_URL=postgresql+psycopg://wellbeing:${dbPassword}@127.0.0.1:55432/wellbeing", "TEST_DATABASE_URL=postgresql+psycopg://wellbeing:${dbPassword}@127.0.0.1:55432/wellbeing_test", "SESSION_SECRET=$sessionSecret", "FRONTEND_ORIGIN=http://localhost:5173", "ENABLE_DEV_AUTH=true", "COOKIE_SECURE=false") | Set-Content -LiteralPath $envPath -Encoding ASCII
}
$dbUrlLine = Get-Content -LiteralPath $envPath | Where-Object { $_ -like 'DATABASE_URL=*' } | Select-Object -First 1
if ($dbUrlLine -notmatch '^DATABASE_URL=postgresql\+psycopg://wellbeing:([^@]+)@127\.0\.0\.1:55432/wellbeing$') { throw 'This helper only manages its isolated localhost:55432 wellbeing cluster; existing .env differs.' }
$dbPassword = $Matches[1]
if (-not (Test-Path -LiteralPath (Join-Path $dataDir 'PG_VERSION'))) {
    $passwordPath = Join-Path $runtimeDir 'init-password'
    $dbPassword | Set-Content -LiteralPath $passwordPath -Encoding ASCII
    try {
        & (Join-Path $pgBin 'initdb.exe') -D $dataDir --username=wellbeing --encoding=UTF8 --locale=C --auth=scram-sha-256 --pwfile=$passwordPath
        if ($LASTEXITCODE -ne 0) { throw 'Isolated database initialization failed.' }
    } finally { Remove-Item -LiteralPath $passwordPath -ErrorAction SilentlyContinue }
}
& (Join-Path $pgBin 'pg_ctl.exe') -D $dataDir status *> $null
if ($LASTEXITCODE -ne 0) {
    & (Join-Path $pgBin 'pg_ctl.exe') -D $dataDir -l (Join-Path $runtimeDir 'postgres.log') -o '-h 127.0.0.1 -p 55432' -w start
    if ($LASTEXITCODE -ne 0) { throw 'Could not start isolated PostgreSQL; check port 55432 and .local/postgres.log.' }
}
$priorPassword = $env:PGPASSWORD
try {
    $env:PGPASSWORD = $dbPassword
    foreach ($dbName in @('wellbeing', 'wellbeing_test')) {
        $exists = & (Join-Path $pgBin 'psql.exe') -X -h 127.0.0.1 -p 55432 -U wellbeing -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$dbName'"
        if ($LASTEXITCODE -ne 0) { throw 'Cannot connect to isolated PostgreSQL.' }
        if ($exists -ne '1') {
            & (Join-Path $pgBin 'createdb.exe') -h 127.0.0.1 -p 55432 -U wellbeing $dbName
            if ($LASTEXITCODE -ne 0) { throw 'Could not create isolated project database.' }
        }
    }
} finally { $env:PGPASSWORD = $priorPassword }
Write-Output 'Isolated project PostgreSQL is ready on 127.0.0.1:55432. Existing Windows database service was not changed.'
