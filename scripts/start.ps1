# =============================================================================
# scripts/start.ps1 — FLARE Windows PowerShell Startup Script
# =============================================================================

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "       FLARE CROSS-LAYER UAV SIMULATION (Windows)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$PythonBin = Join-Path $RepoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $PythonBin)) {
    $PythonBin = "python"
}

# Run the cross-platform python orchestrator
& $PythonBin "$RepoRoot\scripts\start.py" $args
