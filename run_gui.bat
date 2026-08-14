@echo off
setlocal
cd /d "%~dp0"

set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"

call conda run -n pyqt312 python -B -c "import PyQt6, openpyxl"
if errorlevel 1 (
  echo [ERROR] Conda, PyQt6, or openpyxl is unavailable in the pyqt312 environment.
  pause
  exit /b 1
)

if /I not "%~1"=="--smoke-test" goto run_gui
set "QT_QPA_PLATFORM=offscreen"
set "SMOKE_DB=%TEMP%\feeddb_gui_smoke_%RANDOM%_%RANDOM%.sqlite"
call conda run -n pyqt312 python -B -c "from PyQt6.QtWidgets import QApplication; from feeddb.gui import MainWindow; app=QApplication([]); window=MainWindow(r'%SMOKE_DB%'); print('GUI smoke test passed:', window.windowTitle(), window.centralWidget().count(), 'tabs'); window.close()"
set "SMOKE_EXIT=%ERRORLEVEL%"
if exist "%SMOKE_DB%" del /q "%SMOKE_DB%"
exit /b %SMOKE_EXIT%

:run_gui
echo Starting Feed Ingredient Database GUI...
call conda run -n pyqt312 python -B -m feeddb.gui %*
if errorlevel 1 (
  echo.
  echo [ERROR] The GUI exited with an error. Keep this window open and copy the message above.
  pause
  exit /b 1
)

endlocal
