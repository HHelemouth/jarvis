@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Debug display, and nothing opens: clap as many times as you want to calibrate.
set JARVIS_DEBUG=1
set JARVIS_SANS_ACTIONS=1
".venv\Scripts\python.exe" jarvis.py
pause
