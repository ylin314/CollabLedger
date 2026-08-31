# CollabLedger 一键拉起：daemon 掉线自愈 + 容器启动 + 健康检查
param([switch]$Rebuild)
$ErrorActionPreference = "Stop"
$dd = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$dirs = @((Join-Path $env:LOCALAPPDATA "Docker\run"), (Join-Path $env:LOCALAPPDATA "docker-secrets-engine"))

function Wait-Daemon([int]$seconds) {
    for ($i = 0; $i -lt [int]($seconds/5); $i++) {
        Start-Sleep 5
        $v = docker info --format "{{.ServerVersion}}" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) { return $true }
    }
    return $false
}

if (-not (Wait-Daemon 5)) {
    Write-Host "Docker daemon 未运行，清理残留并启动 Docker Desktop…"
    foreach ($dir in $dirs) {
        if (-not (Test-Path $dir)) { continue }
        Get-ChildItem $dir -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $n = $_.Name
            wsl -e rm -f "/mnt/c/Users/$env:USERNAME/AppData/Local/Docker/run/$n" 2>$null
            wsl -e rm -f "/mnt/c/Users/$env:USERNAME/AppData/Local/docker-secrets-engine/$n" 2>$null
        }
    }
    if (Test-Path $dd) { Start-Process $dd -WindowStyle Hidden }
    if (-not (Wait-Daemon 180)) { throw "Docker daemon 3 分钟未就绪，请手动打开 Docker Desktop 查看报错" }
}
Write-Host "Docker daemon 就绪"

if ($Rebuild) { docker compose up -d --build } else { docker compose up -d }
if ($LASTEXITCODE -ne 0) { throw "docker compose up 失败" }

$st = ""
for ($i = 0; $i -lt 24; $i++) {
    Start-Sleep 5
    $st = docker inspect --format "{{.State.Health.Status}}" collab-ledger 2>$null
    if ($st -eq "healthy") { break }
}
if ($st -ne "healthy") { throw "容器未达到 healthy：$st" }
Write-Host "服务就绪：http://127.0.0.1:8000  (healthcheck: healthy)"

$os = Get-CimInstance Win32_OperatingSystem
$freeGB = [math]::Round($os.FreePhysicalMemory/1MB, 1)
Write-Host "当前可用内存: $freeGB GB"
if ($freeGB -lt 2) { Write-Host "警告：可用内存不足 2GB，Docker Desktop 可能再次被系统挤掉，建议关闭高内存程序。" }