param(
    [Parameter(Mandatory=$true)][string]$BackupFile,
    [switch]$Local,
    [switch]$Yes
)
$ErrorActionPreference = 'Stop'
if (-not $Yes) { throw 'Restore is destructive. Re-run with -Yes after confirming the selected backup.' }
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$backupRoot = [System.IO.Path]::GetFullPath((Join-Path $root 'backups'))
$source = [System.IO.Path]::GetFullPath($BackupFile)
if (-not $source.StartsWith($backupRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "BackupFile must be inside $backupRoot"
}
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Backup not found: $source" }
if ($Local) {
    & python (Join-Path $PSScriptRoot 'sqlite_backup.py') restore --database (Join-Path $root 'collab.db') --input $source --yes
} else {
    $name = [System.IO.Path]::GetFileName($source)
    & docker compose stop collab-ledger
    if ($LASTEXITCODE -ne 0) { throw 'Failed to stop application container' }
    try {
        & docker compose run --rm --no-deps collab-ledger python /app/scripts/sqlite_backup.py restore --database /data/collab.db --input "/backups/$name" --yes
        if ($LASTEXITCODE -ne 0) { throw 'Container restore failed' }
    } finally {
        & docker compose up -d collab-ledger
    }
}
if ($LASTEXITCODE -ne 0) { throw "Restore failed with exit code $LASTEXITCODE" }
Write-Host "Restore complete and integrity checked: $source"
