"""Structural directory membership for maps, separate from code dependencies.

Virtual directory nodes and 'contains' edges are derived from actual indexed
file paths, not guesses about runtime imports. SQLite dependency graph stays
untouched: impact/scope queries cannot mistake siblings for dependencies.
"""
from __future__ import annotations
from pathlib import PurePosixPath

ORDER={"encrypted":0,"failed":1,"pending":2,"running":3,"done":4,"external":5}

def enrich_with_directories(nodes:list[dict],edges:list[dict]):
    directories={}
    membership={}
    for n in nodes:
        key=n.get("id","")
        if not key.startswith("file:"):
            continue
        relative=key[5:].replace("\\","/")
        parts=relative.split("/")
        if not relative or "." in parts or ".." in parts or any(not p for p in parts):
            continue
        parent="dir:."
        for depth in range(1,len(parts)):
            folder="dir:"+"/".join(parts[:depth])
            if folder not in directories:
                directories[folder]={"id":folder,"type":"directory","status":"done",
                                     "depth":depth,"sha256":None,"error":None,
                                     "updated":None,"virtual":True}
            membership[(parent,folder)]=None
            parent=folder
        membership[(parent,key)]=None
        # Aggregate descendant health in each parent; no directory scanning.
        for index in range(len(parts)):
            folder="dir:." if index==0 else "dir:"+"/".join(parts[:index])
            item=directories.setdefault(
                folder,{"id":folder,"type":"directory","status":"done",
                        "depth":index,"sha256":None,"error":None,
                        "updated":None,"virtual":True})
            status=n.get("status","pending")
            if ORDER.get(status,2)<ORDER.get(item["status"],4):
                item["status"]="pending" if status=="running" else status

    seen={(e["from"],e["to"],e["relation"]) for e in edges}
    struct_edges=[]
    for src,dst in membership:
        if (src,dst,"contains") not in seen:
            struct_edges.append({"from":src,"to":dst,"relation":"contains",
                                 "evidence":"filesystem"})
    return nodes+sorted(directories.values(),key=lambda d:d["id"]),edges+struct_edges
