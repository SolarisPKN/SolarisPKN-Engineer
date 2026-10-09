"""Read-only reverse-impact analysis with explicit evidence and uncertainty.

The purpose is NOT to certify deletion safety. Missing edges do not imply
unused files, especially in OS images, binary formats and computed paths.
All traversals exclude filesystem 'contains' edges and treat 'generates'
as production provenance rather than a runtime dependency.
"""
from __future__ import annotations
from collections import defaultdict, deque
from contextlib import closing
from pathlib import PurePosixPath

PRODUCTION={"generates","produces","writes-output"}
STRUCTURAL={"contains"}
LOW_CONFIDENCE={"inferred","heuristic","probable","possible"}
ENTRY_FILES={"package.json","astro.config.mjs","vite.config.js","vite.config.ts",
             "next.config.js","nuxt.config.ts","tsconfig.json","settings.json",
             "pyproject.toml","requirements.txt","dockerfile","makefile",
             "go.mod","cargo.toml","index.html","manifest.webmanifest"}
ENTRY_SUFFIXES=(".service",".socket",".timer",".desktop")
ENTRY_PREFIXES=("src/pages/","pages/","app/","scripts/","public/robots.txt",
                "public/manifest.webmanifest",".github/workflows/")

def likely_entrypoint(key):
    if not key.startswith("file:"):return False
    rel=key[5:]
    p=PurePosixPath(rel)
    if p.name.lower() in ENTRY_FILES or p.suffix.lower() in ENTRY_SUFFIXES:return True
    return any(rel.startswith(prefix) for prefix in ENTRY_PREFIXES)

def impact_graph(nodes, edges, key, max_depth=28, max_items=12000):
    node_by_id={n.get("id",n.get("key")):n for n in nodes}
    incoming=defaultdict(list)
    outgoing=defaultdict(list)
    production=defaultdict(list)
    for e in edges:
        a=e.get("from",e.get("source",""))
        b=e.get("to",e.get("target",""))
        relation=e.get("relation","")
        if relation in STRUCTURAL:continue
        if relation in PRODUCTION:
            production[b].append(dict(e))
            continue
        if a and b:
            incoming[b].append({"from":a,"to":b,"relation":relation,
                                "evidence":e.get("evidence","unknown")})
            outgoing[a].append({"from":a,"to":b,"relation":relation,
                                "evidence":e.get("evidence","unknown")})
    seen={key}
    queue=deque([(key,0,False)])
    parent={}
    consumers=[]
    reached_entries=[]
    possible_paths=0
    while queue and len(seen)<max_items:
        target,depth,uncertain=queue.popleft()
        if depth>=max_depth:continue
        for edge in incoming.get(target,[]):
            source=edge["from"]
            next_uncertain=uncertain or edge["evidence"] in LOW_CONFIDENCE
            if source in seen:continue
            seen.add(source);parent[source]=edge
            is_entry=likely_entrypoint(source)
            consumers.append({"id":source,"distance":depth+1,
                              "evidence":edge["evidence"],
                              "certainty":"possible" if next_uncertain else "declared",
                              "relation":edge["relation"]})
            if next_uncertain:possible_paths+=1
            if is_entry:reached_entries.append(source)
            queue.append((source,depth+1,next_uncertain))
    direct=incoming.get(key,[])
    definite=sum(x["evidence"] not in LOW_CONFIDENCE for x in direct)
    possible=len(direct)-definite
    targets=outgoing.get(key,[])
    if definite:
        state="referenced"
        message="Referenced directly in source/configuration. Removing it can break consumers."
    elif possible:
        state="possibly-referenced"
        message="Referenced by inferred/static patterns; usage needs verification."
    elif likely_entrypoint(key):
        state="entrypoint"
        message="Potential entrypoint/configuration; zero inbound edges is normal and not evidence of obsolescence."
    else:
        state="unresolved"
        message="No semantic inbound reference found with current analyzers. NOT safe-to-delete proof."
    def trace(source):
        chain=[source]
        while source in parent and len(chain)<max_depth+2:
            source=parent[source]["to"]
            chain.append(source)
        return chain
    reachable=sorted(reached_entries,key=lambda x:(len(trace(x)),x))
    return {
      "key":key,"usage_status":state,"explanation":message,
      "analysis_status":node_by_id.get(key,{}).get("status","unknown"),
      "safe_to_delete":None,
      "safe_to_delete_reason":"Unproven: dynamic loading, build tooling, binary access, external callers, and runtime-only dependencies may be missing.",
      "direct_consumers":direct[:max_items],"direct_consumer_count":len(direct),
      "direct_declared_count":definite,"direct_inferred_count":possible,
      "transitive_consumer_count":len(consumers),
      "transitive_consumers":consumers[:max_items],
      "entrypoint_chains":[{"entry":x,"chain":trace(x)} for x in reachable[:50]],
      "producers":production.get(key,[])[:100],
      "outgoing":targets[:300],
      "limits":{"max_depth":max_depth,"max_nodes":max_items,
                "truncated":bool(queue) or len(direct)>max_items,
                "cannot_prove_absence":True},
    }


