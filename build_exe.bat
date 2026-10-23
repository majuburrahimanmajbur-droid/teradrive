@echo off
cd /d "%~dp0"
echo Building TeraDrive.exe (needs Python 3)...
py -3 -m pip install pyinstaller
py -3 -m PyInstaller --onefile --noconsole --name TeraDrive teradrive.py
echo.
echo Done. Your app is in the "dist" folder: dist\TeraDrive.exe
pause
