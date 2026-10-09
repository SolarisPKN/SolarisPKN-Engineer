"""SolarisPKN-Engineer localhost web application.

Launch: python server.py ; Windows: INICIAR_ENGINEER.cmd
Scans run in background threads *inside this application process*, not ChatGPT.
Only 127.0.0.1 is bound. Mutations require an ephemeral CSRF header token.
"""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
from pathlib import Path
import secrets
import os
import time
import sqlite3
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser

from workspace import app_folder, register, list_projects, paths, load
from api_extensions import get_extension, post_extension
from integrations import find_profile
from resource_controls import Governor
from engineer import build_index, inside_root
from export_graph import export_graph
from live_view import render_live_map
from ai_analysis import AIConfig, annotate_database, prepare_file
from authorized_unlock import unlock_zip
from credential_vault import save_secret, load_secret, has_secret, delete_secret, supported as vault_supported


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {}
        self.token = secrets.token_urlsafe(36)
        self.running = None
        self.stop_event = threading.Event()

    def snapshot(self, project_id):
        with self.lock:
            job = self.jobs.get(project_id, {})
            return dict(job)

    def begin(self, project_id, operation, data):
        with self.lock:
            if self.stop_event.is_set():
                raise ValueError("Engineer is shutting down")
            if self.running:
                raise ValueError("Already working on project " + self.running)
            self.running = project_id
            self.jobs[project_id] = {
                "operation": operation, "state": "running", "message": "Started",
            }
        thread = threading.Thread(
            target=self._perform, args=(project_id, operation, data),
            name="engineer-" + operation, daemon=True,
        )
        thread.start()

    def _perform(self, project_id, operation, options):
        try:
            meta, db_path, output, log_path = paths(project_id)
            root = Path(meta["root"]).resolve(strict=True)
            governor = Governor(str(options.get("profile", "balanced")))
            governor.priority()
            with log_path.open("a", encoding="utf-8", buffering=1) as logs:
                with redirect_stdout(logs), redirect_stderr(logs):
                    if operation == "scan":
                        build_index(
                            root, db_path, meta["entry"],
                            bool(options.get("all", False)) or not bool(meta["entry"]),
                            int(options.get("workers", 1)),
                            bool(options.get("refresh", False)),
                            int(options.get("max_mb", 0)) * 1024 * 1024,
                            str(options.get("worker_mode", "thread")),
                            governor.mode,
                            self.stop_event,
                        )
                        if options.get("ai") and not self.stop_event.is_set():
                            cfg = build_config(options)
                            annotate_database(db_path, cfg, max_files=int(options.get("max_ai_files", 0)), cancel_event=self.stop_event)
                        if not self.stop_event.is_set():
                            export_graph(db_path, output)
                    elif operation == "annotate":
                        cfg = build_config(options)
                        annotate_database(db_path, cfg, retry_failed=bool(options.get("retry", False)),
                                          max_files=int(options.get("max_ai_files", 0)),
                                          cancel_event=self.stop_event)
                        if not self.stop_event.is_set():
                            export_graph(db_path, output)
                    elif operation == "unlock":
                        secret = options.pop("password", None)
                        remember = bool(options.pop("remember", False))
                        use_saved = bool(options.pop("use_saved", False))
                        credential_id = project_id + ":" + options["relative"]
                        if use_saved:
                            secret = load_secret("archive", credential_id)
                            if secret is None:
                                raise ValueError("No encrypted password was saved for this archive")
                        try:
                            result = unlock_zip(root, db_path, options["relative"], secret, cancel_event=self.stop_event)
                            if remember and result["protected_done"] and not result["encrypted"] and not result["skipped"]:
                                save_secret("archive", credential_id, secret)
                            elif remember:
                                print("Password was NOT saved because some archive entries could not be unlocked.")
                        finally:
                            secret = None
                        if not self.stop_event.is_set():
                            export_graph(db_path, output)
                    elif operation == "export":
                        export_graph(db_path, output)
                    else:
                        raise ValueError("Unsupported operation")
            with self.lock:
                stopped = self.stop_event.is_set()
                self.jobs[project_id] = {
                    "operation": operation,
                    "state": "cancelled" if stopped else "done",
                    "message": "Stopped by tray" if stopped else "Completed",
                }
        except Exception as ex:
            message = (type(ex).__name__ + ": " + str(ex))[:350]
            try:
                _, _, _, log_path = paths(project_id)
                with log_path.open("a", encoding="utf-8") as logs:
                    logs.write("ERROR " + message + "\n")
            except OSError:
                pass
            with self.lock:
                self.jobs[project_id] = {
                    "operation": operation, "state": "failed", "message": message,
                }
        finally:
            with self.lock:
                self.running = None


