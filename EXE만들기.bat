@echo off
title Laika - EXE Build
cd /d "%~dp0"

echo ================================================
echo   Laika EXE Build
echo ================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [!] Python 이 설치되어 있지 않습니다.
    echo     python.org 에서 3.10 이상을 설치하고
    echo     설치 화면의 "Add Python to PATH" 를 반드시 체크하세요.
    echo.
    pause
    exit /b 1
)

echo [1/2] 필요한 패키지를 설치합니다. 몇 분 걸립니다...
echo.
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [!] 패키지 설치에 실패했습니다. 위 내용을 확인하세요.
    pause
    exit /b 1
)

echo.
echo [2/2] EXE 를 만듭니다. 5~15분 걸립니다...
echo.
python build_exe.py

echo.
echo ================================================
echo   완료. dist 폴더 안을 확인하세요.
echo ================================================
pause
