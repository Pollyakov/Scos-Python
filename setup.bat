@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo  SCOS - installation
echo ============================================
echo.

rem ---- 1. Python ---------------------------------------------------------
echo [1/5] Looking for Python 3.11 or newer...
set "PY=python"
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 goto have_python
set "PY=py -3"
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 goto have_python
echo.
echo ERROR: Python 3.11 or newer was not found.
echo   Install Python 3.13 from https://www.python.org/downloads/windows/
echo   and tick "Add python.exe to PATH" on the installer's first page.
echo   Then run setup.bat again.
goto failed

:have_python
%PY% -c "import sys; print('      found Python', sys.version.split()[0])"

rem ---- 2. The project's own environment ----------------------------------
echo [2/5] Preparing the project's own Python environment (folder "venv")...
if exist "venv\Scripts\python.exe" goto have_venv
%PY% -m venv venv
if errorlevel 1 goto venv_failed
:have_venv
if not exist "venv\Scripts\python.exe" goto venv_failed

rem ---- 3. Libraries ------------------------------------------------------
echo [3/5] Installing the libraries (needs the internet, a few minutes)...
"venv\Scripts\python.exe" -m pip install --upgrade pip --quiet --disable-pip-version-check
"venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 goto pip_failed

rem ---- 4. Desktop icon ---------------------------------------------------
if /i "%~1"=="/noicon" goto skip_icon
echo [4/5] Putting the SCOS icon on the desktop...
"venv\Scripts\python.exe" tools\make_desktop_shortcut.py
if errorlevel 1 echo       WARNING: no desktop icon was made - the app still works: see README.md
goto check
:skip_icon
echo [4/5] Desktop icon skipped (/noicon).

rem ---- 5. Check ----------------------------------------------------------
:check
echo [5/5] Checking the installation...
echo.
"venv\Scripts\python.exe" tools\check_install.py
if errorlevel 1 goto check_failed

echo.
echo ============================================
echo  Installation complete.
echo  Start the app with the SCOS icon on the desktop.
echo ============================================
if /i not "%~2"=="/nopause" pause
exit /b 0

:venv_failed
echo.
echo ERROR: could not create the folder "venv".
goto failed

:pip_failed
echo.
echo ERROR: a library could not be installed. Check the internet connection
echo        and run setup.bat again - it continues where it stopped.
goto failed

:check_failed
echo.
echo ERROR: the installation check found a problem - see the lines marked
echo        "PROBLEM" above.
goto failed

:failed
echo.
echo ============================================
echo  Installation NOT complete.
echo ============================================
if /i not "%~2"=="/nopause" pause
exit /b 1
