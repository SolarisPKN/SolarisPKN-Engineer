"""Focused dependency and impact analysis for one file, folder or ISO path.

For complete reverse-impact analysis, first inventory the larger project.
SQLite recursive CTE uses UNION to eliminate cycles. Results are paginated.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path


def scope(db_path:Path, reference:str, direction:str="both", offset:int=0, limit:int=200):
    if direction not in ("both","dependencies","dependents"):
        raise ValueError("Direction must be dependencies, dependents or both")
    if not reference or len(reference)>1000:
        raise ValueError("Choose a file key or directory prefix")
    if not db_path.exists():
        return {"total":0,"nodes":[],"reference":reference,"direction":direction}
    if reference.startswith(("file:","iso:","external:")):
        seed="SELECT key FROM nodes WHERE key = ?"
        arg=reference
    else:
        # directory relative to indexed root, e.g. src/runtime/
        prefix=reference.replace("\\","/").strip("/")
        if ".." in prefix.split("/"):raise ValueError("Invalid path")
        seed="SELECT key FROM nodes WHERE key LIKE ? ESCAPE '\\'"
        arg=("file:"+prefix+"/").replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%"
    if direction=="dependencies":
        expand="SELECT e.target FROM edges e JOIN reach r ON e.source=r.key"
    elif direction=="dependents":
        expand="SELECT e.source FROM edges e JOIN reach r ON e.target=r.key"
    else:
        expand=("SELECT e.target FROM edges e JOIN reach r ON e.source=r.key "
                "UNION SELECT e.source FROM edges e JOIN reach r ON e.target=r.key")
    sql="WITH RECURSIVE reach(key) AS ("+seed+" UNION "+expand+") "
    with sqlite3.connect(str(db_path),timeout=30) as db:
        total=db.execute(sql+"SELECT count(*) FROM reach",(arg,)).fetchone()[0]
        rows=db.execute(sql+"SELECT key FROM reach ORDER BY key LIMIT ? OFFSET ?",
                        (arg,max(1,min(limit,2000)),max(0,offset))).fetchall()
        return {"total":total,"nodes":[r[0] for r in rows],"reference":reference,
                "direction":direction,"offset":offset,"complete_reverse_index":False}
