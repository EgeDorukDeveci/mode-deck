@echo off
setlocal
cd /d "%~dp0"
py -3 "%~dp0mode_deck.py" --preview gaming
echo.
echo Preview only. No Windows settings or applications were changed.
pause
