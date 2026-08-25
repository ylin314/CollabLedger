param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\backups'),
    [switch]$Local
)
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$output = [System.IO.Path]::GetFullPath($OutputDirectory)
if (-not $output.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDirectory must be inside workspace: $root"
}
New-Item -ItemType Directory -Force -Path $output | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$name = "collab-$stamp.db"
if ($Local) {
    & python (Join-Path $PSScriptRoot 'sqlite_backup.py') backup --database (Join-Path $root 'collab.db') --output (Join-Path $output $name)
} else {
    & docker compose exec -T collab-ledger python /app/scripts/sqlite_backup.py backup --database /data/collab.db --output "/backups/$name"
}
if ($LASTEXITCODE -ne 0) { throw "Backup failed with exit code $LASTEXITCODE" }
Write-Host "Backup complete: $(Join-Path $output $name)"
