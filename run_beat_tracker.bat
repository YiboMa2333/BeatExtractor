@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PYTHON=venv\Scripts\python.exe"
) else (
    py -3 --version >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON=py -3"
    ) else (
        python --version >nul 2>&1
        if errorlevel 1 goto missing_python
        set "PYTHON=python"
    )
)
%PYTHON% -u src\cmd_app.py
if errorlevel 1 (
    echo.
    echo Install dependencies using this Python:
    echo %PYTHON% -m pip install -r requirements.txt
    pause
)
exit /b
:missing_python
echo Python was not found. Install Python 3.11 or newer from python.org.
echo Enable "Add python.exe to PATH" during installation, then try again.
pause
exit /b 1