def build_config(data):
    provider_id = str(data.get("provider_id", "ollama"))
    preset = find_profile(provider_id)
    if preset is None:
        raise ValueError("Select a configured AI provider")
    cfg = AIConfig(provider=preset["protocol"], model=preset["model"],
                   endpoint=preset["endpoint"],
                   api_key_env=preset["key_env"], vault_id=provider_id,
                   language=str(data.get("language", "es")),
                   allow_remote=bool(data.get("allow_remote", False)),
                   max_chars=12000, timeout=120)
    cfg.validate()
    return cfg


def project_status(project_id, state):
    meta, db_path, output, log_path = paths(project_id)
    counts, edges, ai_counts = {}, 0, {}
    if db_path.exists():
        with sqlite3.connect(str(db_path), timeout=2) as db:
            try:
                counts = dict(db.execute("SELECT status,count(*) FROM nodes GROUP BY status"))
                edges = db.execute("SELECT count(*) FROM edges").fetchone()[0]
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_notes'").fetchone():
                    ai_counts = dict(db.execute("SELECT state,count(*) FROM ai_notes GROUP BY state"))
            except sqlite3.OperationalError:
                pass
    recent_log = ""
    if log_path.exists():
        with log_path.open("rb") as logs:
            logs.seek(max(0, logs.seek(0, 2) - 3500))
            recent_log = logs.read().decode("utf-8", errors="replace")
    return {
        "project": meta, "job": state.snapshot(project_id),
        "nodes": counts, "edges": edges, "ai": ai_counts,
        "has_map": (output / "mapa.html").exists(),
        "log": recent_log[-3000:],
    }


def get_directory_node(db_path, key):
    """Read a virtual directory from real indexed file paths only."""
    relative = key.removeprefix("dir:").replace("\\", "/")
    if (not relative or relative.startswith("/") or
            any(p in ("", ".", "..") for p in relative.split("/") if relative != ".")):
        raise ValueError("Invalid virtual directory")
    prefix = "file:" + ("" if relative == "." else relative + "/")
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    children = {}
    statuses = {}
    with sqlite3.connect(str(db_path), timeout=5) as db:
        rows = db.execute("SELECT key,status FROM nodes WHERE kind='file' AND "
                          "key LIKE ? ESCAPE '\\' ORDER BY key", (escaped + "%",))
        for full,status in rows:
            statuses[status] = statuses.get(status, 0) + 1
            remaining = full[len(prefix):]
            parts = remaining.split("/")
            if len(parts) > 1:
                child = "dir:" + ("" if relative == "." else relative + "/") + parts[0]
            else:
                child = full
            if child not in children:
                children[child] = status
            else:
                priority = {"encrypted":0,"failed":1,"pending":2,"running":2,"done":3,"external":4}
                if priority.get(status,2) < priority.get(children[child],3):
                    children[child] = status
    if not statuses:
        raise ValueError("Directory has no indexed files")
    for priority in ("encrypted","failed","pending","running","done"):
        if statuses.get(priority):
            aggregate = "pending" if priority == "running" else priority
            break
    else:
        aggregate = "done"
    if relative == ".":
        incoming = []
    else:
        parent = relative.rpartition("/")[0] or "."
        incoming = [{"source":"dir:" + parent,"relation":"contains","evidence":"filesystem"}]
    outgoing = [{"target":child,"relation":"contains","evidence":"filesystem"}
                for child in sorted(children)[:200]]
    return {"key":key,"kind":"directory","status":aggregate,
            "depth":0 if relative == "." else relative.count("/") + 1,
            "digest":None,"error":None,"incoming":incoming,
            "outgoing":outgoing,"preview":None,"total_children":len(children)}


