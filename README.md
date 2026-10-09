# SolarisPKN-Engineer

**Analizador local experimental de dependencias, archivos, binarios e impacto inverso, con grafo interactivo y documentación opcional mediante IA.**

[English](README.en.md) | Español

> **Estado:** `v0.1.0-alpha` (experimental). El escaneo estático puede omitir relaciones dinámicas; la ausencia de enlaces no demuestra que un archivo sea seguro para borrar. El EXE y las pruebas interactivas de Windows requieren validación antes de considerarlo estable.

## Propósito

SolarisPKN-Engineer crea un mapa persistente de cómo se relacionan archivos fuente, ejecutables, bibliotecas y recursos. Recorre dependencias transitivas, evita analizar nodos duplicados, sobrevive a interrupciones y genera tanto un grafo HTML propio como notas para Obsidian. Un módulo de IA opcional lee fragmentos de código, comenta su función y mantiene un índice enlazado.

No instala dependencias, ejecuta EXE desconocidos ni modifica intencionalmente los archivos originales.

## Bandeja de Windows (tray) — abrir, reiniciar y cerrar

Desde esta versión, **INICIAR_ENGINEER.cmd** inicia el nuevo `tray_launcher.py`. Usa el sistema de bandeja de Windows a través de `ctypes` (sin bibliotecas Python adicionales), con una única instancia activa por usuario.

- **Doble clic en el ícono:** abrir el panel web en `http://127.0.0.1:8765/`.
- **Clic derecho → Abrir panel:** abrir el navegador.
- **Estado del motor:** saber si el servidor de Engineer está activo y si está analizando.
- **Reiniciar Engineer:** pedir al servidor un cierre ordenado, detener el trabajo en el siguiente punto seguro y volver a iniciar el proceso.
- **Cerrar Engineer por completo:** cerrar el servidor y quitar el icono de la bandeja; no cerrar SolarisPKN-IA, Bridge ni procesos ajenos.

Si el motor está analizando, Engineer solicita confirmación antes de interrumpirlo. La cola queda en SQLite; el archivo que se estaba procesando se reanaliza si no alcanzó a confirmar su resultado. Si el servidor no termina durante 15 segundos, aparece una **segunda confirmación** para finalizar por la fuerza el proceso de Engineer y sus subprocesos. No se fuerzan cierres sin permiso explícito.

Al cerrar el servidor se detienen nuevas tareas y se espera a los trabajos activos. Para grandes archivos o llamadas IA bloqueadas, la salida puede no ser inmediata. Los resultados ya confirmados se conservan.

**Primer cambio desde una versión anterior:** cerrá la consola antigua de `server.py` antes de iniciar el nuevo tray; el supervisor no se apropia de servidores ajenos que estén usando el puerto 8765. Si aparece un conflicto, cerrá la instancia antigua y seleccioná **Reiniciar Engineer** desde la bandeja.

**Empaquetado:** `COMPILAR_EXE.cmd` usa ahora `tray_launcher.py` como punto de entrada del ejecutable. El script existe, pero el EXE aún requiere compilación y validación con PyInstaller en el Windows anfitrión. El registro de errores del proceso gestionado se encuentra en `.private/server-process.log`.

## Requisitos y ubicación

- Python 3.10 o superior. El motor y el servidor web utilizan la biblioteca estándar.
- Git en PATH, opcional para el historial.
- Navegador web moderno.
- Para IA: Ollama local o endpoint HTTP compatible con OpenAI.
- Para un EXE portable: PyInstaller, opcional.

Instalación: extraé o cloná el repositorio en una carpeta propia con permisos de escritura, por ejemplo `SolarisPKN-Engineer/`. El programa no necesita la carpeta privada de desarrollo de Solaris.

### Abrir la interfaz en Windows

Doble clic en **INICIAR_ENGINEER.cmd**, o desde PowerShell:

    cd .\SolarisPKN-Engineer
    py -3 tray_launcher.py

Para ejecutar solamente el servidor, sin el icono de bandeja:

    py -3 server.py

Abrir: **http://127.0.0.1:8765/**

Linux/macOS:

    python3 server.py

Desactivar apertura automática del navegador:

    python3 server.py --no-browser

