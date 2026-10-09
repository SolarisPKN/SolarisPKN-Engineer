@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
echo [1/3] Ejecutando pruebas unitarias...
where py >nul 2>nul
if errorlevel 1 (
 echo [ERROR] No se encontro Python Launcher (py). Se requiere Python 3.10+.
 pause
 exit /b 1
)
py -3 -m unittest discover -s tests -v
if errorlevel 1 (
 echo [NO APTO] Los tests fallaron. No publicar como version estable.
 pause
 exit /b 1
)
echo [2/3] Comprobando carpeta raiz del repositorio...
where git >nul 2>nul
if errorlevel 1 (
 echo [AVISO] Git no instalado. Tests aprobados, pero falta revisar archivos antes de publicar.
 pause
 exit /b 0
)
for /f "delims=" %%r in ('git rev-parse --show-toplevel 2^>nul') do set "GITROOT=%%r"
if not defined GITROOT (
 echo [AVISO] Repo todavia no inicializado. Ejecutar git init dentro de esta carpeta.
 pause
 exit /b 0
)
for %%d in ("!GITROOT!") do set "GITROOT=%%~fd"
for %%d in ("%CD%") do set "CURRENT=%%~fd"
if /I not "!GITROOT!"=="!CURRENT!" (
 echo [BLOQUEO] Esta carpeta pertenece a un repo superior: !GITROOT!
 echo No publicar SolarisPKN-IA completo. Crear un repo INDEPENDIENTE aqui.
 pause
 exit /b 1
)
echo [3/3] Revisar archivos preparados y reglas de exclusion...
git status --short
git diff --cached --check
if errorlevel 1 (
 echo [NO APTO] Hay problemas en los cambios preparados.
 pause
 exit /b 1
)
git check-ignore -v ".private/credentials.dpapi.json" "proyectos/ejemplo/indice.sqlite"
echo [APTO PARA REVISION] Tests aprobados y raiz Git independiente. Revisar git diff --cached --name-only ANTES del push.
pause
endlocal
