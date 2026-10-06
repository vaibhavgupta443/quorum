@echo off
rem ONE-TIME SETUP. Double-click this once. Free, light, nothing heavy runs on your PC.
rem (No parenthesised blocks around the key prompt: Windows would read %GROQKEY% before you paste it.)
cd /d "%~dp0"
echo.
echo === Quorum one-time setup ===
echo.

if exist "..\.venv\Scripts\python.exe" goto have_venv
echo [1/3] Creating a Python environment...
python -m venv ..\.venv
goto install
:have_venv
echo [1/3] Python environment found.

:install
echo       Installing Python packages (web server, MCP)...
"..\.venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 goto pip_failed

rem A real key line looks like GROQ_API_KEY=gsk_...  An empty or placeholder line does not count.
if not exist ".env" goto ask_key
findstr /b /c:"GROQ_API_KEY=gsk_" ".env" >nul
if not errorlevel 1 goto have_key

:ask_key
echo.
echo [2/3] Free AI key. Get one in 1 minute, no credit card:
echo       console.groq.com  ^>  API Keys  ^>  Create API Key  ^>  copy it
start https://console.groq.com/keys
set "GROQKEY="
set /p GROQKEY=      Paste your key here and press Enter:
if "%GROQKEY%"=="" goto no_key
> ".env" echo GROQ_API_KEY=%GROQKEY%
echo       Saved to .env
goto check

:have_key
echo [2/3] Your key is already saved in .env. Keeping it.

:check
echo [3/3] Checking everything...
"..\.venv\Scripts\python.exe" -m app.cli doctor
echo.
echo Setup finished. Double-click start.bat to run Quorum.
pause
exit /b 0

:no_key
echo.
echo No key was entered. Run setup.bat again and paste the key (it starts with gsk_).
pause
exit /b 1

:pip_failed
echo Package install failed. Check your internet connection and retry.
pause
exit /b 1
