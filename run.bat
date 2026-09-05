@echo off
setlocal
cd /d "%~dp0"

set "VENV_DIR=%~dp0.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "INSTALL_MARKER=%VENV_DIR%\.dependencies-installed"
set "SYSTEM_PYTHON="

if exist "%VENV_PYTHON%" goto install_dependencies

echo [setup] Creating the project-local Python environment...
where py >nul 2>&1
if not errorlevel 1 goto create_with_py

for /f "delims=" %%P in ('where python 2^>nul') do if not defined SYSTEM_PYTHON set "SYSTEM_PYTHON=%%P"
if defined SYSTEM_PYTHON goto create_with_path

for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do if exist "%%~fD\python.exe" set "SYSTEM_PYTHON=%%~fD\python.exe"
if defined SYSTEM_PYTHON goto create_with_path
goto python_missing

:create_with_py
py -3 -m venv "%VENV_DIR%"
if errorlevel 1 goto venv_failed
goto install_dependencies

:create_with_path
"%SYSTEM_PYTHON%" -m venv "%VENV_DIR%"
if errorlevel 1 goto venv_failed

:install_dependencies
if exist "%INSTALL_MARKER%" goto start_app

echo [setup] Installing dependencies. Internet access is required once...
"%VENV_PYTHON%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto install_failed
type nul >"%INSTALL_MARKER%"

:start_app
echo [start] Starting Voice Hotkey GUI...
"%VENV_DIR%\Scripts\pythonw.exe" "%~dp0gui.py"
if errorlevel 1 goto app_failed
exit /b 0

:python_missing
echo [error] Python 3 was not found in PATH or the standard per-user install folder.
goto failed

:venv_failed
echo [error] The local Python environment could not be created.
goto failed

:install_failed
echo [error] Dependency installation failed. Check the pip output above.
goto failed

:app_failed
echo [error] The application stopped with an error. Read the output above.

:failed
pause
exit /b 1
