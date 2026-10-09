"""Portable project workspaces for SolarisPKN-Engineer.

All generated data stays under Engineer/proyectos/. Source projects are read-only.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys


def app_folder() -> Path:
    # In frozen Windows .exe, store workspaces beside the executable.
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def projects_folder() -> Path:
    base = app_folder() / "proyectos"
    base.mkdir(parents=True, exist_ok=True)
    return base


def slug(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-_").lower()
    return (value[:48] or "proyecto")


def select_target(target: str):
    if not isinstance(target, str) or not target.strip():
        raise ValueError('Select a folder or file path before adding a project')
    path = Path(target).expanduser().resolve(strict=True)
    if path.is_dir():
        return path, None, path.name or "Proyecto"
    if path.is_file():
        return path.parent, path.name, path.stem
    raise ValueError("Choose an existing source folder or executable/source file")


def register(target: str) -> dict:
    root, entry, label = select_target(target)
    identity = str(root).casefold() + "\0" + str(entry or "")
    project_id = slug(label) + "-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    folder = projects_folder() / project_id
    folder.mkdir(parents=True, exist_ok=True)
    metadata = folder / "proyecto.json"
    if metadata.exists():
        return load(project_id)
    record = {
        "id": project_id, "name": label, "root": str(root),
        "entry": entry, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "database": "indice.sqlite", "output": "mapa",
    }
    temporary = folder / "proyecto.json.tmp"
    temporary.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, metadata)
    return record


def load(project_id: str) -> dict:
    if not re.fullmatch(r"[a-z0-9_-]{1,90}", project_id):
        raise ValueError("Invalid project id")
    folder = projects_folder() / project_id
    data = json.loads((folder / "proyecto.json").read_text(encoding="utf-8"))
    if data.get("id") != project_id or data.get("database") != "indice.sqlite":
        raise ValueError("Malformed workspace")
    return data


def paths(project_id: str):
    info = load(project_id)
    folder = projects_folder() / project_id
    return info, folder / "indice.sqlite", folder / "mapa", folder / "actividad.log"


def list_projects() -> list[dict]:
    results = []
    for entry in projects_folder().iterdir():
        if entry.is_dir() and (entry / "proyecto.json").is_file():
            try:
                results.append(load(entry.name))
            except (ValueError, OSError, json.JSONDecodeError):
                continue
    return sorted(results, key=lambda p: p["name"].casefold())
