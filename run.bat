@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py -3 teradrive.py & goto :eof)
where python >nul 2>nul && (python teradrive.py & goto :eof)
echo Python 3 is not installed. Get it from python.org (tick "Add to PATH"), or run build_exe.bat on a PC that has it.
pause
