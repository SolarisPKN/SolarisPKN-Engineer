@echo off
setlocal
cd /d "%~dp0"
rem Solarispkn-Engineer now starts through its native Windows system tray.
where pyw >nul 2>nul
if not errorlevel 1 (
  start "" pyw -3 "%~dp0tray_launcher.py"
  exit /b 0
)
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw "%~dp0tray_launcher.py"
  exit /b 0
)
where py >nul 2>nul
if not errorlevel 1 (
  echo [INFO] pyw no disponible: se iniciara Engineer en una consola minimizada.
  start "" /min py -3 "%~dp0tray_launcher.py"
  exit /b 0
)
echo [ERROR] Se requiere Python 3.10+ o SolarisPKN-Engineer.exe.
pause
exit /b 1
