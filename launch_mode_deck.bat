@echo off
setlocal
cd /d "%~dp0"

if exist "%~dp0Mode Deck.exe" (
  start "" "%~dp0Mode Deck.exe"
  exit /b 0
)

where py >nul 2>nul
if %errorlevel%==0 (
  start "" pyw -3 "%~dp0mode_deck.py"
  exit /b 0
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%~dp0mode_deck.py"
  exit /b 0
)

echo Python 3 was not found.
pause
exit /b 1
