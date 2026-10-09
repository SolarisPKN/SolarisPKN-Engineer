#!/usr/bin/env python3
"""SolarisPKN-Engineer: incremental, persistent dependency discovery.

Python 3.10+. No third-party dependencies. Read-only analysis of targets.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import hashlib
import mmap
from concurrent.futures import ProcessPoolExecutor
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from typing import Iterable

from binary_readers import inspect_binary
from encrypted_formats import EncryptedContentError, encryption_reason
from iso_reader import scan_iso
from resource_controls import Governor
from web_dependencies import source_references
from language_catalog import classify_file
from static_imports import find_references as extra_static_refs
from project_semantics import semantic_references

SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".next", ".idea", ".vs", ".gradle", "target",
})
SCAN_EXTS = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".java", ".kt", ".cs", ".go",
    ".rs", ".rb", ".php", ".sh", ".ps1", ".html", ".css", ".yaml", ".yml",
    ".toml", ".xml", ".iso", ".exe", ".dll", ".sys", ".ocx", ".so", ".dylib",
    ".astro", ".mdx", ".vue", ".svelte", ".scss", ".sass", ".less", ".svg",
})
MAX_SCAN_BYTES = 32 * 1024 * 1024
TEXT_LIMIT = 4 * 1024 * 1024
DEP_PATTERN = re.compile(
    r"""(?:from\s+["']([^"']+)["']|require\s*\(\s*["']([^"']+)["']\s*\)|import\s*\(\s*["']([^"']+)["']\s*\))"""
)
IMPORT_PATTERN = re.compile(r"""^\s*(?:import|export)\s+.*?\sfrom\s+["']([^"']+)["']""", re.M)
C_INCLUDE = re.compile(r"""^\s*#\s*include\s*([<"])([^>"]+)[>"]""", re.M)
SOURCE_EXTENSIONS = ("", ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json",
                     ".c", ".cc", ".cpp", ".h", ".hpp", ".rs", ".go")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def kind_for(path: Path) -> str:
    return "binary" if path.suffix.lower() in {".exe", ".dll", ".so", ".dylib", ".sys", ".ocx"} else "source"


def inside_root(root: Path, path: Path) -> str | None:
    """Canonicalize and reject directory traversal and symlink escapes."""
    try:
        resolved = path.resolve(strict=False)
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return None


def local_candidate(root: Path, origin: Path, spec: str, *, root_first: bool = False) -> str | None:
    """Find local file from a static reference without touching the network."""
    if not spec or "\x00" in spec or len(spec) > 512:
        return None
    bases = [root, origin.parent] if root_first else [origin.parent, root]
    checked: set[str] = set()
    for base in bases:
        for ext in SOURCE_EXTENSIONS:
            raw = base / (spec + ext)
            for candidate in (raw, raw / "__init__.py", raw / "index.js", raw / "index.ts"):
                local = inside_root(root, candidate)
                if local is None or local in checked:
                    continue
                checked.add(local)
                if candidate.is_file():
                    return local
    return None