def get_node(project_id, key):
    meta, db_path, _, _ = paths(project_id)
    if key.startswith("dir:"):
        return get_directory_node(db_path,key)
    with sqlite3.connect(str(db_path), timeout=3) as db:
        db.row_factory = sqlite3.Row
        r = db.execute("SELECT * FROM nodes WHERE key=?", (key,)).fetchone()
        if not r:
            raise ValueError("Node was not found")
        node = dict(r)
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_types'").fetchone():
            typed = db.execute("SELECT language,category,analyzer,evidence,candidates "
                               "FROM file_types WHERE key=?", (key,)).fetchone()
            if typed:
                node["file_type"] = {"language": typed["language"], "category": typed["category"],
                                     "analyzer": typed["analyzer"], "evidence": typed["evidence"],
                                     "candidates": json.loads(typed["candidates"])}
        node["outgoing"] = [dict(x) for x in db.execute(
            "SELECT target,relation,evidence FROM edges WHERE source=? LIMIT 200", (key,))]
        node["incoming"] = [dict(x) for x in db.execute(
            "SELECT source,relation,evidence FROM edges WHERE target=? LIMIT 200", (key,))]
        if key.startswith("file:"):
            relative = key[5:].replace("\\", "/")
            if relative and ".." not in relative.split("/"):
                parent = relative.rpartition("/")[0] or "."
                node["incoming"].append({"source":"dir:"+parent,"relation":"contains",
                                         "evidence":"filesystem"})
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_notes'").fetchone():
            ai = db.execute(
                "SELECT note_json,source_sha256,model,language FROM ai_notes "
                "WHERE node_key=? AND state='done'", (key,)
            ).fetchone()
            if ai and ai["source_sha256"] == node["digest"]:
                node["ai"] = {"commentary": json.loads(ai["note_json"]),
                              "model": ai["model"], "language": ai["language"]}
    if key.startswith("file:"):
        try:
            data = prepare_file(Path(meta["root"]).resolve(), key, [], 4000)
            node["preview"] = data["code"]
            node["truncated"] = data["truncated"]
        except (PermissionError, ValueError, OSError):
            node["preview"] = None
    return node


