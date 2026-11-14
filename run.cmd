@echo off
rem URBANOVA on Windows without changing the PowerShell script policy. Same commands as run.ps1:
rem   run.cmd          start the app at http://127.0.0.1:8000 (installs everything on the first run)
rem   run.cmd setup    install       run.cmd test    run the tests       run.cmd demo    seed the sample project
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