def references(root: Path, relative: str, content: bytes) -> list[tuple[str, str, str]]:
    """Return (node_key, relationship, confidence); external nodes never executed."""
    file_path = root / relative
    suffix = file_path.suffix.lower()
    results: list[tuple[str, str, str]] = []

    def add(spec: str, relation: str, confidence: str = "declared",
            mode: str = "path", group: str = "") -> None:
        spec = spec.strip()
        if not spec or spec.startswith(("http://", "https://", "data:")):
            if spec:
                results.append(("external:url:" + spec[:350], "network", "declared"))
            return
        if mode == "python":
            base = file_path.parent if spec.startswith(".") else root
            module = spec.lstrip(".").replace(".", "/")
            if spec.startswith("."):
                levels = len(spec) - len(spec.lstrip("."))
                for _ in range(max(0, levels - 1)):
                    base = base.parent
            found = local_candidate(root, base / "__ref__", module, root_first=False)
            if not found and not spec.startswith("."):
                found = local_candidate(root, file_path, module, root_first=True)
        else:
            found = local_candidate(root, file_path, spec, root_first=False)
        if found is not None:
            results.append(("file:" + found, relation, confidence))
        else:
            results.append(("external:" + (group or mode) + ":" + spec[:350],
                            relation, "probable" if confidence == "heuristic" else confidence))

    is_binary = suffix in {".exe", ".dll", ".sys", ".ocx", ".so", ".dylib"} or (
        content[:2] == b"MZ" or content[:4] == bytes.fromhex("7f454c46") or
        content[:4] in (bytes.fromhex("cefaedfe"), bytes.fromhex("cffaedfe"),
                        bytes.fromhex("feedface"), bytes.fromhex("feedfacf"))
    )
    if is_binary:
        binary_refs = []
        for name, level in inspect_binary(file_path, content):
            local = local_candidate(root, file_path, name)
            target = ("file:" + local) if local is not None else ("external:binary:" + name)
            binary_refs.append((target, "binary-import", level))
        return list(dict.fromkeys(binary_refs))

    if suffix in {".astro", ".mdx", ".html", ".htm", ".js", ".jsx", ".ts", ".tsx",
                  ".mjs", ".cjs", ".css", ".scss", ".sass", ".less", ".vue",
                  ".svelte", ".svg"}:
        return source_references(root, relative, content)

    if suffix in {".json",".webmanifest",".md"}:
        rels=semantic_references(root, relative, content)
        if file_path.name=="package.json":
            try:
                info=json.loads(content.decode("utf-8-sig"))
                for field in ("dependencies","devDependencies","optionalDependencies","peerDependencies"):
                    for pkg,version in (info.get(field) or {}).items():
                        rels.append(("external:npm:"+str(pkg)+"@"+str(version),
                                     "package-manifest","declared"))
            except (ValueError,TypeError,AttributeError) as err:
                raise ValueError("Invalid package.json: "+str(err)[:160]) from err
        return list(dict.fromkeys(rels))

    if suffix == ".py" or suffix == ".pyi":
        try:
            tree = ast.parse(content.decode("utf-8-sig", errors="replace"), filename=relative)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        add(alias.name, "python-import", mode="python")
                elif isinstance(node, ast.ImportFrom):
                    base = "." * node.level + (node.module or "")
                    if node.module:
                        add(base, "python-import", mode="python")
                    for alias in node.names:
                        if alias.name != "*":
                            add(base + ("." if node.module else "") + alias.name,
                                "python-member", "heuristic", "python")
                elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
                    spec = node.args[0].value
                    if not isinstance(spec, str):
                        continue
                    function = node.func
                    if isinstance(function, ast.Name) and function.id == "open":
                        add(spec, "file-open", "declared")
                    elif isinstance(function, ast.Attribute) and function.attr in {"import_module", "__import__"}:
                        add(spec, "dynamic-import", "declared", "python")
                    elif isinstance(function, ast.Name) and function.id == "__import__":
                        add(spec, "dynamic-import", "declared", "python")
        except (SyntaxError, ValueError) as ex:
            raise ValueError("Python syntax cannot be parsed: " + str(ex)[:180]) from ex
        return list(dict.fromkeys(results))

    text = content.decode("utf-8-sig", errors="replace")
    if suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
        for spec in IMPORT_PATTERN.findall(text):
            add(spec, "js-import", mode="path" if spec.startswith(".") else "package")
        for match in DEP_PATTERN.finditer(text):
            spec = next((g for g in match.groups() if g), "")
            add(spec, "js-load", mode="path" if spec.startswith(".") else "package")
    elif suffix in {".c", ".cc", ".cpp", ".h", ".hpp"}:
        for delimiter, spec in C_INCLUDE.findall(text):
            add(spec, "c-include", mode="path" if delimiter == '"' else "system-header")
    elif suffix in {".java", ".kt"}:
        for spec in re.findall(r"^\s*import\s+(?:static\s+)?([\w.*]+)\s*;?", text, re.M):
            add(spec, "java-import", "heuristic", "java")
    elif suffix == ".cs":
        for spec in re.findall(r"^\s*using\s+(?:static\s+)?([\w.]+)\s*;", text, re.M):
            add(spec, "csharp-using", "heuristic", "dotnet")
    elif suffix == ".go":
        for spec in re.findall(r'^\s*(?:import\s+)?(?:\w+\s+)?"([^"]+)"', text, re.M):
            add(spec, "go-import", mode="go")
    elif suffix == ".rs":
        for spec in re.findall(r"^\s*(?:mod|use)\s+([\w:]+)", text, re.M):
            add(spec, "rust-module", "heuristic", "rust")
    elif suffix in {".php", ".rb"}:
        pass  # Resolved by static_imports with language-specific file suffixes.
    elif file_path.name == "package.json":
        try:
            metadata = json.loads(text)
            for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
                for pkg, version in (metadata.get(section) or {}).items():
                    add(str(pkg) + "@" + str(version), "package-manifest", "declared", "npm")
        except (ValueError, AttributeError, TypeError) as ex:
            raise ValueError("Invalid package.json: " + str(ex)[:180]) from ex
    elif file_path.name == "requirements.txt":
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("#") and not s.startswith(("-", ".")):
                add(s.split(";")[0], "package-manifest", "declared", "pip")
    elif file_path.name == "go.mod":
        for dep in re.findall(r"^\s*([\w./-]+\.[\w./-]+)\s+v\d", text, re.M):
            add(dep, "package-manifest", "declared", "go")
    elif suffix == ".toml":
        # Generic metadata: no claim that every TOML string is a dependency.
        if file_path.name == "Cargo.toml":
            in_section = False
            for line in text.splitlines():
                if line.startswith("["):
                    in_section = line.startswith(("[dependencies]", "[dev-dependencies]", "[build-dependencies]"))
                elif in_section and "=" in line and not line.lstrip().startswith("#"):
                    add(line.split("=", 1)[0].strip(), "package-manifest", "declared", "cargo")
    results.extend(extra_static_refs(root, relative, content))
    return list(dict.fromkeys(results))


