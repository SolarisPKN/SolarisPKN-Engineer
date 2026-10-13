"""Opt-in public-web and local-file change observer for SolarisPKN-Engineer.

Stdlib-first, with optional BLAKE3 support. Read-only; never executes watched content.
External fetches are restricted to global public IPs and pinned sockets (SSRF defense).
All observations stay in Engineer/.private/, which is ignored by Git.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from http.client import HTTPConnection, HTTPSConnection
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import sqlite3
import threading
from urllib.parse import quote, urlsplit, urlunsplit

from workspace import app_folder
from change_history import format_diff, git_file_history, text_snapshots
from github_provenance import github_deployment_evidence, repo_name

MAX_RESPONSE = 1_000_000
MAX_LOCAL_FILE = 32 * 1024 * 1024
MAX_FILES = 8000
MAX_VERIFY_FILE = 32 * 1024 * 1024 * 1024  # 32 GiB, explicitly requested one-off verification only
ALGORITHMS = {"sha256": 64, "sha512": 128, "blake3": 64, "md5": 32}
MIN_INTERVAL = 60
EXCLUDED_DIRS = {".git", ".private", "__pycache__", "node_modules", ".venv", ".venv-engineer-build"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_hasher(algorithm: str = "sha256"):
    """Instantiate the selected algorithm; never silently substitute another hash."""
    if not isinstance(algorithm, str) or algorithm not in ALGORITHMS:
        raise ValueError("Algoritmo no admitido: elegí SHA-256, SHA-512, BLAKE3 o MD5")
    if algorithm == "blake3":
        try:
            from blake3 import blake3
        except ImportError as exc:
            raise ValueError("BLAKE3 no está instalado: ejecutá 'python -m pip install blake3'") from exc
        return blake3()
    if algorithm == "md5":
        # MD5 is for interoperability, not a security/integrity trust decision.
        try:
            return hashlib.md5(usedforsecurity=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("MD5 no está disponible en este entorno") from exc
    return hashlib.new(algorithm)


def digest(data: bytes, algorithm: str = "sha256") -> str:
    hasher = new_hasher(algorithm)
    hasher.update(data)
    return hasher.hexdigest()


def digest_file(path: Path, algorithm: str = "sha256") -> str:
    """Stream the entire file without buffering it in memory."""
    hasher = new_hasher(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def algorithm_status() -> dict:
    try:
        new_hasher("blake3")
        blake3_ok = True
    except ValueError:
        blake3_ok = False
    return {"algorithms": [
        {"id": name, "available": name != "blake3" or blake3_ok,
         "digest_length": hex_digits,
         "legacy": name == "md5"}
        for name, hex_digits in ALGORITHMS.items()
    ]}


def verify_local_file(path: str, expected: str, algorithm: str = "sha256") -> dict:
    """Explicit, one-off whole-file checksum verification, including large ISO images.

    No file content is stored or uploaded; expected digests must be from a trusted source.
    """
    kind, filename = target_kind(path)
    if kind != "local":
        raise ValueError("La verificación de descarga requiere un archivo local")
    selected = Path(filename)
    if not selected.is_file():
        raise ValueError("Elegí un archivo, no una carpeta")
    new_hasher(algorithm)
    if not isinstance(expected, str) or not re.fullmatch(
            r"[0-9a-fA-F]{" + str(ALGORITHMS[algorithm]) + r"}", expected.strip()):
        raise ValueError("La huella esperada debe tener " +
                         str(ALGORITHMS[algorithm]) + " dígitos hexadecimales para " + algorithm.upper())
    size = selected.stat().st_size
    if size > MAX_VERIFY_FILE:
        raise ValueError("Archivo demasiado grande para la verificación manual (máximo 32 GiB)")
    actual = digest_file(selected, algorithm)
    expected = expected.strip().lower()
    return {"file": str(selected), "algorithm": algorithm, "size": size,
            "expected": expected, "actual": actual, "matches": actual == expected,
            "warning": "MD5 no protege frente a colisiones intencionales" if algorithm == "md5" else None}


def target_kind(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError("Especificá una URL o una ruta local válida (hasta 2048 caracteres)")
    value = value.strip()
    if value.lower().startswith(("http://", "https://")):
        url = urlsplit(value)
        if (url.scheme not in ("http", "https") or not url.hostname
                or url.username is not None or url.password is not None
                or url.port not in (None, 80, 443) or "\\" in value
                or any(ord(c) < 32 for c in value)):
            raise ValueError("Solo URLs HTTP(S) públicas sin credenciales ni puertos privados")
        if (url.scheme == "https" and url.port == 80) or (url.scheme == "http" and url.port == 443):
            raise ValueError("Puerto incompatible con el protocolo")
        host = url.hostname.rstrip(".").lower()
        if not host or host == "localhost" or host.endswith((".local", ".internal", ".localhost", ".test", ".invalid")):
            raise ValueError("Los hosts locales o reservados están bloqueados")
        return "web", urlunsplit((url.scheme, url.netloc.lower(), url.path or "/", url.query, ""))
    if "://" in value:
        raise ValueError("Protocolo no admitido")
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_file() and not path.is_dir():
        raise ValueError("La ruta no es archivo ni carpeta")
    if path.is_symlink():
        raise ValueError("No se siguen enlaces simbólicos")
    private = (app_folder() / ".private").resolve()
    if path == private or private in path.parents:
        raise ValueError("No se monitorea la base de datos privada de Engineer")
    return "local", str(path)


def public_ip_for(host: str, port: int) -> str:
    """Resolve once, reject mixed public/private answers and use the vetted IP for TCP."""
    try:
        parsed = ipaddress.ip_address(host)
        ips = [parsed]
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,253}", host):
            raise ValueError("Nombre de host inválido")
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ips = [ipaddress.ip_address(item[4][0]) for item in infos]
    if not ips or not all(ip.is_global for ip in ips):
        raise ValueError("Destino no público o resolución DNS insegura")
    return str(ips[0])


class _PinnedHTTP(HTTPConnection):
    def __init__(self, host: str, port: int, ip: str):
        super().__init__(host, port, timeout=8)
        self._approved_ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._approved_ip, self.port), self.timeout)


class _PinnedHTTPS(HTTPSConnection):
    def __init__(self, host: str, port: int, ip: str):
        super().__init__(host, port, timeout=8, context=ssl.create_default_context())
        self._approved_ip = ip

    def connect(self):
        raw = socket.create_connection((self._approved_ip, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def fetch_public(url: str, etag: str | None = None, modified: str | None = None) -> dict:
    kind, url = target_kind(url)
    if kind != "web":
        raise ValueError("Se esperaba una URL")
    parts = urlsplit(url)
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    ip = public_ip_for(host, port)
    conn = (_PinnedHTTPS if parts.scheme == "https" else _PinnedHTTP)(host, port, ip)
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parts.query:
        path += "?" + quote(parts.query, safe="/%:@!$&'()*+,;=?-._~")
    headers = {"User-Agent": "SolarisPKN-Engineer-ChangeObserver/0.1",
               "Accept": "text/html,application/json,text/plain,*/*;q=0.5",
               "Accept-Encoding": "identity", "Cache-Control": "no-cache"}
    if etag:
        headers["If-None-Match"] = etag[:500]
    if modified:
        headers["If-Modified-Since"] = modified[:150]
    try:
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        status = response.status
        if status == 304:
            return {"status": 304}
        if status != 200:
            # Explicitly never follow redirects: they might lead into a private network.
            raise ValueError("Respuesta HTTP " + str(status) + " (redirecciones no seguidas)")
        if response.getheader("Content-Encoding", "identity").lower() not in ("identity", ""):
            raise ValueError("El servidor ignoró Accept-Encoding: identity")
        if int(response.getheader("Content-Length") or "0") > MAX_RESPONSE:
            raise ValueError("Recurso mayor al límite de 1 MB")
        body = response.read(MAX_RESPONSE + 1)
        if len(body) > MAX_RESPONSE:
            raise ValueError("Recurso mayor al límite de 1 MB")
        return {"status": 200, "body": body,
                "type": (response.getheader("Content-Type") or "").lower(),
                "etag": (response.getheader("ETag") or "")[:500],
                "modified": (response.getheader("Last-Modified") or "")[:150]}
    finally:
        conn.close()


class _VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.words = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.words.append(data)


def visible_content(body: bytes, content_type: str) -> str:
    text = body.decode("utf-8-sig", errors="replace")
    if "html" in content_type or text.lstrip().lower().startswith(("<!doctype html", "<html")):
        parser = _VisibleHTML()
        parser.feed(text)
        text = " ".join(parser.words)
    elif "json" in content_type:
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False, sort_keys=True)
        except json.JSONDecodeError:
            pass
    return " ".join(text.split())


def local_manifest(path: str, algorithm: str = "sha256") -> tuple[str, dict]:
    """Read-only snapshot. Oversized files use metadata markers, never mislabeled hashes."""
    root = Path(path)
    files = {}
    if root.is_file():
        candidates = [(root.name, root)]
    else:
        candidates = []
        for directory, subdirs, names in os.walk(root, followlinks=False):
            subdirs[:] = sorted(d for d in subdirs if d not in EXCLUDED_DIRS
                                and not (Path(directory) / d).is_symlink())
            for name in sorted(names):
                current = Path(directory) / name
                if not current.is_symlink() and current.is_file():
                    candidates.append((current.relative_to(root).as_posix(), current))
                    if len(candidates) > MAX_FILES:
                        raise ValueError("Demasiados archivos: restringí el directorio (límite 8000)")
    for relative, current in candidates:
        stat = current.stat()
        if stat.st_size > MAX_LOCAL_FILE:
            marker = "metadata-only:" + str(stat.st_size) + ":" + str(stat.st_mtime_ns)
        else:
            marker = algorithm + ":" + digest_file(current, algorithm)
        files[relative] = marker
    encoded = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return digest(encoded, algorithm), files


class ChangeMonitor:
    def __init__(self, database: str | Path | None = None):
        self.path = Path(database) if database is not None else app_folder() / ".private" / "change-monitor.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.active = set()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS targets(
                  id INTEGER PRIMARY KEY, target TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
                  interval_seconds INTEGER NOT NULL, last_sha TEXT, last_semantic TEXT,
                  last_sample TEXT, last_manifest TEXT, etag TEXT, modified TEXT,
                  checked_at TEXT, next_check TEXT, last_state TEXT, last_error TEXT,
                  algorithm TEXT NOT NULL DEFAULT 'sha256',
                  record_diffs INTEGER NOT NULL DEFAULT 0,
                  last_texts TEXT,
                  github_repo TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS events(
                  id INTEGER PRIMARY KEY, target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
                  at TEXT NOT NULL, type TEXT NOT NULL, summary TEXT NOT NULL, details TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_target_id ON events(target_id,id);
            """)
            # In-place migration: existing SHA-256 reference values remain valid.
            columns = {row[1] for row in db.execute("PRAGMA table_info(targets)")}
            if "algorithm" not in columns:
                db.execute("ALTER TABLE targets ADD COLUMN algorithm TEXT NOT NULL DEFAULT 'sha256'")
            if "record_diffs" not in columns:
                db.execute("ALTER TABLE targets ADD COLUMN record_diffs INTEGER NOT NULL DEFAULT 0")
            if "last_texts" not in columns:
                db.execute("ALTER TABLE targets ADD COLUMN last_texts TEXT")
            if "github_repo" not in columns:
                db.execute("ALTER TABLE targets ADD COLUMN github_repo TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def connect(self):
        """Commit/rollback transactions and close the handle on Windows and POSIX."""
        db = sqlite3.connect(str(self.path), timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=10000")
            with db:
                yield db
        finally:
            db.close()

    def add(self, target: str, interval: int = 300, algorithm: str = "sha256",
            record_diffs: bool = False, github_repo: str = "") -> dict:
        kind, normalized = target_kind(target)
        new_hasher(algorithm)  # Reject unavailable BLAKE3 before persisting any changes.
        if not isinstance(github_repo, str):
            raise ValueError("Repositorio GitHub inválido")
        if github_repo:
            repo_name(github_repo)
        if github_repo and kind != "web":
            raise ValueError("Asociación de despliegue GitHub: solo para URLs web")
        if github_repo and not normalized.startswith("https://"):
            raise ValueError("Para asociar un deployment GitHub se requiere HTTPS")
        if not isinstance(record_diffs, bool):
            raise ValueError("La opción de diffs debe ser verdadera o falsa")
        if isinstance(interval, bool) or not isinstance(interval, int) or not MIN_INTERVAL <= interval <= 86400:
            raise ValueError("Intervalo válido: 60 a 86400 segundos")
        with self.lock, self.connect() as db:
            previous = db.execute("SELECT id, algorithm, record_diffs FROM targets WHERE target=?", (normalized,)).fetchone()
            if previous is None:
                db.execute("INSERT INTO targets(target,kind,interval_seconds,algorithm,record_diffs,github_repo,next_check,last_state)"
                           " VALUES(?,?,?,?,?,?,?,?)",
                           (normalized, kind, interval, algorithm, int(record_diffs), github_repo, now(), "pending"))
            elif previous["id"] in self.active:
                raise ValueError("No se puede cambiar la configuración mientras se está calculando")
            elif previous["algorithm"] != algorithm:
                # Never compare hashes generated by different algorithms; create a fresh baseline.
                db.execute("""
                    UPDATE targets SET interval_seconds=?, algorithm=?, record_diffs=?,
                      github_repo=?, next_check=?, last_state='pending', last_sha=NULL,
                      last_semantic=NULL, last_sample=NULL, last_manifest=NULL,
                      last_texts=NULL, etag=NULL, modified=NULL, checked_at=NULL,
                      last_error=NULL WHERE id=?
                """, (interval, algorithm, int(record_diffs), github_repo, now(), previous["id"]))
                db.execute("INSERT INTO events(target_id,at,type,summary,details) VALUES(?,?,?,?,?)",
                           (previous["id"], now(), "algorithm",
                            "Se cambió el algoritmo; se requiere una nueva referencia",
                            json.dumps({"from": previous["algorithm"], "to": algorithm})))
            else:
                db.execute("""UPDATE targets SET interval_seconds=?, record_diffs=?,
                    github_repo=?, last_texts=CASE WHEN ?=0 THEN NULL ELSE last_texts END
                    WHERE id=?""",
                    (interval, int(record_diffs), github_repo, int(record_diffs), previous["id"]))
            row = db.execute("SELECT * FROM targets WHERE target=?", (normalized,)).fetchone()
            return self._safe(row)

    @staticmethod
    def _safe(row) -> dict:
        result = dict(row)
        result.pop("last_manifest", None)
        result.pop("last_sample", None)
        result.pop("last_texts", None)
        result["record_diffs"] = bool(result.get("record_diffs"))
        return result

    def targets(self) -> list[dict]:
        with self.lock, self.connect() as db:
            return [self._safe(row) for row in db.execute("SELECT * FROM targets ORDER BY id")]

    def events(self, limit: int = 50) -> list[dict]:
        with self.lock, self.connect() as db:
            rows = db.execute("SELECT e.id,e.at,e.type,e.summary,e.details,t.target "
                              "FROM events e JOIN targets t ON t.id=e.target_id "
                              "ORDER BY e.id DESC LIMIT ?", (max(1, min(100, limit)),))
            return [dict(row) for row in rows]

    def remove(self, target_id: int) -> dict:
        with self.lock, self.connect() as db:
            db.execute("DELETE FROM targets WHERE id=?", (target_id,))
            return {"removed": db.total_changes > 0}

    def due(self, limit: int = 3) -> list[int]:
        with self.lock, self.connect() as db:
            return [r[0] for r in db.execute(
                "SELECT id FROM targets WHERE next_check<=? ORDER BY next_check LIMIT ?",
                (now(), max(1, min(10, limit))))]

    def check(self, target_id: int) -> dict:
        with self.lock:
            if target_id in self.active:
                return {"id": target_id, "state": "busy"}
            with self.connect() as db:
                row = db.execute("SELECT * FROM targets WHERE id=?", (target_id,)).fetchone()
                if row is None:
                    raise ValueError("Objetivo desconocido")
                previous = dict(row)
            self.active.add(target_id)
        update = {}
        state = "unchanged"
        summary = ""
        details = {}
        try:
            algorithm = previous["algorithm"]
            if previous["kind"] == "web":
                fetched = fetch_public(previous["target"], previous["etag"], previous["modified"])
                if fetched["status"] == 304:
                    if previous["last_sha"] is None:
                        raise ValueError("304 sin una versión anterior")
                else:
                    body = fetched["body"]
                    new_sha = digest(body, algorithm)
                    canonical = visible_content(body, fetched["type"])
                    semantic = digest(canonical.encode("utf-8"), algorithm)
                    sample = canonical[:12000] if previous["record_diffs"] else canonical[:1600]
                    update = {"last_sha": new_sha, "last_semantic": semantic,
                              "last_sample": sample, "etag": fetched["etag"],
                              "modified": fetched["modified"]}
                    if previous["last_sha"] is None:
                        state = "baseline"
                        summary = "Se registró la primera versión de la web"
                    elif new_sha != previous["last_sha"]:
                        state = "content" if semantic != previous["last_semantic"] else "technical"
                        summary = ("Cambió el contenido visible" if state == "content"
                                   else "Cambió el recurso, sin cambios de texto detectados")
                        details = {"before": previous["last_sha"][:16],
                                   "after": new_sha[:16],
                                   "previous_excerpt": (previous["last_sample"] or "")[:350],
                                   "current_excerpt": sample[:350]}
                        if previous["record_diffs"]:
                            previous_text = previous["last_sample"] or ""
                            details["diff"] = format_diff(previous_text, sample, web=True)
                            details["diff_note"] = ("Texto HTTP extraído, no DOM JavaScript; "
                                                    "puede estar truncado a 12000 caracteres")
                        if previous["github_repo"]:
                            # Correlation is explicitly configured; failures do not break hashing.
                            try:
                                details["deployment"] = github_deployment_evidence(
                                    previous["github_repo"], previous["target"])
                            except (ValueError, OSError, TimeoutError) as exc:
                                details["deployment"] = {
                                    "matches": [], "verified_content": False,
                                    "note": "No se pudo verificar el historial de deployments GitHub: " +
                                            str(exc)[:180]}
            else:
                new_sha, manifest = local_manifest(previous["target"], algorithm)
                update = {"last_sha": new_sha,
                          "last_manifest": json.dumps(manifest, ensure_ascii=False, sort_keys=True)}
                before = json.loads(previous["last_manifest"] or "{}")
                added = sorted(manifest.keys() - before.keys())
                removed = sorted(before.keys() - manifest.keys())
                changed = sorted(k for k in manifest.keys() & before.keys() if manifest[k] != before[k])
                if previous["record_diffs"]:
                    selection = list(dict.fromkeys(changed + added + list(manifest)))
                    snapshots = text_snapshots(previous["target"], selection)
                    update["last_texts"] = json.dumps(snapshots, ensure_ascii=False)
                if previous["last_sha"] is None:
                    state = "baseline"
                    summary = "Se registró el estado inicial de los archivos"
                elif new_sha != previous["last_sha"]:
                    state = "files"
                    details = {"added": added[:30], "removed": removed[:30], "modified": changed[:30],
                               "counts": {"added": len(added), "removed": len(removed), "modified": len(changed)}}
                    summary = f"Archivos: +{len(added)} / -{len(removed)} / modificados {len(changed)}"
                    paths = (changed + added + removed)[:12]
                    details["git"] = git_file_history(previous["target"], paths)
                    if previous["record_diffs"]:
                        old_texts = json.loads(previous["last_texts"] or "{}")
                        new_texts = json.loads(update["last_texts"])
                        shown = []
                        for filename in paths:
                            if filename in old_texts or filename in new_texts:
                                # Existing files need both snapshots; new/deleted files use an empty side.
                                if filename in changed and (filename not in old_texts or filename not in new_texts):
                                    continue
                                before_text = old_texts.get(filename, "")
                                after_text = new_texts.get(filename, "")
                                patch = format_diff(before_text, after_text)
                                if patch:
                                    shown.append({"file": filename, "patch": patch})
                        details["diffs"] = shown
                        details["diff_note"] = ("Extractos de texto guardados en .private, máximo "
                                                "60 archivos de hasta 8 KiB; los demás solo muestran hashes")
        except (OSError, ValueError, UnicodeError, socket.timeout, ssl.SSLError) as error:
            state = "error"
            summary = (str(error) or type(error).__name__)[:250]
        finally:
            with self.lock:
                self.active.discard(target_id)
        checked = now()
        next_check = (datetime.now(timezone.utc) + timedelta(seconds=previous["interval_seconds"])).isoformat(timespec="seconds")
        with self.lock, self.connect() as db:
            set_fields = {"checked_at": checked, "next_check": next_check,
                          "last_state": state, "last_error": summary if state == "error" else None}
            if state != "error":
                set_fields.update(update)
            assignments = ", ".join(f"{key}=?" for key in set_fields)
            db.execute("UPDATE targets SET " + assignments + " WHERE id=?",
                       (*set_fields.values(), target_id))
            if state in ("baseline", "content", "technical", "files", "error"):
                event_details = dict(details)
                event_details["algorithm"] = previous["algorithm"]
                db.execute("INSERT INTO events(target_id,at,type,summary,details) VALUES(?,?,?,?,?)",
                           (target_id, checked, state, summary, json.dumps(event_details, ensure_ascii=False)))
            return {"id": target_id, "algorithm": previous["algorithm"], "state": state,
                    "message": summary, "checked_at": checked,
                    "next_check": next_check, "details": details}


_instance = None
_instance_lock = threading.Lock()


def get_monitor() -> ChangeMonitor:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = ChangeMonitor()
        return _instance


def background_loop(stop_event: threading.Event) -> None:
    """Opt-in targets only; no network operation occurs until a target is added."""
    monitor = get_monitor()
    while not stop_event.wait(10):
        for target_id in monitor.due(limit=3):
            if stop_event.is_set():
                return
            monitor.check(target_id)
