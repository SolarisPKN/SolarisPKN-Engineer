@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem Always use a clean Python 3.12 build environment; never install into System32.
rem PYTHONHOME/PYTHONPATH from another application can corrupt Python's library path.
set "PYTHONHOME="
set "PYTHONPATH="
set "BUILD_PY=%CD%\.venv-engineer-build\Scripts\python.exe"

echo [1/4] Verificando Python 3.12...
py -3.12 -c "import sys, encodings, venv; assert sys.version_info[:2] == (3,12); print(sys.executable); print('Base:', sys.base_prefix)"
if errorlevel 1 goto :python_error

if not exist "%BUILD_PY%" (
  echo [2/4] Creando entorno de compilacion aislado...
  py -3.12 -m venv "%CD%\.venv-engineer-build"
  if errorlevel 1 goto :build_error
) else (
  echo [2/4] Reutilizando entorno de compilacion aislado...
)

"%BUILD_PY%" -c "import sys, encodings; assert sys.prefix != sys.base_prefix; print('Entorno:', sys.prefix)"
if errorlevel 1 (
  echo ERROR: El entorno de compilacion esta danado.
  echo Borra solo la carpeta .venv-engineer-build y vuelve a ejecutar este script.
  goto :build_error
)

"%BUILD_PY%" -m pip --version
if errorlevel 1 (
  echo Instalando pip dentro del entorno aislado...
  "%BUILD_PY%" -m ensurepip --upgrade
  if errorlevel 1 goto :build_error
)

"%BUILD_PY%" -c "import PyInstaller, blake3" >nul 2>&1
if errorlevel 1 (
  echo [3/4] Instalando PyInstaller y BLAKE3 SOLO en el entorno del proyecto...
  "%BUILD_PY%" -m pip install --disable-pip-version-check "pyinstaller>=6.0" "blake3>=1.0"
  if errorlevel 1 goto :build_error
) else (
  echo [3/4] PyInstaller y BLAKE3 ya disponibles.
)

echo Version de PyInstaller:
"%BUILD_PY%" -m PyInstaller --version
if errorlevel 1 goto :build_error

echo [4/4] Compilando SolarisPKN-Engineer...
"%BUILD_PY%" -m PyInstaller --noconfirm --clean --onefile --noconsole --name "SolarisPKN-Engineer" --distpath "." --workpath ".build" --specpath ".build" --hidden-import "blake3" --add-data "%CD%\dashboard_v2.html;." --add-data "%CD%\monitor_dashboard.html;." --add-data "%CD%\visualizer.html;." "tray_launcher.py"
if errorlevel 1 goto :build_error

if not exist "SolarisPKN-Engineer.exe" (
  echo ERROR: La compilacion no genero SolarisPKN-Engineer.exe.
  goto :build_error
)
echo.
echo Compilacion finalizada: %CD%\SolarisPKN-Engineer.exe
goto :done

:python_error
echo.
echo ERROR: Python 3.12 no se pudo iniciar correctamente.
echo Comproba con: py -0p
echo Y ejecuta: py -3.12 -c "import sys; print(sys.executable, sys.prefix)"
goto :fail

:build_error
echo.
echo ERROR: La compilacion fallo. Revisa el error REAL mostrado arriba.
echo No hace falta modificar el Python global ni instalar paquetes en System32.
:fail
if not defined CI pause
exit /b 1

:done
if not defined CI pause
exit /b 0
