@echo off
setlocal
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 "%~dp0local.py" web %*
) else (
  python "%~dp0local.py" web %*
)
exit /b %errorlevel%
