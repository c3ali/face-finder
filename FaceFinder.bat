@echo off
setlocal
REM Face Finder — lanceur portable Windows
REM Double-clic -> installe les deps (1ere fois) -> lance l'app GUI moderne
title Face Finder

set PYTHON=C:\Users\DELL\face-finder-venv\Scripts\python.exe

if not exist "%PYTHON%" (
  echo Python venv introuvable. Lance install.bat puis relance.
  pause
  exit /b
)

echo [1/2] Verification des dependances...
"%PYTHON%" -m pip show insightface >nul 2>nul
if %errorlevel% neq 0 (
  echo [1/2] Installation des dependances (~2 min, 1 fois)...
  "%PYTHON%" -m pip install --quiet --upgrade pip
  "%PYTHON%" -m pip install --quiet insightface onnxruntime pillow numpy pillow-heif pyinstaller
)

echo [2/2] Lancement de Face Finder...
"%PYTHON%" "%~dp0app.py"
echo.
echo App fermee. Relance en double-cliquant sur FaceFinder.bat
pause
