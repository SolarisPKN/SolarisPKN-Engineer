"""Additional localhost API handlers for provider settings, GitHub and lazy file tree."""
from __future__ import annotations
from pathlib import Path
import re
import sqlite3
from integrations import catalog, check_provider, find_profile, github_status, github_repos, update_profile
from workspace import paths
from resource_controls import hardware


def aggregate_status(counts):
    """A directory uses the most actionable status among indexed descendants."""
    for status in ("encrypted", "pending", "running", "failed", "done", "external"):
        if counts.get(status, 0):
            return "pending" if status == "running" else status
    return "pending"
from scope import scope
from live_view import status_snapshot
from language_catalog import status as format_status, refresh_linguist
from impact_analysis import impact_from_sqlite
from change_monitor import get_monitor, algorithm_status, verify_local_file
from github_provenance import github_compare, github_blame, github_deployment_evidence
from change_history import local_git_blame


def get_extension(path, query: dict):
    if path == "/api/monitor/algorithms":
        return algorithm_status()
    if path == "/api/monitor/targets":
        return {"targets": get_monitor().targets()}
    if path == "/api/monitor/events":
        return {"events": get_monitor().events(int(query.get("limit", ["50"])[0]))}
    if path == "/api/integrations":
        return {"providers": catalog(), "github": {"configured": __import__("os").environ.get("GITHUB_TOKEN") is not None}}
    if path == "/api/hardware":
        return hardware()
    if path == "/api/formats":
        return format_status()
    if path == "/api/graph/statuses":
        _, db_path, _, _ = paths(query.get("project", [""])[0])
        return status_snapshot(db_path)
    if path == "/api/impact":
        _,db_path,_,_=paths(query.get("project",[""])[0])
        return impact_from_sqlite(db_path,query.get("key",[""])[0])
    if path == "/api/scope":
        _,db_path,_,_=paths(query.get("project",[""])[0])
        return scope(db_path, query.get("reference",[""])[0],
                     query.get("direction",["both"])[0],
                     int(query.get("offset",["0"])[0]))
    if path == "/api/github/status":
        return github_status()
    if path == "/api/github/repositories":
        return {"repositories": github_repos(int(query.get("limit", ["30"])[0]))}
    if path == "/api/tree":
        project = query.get("project", [""])[0]
        prefix = query.get("prefix", [""])[0].replace("\\", "/")
        if len(prefix) > 950 or prefix.startswith("/") or ".." in prefix.split("/"):
            raise ValueError("Invalid indexed tree prefix")
        _,db_path,_,_=paths(project)
        if not db_path.is_file():
            return {"children":[],"prefix":prefix,"truncated":False}
        iso_mode=prefix.startswith(("iso:","zip:"))
        prefix=prefix.rstrip("/") + "/" if prefix else ""
        stem=prefix if iso_mode else "file:"+prefix
        escaped=stem.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")
        roots={}
        with sqlite3.connect(str(db_path),timeout=4) as db:
            rows=db.execute(
                "SELECT key,status FROM nodes WHERE key LIKE ? ESCAPE '\\' ORDER BY key",
                (escaped+"%",)
            )
            for key,status in rows:
                remainder=key[len(stem):]
                if not remainder:continue
                first=remainder.split("/",1)[0]
                nested="/" in remainder
                if first not in roots:
                    is_iso_image=key.startswith("file:") and first.lower().endswith(".iso") and not nested
                    is_zip_image=key.startswith("file:") and first.lower().endswith((".zip",".jar",".whl")) and not nested
                    archive=is_iso_image or is_zip_image
                    roots[first]={"name":first,"directory":nested or archive,
                                  "key":None if nested else key,
                                  "prefix":(("iso:" if is_iso_image else "zip:")+prefix+first+"!/") if archive else
                                           (prefix+first+"/" if nested else None),
                                  "status":status,"statuses":{status:1}}
                else:
                    counts = roots[first]["statuses"]
                    counts[status] = counts.get(status, 0) + 1
                    if nested:
                        roots[first]["directory"]=True
                if len(roots)>=2000:break
        for item in roots.values():
            item["status"] = aggregate_status(item["statuses"])
        children=sorted(roots.values(),key=lambda x:(not x["directory"],x["name"].lower()))
        return {"prefix":prefix,"children":children,"truncated":len(roots)>=2000}
    return None


def post_extension(path, data: dict):
    if path == "/api/github/compare":
        return github_compare(data.get("repository", ""), data.get("base", ""),
                              data.get("head", ""))
    if path == "/api/github/blame":
        return github_blame(data.get("repository", ""), data.get("ref", ""),
                            data.get("file", ""), data.get("start", 1), data.get("end", 80))
    if path == "/api/monitor/blame":
        target_id = data.get("id")
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            raise ValueError("ID de objetivo inválido")
        targets = get_monitor().targets()
        matches = [item for item in targets if item["id"] == target_id and item["kind"] == "local"]
        if not matches:
            raise ValueError("Seleccioná un objetivo local registrado")
        return local_git_blame(matches[0]["target"], data.get("file", ""),
                               data.get("start", 1), data.get("end", 80))
    if path == "/api/monitor/deployments":
        target_id = data.get("id")
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            raise ValueError("ID de objetivo inválido")
        targets = get_monitor().targets()
        matches = [item for item in targets if item["id"] == target_id and
                   item["kind"] == "web" and item.get("github_repo")]
        if not matches:
            raise ValueError("Registrá una web HTTPS y su repositorio GitHub para ver deployments")
        return github_deployment_evidence(matches[0]["github_repo"], matches[0]["target"])
    if path == "/api/monitor/add":
        return {"target": get_monitor().add(data.get("target", ""),
                    data.get("interval_seconds", 300), data.get("algorithm", "sha256"),
                    data.get("record_diffs", False), data.get("github_repo", ""))}
    if path == "/api/monitor/verify":
        return verify_local_file(data.get("file", ""), data.get("expected", ""),
                                 data.get("algorithm", "sha256"))
    if path == "/api/monitor/check":
        return get_monitor().check(int(data.get("id", 0)))
    if path == "/api/monitor/remove":
        return get_monitor().remove(int(data.get("id", 0)))
    if path == "/api/formats/update":
        if data.get("consent") is not True:
            raise ValueError("Explicit consent required for external catalog update")
        return refresh_linguist()
    if path == "/api/integrations/update":
        profile_id=str(data.get("id",""))
        return {"profile": update_profile(profile_id,data.get("settings",{}))}
    if path == "/api/integrations/check":
        profile_id=str(data.get("id",""))
        return check_provider(profile_id, bool(data.get("allow_remote",False)))
    return None
