@echo off
rem Double-click to start Quorum and open it in your browser. (Run setup.bat once first.)
cd /d "%~dp0"

if not exist "..\.venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)

"..\.venv\Scripts\python.exe" -m app.cli doctor

start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8000"
"..\.venv\Scripts\python.exe" -m app.cli serve
pause
