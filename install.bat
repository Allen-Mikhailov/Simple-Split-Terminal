@echo off
setlocal enabledelayedexpansion

REM --- Simple Split Terminal (sst) — Windows installer ---

set "SCRIPT_DIR=%~dp0"

echo === sst installer ===
echo.

REM 1. Check for Python 3
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: python not found. Install Python 3.8+ and try again.
    echo        Make sure "Add Python to PATH" is checked during install.
    pause
    exit /b 1
)
python --version

REM 2. Install dependencies
echo.
echo Installing dependencies...
python -m pip install pyserial windows-curses

REM 3. Create launcher batch file
set "LAUNCHER=%SCRIPT_DIR%sst.bat"
(
    echo @echo off
    echo python "%SCRIPT_DIR%index.py" %%*
) > "%LAUNCHER%"
echo Launcher created: %LAUNCHER%

REM 4. Offer to add to user PATH
echo.
set /p ADD_PATH="Add launcher to your user PATH? [y/N] "
if /i "!ADD_PATH!"=="y" (
    set "USER_PATH="
    for /f "tokens=2*" %%a in ('reg query "HKCU\Environment" /v PATH 2^>nul') do set "USER_PATH=%%b"
    echo !USER_PATH! | findstr /i /c:"%SCRIPT_DIR%" >nul
    if !ERRORLEVEL! NEQ 0 (
        if "!USER_PATH!"=="" (
            set "NEW_PATH=%SCRIPT_DIR%"
        ) else (
            set "NEW_PATH=!USER_PATH!;%SCRIPT_DIR%"
        )
        reg add "HKCU\Environment" /v PATH /t REG_EXPAND_SZ /d "!NEW_PATH!" /f >nul
        echo Added to user PATH. Restart your terminal for it to take effect.
    ) else (
        echo Already in PATH.
    )
)

echo.
echo Done. Run: sst ^<port^> [-b BAUD] [-s vertical^|horizontal]
pause
