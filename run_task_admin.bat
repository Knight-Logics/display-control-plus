@echo off
set SCRIPT=ensure_overlay_bg_task.py
set PYTHONW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe
set VBS=run_as_admin.vbs

:: Use VBS to run the Python script as admin
cscript //nologo "%VBS%" "%SCRIPT%"