def stream_large_source(root: Path, relative: str, file: Path) -> list[tuple[str, str, str]]:
    """Heuristic streaming fallback for arbitrarily long sources.

    Bounded chunks and overlap; not equivalent to full-language parsing.
    """
    found: set[tuple[str,str,str]] = set()
    carry = b""
    with file.open("rb") as inp:
        while True:
            data = inp.read(256 * 1024)
            if not data:
                if not carry: break
                chunk = carry; carry = b""
            else:
                chunk = carry + data
                carry = chunk[-4096:]
                if len(chunk) > 4096:
                    chunk = chunk[:-4096]
                else:
                    continue
            if file.suffix.lower() in (".py", ".pyi"):
                text = chunk.decode("utf-8", errors="replace")
                for spec in re.findall(r"(?m)^\s*import\s+([\w.]+)", text):
                    local=local_candidate(root, file, spec.replace(".", "/"), root_first=True)
                    found.add(("file:" + local if local else "external:python:" + spec,
                               "python-import", "heuristic"))
                for spec in re.findall(r"(?m)^\s*from\s+([\w.]+)\s+import\b", text):
                    local=local_candidate(root, file, spec.replace(".", "/"), root_first=True)
                    found.add(("file:" + local if local else "external:python:" + spec,
                               "python-import", "heuristic"))
            else:
                found.update(references(root, relative, chunk))
            if not data: break
    return sorted(found)