def impact_from_sqlite(db_path, key, max_items=10000, max_depth=25):
    """Bounded reverse walk on indexed SQLite edges; no entire-OS graph materialization."""
    import sqlite3
    max_items=min(max(100,int(max_items)),20000)
    max_depth=min(max(1,int(max_depth)),50)
    nodes={}
    edges=[]
    visited={key}
    frontier=[key]
    with closing(sqlite3.connect(str(db_path),timeout=15)) as db:
        db.row_factory=sqlite3.Row
        node=db.execute("SELECT key,status,kind FROM nodes WHERE key=?",(key,)).fetchone()
        if node is None:
            raise ValueError("Indexed node not found")
        nodes[key]={"id":key,"status":node["status"],"type":node["kind"]}
        for depth in range(max_depth):
            if not frontier or len(visited)>=max_items:
                break
            incoming=[]
            for offset in range(0,len(frontier),350):
                group=frontier[offset:offset+350]
                query="SELECT source,target,relation,evidence FROM edges WHERE target IN ("+(",".join("?" for _ in group))+")"
                incoming.extend(dict(x) for x in db.execute(query,group))
            following=[]
            for row in incoming:
                edges.append({"from":row["source"],"to":row["target"],
                              "relation":row["relation"],"evidence":row["evidence"]})
                source=row["source"]
                if source not in visited and len(visited)<max_items:
                    visited.add(source);following.append(source)
            frontier=following
        for offset in range(0,len(visited),350):
            group=list(visited)[offset:offset+350]
            query="SELECT key,status,kind FROM nodes WHERE key IN ("+(",".join("?" for _ in group))+")"
            for row in db.execute(query,group):
                nodes[row["key"]]={"id":row["key"],"status":row["status"],"type":row["kind"]}
        for row in db.execute("SELECT source,target,relation,evidence FROM edges WHERE source=?",(key,)):
            edges.append({"from":row["source"],"to":row["target"],
                          "relation":row["relation"],"evidence":row["evidence"]})
    result=impact_graph(list(nodes.values()),edges,key,max_depth,max_items)
    if frontier or len(visited)>=max_items:
        result["limits"]["truncated"]=True
    return result


def attach_usage_labels(nodes, edges):
    """Label graph evidence separately from the already-stored scan status."""
    declared=defaultdict(int)
    inferred=defaultdict(int)
    producers=defaultdict(int)
    for e in edges:
        rel=e.get("relation","")
        if rel in STRUCTURAL:continue
        target=e.get("to",e.get("target",""))
        if rel in PRODUCTION:
            producers[target]+=1
        elif e.get("evidence","unknown") in LOW_CONFIDENCE:
            inferred[target]+=1
        else:
            declared[target]+=1
    for n in nodes:
        key=n.get("id",n.get("key",""))
        if declared[key]:
            usage="referenced"
        elif inferred[key]:
            usage="possibly-referenced"
        elif likely_entrypoint(key):
            usage="entrypoint"
        else:
            usage="unresolved"
        n["usage"]={"status":usage,"declared_incoming":declared[key],
                    "inferred_incoming":inferred[key],
                    "producers":producers[key],
                    "safe_to_delete":None}
    return nodes
