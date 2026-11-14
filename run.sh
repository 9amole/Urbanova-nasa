#!/usr/bin/env bash
# URBANOVA — setup and run helper (macOS / Linux).
#   bash run.sh         start the app at http://127.0.0.1:8000 (installs everything on the first run)
#   bash run.sh setup   create .venv with Python 3.11 or 3.12 and install the pinned dependencies
#   bash run.sh test    run the automated tests (offline, mocked providers; about 6 minutes)
#   bash run.sh demo    seed ./data with the sample project using live providers (~2 min)
set -euo pipefail
cd "$(dirname "$0")"
task="${1:-serve}"

venv_python() { if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo .venv/Scripts/python.exe; fi; }

find_python() {  # the tested versions: pyproject.toml requires >=3.11,<3.13
  for c in python3.11 python3.12 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2] in ((3, 11), (3, 12)) else 1)' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  echo "URBANOVA needs Python 3.11 or 3.12: https://www.python.org/downloads/ (macOS: brew install python@3.11)" >&2
  return 1
}

install() {
  local p; p="$(find_python)"
  echo "Creating .venv with $p ..."
  "$p" -m venv .venv
  "$(venv_python)" -m pip install --upgrade pip
  "$(venv_python)" -m pip install -r requirements.txt
  echo "Setup complete. Start the app with: bash run.sh"
}

installed() { [ -x .venv/bin/python ] || [ -x .venv/Scripts/python.exe ] || { echo "First run: installing URBANOVA (about 3-5 minutes) ..."; install; }; }

case "$task" in
  setup) install ;;
  test) installed; "$(venv_python)" -m pytest -q tests ;;
  demo) installed; URBANOVA_DATA_DIR="$PWD/data" "$(venv_python)" scripts/smoke.py "$PWD/data" ;;
  *) installed; echo "URBANOVA is starting: open http://127.0.0.1:${URBANOVA_PORT:-8000}  (Ctrl+C stops it)"; exec "$(venv_python)" -m urbanova.main ;;
esac
