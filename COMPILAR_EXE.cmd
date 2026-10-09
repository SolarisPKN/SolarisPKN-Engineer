@echo off
setlocal
cd /d "%~dp0"
py -3 -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
  echo PyInstaller no instalado. Instalar manualmente con:
  echo py -3 -m pip install pyinstaller
  pause
  exit /b 1
)
py -3 -m PyInstaller --noconfirm --clean --onefile --noconsole --name "SolarisPKN-Engineer" --distpath "." --workpath ".build" --specpath ".build" --add-data "dashboard_v2.html;." --add-data "visualizer.html;." "tray_launcher.py"
if errorlevel 1 (
  echo Error al compilar. Revisar registro de PyInstaller.
  pause
  exit /b 1
)
echo Compilado: SolarisPKN-Engineer.exe (con bandeja Windows)
pause
endlocal
