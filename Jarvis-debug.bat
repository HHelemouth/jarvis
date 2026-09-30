@echo off
chcp 65001 >nul
cd /d "%~dp0"
set JARVIS_DEBUG=1
".venv\Scripts\python.exe" jarvis.py
pause
