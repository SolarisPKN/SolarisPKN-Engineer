"""Read-only GitHub compare, GraphQL blame and deployment evidence for Engineer.

Only api.github.com is contacted. No redirect, arbitrary host, or executable.
Tokens come from the server-side DPAPI vault or GITHUB_TOKEN; never return them.
A deployment record is evidence of a *reported* deployment, not proof that
every byte observed at a public URL came from the reported commit.
"""
from __future__ import annotations

from datetime import datetime, timezone
import http.client
import json
import os
import re
import ssl
from urllib.parse import quote, urlsplit

MAX_RESPONSE_BYTES = 2_000_000
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_REF = re.compile(r"^[A-Za-z0-9_./+-]{1,180}$")
_SHA = re.compile(r"^[a-fA-F0-9]{40,64}$")
MAX_FILES = 40
MAX_BLAME_RANGES = 120


def repo_name(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or not _REPO.fullmatch(value) or ".." in value.split("/"):
        raise ValueError("Repositorio inválido: usá owner/repo, sin URL ni credenciales")
    return tuple(value.split("/", 1))


def ref_name(value: str) -> str:
    if (not isinstance(value, str) or not _REF.fullmatch(value) or
            ".." in value.split("/") or value.startswith(("/", ".", "-")) or "//" in value):
        raise ValueError("Ref Git inválida: indicá un SHA, tag o nombre de rama")
    return value


def file_name(value: str) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 400 or "\\" in value or
            value.startswith(("/", ".")) or
            any(x in (".", "..", "") for x in value.split("/")) or
            any(ord(ch) < 32 for ch in value)):
        raise ValueError("Ruta del archivo inválida o fuera del repositorio")
    return value


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        return token
    try:
        from credential_vault import load_secret
        return load_secret("github", "default") or ""
    except (OSError, RuntimeError, ValueError):
        return ""


def github_api(path: str, *, payload: dict | None = None, require_auth: bool = False) -> dict | list:
    """Fixed-origin GitHub HTTPS API, response-size capped and no redirects."""
    if not path.startswith("/") or "//" in path or "#" in path or "?" in path and "\n" in path:
        raise ValueError("Ruta GitHub inválida")
    token = _token()
    if require_auth and not token:
        raise ValueError("GitHub Blame remoto requiere token GitHub de solo lectura configurado")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "SolarisPKN-Engineer/0.1",
        "X-GitHub-Api-Version": "2026-03-10",
        "Accept-Encoding": "identity",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if body is not None:
        headers["Content-Type"] = "application/json"
        if len(body) > 10000:
            raise ValueError("Consulta GitHub demasiado grande")
    conn = http.client.HTTPSConnection("api.github.com", 443, timeout=10,
                                       context=ssl.create_default_context())
    try:
        conn.request("POST" if payload is not None else "GET", path, body=body, headers=headers)
        res = conn.getresponse()
        if res.status in (301, 302, 303, 307, 308):
            raise ValueError("GitHub redirigió la consulta; no se siguen redirecciones")
        if res.status not in (200, 201):
            if res.status in (401, 403):
                raise ValueError("GitHub rechazó el acceso o se alcanzó el límite de solicitudes; verificá el token y permisos")
            if res.status == 404:
                raise ValueError("Repositorio/recurso no encontrado o sin acceso en GitHub")
            raise ValueError(f"GitHub respondió HTTP {res.status}")
        if int(res.getheader("Content-Length") or "0") > MAX_RESPONSE_BYTES:
            raise ValueError("Respuesta GitHub demasiado grande")
        raw = res.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("Respuesta GitHub demasiado grande")
        result = json.loads(raw)
        if not isinstance(result, (dict, list)):
            raise ValueError("Respuesta de GitHub inesperada")
        return result
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError("No se pudo consultar GitHub mediante HTTPS") from None
    finally:
        conn.close()


def github_compare(repo: str, base: str, head: str) -> dict:
    owner, name = repo_name(repo)
    base, head = ref_name(base), ref_name(head)
    data = github_api("/repos/" + owner + "/" + name + "/compare/" +
                      quote(base, safe="") + "..." + quote(head, safe=""))
    if not isinstance(data, dict):
        raise ValueError("Comparación GitHub inválida")
    commits = []
    for item in (data.get("commits") or [])[:30]:
        sha = item.get("sha", "")
        if not _SHA.fullmatch(sha):
            continue
        detail = item.get("commit") or {}
        author = detail.get("author") or {}
        user = item.get("author") or {}
        commits.append({"sha": sha, "message": str(detail.get("message") or "")[:350],
                        "author": str(author.get("name") or "")[:120],
                        "github_login": user.get("login"), "date": author.get("date"),
                        "url": "https://github.com/" + repo + "/commit/" + sha})
    files = []
    for item in (data.get("files") or [])[:MAX_FILES]:
        path = item.get("filename", "")
        if not isinstance(path, str) or len(path) > 400:
            continue
        files.append({"path": path, "status": str(item.get("status") or "")[:30],
                      "additions": int(item.get("additions") or 0),
                      "deletions": int(item.get("deletions") or 0),
                      "patch": str(item.get("patch") or "")[:12000],
                      "patch_available": isinstance(item.get("patch"), str)})
    return {"repository": repo, "base": base, "head": head,
            "total_commits": data.get("total_commits", 0),
            "ahead_by": data.get("ahead_by", 0), "behind_by": data.get("behind_by", 0),
            "status": data.get("status"), "commits": commits, "files": files,
            "truncated": len(data.get("files") or []) > MAX_FILES or
                         len(data.get("commits") or []) > 30,
            "note": ("Son commits y parches entregados por GitHub. El autor declarado "
                     "de un commit no identifica automáticamente al editor de cada línea.")}


