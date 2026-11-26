@echo off
setlocal
cd /d "%~dp0"
rem Keep a double-clicked console open after completion, without another prompt.
rem --no-pause runs once and exits for automated launcher tests.
if "%~1"=="" (
    "%ComSpec%" /d /k ""%~f0" --console"
    exit /b
)
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
if "%~1"=="--quality" (
    %PYTHON% -u src\cmd_app.py --mode quality
) else (
    %PYTHON% -u src\cmd_app.py
)
set "TRACKER_EXIT=%ERRORLEVEL%"
if errorlevel 1 (
    echo.
    echo Music analyser stopped. Read the reason above.
    echo If dependencies are missing, run:
    echo %PYTHON% -m pip install -r requirements.txt
)
exit /b %TRACKER_EXIT%
:missing_python
echo Python was not found. Install Python 3.11 or newer from python.org.
echo Enable "Add python.exe to PATH" during installation, then try again.
exit /b 1
