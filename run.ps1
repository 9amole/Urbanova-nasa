# URBANOVA — setup and run helper (Windows PowerShell). If Windows says "running scripts is disabled", use run.cmd instead
# (same commands: run.cmd, run.cmd setup, run.cmd test).
#   .\run.ps1         start the app at http://127.0.0.1:8000 (installs everything on the first run)
#   .\run.ps1 setup   create .venv with Python 3.11 or 3.12 and install the pinned dependencies
#   .\run.ps1 test    run the automated tests (offline, mocked providers; about 6 minutes)
#   .\run.ps1 demo    seed ./data with the sample project using live providers (~2 min)
param([string]$Task = "serve")
$ErrorActionPreference = "Stop"
$venv = Join-Path $PSScriptRoot ".venv"
$py = Join-Path $venv "Scripts\python.exe"

function Find-Python {
  # The tested versions: pyproject.toml requires >=3.11,<3.13. Prefer the Windows "py" launcher, then python on PATH.
  $check = "import sys; sys.exit(0 if sys.version_info[:2] in ((3, 11), (3, 12)) else 1)"
  if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in "-3.11", "-3.12") {
      & py $v -c $check 2>$null
      if ($LASTEXITCODE -eq 0) { return @("py", $v) }
    }
  }
  foreach ($cmd in "python", "python3") {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
      & $cmd -c $check 2>$null
      if ($LASTEXITCODE -eq 0) { return @($cmd) }
    }
  }
  throw "URBANOVA needs Python 3.11 or 3.12. Install Python 3.11 from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this again."
}

function Invoke-Checked([string]$exe, [string[]]$arguments) {
  & $exe @arguments
  if ($LASTEXITCODE -ne 0) { throw "Failed: $exe $($arguments -join ' ')" }
}

function Install-Urbanova {
  $p = Find-Python
  $launcher = $p[0]; $launcherArgs = @($p | Select-Object -Skip 1)
  Write-Host "Creating .venv with $($p -join ' ') ..."
  Invoke-Checked $launcher ($launcherArgs + @("-m", "venv", $venv))
  Invoke-Checked $py @("-m", "pip", "install", "--upgrade", "pip")
  Invoke-Checked $py @("-m", "pip", "install", "-r", (Join-Path $PSScriptRoot "requirements.txt"))
  Write-Host "Setup complete. Start the app with: .\run.ps1   (or run.cmd)"
}

function Assert-Installed {
  if (-not (Test-Path $py)) { Write-Host "First run: installing URBANOVA (about 3-5 minutes) ..."; Install-Urbanova }
}

switch ($Task) {
  "setup" { Install-Urbanova }
  "test"  { Assert-Installed; Set-Location $PSScriptRoot; & $py -m pytest -q tests }
  "demo"  { Assert-Installed; $env:URBANOVA_DATA_DIR = (Join-Path $PSScriptRoot "data"); & $py (Join-Path $PSScriptRoot "scripts\smoke.py") $env:URBANOVA_DATA_DIR }
  default {
    Assert-Installed
    Set-Location $PSScriptRoot
    Write-Host "URBANOVA is starting: open http://127.0.0.1:$(if ($env:URBANOVA_PORT) { $env:URBANOVA_PORT } else { '8000' })  (Ctrl+C stops it)"
    & $py -m urbanova.main
  }
}
