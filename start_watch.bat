@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0watch.ps1" -Rows 2 -Columns 3
if errorlevel 1 pause
