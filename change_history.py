"""Bounded opt-in text diffs and honest Git commit provenance for local monitoring.

No shell commands, no Git write operations, no remote network calls, no hooks.
Commit authorship describes the Git record, not necessarily the person who
made a currently uncommitted edit or published an external website.
"""
from __future__ import annotations

import difflib
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

MAX_TEXT_BYTES = 8192
MAX_SNAPSHOT_FILES = 60
MAX_SNAPSHOT_BYTES = 160_000
EXCLUDED_NAMES = {".env", ".gitconfig", "id_rsa", "id_ed25519", "credentials", "secrets.json"}
EXCLUDED_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".env", ".sqlite", ".db", ".bin", ".exe", ".iso"}


def safe_source(relative: str) -> bool:
    path = Path(relative)
    if any(part.lower().startswith(".env") or part.lower() in EXCLUDED_NAMES for part in path.parts):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return not any(part in {".git", ".private", "node_modules", ".venv", ".venv-engineer-build"} for part in path.parts)


def text_snapshots(root: str, paths: list[str]) -> dict[str, str]:
    """Limited local source excerpts; caller must explicitly opt into persistence."""
    base = Path(root).resolve()
    is_file = base.is_file()
    home = base.parent if is_file else base
    result: dict[str, str] = {}
    bytes_stored = 0
    for relative in paths:
        if len(result) >= MAX_SNAPSHOT_FILES:
            break
        if not isinstance(relative, str) or not safe_source(relative):
            continue
        file = home / relative
        try:
            if file.is_symlink() or not file.is_file():
                continue
            resolved = file.resolve(strict=True)
            if not resolved.is_relative_to(home):
                continue
            size = resolved.stat().st_size
            if size > MAX_TEXT_BYTES or bytes_stored + size > MAX_SNAPSHOT_BYTES:
                continue
            raw = resolved.read_bytes()
            if b"\x00" in raw:
                continue
            body = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if "\ufffd" in body:
            continue
        result[relative] = body
        bytes_stored += len(raw)
    return result


def format_diff(before: str, after: str, *, web: bool = False) -> str:
    """Return a textual unified diff, bounded by a fixed size and hunk count."""
    if web:
        # Canonical HTML text is whitespace-normalized to a single line.
        # Wrap into word lines so the diff remains readable.
        def words(text: str) -> list[str]:
            parts = text.split()
            return [" ".join(parts[i:i + 12]) + "\n" for i in range(0, min(len(parts), 2400), 12)]
        left, right = words(before), words(after)
    else:
        left = before.splitlines(keepends=True)
        right = after.splitlines(keepends=True)
    if left == right:
        return ""
    output = []
    total = 0
    for line in difflib.unified_diff(left, right, fromfile="anterior", tofile="actual", n=2):
        if len(output) >= 180 or total + len(line) > 16_000:
            output.append("... diff truncado por seguridad ...\n")
            break
        output.append(line)
        total += len(line)
    return "".join(output)


def _git(cwd: Path, *args: str) -> str | None:
    env = os.environ.copy()
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
               GIT_NO_REPLACE_OBJECTS="1", GIT_CONFIG_NOSYSTEM="1")
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=3, env=env, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout[:5000].strip()


def _github_remote(url: str) -> str | None:
    """Only generate trusted github.com commit URLs from exact SSH/HTTPS remotes."""
    if url.startswith("git@github.com:"):
        tail = url[len("git@github.com:"):]
    elif url.startswith("ssh://git@github.com/"):
        tail = url[len("ssh://git@github.com/"):]
    else:
        try:
            parsed = urlsplit(url)
        except ValueError:
            return None
        if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.username or parsed.password:
            return None
        tail = parsed.path.lstrip("/")
    tail = tail.removesuffix(".git").strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", tail):
        return None
    return "https://github.com/" + tail