_BLAME_QUERY = """query EngineerBlame($owner:String!,$repo:String!,$expr:String!) {
 repository(owner:$owner,name:$repo) {
   object(expression:$expr) {
     ... on Blob {
       isBinary
       blame {
         ranges {
           startingLine endingLine
           commit {
             oid messageHeadline
             author { name date user { login } }
           }
         }
       }
     }
   }
 }
}"""


def github_blame(repo: str, ref: str, path: str, start: int = 1, end: int = 80) -> dict:
    owner, name = repo_name(repo)
    ref, path = ref_name(ref), file_name(path)
    if (isinstance(start, bool) or isinstance(end, bool)
            or not isinstance(start, int) or not isinstance(end, int)
            or not 1 <= start <= end or end - start >= 120):
        raise ValueError("Rango de líneas inválido (hasta 120 líneas)")
    data = github_api("/graphql", payload={
        "query": _BLAME_QUERY,
        "variables": {"owner": owner, "repo": name, "expr": ref + ":" + path},
    }, require_auth=True)
    if not isinstance(data, dict) or data.get("errors"):
        raise ValueError("GitHub GraphQL no pudo entregar blame; verificá permisos de lectura")
    obj = ((data.get("data") or {}).get("repository") or {}).get("object")
    if not isinstance(obj, dict) or obj.get("isBinary"):
        raise ValueError("Archivo inexistente o binario; GitHub Blame no disponible")
    blame = obj.get("blame")
    if not isinstance(blame, dict):
        raise ValueError("Blame no disponible para el archivo")
    ranges = []
    for part in (blame.get("ranges") or [])[:2000]:
        a, b = part.get("startingLine"), part.get("endingLine")
        if not isinstance(a, int) or not isinstance(b, int) or b < start or a > end:
            continue
        commit = part.get("commit") or {}
        sha = commit.get("oid", "")
        if not _SHA.fullmatch(sha):
            continue
        author = commit.get("author") or {}
        user = author.get("user") or {}
        ranges.append({"start": max(start, a), "end": min(end, b),
                       "sha": sha, "author": str(author.get("name") or "")[:120],
                       "github_login": user.get("login"),
                       "date": author.get("date"),
                       "message": str(commit.get("messageHeadline") or "")[:200],
                       "url": "https://github.com/" + repo + "/commit/" + sha})
        if len(ranges) >= MAX_BLAME_RANGES:
            break
    return {"repository": repo, "ref": ref, "path": path, "lines": [start, end],
            "ranges": ranges, "origin": "GitHub GraphQL blame",
            "note": "Autores declarados en Git; no es una prueba forense de identidad."}


def _matches_deployment(observed_url: str, environment_url: str) -> bool:
    """Require HTTPS and same host; a deployment base path must prefix URL on a segment."""
    try:
        observed, env = urlsplit(observed_url), urlsplit(environment_url)
    except ValueError:
        return False
    if observed.scheme != "https" or env.scheme != "https" or not observed.hostname or not env.hostname:
        return False
    if observed.hostname.lower().rstrip(".") != env.hostname.lower().rstrip("."):
        return False
    if observed.port not in (None, 443) or env.port not in (None, 443):
        return False
    base = (env.path or "/").rstrip("/")
    return not base or observed.path == base or observed.path.startswith(base + "/")


def github_deployment_evidence(repo: str, observed_url: str) -> dict:
    owner, name = repo_name(repo)
    if not isinstance(observed_url, str) or not observed_url.startswith("https://"):
        raise ValueError("La asociación de despliegues requiere una URL HTTPS pública")
    # Only fetch read-only GitHub deployment records. Do not trust arbitrary redirect URLs.
    deployments = github_api(f"/repos/{owner}/{name}/deployments?per_page=8")
    if not isinstance(deployments, list):
        raise ValueError("GitHub devolvió despliegues inesperados")
    matches = []
    for deployment in deployments[:8]:
        deployment_id = deployment.get("id")
        sha = deployment.get("sha", "")
        if not isinstance(deployment_id, int) or not _SHA.fullmatch(sha):
            continue
        statuses = github_api(f"/repos/{owner}/{name}/deployments/{deployment_id}/statuses?per_page=1")
        if not isinstance(statuses, list) or not statuses:
            continue
        status = statuses[0]
        environment_url = status.get("environment_url") or ""
        if status.get("state") != "success" or not _matches_deployment(observed_url, environment_url):
            continue
        matches.append({
            "sha": sha, "deployment_id": deployment_id,
            "environment": str(deployment.get("environment") or "")[:100],
            "deployed_at": status.get("created_at"),
            "environment_url": environment_url[:500],
            "url": "https://github.com/" + repo + "/commit/" + sha,
            "evidence": "github_deployment_status_success",
            "confidence": "reported_deployment_not_content_proof",
        })
        if len(matches) >= 3:
            break
    return {"repository": repo, "observed_url": observed_url, "matches": matches,
            "verified_content": False,
            "note": ("Coincide la URL publicada con un deployment GitHub marcado success; "
                     "NO verifica criptográficamente que el HTML servido provenga de ese commit. "
                     "Sin deployment coincidente la autoría es desconocida.")}
