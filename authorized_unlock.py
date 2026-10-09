"""Owner-authorized encrypted ZIP analysis, with ephemeral credentials.

Unlocked member contents are streamed in memory. No decrypted files are
written to the original project or Engineer workspace. Passwords are never
persisted, logged, sent to AI, or included in graph metadata.

ZIP legacy encryption (ZipCrypto) works with Python's standard library.
WinZip AES ZIP needs an optional future adapter, not silent fallback.
"""
from __future__ import annotations

import ast
import hashlib
import posixpath
from pathlib import Path, PurePosixPath
import re
import sqlite3
import zipfile

from engineer import database, inside_root, utcnow

READ_BLOCK = 128 * 1024
SOURCE_PREFIX = 4 * 1024 * 1024
MAX_IMPORTS_PER_MEMBER = 4000
PYTHON_MODULE = re.compile(r"(?m)^\s*(?:from\s+([\w.]+)\s+import\b|import\s+([\w.]+))")
JS_IMPORT = re.compile(
    r"""(?:\bfrom\s+|\brequire\s*\(\s*|\bimport\s*\(\s*)['"]([^'"]+)['"]"""
)
C_INCLUDE = re.compile(r'(?m)^\s*#\s*include\s*"([^"]+)"')


def safe_member_name(name: str) -> str | None:
    """Normalize virtual names without filesystem extraction or traversal."""
    if not name or "\x00" in name or name.startswith(("/", "\\")) or "\\" in name:
        return None
    parts = name.strip("/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    # ZIP members must not resemble drive roots (no Windows path semantics).
    if any(":" in p for p in parts):
        return None
    return "/".join(parts)


def member_specs(name: str, source: bytes, truncated: bool):
    """Static imports from authorized plaintext excerpts. No source execution."""
    ext = PurePosixPath(name).suffix.lower()
    text = source.decode("utf-8-sig", errors="replace")
    specs: list[tuple[str, str]] = []
    if ext in (".py", ".pyi"):
        if not truncated:
            try:
                tree = ast.parse(text, filename=name)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        specs.extend((alias.name, "python-import") for alias in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        spec = "." * node.level + (node.module or "")
                        if spec:
                            specs.append((spec, "python-import"))
                return specs[:MAX_IMPORTS_PER_MEMBER]
            except (SyntaxError, ValueError):
                pass
        for match in PYTHON_MODULE.finditer(text):
            value = match.group(1) or match.group(2)
            if value:
                specs.append((value, "python-import"))
    elif ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
        specs.extend((m, "js-import") for m in JS_IMPORT.findall(text))
    elif ext in (".c", ".cc", ".cpp", ".h", ".hpp"):
        specs.extend((m, "c-include") for m in C_INCLUDE.findall(text))
    return specs[:MAX_IMPORTS_PER_MEMBER]


def resolve_member(spec: str, name: str, known: set[str], relation: str) -> str | None:
    base = posixpath.dirname(name)
    if relation == "python-import":
        if spec.startswith("."):
            lead = len(spec) - len(spec.lstrip("."))
            base_parts = base.split("/") if base else []
            base_parts = base_parts[:max(0, len(base_parts) - max(0, lead-1))]
            module = "/".join(base_parts + spec.lstrip(".").split("."))
            candidates = [module+".py",module+"/__init__.py"]
        else:
            module = spec.replace(".", "/")
            candidates = [module+".py",module+"/__init__.py",base+"/"+module+".py"]
    else:
        if not spec.startswith((".", "/")) and relation=="js-import":
            return None
        module = posixpath.normpath(posixpath.join(base, spec))
        if module.startswith("../") or module=="..":
            return None
        candidates=[module,*(module+ext for ext in (".js",".ts",".jsx",".tsx",".json",".h",".hpp")),
                    module+"/index.js",module+"/index.ts"]
    return next((candidate for candidate in candidates if candidate in known), None)


def _status(db: sqlite3.Connection, key: str, status: str, digest=None, error=None, depth=1):
    kind = "zip"
    db.execute(
        "INSERT INTO nodes(key,kind,status,depth,digest,error,updated) VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET status=excluded.status,depth=excluded.depth,"
        "digest=excluded.digest,error=excluded.error,updated=excluded.updated",
        (key,kind,status,depth,digest,error,utcnow())
    )


def unlock_zip(root: Path, db_path: Path, relative: str, password: str,
               *, max_members: int = 0, cancel_event=None) -> dict:
    """Index encrypted ZIP member streams into the same project graph.

    A failed member remains encrypted, a supported successfully read member is
    done. The container stays red if any member cannot be decrypted.
    """
    root = root.resolve(strict=True)
    path = root / relative
    if inside_root(root, path) != relative or not path.is_file():
        raise ValueError("Selected archive must exist inside the project")
    if path.suffix.lower() not in (".zip", ".jar", ".whl"):
        raise ValueError("ZIP password method only supports ZIP-compatible containers")
    if not 0 <= len(password) <= 1024:
        raise ValueError("Invalid credential length")
    credential = password.encode("utf-8") or None
    db = database(db_path, root)
    archive_key = "file:" + relative
    try:
        original = db.execute("SELECT depth,status FROM nodes WHERE key=?", (archive_key,)).fetchone()
        if not original:
            raise ValueError("Index the archive first before unlocking")
        depth = original[0]
        outcome = {"done":0,"encrypted":0,"skipped":0,"inspected":0,"protected_done":0}
        with zipfile.ZipFile(path, "r", allowZip64=True) as archive:
            entries = archive.infolist()
            known = {name for item in entries
                     if not item.is_dir() and (name := safe_member_name(item.filename)) is not None}
            # Names are metadata, not evidence a payload has been decrypted.
            for item in entries:
                if cancel_event is not None and cancel_event.is_set():
                    break
                if item.is_dir():
                    continue
                name = safe_member_name(item.filename)
                if name is None:
                    outcome["skipped"] += 1
                    continue
                if max_members and outcome["inspected"] >= max_members:
                    break
                outcome["inspected"] += 1
                key = "zip:" + relative + "!/" + name
                payload = bytearray()
                h = hashlib.sha256()
                total = 0
                try:
                    # Encrypted ZIP data is decrypted by the standard library
                    # when ZIP legacy encryption is used and pwd is provided.
                    with archive.open(item, "r", pwd=credential) as inp:
                        while True:
                            block = inp.read(READ_BLOCK)
                            if not block:
                                break
                            h.update(block)
                            total += len(block)
                            if len(payload) < SOURCE_PREFIX:
                                payload.extend(block[:SOURCE_PREFIX-len(payload)])
                    truncated = total > len(payload)
                    deps = member_specs(name, bytes(payload), truncated)
                    with db:
                        db.execute("DELETE FROM edges WHERE source=?", (key,))
                        _status(db, key, "done", h.hexdigest(), depth=depth + 1)
                        db.execute(
                            "INSERT OR REPLACE INTO edges(source,target,relation,evidence) VALUES(?,?,?,?)",
                            (archive_key,key,"contains","observed")
                        )
                        for spec, reltype in deps:
                            resolved = resolve_member(spec,name,known,reltype)
                            target = ("zip:"+relative+"!/"+resolved) if resolved else (
                                "external:zip-package:"+spec[:250])
                            if resolved is None:
                                db.execute("INSERT OR IGNORE INTO nodes(key,kind,status,depth,updated) "
                                           "VALUES(?,'external','external',?,?)",
                                           (target,depth+2,utcnow()))
                            db.execute("INSERT OR REPLACE INTO edges VALUES(?,?,?,?)",
                                       (key,target,reltype,"heuristic" if truncated else "declared"))
                    outcome["done"] += 1
                    if item.flag_bits & 1:
                        outcome["protected_done"] += 1
                except (RuntimeError, NotImplementedError, ValueError, EOFError,
                        zipfile.BadZipFile, OSError) as ex:
                    # Do not include exception string: some libraries may echo secrets.
                    info = (type(ex).__name__ + ": encrypted member could not be decoded")[:180]
                    with db:
                        _status(db,key,"encrypted",error=info,depth=depth+1)
                        db.execute("INSERT OR REPLACE INTO edges VALUES(?,?,?,?)",
                                   (archive_key,key,"contains","declared"))
                    outcome["encrypted"] += 1
                finally:
                    payload.clear()
        with db:
            # Preserve container red if its contents are only partly accessible.
            stopped = cancel_event is not None and cancel_event.is_set()
            incomplete = stopped or (max_members and outcome["inspected"] < len(known))
            newstatus = "pending" if incomplete else (
                "encrypted" if outcome["encrypted"] or outcome["skipped"] else "done"
            )
            reason = ("Interrupted by tray; re-run unlock" if incomplete else
                      "Archive partly unreadable" if newstatus=="encrypted" else None)
            db.execute("UPDATE nodes SET status=?,error=?,updated=? WHERE key=?",
                (newstatus,reason,utcnow(),archive_key))
        print("ZIP unlock:",relative,"members:",outcome["inspected"],
              "done:",outcome["done"],"encrypted:",outcome["encrypted"],
              "skipped:",outcome["skipped"],flush=True)
        return outcome
    finally:
        db.close()