def git_file_history(target: str, paths: list[str], *, limit: int = 12) -> dict:
    """Get last Git commit (author and committer) per changed file when available.

    Dirty or untracked working-tree files are not attributed to prior commits.
    Git user names/emails are self-declared metadata, NOT verified identities.
    """
    root = Path(target).resolve()
    cwd = root.parent if root.is_file() else root
    top_raw = _git(cwd, "rev-parse", "--show-toplevel")
    if not top_raw:
        return {"available": False, "reason": "La carpeta no contiene un repositorio Git accesible"}
    git_root = Path(top_raw).resolve()
    try:
        cwd.relative_to(git_root)
    except ValueError:
        return {"available": False, "reason": "Repositorio Git fuera del objetivo"}
    origin = _git(cwd, "remote", "get-url", "origin") or ""
    github = _github_remote(origin)
    entries = []
    for rel in paths[:limit]:
        if not isinstance(rel, str):
            continue
        candidate = (root.parent if root.is_file() else root) / rel
        try:
            safe = candidate.resolve(strict=False).relative_to(git_root).as_posix()
        except ValueError:
            continue
        if safe.startswith("../") or safe == ".":
            continue
        spec = ":(top,literal)" + safe
        dirty = _git(cwd, "status", "--porcelain=1", "--untracked-files=normal", "--", spec)
        commit_text = _git(cwd, "log", "-1",
                           "--format=%H%x1f%an%x1f%ae%x1f%aI%x1f%cn%x1f%cI%x1f%s",
                           "--", spec)
        item = {"path": rel, "working_tree_changed": bool(dirty),
                "attribution": ("unavailable" if dirty is None else
                                "uncommitted" if dirty else
                                "last_commit" if commit_text else "unavailable")}
        if commit_text:
            parts = commit_text.split("\x1f", 6)
            if len(parts) == 7 and re.fullmatch(r"[0-9a-f]{40,64}", parts[0]):
                item["last_commit"] = {
                    "sha": parts[0], "author": parts[1][:120], "author_email": parts[2][:160],
                    "authored_at": parts[3][:40], "committer": parts[4][:120],
                    "committed_at": parts[5][:40], "message": parts[6][:250],
                    "url": github + "/commit/" + parts[0] if github else None,
                }
        entries.append(item)
    return {"available": True, "github_repository": github, "files": entries,
            "notice": "Git guarda autores declarados en commits; no demuestra quién editó un archivo sin commit."}


def local_git_blame(target: str, relative: str, start: int = 1, end: int = 80) -> dict:
    """Line-level blame for one tracked local Git file, no remote requests.

    Current uncommitted lines are explicitly marked as unknown; commit authors
    are Git metadata and not proof of who physically edited the content.
    """
    if (not isinstance(relative, str) or not 1 <= len(relative) <= 400 or
            relative.startswith(("/", ".")) or "\\" in relative or
            any(part in (".", "..", "") for part in relative.split("/"))):
        raise ValueError("Ruta de archivo inválida")
    if (isinstance(start, bool) or isinstance(end, bool) or
            not isinstance(start, int) or not isinstance(end, int) or
            not 1 <= start <= end or end - start >= 120):
        raise ValueError("El rango debe ser de 1 a 120 líneas")
    root = Path(target).resolve()
    home = root.parent if root.is_file() else root
    candidate = home / relative
    if candidate.is_symlink():
        raise ValueError("No se siguen enlaces simbólicos")
    file = candidate.resolve()
    if not file.is_relative_to(home) or not file.is_file():
        raise ValueError("Archivo fuera del objetivo o inexistente")
    top = _git(home, "rev-parse", "--show-toplevel")
    if not top:
        raise ValueError("Objetivo sin repositorio Git local")
    gitroot = Path(top).resolve()
    if not file.is_relative_to(gitroot):
        raise ValueError("Archivo fuera del repositorio Git")
    rel_git = file.relative_to(gitroot).as_posix()
    # git blame accepts an exact path, unlike the pathspec accepted by git log.
    # Path is resolved inside gitroot and '--' stops option interpretation.
    cmd = ["git", "-c", "core.fsmonitor=false", "-C", str(gitroot),
           "blame", "--line-porcelain", "-L", f"{start},{end}", "--", rel_git]
    env = os.environ.copy()
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
               GIT_CONFIG_NOSYSTEM="1", GIT_NO_REPLACE_OBJECTS="1")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=6, check=False, env=env,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Git blame local no está disponible") from None
    if proc.returncode != 0:
        raise ValueError("Git blame no pudo consultar ese archivo/rango")
    if len(proc.stdout) > 250_000:
        raise ValueError("Blame demasiado grande para mostrar")
    records = []
    current: dict = {}
    final_lineno = start
    for line in proc.stdout.splitlines():
        match = re.match(r"^([0-9a-f]{40,64})\s+\d+\s+(\d+)(?:\s+\d+)?$", line)
        if match:
            current = {"sha": match.group(1), "line": int(match.group(2))}
            final_lineno = current["line"]
        elif line.startswith("author "):
            current["author"] = line[7:][:120]
        elif line.startswith("author-time "):
            current["authored_at_unix"] = int(line[12:]) if line[12:].isdigit() else None
        elif line.startswith("summary "):
            current["summary"] = line[8:][:160]
        elif line.startswith("\t"):
            sha = current.get("sha") or ""
            uncommitted = bool(re.fullmatch(r"0+", sha)) or current.get("author") in ("Not Committed Yet", "Not Committed")
            records.append({"line": final_lineno, "text": line[1:][:500],
                            "sha": None if uncommitted else sha,
                            "author": None if uncommitted else current.get("author"),
                            "date_unix": current.get("authored_at_unix"),
                            "message": current.get("summary"), "uncommitted": uncommitted})
        if len(records) >= 120:
            break
    return {"path": relative, "lines": records, "source": "git blame --line-porcelain",
            "note": "La información procede de Git local; líneas sin commit no tienen autor verificado."}
