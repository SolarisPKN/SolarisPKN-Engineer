"""Export the Engineer SQLite graph to portable JSON, HTML and Obsidian Markdown."""
from __future__ import annotations
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
from graph_structure import enrich_with_directories
from graph_diagnostics import audit_graph
from impact_analysis import attach_usage_labels


def note_name(key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    short = key.rsplit("/", 1)[-1].replace("|", "-").replace("[", "").replace("]", "")
    short = short.replace("\\", "-").replace(":", "-").replace("#", "-")
    return (short[:65] or "unknown") + "-" + digest


def export_graph(db_path: Path, output: Path) -> None:
    if not db_path.is_file():
        raise FileNotFoundError("Run scan first: " + str(db_path))
    output.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_path))
    try:
        root_row = db.execute("SELECT value FROM metadata WHERE name='project_root'").fetchone()
        root = root_row[0] if root_row else "(unknown)"
        nodes = [
            {"id": r[0], "type": r[1], "status": r[2],
             "depth": r[3], "sha256": r[4], "error": r[5], "updated": r[6]}
            for r in db.execute("SELECT key,kind,status,depth,digest,error,updated FROM nodes ORDER BY id")
        ]
        edges = [
            {"from": r[0], "to": r[1], "relation": r[2], "evidence": r[3]}
            for r in db.execute("SELECT source,target,relation,evidence FROM edges ORDER BY source,target")
        ]
        ai_notes = {}
        node_hashes = {node["id"]: node["sha256"] for node in nodes}
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_notes'").fetchone():
            for key, raw, source_sha, model, lang in db.execute(
                "SELECT node_key,note_json,source_sha256,model,language FROM ai_notes "
                "WHERE state='done' AND note_json IS NOT NULL"):
                try:
                    note = json.loads(raw)
                    if node_hashes.get(key) == source_sha and isinstance(note, dict):
                        ai_notes[key] = {'content': note, 'model': model, 'language': lang}
                except (ValueError, TypeError):
                    continue
    finally:
        db.close()
    nodes,edges = enrich_with_directories(nodes,edges)
    attach_usage_labels(nodes,edges)
    for node in nodes:
        ai = ai_notes.get(node['id'])
        if ai:
            node['ai'] = ai
    payload = {"projectRoot": root, "nodes": nodes, "edges": edges}
    (output / "mapa.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    audit=audit_graph(nodes,edges)
    (output / "diagnostico-relaciones.json").write_text(
        json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    template = Path(__file__).with_name("visualizer.html").read_text(encoding="utf-8")
    embedded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    (output / "mapa.html").write_text(template.replace("/*__ENGINEER_DATA__*/ null", embedded), encoding="utf-8")

    notes_dir = output / "Obsidian" / "Archivos"
    notes_dir.mkdir(parents=True, exist_ok=True)
    outgoing: dict[str, list[dict]] = defaultdict(list)
    incoming: dict[str, list[dict]] = defaultdict(list)
    for edge in edges:
        outgoing[edge["from"]].append(edge)
        incoming[edge["to"]].append(edge)
    index = ["# SolarisPKN-Engineer", "", "Mapa de dependencias",
             "", "- [[Archivos/Indice|Índice de nodos]]",
             "- [[Indice-IA|Índice de explicaciones con IA]]",
             "- [Visor HTML](../mapa.html)", ""]
    (output / "Obsidian" / "Inicio.md").write_text("\n".join(index), encoding="utf-8")
    listing = ["# Índice de nodos", ""]
    ai_index = ["# Índice de explicaciones con IA", "",
                "> Estas descripciones son interpretaciones de un modelo, no pruebas de ejecución.", ""]
    for node in nodes:
        key = node["id"]
        note = note_name(key)
        listing.append("- [[" + note + "|" + key.replace("|", "-").replace("]", "") + "]]")
        content = ["# " + key.replace("[", "").replace("]", ""), "",
                   "- Tipo: " + str(node["type"]),
                   "- Estado: " + str(node["status"]),
                   "- Profundidad: " + str(node["depth"]),
                   "- Evidencia: " + ("analizada" if node["status"] == "done" else node["status"]),
                   "- SHA256: " + str(node["sha256"] or "no calculado")]
        if node["error"]:
            content += ["- Error: " + str(node["error"]).replace("\n", " ")[:450]]
        semantic_out=[e for e in outgoing[key] if e["relation"]!="contains"]
        semantic_in=[e for e in incoming[key] if e["relation"]!="contains"]
        folders_out=[e for e in outgoing[key] if e["relation"]=="contains"]
        folders_in=[e for e in incoming[key] if e["relation"]=="contains"]
        content += ["", "## Dependencias declaradas o inferidas"]
        content.extend(
            "- [[" + note_name(e["to"]) + "]] (" + e["relation"] + "; " + e["evidence"] + ")"
            for e in semantic_out
        )
        if not semantic_out:
            content.append("- Sin dependencias salientes detectadas (no implica archivo sin uso)")
        content += ["", "## Referenciado por"]
        content.extend(
            "- [[" + note_name(e["from"]) + "]] (" + e["relation"] + "; " + e["evidence"] + ")"
            for e in semantic_in
        )
        content += ["", "## Estructura: contiene"]
        content.extend("- [[" + note_name(e["to"]) + "]]" for e in folders_out)
        content += ["", "## Estructura: ubicado en"]
        content.extend("- [[" + note_name(e["from"]) + "]]" for e in folders_in)
        ai = ai_notes.get(key)
        if ai:
            doc = ai["content"]
            content.extend(["", "## Comentario de IA", "",
                            "> Interpretación automática; verificar con el código y las pruebas.",
                            "", "**Modelo:** " + ai["model"], ""])
            def safe(text):
                # Do not embed arbitrary HTML in markdown generated by a model.
                return str(text or "").replace("<", "&lt;").replace(">", "&gt;").replace("\r", "")
            content.append("**Qué hace:** " + safe(doc.get("summary", "")))
            for title, field in (("Responsabilidades", "responsibilities"),
                                 ("Símbolos importantes", "important_symbols")):
                content.extend(["", "### " + title])
                content.extend("- " + safe(item) for item in doc.get(field, []) if isinstance(item, str))
            for title, field in (("Entradas y salidas", "inputs_outputs"),
                                 ("Notas", "notes"), ("Límites del análisis", "limitations")):
                content.extend(["", "### " + title, "", safe(doc.get(field, ""))])
            ai_index.append("- [[" + note + "|" + key.replace("|", "-").replace("]", "") +
                            "]] — " + safe(doc.get("summary", ""))[:240].replace("\n", " "))
        (notes_dir / (note + ".md")).write_text("\n".join(content) + "\n", encoding="utf-8")
    (notes_dir / "Indice.md").write_text("\n".join(listing) + "\n", encoding="utf-8")
    if len(ai_index) == 4:
        ai_index.append("Sin explicaciones de IA generadas todavía. Ejecutá `annotate`.")
    (output / "Obsidian" / "Indice-IA.md").write_text("\n".join(ai_index) + "\n", encoding="utf-8")
    print("HTML:", output / "mapa.html")
    print("JSON:", output / "mapa.json")
    print("Obsidian:", output / "Obsidian")
    print("Audit:", output / "diagnostico-relaciones.json")
    print("Exported", len(nodes), "nodes and", len(edges), "relations")
