@echo off
setlocal

set "GIT=C:\Users\jiang\cmd\git.exe"
set "REMOTE=https://github.com/jiangyu877/analyse-cc.git"

cd /d "%~dp0"

if not exist "%GIT%" (
    echo Git was not found at:
    echo %GIT%
    pause
    exit /b 1
)

"%GIT%" --version

if not exist ".git\HEAD" (
    echo Initializing repository...
    "%GIT%" init -b main
    if errorlevel 1 goto :failed
)

"%GIT%" config user.name "jiangyu877"

for /f "delims=" %%E in ('"%GIT%" config user.email 2^>nul') do set "GIT_EMAIL=%%E"
if not defined GIT_EMAIL (
    set /p "GIT_EMAIL=Enter your GitHub email: "
)
if not defined GIT_EMAIL (
    echo A GitHub email is required.
    pause
    exit /b 1
)

"%GIT%" config user.email "%GIT_EMAIL%"
"%GIT%" add .
if errorlevel 1 goto :failed

"%GIT%" diff --cached --quiet
if errorlevel 1 (
    "%GIT%" commit -m "Initial production-ready release"
    if errorlevel 1 goto :failed
) else (
    echo No new changes to commit.
)

"%GIT%" remote get-url origin >nul 2>&1
if errorlevel 1 (
    "%GIT%" remote add origin "%REMOTE%"
) else (
    "%GIT%" remote set-url origin "%REMOTE%"
)
if errorlevel 1 goto :failed

echo Pushing main to GitHub...
"%GIT%" push -u origin main
if errorlevel 1 goto :failed

echo.
echo Push completed successfully.
pause
exit /b 0

:failed
echo.
echo The operation failed. Keep this window open and send its error message to Codex.
pause
exit /b 1
