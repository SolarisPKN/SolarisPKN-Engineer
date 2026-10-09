"""Prepare a clean, source-only Engineer folder for review and GitHub.

Copies an EXPLICIT allowlist. Never copies projects, database indexes, logs,
private credentials, workstation config, binaries or the parent SolarisPKN-IA.
Runs Windows unit tests first; will not publish or commit anything.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "dist" / "github-export" / "SolarisPKN-Engineer"

PUBLIC_FILES = (
    ".gitignore",
    "README.md", "README.es.md", "RELEASE_CHECKLIST.md",
    "INICIAR_ENGINEER.cmd", "COMPILAR_EXE.cmd", "VERIFICAR_REPO.cmd",
    "PREPARAR_GITHUB.cmd", "PREPARAR_GITHUB.py",
    "ai_analysis.py", "api_extensions.py", "authorized_unlock.py",
    "binary_readers.py", "credential_vault.py",
    "dashboard_v2.html", "elf_reader.py",
    "encrypted_formats.py", "engineer.py", "export_graph.py",
    "generated_files.py", "graph_diagnostics.py", "graph_structure.py",
    "impact_analysis.py", "integrations.py", "iso_reader.py",
    "language_catalog.py", "labs_public_example.py", "live_view.py", "macho_reader.py",
    "pe_reader.py", "project_semantics.py", "resource_controls.py",
    "scope.py", "server.py", "static_imports.py", "tray_launcher.py",
    "visualizer.html", "web_dependencies.py", "workspace.py",
)

# Only reviewed user-provided screenshots may enter the public GitHub export.
PUBLIC_SCREENSHOTS = (
    "docs/screenshots/dependency-inspector.webp",
    "docs/screenshots/folder-overview.webp",
    "docs/screenshots/folder-node-map.webp",
)

EXCLUDE_PARTS = {
    ".private", ".git", "proyectos", "datos", "salida", "dist",
    ".build", "build", "__pycache__", ".pytest_cache"
}
BANNED_SUFFIXES = (".sqlite", ".db", ".log", ".pem", ".key", ".iso", ".exe",
                   ".p12", ".pfx", ".zip", ".env", ".pyc", ".sqlite-wal")
BANNED_NAMES = {"dashboard.html", ".env", "credentials.json", "settings.json"}


def eligible(rel: Path) -> bool:
    parts = rel.parts
    if not parts or any(part.lower() in EXCLUDE_PARTS for part in parts):
        return False
    if rel.name.lower() in BANNED_NAMES:
        return False
    if any(rel.name.lower().endswith(ext) for ext in BANNED_SUFFIXES):
        return False
    if rel.as_posix() in PUBLIC_SCREENSHOTS:
        return len(parts) == 3 and rel.suffix.lower() == ".webp"
    if rel.parts[0] == "tests":
        return len(parts) == 2 and rel.suffix == ".py" and rel.name.startswith("test_")
    if rel.as_posix() in (".github/workflows/tests.yml", ".github/workflows/labs-scan.yml"):
        return True
    return len(parts) == 1 and rel.as_posix() in PUBLIC_FILES


def safe_sources() -> list[Path]:
    names = [Path(n) for n in PUBLIC_FILES]
    names.extend(Path(n) for n in PUBLIC_SCREENSHOTS)
    names.extend([Path(".github/workflows/tests.yml"),
                  Path(".github/workflows/labs-scan.yml")])
    names.extend(
        Path("tests") / p.name
        for p in (ROOT / "tests").glob("test_*.py")
        if p.is_file() and not p.is_symlink()
    )
    missing = [str(name) for name in (*PUBLIC_FILES, *PUBLIC_SCREENSHOTS)
               if not (ROOT / name).is_file()]
    if missing:
        raise RuntimeError("Faltan fuentes públicas requeridas: " + ", ".join(missing))
    for path in names:
        source = ROOT / path
        if not eligible(path) or not source.is_file() or source.is_symlink():
            raise RuntimeError("Archivo no permitido: " + str(path))
        if source.resolve().parent == ROOT.parent:
            raise RuntimeError("El origen no pertenece a Engineer")
    return sorted(names)


def main() -> int:
    print("[1/3] Ejecutando tests antes de preparar el commit...", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=ROOT, timeout=600, check=False
        )
    except subprocess.TimeoutExpired:
        print("NO APTO: los tests superaron 10 minutos; revisar antes de publicar.")
        return 1
    if result.returncode != 0:
        print("NO APTO: hay tests fallando. No se reemplazó la copia GitHub.")
        return result.returncode or 1

    print("[2/3] Preparando exclusivamente los archivos de código públicos...")
    files = safe_sources()
    temporary = DEST.parent / "_engineer_export_incompleto"
    if temporary.exists():
        if temporary.is_symlink() or (temporary / ".git").exists():
            raise RuntimeError("No se borrará un directorio temporal ajeno o con Git")
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    manifest = {}
    try:
        for rel in files:
            src = ROOT / rel
            destination = temporary / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, destination)
            manifest[rel.as_posix()] = hashlib.sha256(destination.read_bytes()).hexdigest()
        # An example scan is optional, not a runtime dependency of the public
        # Engineer checkout. It can only be exported from a host that has
        # reviewed Labs data and the verified public Git file manifest.
        private_sample = ROOT / "public_scan.py"
        labs_index = ROOT / "proyectos" / "solarispkn-labs-7e879aa61821" / "indice.sqlite"
        public_manifest = ROOT / "LABS_PUBLIC_MANIFEST.json"
        if private_sample.is_file() and labs_index.is_file() and public_manifest.is_file():
            from public_scan import export_public_example, DEST_REL
            sample=export_public_example(temporary / DEST_REL)
            for filename in sample["publishedFiles"]:
                rel=DEST_REL / filename
                file=temporary / rel
                if not file.is_file() or file.stat().st_size>3_000_000:
                    raise RuntimeError("Invalid public scan size/path: "+str(rel))
                manifest[rel.as_posix()]=hashlib.sha256(file.read_bytes()).hexdigest()
            print("Public Labs scan: ", sample["files"], "files,", sample["semanticEdges"], "semantic edges.")
        else:
            print("No local public Labs scan: publishing only Engineer sources and tests.")
        (temporary / "ARCHIVOS_PUBLICADOS.json").write_text(
            json.dumps({"version": "v0.1.0-alpha", "sources": manifest},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if DEST.exists():
            if DEST.is_symlink() or (DEST / ".git").exists():
                raise RuntimeError("La carpeta de salida contiene un repo Git. No sobrescribir.")
            shutil.rmtree(DEST)
        temporary.rename(DEST)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)

    print("[3/3] Listo para revisión y copia manual.")
    print("Carpeta limpia:", DEST)
    print("Archivos fuente:", len(files))
    print("Sin datos privados, proyectos analizados, contraseñas, logs ni EXE.")
    print("AVISO: todavía revisá los archivos antes de publicarlos en un repo público.")
    print("No se creó commit ni se hizo push. El EXE debe probarse en Windows.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        sys.exit(1)
