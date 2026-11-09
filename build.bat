@echo off
setlocal
title Build PDF Studio
cd /d "%~dp0"

echo.
echo ===============================================
echo   PDF Studio - Windows build
echo ===============================================
echo.

rem ---- find Python 3.10+ -------------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python was not found.
    echo Install Python 3.11 or newer from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup. Then run this file again.
    pause
    exit /b 1
)

rem ---- virtual environment ---------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating virtual environment...
    %PY% -m venv .venv || goto :fail
)
set "VPY=.venv\Scripts\python.exe"

echo [2/4] Installing packages (first time takes a few minutes)...
"%VPY%" -m pip install --upgrade pip >nul
"%VPY%" -m pip install -r requirements.txt pyinstaller || goto :fail

rem ---- build the .exe ----------------------------------------------------------
echo [3/4] Building PDF Studio.exe ...
if exist build rmdir /s /q build
if exist "dist\PDF Studio" rmdir /s /q "dist\PDF Studio"
"%VPY%" -m PyInstaller --noconfirm --clean --windowed ^
    --name "PDF Studio" ^
    --icon pdfstudio.ico ^
    --add-data "pdfstudio.ico;." ^
    --collect-submodules pdf2docx ^
    --exclude-module tkinter ^
    --exclude-module matplotlib ^
    --exclude-module pandas ^
    --exclude-module scipy ^
    --exclude-module IPython ^
    --exclude-module pkg_resources ^
    --exclude-module setuptools ^
    --exclude-module PySide6.QtWebEngineCore ^
    --exclude-module PySide6.Qt3DCore ^
    --exclude-module PySide6.QtQuick ^
    --exclude-module PySide6.QtQml ^
    --exclude-module PySide6.QtMultimedia ^
    main.py || goto :fail

rem ---- optional installer (needs Inno Setup 6) ---------------------------------
echo [4/4] Looking for Inno Setup to make an installer...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if defined ISCC (
    "%ISCC%" installer.iss || goto :fail
    echo.
    echo Installer created:  dist\PDFStudio-Setup.exe
) else (
    echo Inno Setup not found - skipping installer. ^(Optional: https://jrsoftware.org/isdl.php^)
)

echo.
echo ===============================================
echo   DONE
echo   App:  dist\PDF Studio\PDF Studio.exe
echo ===============================================
echo.
explorer "dist\PDF Studio"
pause
exit /b 0

:fail
echo.
echo *** Build failed - see the messages above. ***
pause
exit /b 1
