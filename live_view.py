"""Bounded live graph snapshots while long scans are running.

No full Markdown export during scanning: the browser can see pending, done,
encrypted and failed nodes directly from SQLite even before the first export.
"""
from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
import sqlite3
from graph_structure import enrich_with_directories
from impact_analysis import attach_usage_labels

MAX_VIEW_NODES = 2200
MAX_VIEW_EDGES = 18000


def graph_snapshot(db_path: Path) -> dict:
    if not db_path.is_file():
        return {"nodes": [], "edges": [], "live": True}
    with closing(sqlite3.connect(str(db_path), timeout=5)) as db:
        db.row_factory = sqlite3.Row
        try:
            row = db.execute("SELECT value FROM metadata WHERE name='project_root'").fetchone()
            root = row[0] if row else ""
            records = db.execute(
                "SELECT id,key,kind,status,depth,digest,error,updated FROM nodes "
                "ORDER BY id LIMIT ?", (MAX_VIEW_NODES,)
            ).fetchall()
            if not records:
                return {"projectRoot": root, "nodes": [], "edges": [], "live": True}
            cutoff = records[-1]["id"]
            nodes = [
                {"id": x["key"], "type": x["kind"], "status": x["status"],
                 "depth": x["depth"], "sha256": x["digest"],
                 "error": x["error"], "updated": x["updated"]}
                for x in records
            ]
            edges = [
                {"from": x[0], "to": x[1], "relation": x[2], "evidence": x[3]}
                for x in db.execute(
                    "SELECT e.source,e.target,e.relation,e.evidence FROM edges e "
                    "JOIN nodes a ON a.key=e.source JOIN nodes b ON b.key=e.target "
                    "WHERE a.id<=? AND b.id<=? ORDER BY a.id LIMIT ?",
                    (cutoff, cutoff, MAX_VIEW_EDGES))
            ]
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_notes'").fetchone():
                latest = db.execute(
                    "SELECT node_key,note_json,source_sha256,model,language FROM ai_notes "
                    "WHERE state='done' AND note_json IS NOT NULL"
                )
                notes = {}
                visible = {n["id"] for n in nodes}
                for key, content, sha, model, lang in latest:
                    if key in visible:
                        notes[key] = (content, sha, model, lang)
                for node in nodes:
                    if node["id"] in notes:
                        content, sha, model, lang = notes[node["id"]]
                        if node["sha256"] == sha:
                            try:
                                node["ai"] = {"content": json.loads(content), "model": model, "language": lang}
                            except (ValueError, TypeError):
                                pass
            nodes,edges = enrich_with_directories(nodes,edges)
            attach_usage_labels(nodes,edges)
            return {"projectRoot": root, "nodes": nodes, "edges": edges, "live": True}
        except sqlite3.OperationalError:
            return {"nodes": [], "edges": [], "live": True}


def status_snapshot(db_path: Path) -> dict:
    if not db_path.is_file():
        return {"statuses": {}, "count": 0}
    with closing(sqlite3.connect(str(db_path), timeout=4)) as db:
        rows = db.execute("SELECT key,status FROM nodes ORDER BY id LIMIT ?", (MAX_VIEW_NODES,)).fetchall()
        total = db.execute("SELECT count(*) FROM nodes").fetchone()[0]
    return {"statuses": dict(rows), "count": total}


def render_live_map(db_path: Path, template_path: Path) -> bytes:
    graph = graph_snapshot(db_path)
    template = template_path.read_text(encoding="utf-8")
    payload = json.dumps(graph, ensure_ascii=False)
    payload = payload.replace("<", "\\u003c").replace("&", "\\u0026")
    return template.replace("/*__ENGINEER_DATA__*/ null", payload).encode("utf-8")
