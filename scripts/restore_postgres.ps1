param(
    [Parameter(Mandatory=$true)][string]$BackupFile,
    [switch]$Yes
)
$ErrorActionPreference = 'Stop'
if (-not $Yes) { throw 'Restore is destructive. Re-run with -Yes after confirming the selected backup.' }
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$backupRoot = [System.IO.Path]::GetFullPath((Join-Path $root 'backups'))
$source = [System.IO.Path]::GetFullPath($BackupFile)
if (-not $source.StartsWith($backupRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw "BackupFile must be inside $backupRoot" }
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Backup not found: $source" }
$pgUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { 'collab' }
$pgDb = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { 'collab_ledger' }
$name = [System.IO.Path]::GetFileName($source)
& docker compose -f compose.yaml -f compose.postgres.yaml --profile postgres exec -T postgres pg_restore --list "/backups/$name" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Selected PostgreSQL backup is invalid' }
& (Join-Path $PSScriptRoot 'backup_postgres.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Pre-restore PostgreSQL backup failed' }
& docker compose -f compose.yaml -f compose.postgres.yaml --profile postgres stop collab-ledger
try {
    & docker compose -f compose.yaml -f compose.postgres.yaml --profile postgres exec -T postgres pg_restore --clean --if-exists --no-owner --no-acl --username $pgUser --dbname $pgDb "/backups/$name"
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL restore failed' }
} finally {
    & docker compose -f compose.yaml -f compose.postgres.yaml --profile postgres up -d collab-ledger
}
Write-Host "PostgreSQL restore complete: $source"
