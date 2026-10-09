"""Sanitized graph of publicly tracked Git files only; never copy source content."""
from __future__ import annotations
import argparse
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from engineer import build_index
from graph_structure import enrich_with_directories
from impact_analysis import attach_usage_labels
from graph_diagnostics import audit_graph

PUBLIC_REPO="https://github.com/SolarisPKN/SolarisPKN-Labs"
REL=re.compile(r"^[a-z][a-z0-9-]{0,60}$")
STATES={"done","pending","running","encrypted","failed","external"}

def public_files(checkout:Path)->set[str]:
    if not (checkout/".git").exists():raise ValueError("Source must be a Git checkout")
    raw=subprocess.check_output(["git","-C",str(checkout),"ls-files","-z"],
                                timeout=30)
    names=set()
    for name in raw.decode("utf-8").split("\0"):
        if not name or len(name)>800 or "\\" in name or name.startswith("/"):continue
        if any(p in ("",".","..") for p in name.split("/")):continue
        if name.startswith((".git/","node_modules/","dist/","build/")):continue
        if (checkout/name).is_file():names.add(name)
    if not 1<=len(names)<=15000:raise ValueError("Invalid public file count")
    return names

def sanitized_snapshot(db_path:Path,allowed:set[str],commit:str)->tuple[dict,dict]:
    if not re.fullmatch(r"[a-f0-9]{40}",commit):raise ValueError("Bad Git commit SHA")
    nodes=[]
    edges=[]
    with closing(sqlite3.connect(str(db_path),timeout=15)) as db:
        for key,status in db.execute("SELECT key,status FROM nodes WHERE key LIKE 'file:%'"):
            if isinstance(key,str) and key.startswith("file:") and key[5:] in allowed:
                nodes.append({"id":key,"type":"file","status":status if status in STATES else "pending",
                              "depth":key[5:].count("/"),"sha256":None,"error":None,"updated":None})
                if len(nodes)>30000:raise ValueError("Graph node limit exceeded")
        found={node["id"] for node in nodes}
        for a,b,relation,evidence in db.execute("SELECT source,target,relation,evidence FROM edges"):
            if a not in found or b not in found or not isinstance(relation,str) or not REL.fullmatch(relation):
                continue
            edges.append({"from":a,"to":b,"relation":relation,
                          "evidence":evidence if evidence in ("declared","inferred","heuristic","observed") else "inferred"})
            if len(edges)>150000:raise ValueError("Graph edge limit exceeded")
    nodes,edges=enrich_with_directories(nodes,edges)
    attach_usage_labels(nodes,edges)
    graph={"projectRoot":"GitHub: SolarisPKN/SolarisPKN-Labs (public scan)",
           "sourceRepository":PUBLIC_REPO,"sourceCommit":commit,
           "sampleOnly":True,"nodes":nodes,"edges":edges}
    return graph,audit_graph(nodes,edges)

def write_example(checkout:Path,output:Path,visualizer:Path):
    checkout=checkout.resolve(strict=True)
    allowed=public_files(checkout)
    commit=subprocess.check_output(["git","-C",str(checkout),"rev-parse","HEAD"],timeout=20,text=True).strip().lower()
    with TemporaryDirectory(prefix="engineer-public-scan-") as temp:
        db=Path(temp)/"index.sqlite"
        build_index(checkout,db,None,True,2,False,4_000_000)
        graph,audit=sanitized_snapshot(db,allowed,commit)
    template=visualizer.read_text(encoding="utf-8")
    marker="/*__ENGINEER_DATA__*/ null"
    if template.count(marker)!=1:raise ValueError("Visualizer placeholder missing")
    js=json.dumps(graph,ensure_ascii=False,separators=(",",":"))
    js=(js.replace("<","\\u003c").replace("&","\\u0026")
          .replace("\u2028","\\u2028").replace("\u2029","\\u2029"))
    readme=(
       "# SolarisPKN-Labs — escaneo público de ejemplo\n\n"
       "Este directorio contiene **solo resultados saneados** del analizador: "
       "nombres de archivos públicos y relaciones de dependencia, no código fuente, "
       "base SQLite, comentarios IA, credenciales ni rutas locales.\n\n"
       "Repositorio de origen: "+PUBLIC_REPO+"\n"
       "Commit público: "+commit+"\n\n"
       "- mapa.html: visualización autónoma.\n"
       "- mapa.json: grafo público.\n"
       "- diagnostico-relaciones.json: cobertura del análisis.\n\n"
       "Los resultados son estáticos. No prueban que un archivo pueda borrarse.\n"
    )
    outputs={
        "mapa.json":json.dumps(graph,ensure_ascii=False,indent=2)+"\n",
        "mapa.html":template.replace(marker,js),
        "diagnostico-relaciones.json":json.dumps(audit,ensure_ascii=False,indent=2)+"\n",
        "README.md":readme,
    }
    output.mkdir(parents=True,exist_ok=True)
    for name,content in outputs.items():
        if str(checkout) in content or str(checkout).replace("\\","/") in content:
            raise RuntimeError("Local path leaked into "+name)
        if re.search(r"(?i)(?:[A-Za-z]:\\(?:Users|Usuarios)\\|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----)",content):
            raise RuntimeError("Sensitive path/material suspected in "+name)
        (output/name).write_text(content,encoding="utf-8")
    return {"source_commit":commit,"public_files":len(allowed),
            "indexed_public_files":sum(n["id"].startswith("file:") for n in graph["nodes"]),
            "semantic_edges":sum(e["relation"]!="contains" for e in graph["edges"]),
            "outputs":list(outputs)}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--checkout",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    result=write_example(args.checkout,args.output,Path(__file__).resolve().parent/"visualizer.html")
    print(json.dumps(result,ensure_ascii=False))
if __name__=="__main__":
    main()
