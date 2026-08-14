@echo off
setlocal
chcp 65001 >nul
set "AUDITOR_DIR=%~dp0"

if "%~1"=="" (
  echo Usage: run_audit.bat SOURCE_DIR [OUTPUT_DIR]
  echo Example: run_audit.bat "D:\source_workbooks" "D:\audit_report"
  exit /b 2
)
set "SOURCE_DIR=%~1"
if "%~2"=="" (
  set "OUTPUT_DIR=%CD%\audit_output"
) else (
  set "OUTPUT_DIR=%~2"
)

echo 源数据目录：%SOURCE_DIR%
echo 报告输出目录：%OUTPUT_DIR%
echo.
set "PYQT_PYTHON=%USERPROFILE%\.conda\envs\pyqt312\python.exe"
if exist "%PYQT_PYTHON%" (
  "%PYQT_PYTHON%" -B "%AUDITOR_DIR%auditor.py" "%SOURCE_DIR%" "%OUTPUT_DIR%"
) else (
  set "PYTHONIOENCODING=utf-8"
  call conda run -n pyqt312 python -B "%AUDITOR_DIR%auditor.py" "%SOURCE_DIR%" "%OUTPUT_DIR%"
)
set "AUDIT_EXIT=%ERRORLEVEL%"
echo.
if "%AUDIT_EXIT%"=="0" echo 审查通过：可以进入建库流程。
if "%AUDIT_EXIT%"=="2" echo 审查未通过：请查看 AUDIT_REPORT.md 和 all_issues.csv。
if not "%AUDIT_EXIT%"=="0" if not "%AUDIT_EXIT%"=="2" echo 审查器运行失败，请查看上方错误。
echo.
pause
exit /b %AUDIT_EXIT%
