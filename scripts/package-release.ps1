param([string]$Frontend = (Join-Path $PSScriptRoot '..\..\stressed'))
$ErrorActionPreference = 'Stop'
$backendRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$frontendRoot = (Resolve-Path $Frontend).Path
Push-Location $frontendRoot
try {
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
} finally { Pop-Location }
$releaseRoot = Join-Path $backendRoot ('.local\releases\release-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
if (Test-Path -LiteralPath $releaseRoot) { throw 'Release folder already exists' }
New-Item -ItemType Directory -Path $releaseRoot | Out-Null
foreach ($item in @('app', 'migrations', 'requirements.txt', 'alembic.ini', 'main.py', 'render.yaml')) {
    Copy-Item -LiteralPath (Join-Path $backendRoot $item) -Destination $releaseRoot -Recurse
}
Copy-Item -LiteralPath (Join-Path $frontendRoot 'dist') -Destination (Join-Path $releaseRoot 'static') -Recurse
Write-Output "Release staged at $releaseRoot. Set FRONTEND_DIST=static and production secrets on the host; migrate before starting. No deployment was performed."
