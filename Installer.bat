@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo === Installation de Jarvis ===
echo.

set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python n'est pas installe.
  echo Installe-le depuis https://www.python.org/downloads/ en cochant "Add python.exe to PATH",
  echo puis relance ce fichier.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creation de l'environnement Python du projet...
  %PY% -m venv .venv || (echo Echec de la creation de .venv & pause & exit /b 1)
)

echo Installation des composants (1 a 2 minutes)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Echec de l'installation & pause & exit /b 1)

".venv\Scripts\python.exe" configurer.py
pause
