# URBANOVA Phase 1 — setup and run helper (Windows PowerShell).
#   .\run.ps1 setup   create .venv and install pinned dependencies
#   .\run.ps1 test    run the automated acceptance tests (offline, mocked providers)
#   .\run.ps1 demo    seed ./data with the sample project using live providers (~2 min)
#   .\run.ps1         start the web app at http://127.0.0.1:8000
param([string]$Task = "serve")
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
switch ($Task) {
  "setup" { python -m venv (Join-Path $PSScriptRoot ".venv"); & $py -m pip install --upgrade pip; & $py -m pip install -r (Join-Path $PSScriptRoot "requirements.txt") }
  "test"  { & $py -m pytest -q (Join-Path $PSScriptRoot "tests") }
  "demo"  { $env:URBANOVA_DATA_DIR = (Join-Path $PSScriptRoot "data"); & $py (Join-Path $PSScriptRoot "scripts\smoke.py") $env:URBANOVA_DATA_DIR }
  default { Set-Location $PSScriptRoot; & $py -m urbanova.main }
}