class Handler(BaseHTTPRequestHandler):
    server_version = "SolarisPKN-Engineer/0.2"
    state = None

    def log_message(self, format, *args):
        # No console required (packaged .exe without console).
        return

    def send(self, code, body, type="application/json; charset=utf-8"):
        raw = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline' data:; connect-src 'self'; frame-ancestors 'self'")
        self.end_headers()
        self.wfile.write(raw)

    def valid_host(self):
        host = self.headers.get("Host", "")
        return host in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

    def do_GET(self):
        if not self.valid_host():
            return self.send(403, json.dumps({"error": "Invalid Host"}))
        url = urlsplit(self.path)
        if url.path == "/__tray/status":
            expected = os.environ.get("ENGINEER_TRAY_CONTROL_TOKEN", "")
            if not expected or not secrets.compare_digest(
                self.headers.get("X-Engineer-Tray-Token", ""), expected
            ):
                return self.json(403, {"error": "Not authorized"})
            with self.state.lock:
                busy = self.state.running
            return self.json(200, {"managed": True, "busy": bool(busy),
                                   "project": busy, "stopping": self.state.stop_event.is_set()})
        query = parse_qs(url.query)
        project = query.get("project", [""])[0]
        try:
            extension = get_extension(url.path, query)
            if extension is not None:
                return self.json(200, extension)
            if url.path == "/":
                html = Path(__file__).with_name("dashboard_v2.html").read_text(encoding="utf-8")
                html = html.replace("/*ENGINEER_TOKEN*/null", json.dumps(self.state.token))
                return self.send(200, html, "text/html; charset=utf-8")
            if url.path == "/api/projects":
                return self.json(200, {"projects": list_projects()})
            if url.path == "/api/vault/status":
                kind = query.get("kind", [""])[0]
                name = query.get("name", [""])[0]
                if kind == "archive":
                    project_id = query.get("project", [""])[0]
                    meta = load(project_id)
                    if not name.startswith("file:") or not name.lower().endswith((".zip", ".jar", ".whl")):
                        raise ValueError("Select a ZIP archive")
                    relative = name[5:]
                    root = Path(meta["root"]).resolve(strict=True)
                    if inside_root(root, root / relative) != relative:
                        raise ValueError("Archive path outside project")
                    identity = project_id + ":" + relative
                elif kind == "ai-provider" and find_profile(name):
                    identity = name
                elif kind == "github" and name == "default":
                    identity = name
                else:
                    raise ValueError("Unknown credential identifier")
                return self.json(200, {"available": has_secret(kind, identity), "supported": vault_supported()})
            if url.path == "/api/status":
                return self.json(200, project_status(project, self.state))
            if url.path == "/api/nodes":
                _, db_path, _, _ = paths(project)
                if not db_path.exists():
                    return self.json(200, {"nodes": [], "total": 0})
                search = query.get("search", [""])[0][:150]
                page = max(0, min(100000, int(query.get("page", ["0"])[0])))
                with sqlite3.connect(str(db_path), timeout=3) as db:
                    predicate = "WHERE key LIKE ?"
                    like = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                    predicate = "WHERE key LIKE ? ESCAPE '\\'"
                    total = db.execute("SELECT count(*) FROM nodes " + predicate, (like,)).fetchone()[0]
                    found = db.execute("SELECT key,kind,status,depth FROM nodes " + predicate +
                                       " ORDER BY id LIMIT 100 OFFSET ?", (like, page * 100)).fetchall()
                return self.json(200, {"nodes": [{"key": r[0], "kind": r[1], "status": r[2],
                                                 "depth": r[3]} for r in found], "total": total})
            if url.path == "/api/node":
                key = query.get("key", [""])[0][:900]
                return self.json(200, get_node(project, key))
            if url.path == "/api/history":
                meta = load(project)
                relative = query.get("file", [""])[0][:500]
                root = Path(meta["root"]).resolve()
                if inside_root(root, root / relative) != relative:
                    raise ValueError("File outside project")
                result = subprocess.run(
                    ["git", "-C", str(root), "log", "--follow", "-n", "30",
                     "--format=%h | %aI | %an | %s", "--", relative],
                    capture_output=True, text=True, timeout=20, check=False)
                return self.json(200, {"history": (result.stdout or result.stderr)[-12000:]})
            if url.path == "/mapa":
                _, db_path, output, _ = paths(project)
                if db_path.is_file():
                    return self.send(200, render_live_map(db_path, Path(__file__).with_name("visualizer.html")), "text/html; charset=utf-8")
                file = output / "mapa.html"
                if file.is_file():
                    return self.send(200, file.read_bytes(), "text/html; charset=utf-8")
                return self.send(200, "<p style='font:16px system-ui;color:white;background:#101a2e;padding:30px'>Ejecutá el análisis para generar el mapa.</p>", "text/html; charset=utf-8")
            return self.json(404, {"error": "Not found"})
        except (OSError, ValueError, KeyError, sqlite3.Error, subprocess.SubprocessError) as ex:
            return self.json(400, {"error": str(ex)[:350]})

    def json(self, status, obj):
        return self.send(status, json.dumps(obj, ensure_ascii=False))

    def do_POST(self):
        if self.valid_host() and urlsplit(self.path).path == "/__tray/shutdown":
            expected = os.environ.get("ENGINEER_TRAY_CONTROL_TOKEN", "")
            if not expected or not secrets.compare_digest(
                self.headers.get("X-Engineer-Tray-Token", ""), expected
            ):
                return self.json(403, {"error": "Not authorized"})
            self.state.stop_event.set()
            def stop_when_ready():
                deadline = time.monotonic() + 35.0
                while time.monotonic() < deadline:
                    with self.state.lock:
                        busy = bool(self.state.running)
                    if not busy:
                        break
                    time.sleep(0.2)
                self.server.shutdown()
            threading.Thread(target=stop_when_ready, daemon=True,
                             name="engineer-tray-shutdown").start()
            return self.json(202, {"accepted": True, "stopping": True})
        if not self.valid_host() or self.headers.get("X-Engineer-Token") != self.state.token:
            return self.json(403, {"error": "Permission denied"})
        length = int(self.headers.get("Content-Length", "0"))
        if not 1 <= length <= 10000:
            return self.json(413, {"error": "Request too large or empty"})
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Invalid JSON payload")
            path = urlsplit(self.path).path
            extension = post_extension(path, body)
            if extension is not None:
                return self.json(200, extension)
            if path == "/api/project":
                record = register(str(body.get("path", "")))
                return self.json(200, {"project": record})
            if path in ("/api/vault/save", "/api/vault/delete"):
                kind = str(body.get("kind", ""))
                name = str(body.get("name", ""))
                if kind == "ai-provider" and find_profile(name):
                    identity = name
                elif kind == "github" and name == "default":
                    identity = name
                elif kind == "archive":
                    project_id = str(body.get("project", ""))
                    meta = load(project_id)
                    if not name.startswith("file:") or not name.lower().endswith((".zip", ".jar", ".whl")):
                        raise ValueError("Select a ZIP archive")
                    relative = name[5:]
                    root = Path(meta["root"]).resolve(strict=True)
                    if inside_root(root, root / relative) != relative:
                        raise ValueError("Archive path outside project")
                    identity = project_id + ":" + relative
                else:
                    raise ValueError("Unknown vault credential target")
                if path.endswith("/save"):
                    if body.get("authorized") is not True:
                        raise ValueError("Explicit authorization required to save this credential")
                    secret = body.pop("secret", None)
                    if not isinstance(secret, str) or not secret:
                        raise ValueError("Secret cannot be empty")
                    save_secret(kind, identity, secret)
                    secret = None
                    return self.json(200, {"saved": True})
                return self.json(200, {"deleted": delete_secret(kind, identity)})
            if path == "/api/unlock-reference":
                if body.get("authorized") is not True:
                    raise ValueError("Explicit permission to analyze this file is required")
                project_id = str(body.get("project", ""))
                meta, db_path, _, _ = paths(project_id)
                key = str(body.get("key", ""))
                if not key.startswith("file:"):
                    raise ValueError("Select an indexed encrypted file")
                if not db_path.is_file():
                    raise ValueError("Index the encrypted file first")
                with sqlite3.connect(str(db_path)) as db:
                    row = db.execute("SELECT status FROM nodes WHERE key=?", (key,)).fetchone()
                    if not row or row[0] != "encrypted":
                        raise ValueError("Only encrypted nodes can register a decrypted reference")
                path_to_plaintext = str(body.get("path", "")).strip()
                if not path_to_plaintext:
                    raise ValueError("Choose the path to your already-decrypted copy")
                derived = register(path_to_plaintext)
                with sqlite3.connect(str(db_path)) as db:
                    with db:
                        target = "external:owner-provided:" + derived["id"]
                        db.execute("INSERT OR IGNORE INTO nodes(key,kind,status,depth,updated) VALUES(?, 'external', 'external', 1, datetime('now'))", (target,))
                        db.execute("INSERT OR REPLACE INTO edges(source,target,relation,evidence) VALUES(?,?,?,?)",
                                   (key, target, "owner-supplied-decryption", "user-declared"))
                return self.json(200, {"project": derived, "message": "Owner-supplied copy registered. Scan it as a separate project."})
            if path in ("/api/scan", "/api/annotate", "/api/export", "/api/unlock"):
                project_id = str(body.get("project", ""))
                load(project_id)
                op = path[len("/api/"):]
                workers = int(body.get("workers", 1))
                if not 1 <= workers <= 32:
                    raise ValueError("Workers must be between 1 and 32")
                if body.get("worker_mode", "thread") not in ("thread", "process"):
                    raise ValueError("Invalid worker mode")
                if not 0 <= int(body.get("max_mb", 0)) <= 2048:
                    raise ValueError("max_mb must be 0 (unlimited) or 1..2048 MiB")
                if body.get("profile", "balanced") not in ("eco", "balanced", "full"):
                    raise ValueError("Unknown performance profile")
                if op == "unlock":
                    if body.get("authorized") is not True:
                        raise ValueError("Explicit authorization by the file owner is required")
                    key = str(body.get("key", ""))
                    if not key.startswith("file:") or not key.lower().endswith((".zip",".jar",".whl")):
                        raise ValueError("This method only supports ZIP-compatible archives")
                    if body.get("use_saved"):
                        if body.get("remember"):
                            raise ValueError("Use saved or store a new credential, not both")
                    elif not isinstance(body.get("password"), str) or not 1 <= len(body["password"]) <= 1024:
                        raise ValueError("Password must contain 1..1024 characters")
                    if body.get("remember") and not vault_supported():
                        raise ValueError("Saving credentials requires Windows DPAPI")
                    original, _, _, _ = paths(project_id)
                    relative = key[5:]
                    root = Path(original["root"]).resolve(strict=True)
                    if inside_root(root, root / relative) != relative:
                        raise ValueError("Archive must be inside the registered project")
                    body["relative"] = relative
                if op == "annotate" or (op == "scan" and body.get("ai")):
                    build_config(body)
                self.state.begin(project_id, op, dict(body, workers=workers))
                return self.json(202, {"accepted": True, "operation": op})
            return self.json(404, {"error": "Unknown action"})
        except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as ex:
            return self.json(400, {"error": str(ex)[:350]})


def main():
    cli = argparse.ArgumentParser(description="SolarisPKN-Engineer browser UI")
    cli.add_argument("--port", type=int, default=8765)
    cli.add_argument("--no-browser", action="store_true")
    args = cli.parse_args()
    if not 1024 <= args.port <= 65535:
        cli.error("Choose a port between 1024 and 65535")
    state = State()
    Handler.state = state
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as http:
        http.daemon_threads = True
        url = f"http://127.0.0.1:{http.server_port}/"
        if not args.no_browser:
            threading.Timer(.8, lambda: webbrowser.open(url)).start()
        try:
            http.serve_forever(poll_interval=.2)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    main()