def analyze(root: Path, key: str, max_bytes: int) -> tuple[list[tuple[str, str, str]], str]:
    if key.startswith("iso:"):
        return scan_iso(root, key)
    relative = key.removeprefix("file:")
    file = root / relative
    # Never follow links outside root, even when the directory contents changed after enqueue.
    if inside_root(root, file) != relative:
        raise ValueError("File escaped project root")
    size = file.stat().st_size
    if max_bytes and size > max_bytes:
        raise ValueError("Configured maximum scan size exceeded: %s bytes" % size)
    with file.open("rb") as inp:
        magic = inp.read(4)
        inp.seek(0)
        h = hashlib.sha256()
        for chunk in iter(lambda: inp.read(1024 * 1024), b""):
            h.update(chunk)
    digest = h.hexdigest()
    reason = encryption_reason(file)
    if reason:
        raise EncryptedContentError(reason)
    binary = (file.suffix.lower() in {".exe", ".dll", ".sys", ".ocx", ".so", ".dylib"}
              or magic[:2] == b"MZ" or magic == bytes.fromhex("7f454c46")
              or magic in (bytes.fromhex("cefaedfe"), bytes.fromhex("cffaedfe"),
                           bytes.fromhex("feedface"), bytes.fromhex("feedfacf")))
    if file.suffix.lower() == ".iso":
        return [("iso:" + relative + "!/", "iso-root", "declared")], digest
    if binary:
        if size == 0:
            return [], digest
        with file.open("rb") as inp, mmap.mmap(inp.fileno(), 0, access=mmap.ACCESS_READ) as view:
            return references(root, relative, view), digest
    if size > TEXT_LIMIT:
        return stream_large_source(root, relative, file), digest
    contents = file.read_bytes()
    return references(root, relative, contents), digest


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nodes (
  id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
  status TEXT NOT NULL, depth INTEGER NOT NULL DEFAULT 0,
  digest TEXT, error TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS edges (
  source TEXT NOT NULL, target TEXT NOT NULL, relation TEXT NOT NULL,
  evidence TEXT NOT NULL, PRIMARY KEY(source,target,relation)
);
CREATE INDEX IF NOT EXISTS nodes_queue ON nodes(status, depth DESC, id);
CREATE INDEX IF NOT EXISTS edges_target ON edges(target);
CREATE TABLE IF NOT EXISTS file_types (
 key TEXT PRIMARY KEY, language TEXT NOT NULL, category TEXT NOT NULL,
 analyzer TEXT NOT NULL, evidence TEXT NOT NULL, candidates TEXT NOT NULL
);
"""


def database(path: Path, root: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path))
    db.executescript(SCHEMA)
    stored = db.execute("SELECT value FROM metadata WHERE name='project_root'").fetchone()
    if stored and stored[0] != str(root):
        raise ValueError("Database belongs to another project: " + stored[0])
    db.execute("INSERT OR IGNORE INTO metadata VALUES('project_root',?)", (str(root),))
    db.execute("UPDATE nodes SET status='pending' WHERE status='running'")
    db.commit()
    return db


def enqueue(db: sqlite3.Connection, key: str, depth: int) -> None:
    kind = "external" if key.startswith("external:") else ("iso" if key.startswith("iso:") else "file")
    status = "external" if kind == "external" else "pending"
    db.execute("INSERT OR IGNORE INTO nodes(key,kind,status,depth,updated) VALUES(?,?,?,?,?)",
               (key, kind, status, depth, utcnow()))


def choose(db: sqlite3.Connection) -> tuple[str, int] | None:
    # Deepest discovered dependency first: depth-first order with one worker.
    row = db.execute("SELECT key,depth FROM nodes WHERE status='pending' ORDER BY depth DESC,id ASC LIMIT 1").fetchone()
    if row:
        db.execute("UPDATE nodes SET status='running',updated=? WHERE key=?", (utcnow(), row[0]))
    return (str(row[0]), int(row[1])) if row else None


def list_source_files(root: Path) -> Iterable[str]:
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and
                         inside_root(root, Path(folder) / d) is not None)
        for name in sorted(files):
            path = Path(folder) / name
            if path.is_file():
                local = inside_root(root, path)
                if local:
                    yield local


def build_index(root: Path, db_path: Path, entry: str | None, all_files: bool,
                workers: int, refresh: bool, max_bytes: int, worker_mode: str = "thread", profile: str = "balanced", cancel_event=None) -> None:
    root = root.resolve(strict=True)
    db = database(db_path, root)
    try:
        if refresh:
            db.execute("UPDATE nodes SET status='pending' WHERE kind IN ('file','iso')")
        if entry:
            file = (root / entry).resolve(strict=True)
            relative = inside_root(root, file)
            if relative is None or not file.is_file():
                raise ValueError("Entry must be a file inside the project")
            enqueue(db, "file:" + relative, 0)
        if all_files:
            for name in list_source_files(root):
                if cancel_event is not None and cancel_event.is_set():
                    break
                enqueue(db, "file:" + name, 0)
        db.commit()
        if worker_mode not in ("thread", "process"):
            raise ValueError("worker_mode must be thread or process")
        governor = Governor(profile)
        futures = {}
        executor_type = ProcessPoolExecutor if worker_mode == "process" and workers > 1 else ThreadPoolExecutor
        with executor_type(max_workers=workers) as pool:
            while True:
                if cancel_event is not None and cancel_event.is_set() and not futures:
                    break
                while len(futures) < workers and not (cancel_event is not None and cancel_event.is_set()):
                    claimed = choose(db)
                    if not claimed:
                        break
                    key, depth = claimed
                    db.commit()
                    futures[pool.submit(analyze, root, key, max_bytes)] = (key, depth)
                if not futures:
                    break
                ready, _ = wait(tuple(futures), timeout=0.3, return_when=FIRST_COMPLETED)
                for task in ready:
                    key, depth = futures.pop(task)
                    try:
                        discovered, digest = task.result()
                        # Atomic node commit: recover from crashes without half-written edges.
                        with db:
                            db.execute("DELETE FROM edges WHERE source=?", (key,))
                            for target, relation, confidence in discovered:
                                db.execute("INSERT OR REPLACE INTO edges VALUES (?,?,?,?)",
                                           (key, target, relation, confidence))
                                enqueue(db, target, depth + 1)
                            db.execute("UPDATE nodes SET status='done',digest=?,error=NULL,updated=? WHERE key=?",
                                       (digest, utcnow(), key))
                            if key.startswith("file:"):
                                detected = classify_file(root / key[5:])
                                db.execute("INSERT OR REPLACE INTO file_types "
                                           "(key,language,category,analyzer,evidence,candidates) "
                                           "VALUES(?,?,?,?,?,?)",
                                           (key,detected["language"],detected["category"],
                                            detected["analyzer"],detected["evidence"],
                                            json.dumps(detected["candidates"],ensure_ascii=False)))
                        print("OK", key, "->", len(discovered), "references", flush=True)
                        governor.pause()
                    except EncryptedContentError as ex:
                        with db:
                            db.execute("UPDATE nodes SET status='encrypted',error=?,updated=? WHERE key=?",
                                       (str(ex)[:450], utcnow(), key))
                        print("ENCRYPTED", key, str(ex)[:160], flush=True)
                    except Exception as ex:
                        with db:
                            db.execute("UPDATE nodes SET status='failed',error=?,updated=? WHERE key=?",
                                       (str(ex)[:450], utcnow(), key))
                        print("ERROR", key, str(ex)[:160], file=sys.stderr, flush=True)
        print("Index:", db_path)
        print("Nodes:", db.execute("SELECT status,count(*) FROM nodes GROUP BY status").fetchall())
        print("Edges:", db.execute("SELECT count(*) FROM edges").fetchone()[0])
    finally:
        db.close()


def show_stats(db_path: Path) -> None:
    db = sqlite3.connect(str(db_path))
    try:
        print("Nodes:", db.execute("SELECT status,count(*) FROM nodes GROUP BY status").fetchall())
        print("Relations:", db.execute("SELECT relation,count(*) FROM edges GROUP BY relation ORDER BY count(*) DESC").fetchall())
        for row in db.execute("SELECT key,error FROM nodes WHERE status='failed' LIMIT 15"):
            print("FAILED:", *row)
    finally:
        db.close()


def file_history(root: Path, relative: str) -> None:
    if inside_root(root, root / relative) != relative:
        raise ValueError("File must be inside project")
    proc = subprocess.run(
        ["git", "-C", str(root), "log", "--follow", "-n", "40",
         "--format=%h | %aI | %an | %s", "--", relative],
        capture_output=True, text=True, timeout=30, check=False,
    )
    print(proc.stdout or proc.stderr)


def main() -> None:
    cli = argparse.ArgumentParser(description="SolarisPKN-Engineer / progressive dependency graph")
    sub = cli.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="Index dependencies recursively")
    scan.add_argument("root", type=Path, help="Project directory, read-only")
    scan.add_argument("--entry", help="Initial file relative to root")
    scan.add_argument("--all", action="store_true", help="Seed every eligible file")
    scan.add_argument("--db", type=Path, default=Path("engineer.sqlite"))
    scan.add_argument("--workers", type=int, default=1)
    scan.add_argument("--worker-mode", choices=("thread", "process"), default="thread")
    scan.add_argument("--refresh", action="store_true", help="Re-analyze known source files")
    scan.add_argument("--max-mb", type=int, default=0)
    scan.add_argument("--profile", choices=("eco","balanced","full"), default="balanced")
    stats = sub.add_parser("stats")
    stats.add_argument("--db", type=Path, default=Path("engineer.sqlite"))
    history = sub.add_parser("history")
    history.add_argument("root", type=Path)
    history.add_argument("file", help="Relative path")
    export = sub.add_parser("export")
    export.add_argument("--db", type=Path, default=Path("engineer.sqlite"))
    export.add_argument("--output", type=Path, default=Path("salida"))
    annotate = sub.add_parser("annotate", help="Explain indexed files with optional AI")
    annotate.add_argument("--db", type=Path, default=Path("engineer.sqlite"))
    for target in (scan, annotate):
        target.add_argument("--ai-provider", choices=("ollama", "openai"), default="ollama")
        target.add_argument("--ai-model", default="qwen2.5-coder:7b")
        target.add_argument("--ai-url", default="http://127.0.0.1:11435/api/chat")
        target.add_argument("--ai-key-env", default="ENGINEER_AI_API_KEY")
        target.add_argument("--ai-language", choices=("es", "en"), default="es")
        target.add_argument("--ai-max-chars", type=int, default=12000)
        target.add_argument("--ai-timeout", type=float, default=120.0)
        target.add_argument("--ai-max-files", type=int, default=0)
        target.add_argument("--ai-retry-failed", action="store_true")
        target.add_argument("--allow-remote-ai", action="store_true")
    scan.add_argument("--ai", action="store_true", help="Annotate scanned files after graph indexing")

    args = cli.parse_args()

    def ai_options():
        from ai_analysis import AIConfig
        return AIConfig(provider=args.ai_provider, model=args.ai_model,
                        endpoint=args.ai_url, api_key_env=args.ai_key_env,
                        language=args.ai_language, max_chars=args.ai_max_chars,
                        timeout=args.ai_timeout, allow_remote=args.allow_remote_ai)

    def run_ai():
        from ai_analysis import annotate_database
        return annotate_database(args.db, ai_options(),
                                 retry_failed=args.ai_retry_failed,
                                 max_files=max(0, args.ai_max_files))
    if args.command == "scan":
        if not args.entry and not args.all:
            cli.error("Specify --entry FILE or --all")
        build_index(args.root, args.db, args.entry, args.all,
                    max(1, min(args.workers, 32)), args.refresh,
                    max(0, args.max_mb) * 1024 * 1024, args.worker_mode, args.profile)
        if args.ai:
            run_ai()
    elif args.command == "annotate":
        run_ai()
    elif args.command == "stats":
        show_stats(args.db)
    elif args.command == "history":
        file_history(args.root.resolve(strict=True), args.file)
    elif args.command == "export":
        from export_graph import export_graph
        export_graph(args.db, args.output)


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    main()
