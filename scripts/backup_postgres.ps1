param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\backups')
)
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$output = [System.IO.Path]::GetFullPath($OutputDirectory)
if (-not $output.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) { throw "OutputDirectory must be inside workspace: $root" }
New-Item -ItemType Directory -Force -Path $output | Out-Null
$pgUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { 'collab' }
$pgDb = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { 'collab_ledger' }
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$name = "collab-postgres-$stamp.dump"
& docker compose -f compose.yaml -f compose.postgres.yaml --profile postgres exec -T postgres pg_dump --format=custom --no-owner --no-acl --username $pgUser --dbname $pgDb --file "/backups/$name"
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL backup failed' }
& docker compose -f compose.yaml -f compose.postgres.yaml --profile postgres exec -T postgres pg_restore --list "/backups/$name" | Out-Null
$file = Join-Path $output $name
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $file) -or (Get-Item -LiteralPath $file).Length -eq 0) { throw 'PostgreSQL backup validation failed or produced an empty file' }
Write-Host "PostgreSQL backup created and verified: $file"
