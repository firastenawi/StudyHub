@echo off
rem Double-click to install / update Study Hub (current user, no admin needed).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
if errorlevel 1 pause