El panel escucha solo en loopback (127.0.0.1). No es un servicio público de Internet.

## Ejemplo público reproducible: SolarisPKN-Labs

El repositorio incorpora un generador **`labs_public_example.py`** y el workflow **`.github/workflows/labs-scan.yml`**. El workflow descarga **solo el checkout público** de [SolarisPKN-Labs](https://github.com/SolarisPKN/SolarisPKN-Labs), ejecuta una indexación estática con Engineer y publica exclusivamente cuatro artefactos en `ejemplos/SolarisPKN-Labs/`: `mapa.html`, `mapa.json`, `diagnostico-relaciones.json` y `README.md`. La salida no contiene el código de Labs, su base SQLite, notas de IA, contraseñas ni rutas absolutas locales. Todos los nodos y aristas publicados deben coincidir con archivos rastreados por Git en el repositorio fuente.

**Importante:** el ejemplo demuestra el análisis actual, no la necesidad o seguridad de eliminar archivos. La generación automatizada requiere GitHub Actions con permiso de escritura; un error de validación detiene el commit.

## Auditoría de relaciones semánticas y carpetas

El auditor de **SolarisPKN-Labs** mostró vínculos omitidos que ahora cubre el motor reutilizable `project_semantics.py` para cualquier proyecto compatible:

- Fuentes (`@font-face url(...)`, CSS y `<link href=...>`), imágenes HTML/MDX y medios enlazados con rutas `/public`.
- Metadatos JSON anidados (`heroImage`, `images`, `imagen`, `imagenes`, `portada`, `foto` y otras claves en inglés/español), incluidos certificados del portfolio.
- Aliases declarados en `astro.config.mjs`, `tsconfig.json` y `jsconfig.json` (por ejemplo `@styles`, `@components`, `@locales`).
- Imports dinámicos literales con variables de segmento (`import(`...${lang}...`)`), patrones `import.meta.glob` y rutas construidas con `path.join` que se pueden resolver por inspección estática sin ejecutar código. Las expansiones se marcan como **inferidas**.
- Relación entre metadatos de una publicación (`post.json`), versiones localizadas `index-es.mdx`/`index-en.mdx` y traducciones del post si existen en las carpetas correspondientes.
- **Pertenencia a carpetas** en mapas HTML, JSON y Obsidian. Las aristas `contains` usan la ruta real del sistema de archivos y son **estructurales**, no dependencias de ejecución. El motor de impacto en SQLite no incluye estas aristas para evitar falsos impactos entre archivos vecinos.

La exportación ahora también genera `diagnostico-relaciones.json`, que distingue archivos referenciados desde otros archivos, posibles puntos de entrada, recursos estáticos sin referencias detectadas y nodos externos no resueltos. **Un archivo sin referencias entrantes no está necesariamente sin uso**: puede ser una ruta, artefacto publicado, archivo de configuración, prueba o recurso invocado desde otro sistema.

**IMPORTANTE tras esta actualización:** reiniciá Engineer, activá **Reanalizar** y analizá **Todo el directorio** de SolarisPKN-Labs. Los vínculos nuevos no aparecen retroactivamente en los nodos ya marcados como completos. Exportá nuevamente el mapa para obtener también el diagnóstico actualizado. No es necesario eliminar `indice.sqlite`.

Los tests `tests/test_labs_semantic_edges.py` recrean de forma sintética los casos encontrados. No ejecutan código de los proyectos auditados. La cobertura es progresiva: una resolución estática de plantillas no demuestra ejecución efectiva de cada rama ni equivale a un AST semántico completo de cada lenguaje.

## Nuevo espacio de trabajo y mapa por carpetas

La pantalla ahora prioriza **el mapa visual**, con una distribución compacta para monitores de 1360–1366 px y diseños responsivos:

- **Menú izquierdo plegable:** Explorador de proyectos, Registro de actividad, Integraciones y Hardware. El registro deja de quitar altura al grafo.
- **Barra superior compacta:** permite iniciar el análisis sin desplegar todas las opciones; `Configurar análisis` muestra/oculta ruta, IA, exportación, escaneo total y actualización.
- **Panel derecho con pestañas:** `Archivos`, `Inspector` y `Sector`. Ya no se apilan el árbol, todas las dependencias y la configuración de alcance en una sola columna interminable.
- **Vistas:** `Mapa + árbol`, `Solo mapa`, `Solo árbol` y `Pantalla amplia`. La tecla `Esc` sale de pantalla amplia.
- **Mapa inicial por carpetas:** agrupa nodos, muestra barras de estados verde/amarillo/rojo y limita a 6–12 grupos por página para que los títulos sigan siendo legibles. Al elegir un grupo, aparece el detalle de archivos. El zoom sigue al cursor.
- **Relaciones contextuales:** para no convertir el mapa en una telaraña, las líneas aparecen sobre todo cuando seleccionás un nodo. `Relaciones: todas` permite mostrar el conjunto completo cuando te resulte útil.
- **Árbol y mapa sincronizados:** seleccionar un archivo en cualquiera de las vistas permite localizarlo en el otro y consultar sus dependencias en `Inspector`.

El sistema opcional de observación de Solaris (capturas por pantalla desde su propio tray) no forma parte del ejecutable Engineer: requiere consentimiento explícito y no envía imágenes automáticamente.

## Interfaz web: primer análisis

1. En **Ruta de carpeta, archivo o EXE** indicar el original: ejemplo C:\Apps\MiPrograma\MiPrograma.exe.
2. Pulsar **Agregar proyecto**. Se crea un directorio de datos dentro del Engineer, no en el programa original.
3. Seleccionar cantidad de **workers**: 1 para mínimo consumo; 2, 4, 8 o 16 para paralelismo. Elegir **Hilos** para consumo menor o **Procesos** para usar varios núcleos de CPU. El panel permite ajustar el límite máximo por archivo (32 MiB a 2 GiB).
4. Si se desea indexar también archivos sin relación con la entrada principal, activar **Escanear todos los archivos conocidos**.
5. Opcionalmente activar **Explicar archivos con IA**, indicando proveedor, URL, modelo e idioma.
6. Pulsar **Analizar**. Un trabajo local crea o reanuda el índice SQLite, luego exporta el mapa.
7. Navegar con zoom, arrastre, búsqueda y clic sobre cada nodo; el panel derecho muestra relaciones, origen de enlaces, comentarios de IA e impacto inverso. La vista previa de contenido completa queda planificada para una versión posterior.
8. Pulsar **Regenerar mapa y notas** para actualizar visualizaciones.

El trabajo local sigue activo mientras esté abierto el servidor. Si este se cierra, la cola sigue guardada, pero el proceso se detiene. Reiniciando el panel y lanzando el mismo análisis se continúa desde los pendientes.

## Estructura de carpetas por programa

    SolarisPKN-Engineer/
      engineer.py                  Motor recursivo / CLI
      ai_analysis.py               Explicaciones IA y caché
      binary_readers.py            Despachador de binarios
      pe_reader.py                 Imports PE Windows
      elf_reader.py                DT_NEEDED Linux
      macho_reader.py              Bibliotecas Mach-O Apple
      workspace.py                 Directorios de proyectos
      server.py                    Servidor web local
      dashboard_v2.html            Centro de control con pestañas
      visualizer.html              Grafo navegable
      export_graph.py              Exportación HTML/JSON/Markdown
      INICIAR_ENGINEER.cmd         Lanzador Windows
      COMPILAR_EXE.cmd             Empaquetado Windows
      README.md
      README.en.md
      tests/
      proyectos/
        mi-programa-<identificador>/
          proyecto.json            Origen, entrada y metadatos
          indice.sqlite            Nodos, aristas y descripciones IA
          actividad.log            Registro de análisis
          mapa/
            mapa.json
            mapa.html
            Obsidian/
              Inicio.md
              Indice-IA.md
              Archivos/
                Indice.md
                <notas individuales>.md

La carpeta **proyectos** es parte del Engineer. Los originales se mantienen donde estén. Los resultados quedan fuera de Git por las reglas de .gitignore. Cada proyecto se identifica por ruta y archivo de entrada para evitar conflictos de nombres.

## Algoritmo incremental y resistente

Ejemplo: A depende de B y C; B depende de E y F; F depende de H; C depende de H e I.

- A se agrega a la cola persistente y se analiza.
- B y C se descubren y se agregan como pendientes.
- Un worker prioriza profundidades mayores, hasta que ya no quedan dependencias de ese camino.
- H aparece por dos caminos pero se analiza una sola vez; se conservan ambas relaciones.
- Si A referencia de vuelta a B, el ciclo no repite el escaneo infinito.
- Con varios workers, distintas ramas avanzan simultáneamente. El orden de finalización no está garantizado.
- SQLite guarda estados pending, running, done, failed y external; tras interrupción, running vuelve a pending.
- El fracaso de un archivo no detiene a los demás; el fallo queda registrado por nodo.
- Se registra un SHA-256 por archivo. La actualización manual --refresh reanaliza nodos existentes; la detección automática de archivos cambiados todavía no es completa.

Límites predeterminados: 32 MiB por archivo y 4 MiB para texto fuente, para no disparar consumo de memoria. No implica que todo el grafo se mantenga en RAM durante el escaneo.

## Qué se puede analizar

| Entrada | Mecanismo | Límites |
|---|---|---|
| Python PY/PYI | AST, imports y referencias literales | Imports dinámicos con nombres variables |
| JS/TS JSX/TSX/MJS/CJS | Imports, require, carga literal | Aliases y resolución de bundlers |
| Astro/MDX/HTML/CSS y estilos modernos | Imports estáticos de componentes, recursos y aliases `@/` y `~/` | Imports dinámicos, plantillas interpoladas, transformaciones de bundlers |
| C/C++ | Inclusiones preprocesador literales | Macros y configuraciones de compilación |
| Java/Kotlin, C#, Go, Rust, PHP, Ruby | Patrones básicos de referencias | No hay semántica profunda completa |
| Manifiestos npm/pip/Cargo/Go | Dependencias declaradas iniciales | Lockfiles y versiones de ejecución parciales |
| Windows EXE/DLL/SYS/OCX | Imports declarados PE; cadenas heurísticas | Carga dinámica, packers, ofuscación |
| ELF Linux | DT_NEEDED en formatos habituales | Cargas dinámicas y formatos avanzados |
| Mach-O macOS | Comandos de carga en formatos thin | Ejecutables fat y variantes |

Los nombres de bibliotecas locales descubiertos en un EXE se buscan dentro del proyecto y se encolan recursivamente. Los demás se anotan como dependencias externas. Un nombre encontrado solo por cadena queda marcado como heurístico, no confirmado.

Las referencias declaradas no demuestran por sí mismas que una dependencia sea indispensable. El motor tampoco reemplaza a un descompilador completo.

## IA por archivo

Es **opt-in**. El detector de dependencias funciona aun sin IA.

Config local sugerida en Solaris:

    Proveedor: ollama
    URL: http://127.0.0.1:11435/api/chat
    Modelo: qwen2.5-coder:7b
    Idioma: es

La IA recibe un fragmento limitado del archivo fuente y las relaciones detectadas. En binarios EXE no recibe código descompilado: solo los nombres y tipos de importaciones detectadas. Devuelve un documento estructurado con resumen, responsabilidades, símbolos importantes, entradas/salidas, notas y límites.

Se guarda en SQLite con SHA-256 del archivo + configuración del modelo + versión del prompt. Se reutiliza al reanudar; si el modelo está desconectado registra fallo, no elimina ni invalida los resultados mecánicos.

La exportación añade:
- **Comentario de IA** dentro de cada nota Markdown individual.
- **Indice-IA.md** con lista enlazada de todas las explicaciones.
- Resumen IA en el panel de inspección del mapa HTML y en mapa.json.

Los resúmenes son **interpretaciones del modelo, no pruebas de ejecución**.

### Conectar una IA remota

Seleccionar proveedor **openai**, especificar un endpoint HTTPS compatible (por ejemplo una ruta /v1/chat/completions) y activar explícitamente **Permitir enviar código a una IA remota**. La clave se pasa por la variable de entorno **ENGINEER_AI_API_KEY**.

No enviar proyectos privados o de terceros a servicios remotos sin autorización. Hay filtros básicos de nombres y patrones de secretos; **no garantizan filtrado completo**. Para archivos sensibles, usar IA local o desactivar esta función. Las instrucciones incrustadas dentro del código son datos y no tienen autoridad sobre el motor.

## CLI para usuarios avanzados

Desde el directorio del Engineer:

    py -3 engineer.py scan "C:\MiProyecto" --entry main.py --db .\datos\mi-proyecto.sqlite --workers 1

Escaneo de EXE:

    py -3 engineer.py scan "C:\MiPrograma" --entry MiPrograma.exe --db .\datos\exe.sqlite --workers 4

Indexar todo el árbol:

    py -3 engineer.py scan "C:\MiProyecto" --all --db .\datos\mi-proyecto.sqlite --workers 4

Explicaciones IA después del análisis:

    py -3 engineer.py annotate --db .\datos\mi-proyecto.sqlite

Con IA desde el escaneo:

    py -3 engineer.py scan "C:\MiProyecto" --entry main.py --db .\datos\mi-proyecto.sqlite --ai

Reintentar IA fallida:

    py -3 engineer.py annotate --db .\datos\mi-proyecto.sqlite --ai-retry-failed

Exportación y estado:

    py -3 engineer.py export --db .\datos\mi-proyecto.sqlite --output .\salida
    py -3 engineer.py stats --db .\datos\mi-proyecto.sqlite
    py -3 engineer.py history "C:\MiProyecto" main.py

El CLI permite rutas personalizadas, mientras que el panel web **organiza automáticamente** los proyectos y resultados en su carpeta interna.

## Compilar un EXE portable

No se requiere EXE para ejecutar el proyecto. Si se desea distribuirlo o abrirlo con doble clic sin depender de Python instalado:

    py -3 -m pip install pyinstaller

Después, desde la carpeta del Engineer:

    COMPILAR_EXE.cmd

Este script solicita a PyInstaller un archivo **SolarisPKN-Engineer.exe**, con dashboard y visualizador incluidos. Al abrirlo, inicia el supervisor de bandeja y desde allí el servidor local. Los proyectos seguirán almacenándose en la carpeta proyectos del mismo directorio.

**El script está creado, pero el EXE aún no fue compilado ni validado en tu PC.** Si el Engineer está en una carpeta sin permisos de escritura, mover la distribución a una carpeta propia del usuario.

## Bóveda cifrada de contraseñas y tokens (Windows DPAPI)

SolarisPKN-Engineer permite **guardar opcionalmente** contraseñas y claves API dentro de la carpeta interna `.private/credentials.dpapi.json`. La información sensible se cifra mediante **Windows DPAPI (CryptProtectData, alcance usuario actual)**. El archivo únicamente contiene blobs cifrados, identificadores hash opacos y fecha de actualización. La carpeta completa `.private/` está excluida de Git.

Desde el inspector de un ZIP protegido podés usar **Recordar cifrada para mi usuario de Windows**, **Usar contraseña guardada** o **Eliminar contraseña guardada**. Engineer la conserva solo si la contraseña permitió descifrar al menos un miembro realmente protegido, sin errores de descifrado. La contraseña nunca se devuelve a la página web, y se usa solamente del lado del servidor para el análisis autorizado.

Desde **Integraciones** también podés guardar y eliminar claves de Gemini, GPT, Claude y cualquier proveedor configurado, además del token de GitHub. Engineer usa primero una variable de entorno cuando está definida; en su ausencia consulta la bóveda DPAPI. El navegador solo recibe si existe una credencial, jamás su valor.

**Seguridad y recuperación:**
- DPAPI protege los datos para el usuario Windows que los guardó; copiar solamente el archivo de la bóveda a otra PC o cuenta **no basta** para descifrarlo. Una reinstalación o pérdida del perfil de Windows puede impedir recuperar las claves. Guardá copias originales en un gestor de contraseñas propio.
- Las credenciales se descifran **temporalmente en memoria** cuando se necesitan. Malware o procesos que controlen tu sesión pueden representar un riesgo: no existe protección absoluta en un dispositivo comprometido.
- El sistema no hace descifrado por fuerza bruta, no guarda texto plano ni exporta las claves a Obsidian o al mapa.
- En plataformas sin Windows DPAPI, la función de guardar secretos se deshabilita expresamente; nunca cambia a un archivo en claro.
- Al borrar una credencial se elimina la entrada del archivo lógico; esto no garantiza el borrado físico de historiales de disco, backups o memoria.
- Las claves usadas por servicios externos sí se envían a esos proveedores por HTTPS durante una solicitud autorizada; el código fuente solo se transmite a una IA remota si activaste esa opción.

## Desbloqueo autorizado de archivos cifrados

Cuando un archivo está rojo (encrypted), su propietario o una persona autorizada puede elegir un método desde el inspector:

- **Contraseña ZIP (ZipCrypto):** introducir la contraseña y confirmar autorización. Engineer lee los miembros en fragmentos, sin extraer fuentes al directorio original ni guardar una copia descifrada en su carpeta. Registra cada miembro en el grafo como nodo de archivo interno, muestra las dependencias disponibles y conserva en rojo los miembros que no pudo leer. La contraseña solo se guarda cuando el usuario marca la opción de recordarla; se cifra con DPAPI y solo después de comprobar el descifrado.
- **Copia ya descifrada por el propietario:** para otros cifrados, el usuario utiliza su herramienta autorizada e indica la ruta del archivo o carpeta descifrada. Engineer crea un proyecto separado y una relación de procedencia declarada por el usuario; todavía necesita escanearse. Este método no significa que Engineer pueda romper el cifrado.

**Límites:** Python ZipFile soporta cifrado tradicional ZipCrypto, no WinZip AES. La contraseña no se guarda en texto plano; si optás por recordarla se cifra mediante DPAPI. Los nombres y las relaciones descubiertas sí se registran. Los archivos y los paquetes enormes todavía pueden consumir recursos importantes y algunos formatos requieren nuevos adaptadores.

## Motor de uso e impacto inverso (nuevo)

**No confundir archivo leído con archivo utilizado.** El semáforo verde/amarillo/rojo representa progreso de lectura; un anillo celeste en el grafo marca referencias declaradas, un anillo ámbar marca inferencias y un anillo violeta señala posibles puntos de entrada. Sin anillo **no significa que sea seguro borrarlo**.

Al seleccionar un archivo en el mapa o el árbol, Engineer muestra ahora su **grafo local de dos niveles** y la ficha **Uso e impacto**:

- **Referenciado:** otro archivo declara su utilización (por ejemplo, el JSON de una publicación referencia su imagen).
- **Posible uso:** relación inferida por alias, plantilla o patrón dinámico; requiere validación.
- **Entrada/configuración:** página, script o configuración que puede no tener importadores dentro del proyecto.
- **Uso no determinado:** ningún importador descubierto con la cobertura actual; no equivale a archivo obsoleto.
- **Producido por:** arista `generates` aparte de las aristas de uso. Un script creador de posts puede producir archivos de imágenes, metadatos y traducciones, pero eso no demuestra que se consuman durante la ejecución.

Los informes consultan el grafo **al revés**, recorriendo consumidores directos y transitivos hasta posibles entradas. No cargan necesariamente todo el grafo en RAM y limitan los resultados de inspección con indicadores de truncamiento.

**Qué detecta en SolarisPKN-Labs:** `scripts/post-service.js` puede generar `src/content/blog/<slug>/post.json`, los contenidos MDX, las traducciones y las imágenes del post; `post.json` declara `heroImage`/`images`; la página dinámica Astro lee los metadatos. Esto permite navegar desde una portada hasta el post y la página. También se reconocen lecturas literales de configuraciones (incluido `settings.json` si existe y el código lo lee) y dependencias de `package.json`.

**Estado de borrado:** ningún resultado marca `safe_to_delete=true`. Un archivo sin referencias detectadas es **candidato a investigar**, no candidato a eliminación automática. Antes de declarar obsoleto algo hacen falta análisis de puntos de entrada, scripts de build, imports dinámicos, trazas de ejecución, tests y contexto de despliegue. En un sistema operativo se necesitan además gestor de paquetes, servicios, arranque, registro/symlinks, módulos y cargas en tiempo de ejecución; se recomienda snapshot y prueba en VM antes de cualquier remoción.

La API de inspección es `GET /api/impact?project=ID&key=file:RUTA`. Las pruebas sintéticas viven en `tests/test_impact_usage.py`; revisar el repositorio en producción exige reiniciar Engineer y **Reanalizar** para recalcular los vínculos.

## Semáforo del mapa y el árbol

- **Verde:** archivo inspeccionado y trabajo terminado (`done`). No implica comprensión total del formato.
- **Amarillo:** archivo registrado en cola o en proceso (`pending` / `running`).
- **Rojo:** se detectó un marcador reconocible de cifrado y el motor no puede analizar el contenido (`encrypted`); el inspector muestra el motivo. No se deduce cifrado únicamente por extensión, entropía alta o fallo de lectura.
- **Violeta/gris:** error de otro tipo (`failed`), por ejemplo archivo dañado o formato todavía no compatible.
- **Azul:** dependencia externa sin resolver (`external`).

Los directorios del árbol heredan un color agregado de los archivos indexados: el rojo señala que alguno de ellos está cifrado. El mapa puede refrescar estos estados durante un escaneo largo; para datos nuevos se carga una instantánea limitada del índice SQLite. Archivos cifrados ya conocidos se reintentan mediante `--refresh`.

## Seguridad, escalabilidad y límites actuales

- No ejecuta binarios inspeccionados ni instala dependencias externas.
- Lee código fuente de la ruta seleccionada; la salida permanece separada.
- El servidor usa 127.0.0.1, control de Host y token de sesión para peticiones POST.
- El motor puede continuar una cola grande, pero disco, CPU, RAM y tamaño de archivos siguen siendo límites reales.
- Un solo trabajo activo por servidor; varios workers dentro de un trabajo.
- El visualizador HTML limita a unos 2.200 nodos visibles simultáneamente; JSON y Markdown incluyen todos, pero la exportación de grafos gigantes aún necesita optimización.
- No está construido todavía el tracing de procesos, análisis dinámico de DLL o plugins ni análisis profundo de funciones o descompilación.
- Los mensajes de commits y las descripciones de IA pueden contener información privada; no compartir exportaciones sin revisión.
- No incluye permisos GitHub por archivo, porque decidimos dejarlo fuera.

## Preparar el primer commit (v0.1.0-alpha)

Ejecutá **`PREPARAR_GITHUB.cmd`** desde la carpeta de desarrollo original. Corre la suite de tests y, si pasa, crea `dist/github-export/SolarisPKN-Engineer` con una **lista permitida de archivos fuente y pruebas**. También puede exportar un **ejemplo saneado** de SolarisPKN-Labs cuando su índice local y manifiesto público están disponibles. No incluye originales, contraseñas, archivos de trabajo ni notas de IA. El script no realiza commits ni pushes. Revisá el manifiesto SHA256 antes de publicar. La vista previa completa de archivos queda para una versión posterior.

## Publicación segura en GitHub

Revisá **RELEASE_CHECKLIST.md** y ejecutá **VERIFICAR_REPO.cmd** para comprobar tests y exclusiones. El repositorio remoto ya existe y contiene una licencia **GNU AGPL-3.0** que debe conservarse. Las pruebas interactivas del tray y la compilación del EXE son verificaciones independientes.

## Diagnóstico y tests

    py -3 -m unittest discover -s tests -v

Los archivos de test contemplan recorrido transitivo, ciclos, workers, caché, reanudación, salidas de Obsidian, IA simulada sin red y exclusión básica de secretos. Para incidencias, consultar actividad.log dentro de cada proyecto.

**Validación pendiente:** las pruebas todavía necesitan ejecutarse y confirmarse en el Windows anfitrión; la presencia del código no demuestra una ejecución exitosa.

## Próximas fases

Resolución de módulos por lenguaje más precisa, lockfiles completos, caché incremental automática, observación dinámica aislada, límites configurables de recursos, historiales temporales de Git integrados, descompilación asistida donde sea legal y posible, agrupación de nodos gigantes, y un EXE compilado y probado.

---

Este repositorio distribuye **solo Engineer** y ejemplos públicos saneados. Nunca publiques el árbol privado completo de Solaris ni sus datos de usuario. Licencia del repositorio: **GNU AGPL-3.0**.
