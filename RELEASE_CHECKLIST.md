# Publicación segura de SolarisPKN-Engineer / Safe GitHub release

**Estado / Status:** prototipo experimental. No se ha verificado todavía el tray interactivo ni la suite completa en el Windows anfitrión. No etiquetar como 1.0/estable.

## Español

### Qué sí se puede versionar
- Fuentes `*.py`, scripts `*.cmd`, HTML, documentación y tests sintéticos.
- Directorio **SolarisPKN-Engineer solamente**, nunca la carpeta superior SolarisPKN-IA.
- Métodos de cifrado del propietario únicamente como código, nunca contraseñas o muestras privadas.

### Exclusiones obligatorias
- `proyectos/` (bases SQLite, logs, índices y mapas, que pueden contener rutas/código privado).
- `.private/` (contraseñas DPAPI cifradas, tokens y logs del servidor).
- `datos/`, `salida/`, `__pycache__/`, `.build/`, `build/`, `dist/` y binarios del proyecto.
- `.env*`, claves privadas y certificados; inspeccionar cualquier nueva carpeta manualmente.

### Copia limpia para el primer commit (recomendado)

Ejecutar `PREPARAR_GITHUB.cmd`. Solo si **toda la suite de tests pasa**, se genera `dist/github-export/SolarisPKN-Engineer`. Copiar al repositorio GitHub **únicamente esa carpeta**, no la instalación completa. El manifiesto SHA256 `ARCHIVOS_PUBLICADOS.json` permite revisar los archivos. La salida `.private/`, `proyectos/`, bases de datos y credenciales no se incluyen.

### Verificaciones en PowerShell
Ubicate **dentro** de `SolarisPKN-Engineer`:

```powershell
cd 'D:\SolarisPKN-IA\proyectos\SolarisPKN-Engineer'
py -3 -m unittest discover -s tests -v
git init
git add .
git status --short
git diff --cached --check
git diff --cached --name-only
git check-ignore -v proyectos/ejemplo/indice.sqlite .private/credentials.dpapi.json
```

**Antes del push:** examiná la lista de archivos agregados y verificá que ninguna entrada pertenezca a `proyectos/`, `.private/`, un archivo de configuración real con credenciales, o a directorios del sistema privado SolarisPKN-IA.

Pruebas manuales pendientes: abrir `INICIAR_ENGINEER.cmd`, verificar que aparezca el icono del área de notificación, abrir el panel, iniciar una prueba corta, reiniciar mientras analiza, reanudar desde SQLite y cerrar todo sin dejar procesos hijos de Engineer. Probar `COMPILAR_EXE.cmd` en Windows.

### Licencia y publicación
El repositorio remoto **SolarisPKN/SolarisPKN-Engineer** ya es público y tiene la licencia **GNU Affero General Public License v3 (AGPL-3.0)**. Conservá su archivo `LICENSE` y verificá que todo el código incorporado pueda distribuirse bajo esa licencia. Esta publicación se identifica como **v0.1.0-alpha**, no como versión estable.

El repositorio remoto existe. El push debe hacerse solo después de verificar código y artefactos.

## English

Publish only the independent SolarisPKN-Engineer directory, not the private parent SolarisPKN-IA. The gitignore excludes internal project indexes, generated maps, DPAPI credentials, build directories and Python caches. Review staged files manually.

Run `python -m unittest discover -s tests -v` and perform interactive Windows tray smoke tests (Open, Status, Restart while scanning, Resume, Exit), and build/test the optional packaged EXE. These checks have **not yet been fully verified** on the host.

The existing public repository is licensed GNU AGPL-3.0; preserve LICENSE and verify code provenance. This is an alpha release, not a stable one.
