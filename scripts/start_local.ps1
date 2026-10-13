# Arranca el sistema completo en Windows para operar con XM (MetaTrader 5).
#
#   powershell -ExecutionPolicy Bypass -File scripts\start_local.ps1
#
# Requisitos: Docker Desktop, el terminal MT5 de XM abierto con "Algo Trading"
# activado, el entorno virtual creado (ver README) y el archivo .env configurado.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "No existe .venv. Crealo con: python -m venv .venv ; .venv\Scripts\pip install -r requirements-local.txt" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $root ".env"))) {
    Write-Host "Falta el archivo .env (copia .env.example a .env y completalo)." -ForegroundColor Red
    exit 1
}

Write-Host "1) Levantando TimescaleDB, Redis y Grafana en Docker..." -ForegroundColor Cyan
docker compose -f docker-compose-dev.yml up -d
if ($LASTEXITCODE -ne 0) { Write-Host "Docker no respondio. Abre Docker Desktop." -ForegroundColor Red; exit 1 }
Start-Sleep -Seconds 5

Write-Host "2) Verificando conexion con MT5..." -ForegroundColor Cyan
& $python scripts\check_mt5.py
if ($LASTEXITCODE -ne 0) { Write-Host "Corrige los problemas de MT5 antes de continuar." -ForegroundColor Red; exit 1 }

# El execution-engine va primero: publica el estado de la cuenta que necesita el risk-manager.
$services = @("execution-engine", "monitoring", "risk-manager", "strategy-engine", "data-collector")
foreach ($svc in $services) {
    Write-Host "3) Iniciando $svc en una ventana nueva..." -ForegroundColor Cyan
    $script = Join-Path $root "services\$svc\src\main.py"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle='$svc'; & '$python' '$script'"
    Start-Sleep -Seconds 2
}

Write-Host "Listo. Grafana: http://localhost:3000 (admin/admin). Para detener: cierra las ventanas (Ctrl+C)." -ForegroundColor Green
