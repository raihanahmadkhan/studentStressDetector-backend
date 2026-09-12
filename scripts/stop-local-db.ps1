$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$dataDir = Join-Path $projectRoot '.local\postgres'
& 'C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe' -D $dataDir -m fast -w stop
