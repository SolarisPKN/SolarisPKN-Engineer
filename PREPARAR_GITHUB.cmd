@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Se requiere Python 3.10 o superior.
  pause
  exit /b 1
)
py -3 "%~dp0PREPARAR_GITHUB.py"
if errorlevel 1 (
  echo [NO APTO] La copia GitHub NO esta lista. Revisar errores y tests.
  pause
  exit /b 1
)
echo [LISTO] Copiar solo la subcarpeta dist\github-export\SolarisPKN-Engineer.
pause
endlocal
