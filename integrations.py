"""Provider catalog and connection settings. No credentials are persisted.

Provider adapters: Ollama, native Gemini, native Anthropic, and
OpenAI-compatible endpoints (OpenAI, Groq, xAI, Mistral, DeepSeek, etc.).
The Solaris preset requires a separately authorized local chat gateway.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError
from workspace import app_folder

PRESETS = {
 "ollama": {"label":"Ollama local", "protocol":"ollama","endpoint":"http://127.0.0.1:11435/api/chat","model":"qwen2.5-coder:7b","key_env":"","remote":False},
 "solaris": {"label":"Solaris (gateway local)","protocol":"openai","endpoint":"http://127.0.0.1:4319/v1/chat/completions","model":"solaris","key_env":"SOLARIS_ENGINEER_TOKEN","remote":False, "caveat":"Requires an OpenAI-compatible Solaris gateway. The private MCP Bridge is not automatically compatible."},
 "openai": {"label":"OpenAI GPT","protocol":"openai","endpoint":"https://api.openai.com/v1/chat/completions","model":"gpt-5.5","key_env":"OPENAI_API_KEY","remote":True},
 "gemini": {"label":"Google Gemini","protocol":"gemini","endpoint":"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent","model":"gemini-3.1-flash-lite","key_env":"GEMINI_API_KEY","remote":True},
 "claude": {"label":"Anthropic Claude","protocol":"anthropic","endpoint":"https://api.anthropic.com/v1/messages","model":"claude-sonnet-4-5","key_env":"ANTHROPIC_API_KEY","remote":True},
 "deepseek": {"label":"DeepSeek","protocol":"openai","endpoint":"https://api.deepseek.com/chat/completions","model":"deepseek-chat","key_env":"DEEPSEEK_API_KEY","remote":True},
 "openrouter": {"label":"OpenRouter","protocol":"openai","endpoint":"https://openrouter.ai/api/v1/chat/completions","model":"openai/gpt-4o-mini","key_env":"OPENROUTER_API_KEY","remote":True},
 "groq": {"label":"Groq","protocol":"openai","endpoint":"https://api.groq.com/openai/v1/chat/completions","model":"llama-3.3-70b-versatile","key_env":"GROQ_API_KEY","remote":True},
 "xai": {"label":"xAI Grok","protocol":"openai","endpoint":"https://api.x.ai/v1/chat/completions","model":"grok-4","key_env":"XAI_API_KEY","remote":True},
 "mistral": {"label":"Mistral","protocol":"openai","endpoint":"https://api.mistral.ai/v1/chat/completions","model":"mistral-small-latest","key_env":"MISTRAL_API_KEY","remote":True},
 "together": {"label":"Together AI","protocol":"openai","endpoint":"https://api.together.xyz/v1/chat/completions","model":"meta-llama/Llama-3.3-70B-Instruct-Turbo","key_env":"TOGETHER_API_KEY","remote":True},
 "custom": {"label":"Otra API compatible","protocol":"openai","endpoint":"http://127.0.0.1:1234/v1/chat/completions","model":"local-model","key_env":"ENGINEER_CUSTOM_API_KEY","remote":False},
}
SAFE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,44}$")
SAFE_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,100}$")


def config_path() -> Path:
    return app_folder() / "integraciones.json"


def catalog() -> list[dict]:
    custom = {}
    f = config_path()
    if f.is_file():
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                custom = doc.get("providers", {})
                if not isinstance(custom, dict):
                    custom = {}
        except (OSError, ValueError):
            custom = {}
    result = []
    for key, preset in PRESETS.items():
        profile = {**preset}
        override = custom.get(key, {})
        if isinstance(override, dict):
            for prop in ("endpoint","model","key_env","label"):
                if isinstance(override.get(prop), str) and len(override[prop]) <= 500:
                    profile[prop] = override[prop]
        if key == "custom":
            profile["remote"] = not is_local(profile["endpoint"])
        profile["id"] = key
        # Metadata only, never materialize secrets.
        env = profile.get("key_env", "")
        profile["credential_configured"] = bool(env and os.environ.get(env))
        result.append(profile)
    return result


def is_local(endpoint: str) -> bool:
    u = urlsplit(endpoint.replace("{model}", "example"))
    return u.hostname in ("127.0.0.1","localhost","::1")


def find_profile(profile_id: str) -> dict:
    return next((p for p in catalog() if p["id"] == profile_id), None)


def update_profile(profile_id: str, params: dict) -> dict:
    if not SAFE_ID.fullmatch(profile_id) or profile_id not in PRESETS:
        raise ValueError("Unknown provider")
    allowed = ("model", "endpoint", "key_env")
    for name in params:
        if name not in allowed:
            raise ValueError("Settings allowed: model, endpoint, key_env")
    existing = find_profile(profile_id)
    new = {k: str(params.get(k, existing[k])).strip() for k in allowed}
    if not new["model"] or len(new["model"]) > 180:
        raise ValueError("Model is required (max 180 characters)")
    if not SAFE_ENV.fullmatch(new["key_env"]) and new["key_env"]:
        raise ValueError("Invalid environment variable name")
    endpoint = new["endpoint"].replace("{model}", "safe-model")
    url = urlsplit(endpoint)
    if url.scheme not in ("http","https") or not url.hostname or url.username or url.password:
        raise ValueError("Endpoint must be an HTTP(S) URL without credentials")
    if not is_local(endpoint) and url.scheme != "https":
        raise ValueError("Remote endpoints require HTTPS")
    if len(new["endpoint"]) > 500:
        raise ValueError("Endpoint too long")
    f=config_path()
    current = {"version":1,"providers":{}}
    if f.is_file():
        try:
            existing_json=json.loads(f.read_text(encoding="utf-8"))
            if isinstance(existing_json,dict):
                current["providers"]=existing_json.get("providers",{})
        except (ValueError,OSError):
            pass
    if not isinstance(current["providers"], dict):
        current["providers"]={}
    current["providers"][profile_id]=new
    temp=f.with_suffix(".tmp")
    temp.write_text(json.dumps(current,ensure_ascii=False,indent=2),encoding="utf-8")
    os.replace(temp,f)
    return find_profile(profile_id)


def request_json(url: str, headers: dict, payload: dict | None = None, timeout: int = 12, limit: int = 200000):
    u=urlsplit(url)
    if not is_local(url) and u.scheme != "https":
        raise ValueError("Remote integration requires HTTPS")
    if u.username or u.password:
        raise ValueError("Credentials in URL are disallowed")
    method="POST" if payload is not None else "GET"
    data=json.dumps(payload).encode("utf-8") if payload is not None else None
    req=Request(url,data=data,headers={**headers,"User-Agent":"SolarisPKN-Engineer/0.3"},method=method)
    opener=build_opener(ProxyHandler({})) if is_local(url) else build_opener()
    try:
        with opener.open(req,timeout=timeout) as response:
            raw=response.read(limit+1)
            if len(raw)>limit: raise ValueError("Remote response too large")
            return json.loads(raw.decode("utf-8"))
    except HTTPError as ex:
        # Do not echo error body; servers may echo credentials, prompts or input.
        raise RuntimeError("HTTP "+str(ex.code)+" from integration") from None
    except URLError as ex:
        raise RuntimeError("Integration not reachable: "+str(ex.reason)[:160]) from None


def check_provider(profile_id: str, allow_remote: bool = False) -> dict:
    p=find_profile(profile_id)
    if not p: raise ValueError("Unknown provider")
    if p["remote"] and not allow_remote:
        return {"ok":False,"reason":"Remote probe requires explicit confirmation"}
    key_env_present=bool(p["key_env"] and os.environ.get(p["key_env"]))
    from credential_vault import has_secret
    key_saved=has_secret("ai-provider",profile_id)
    if p["remote"] and not (key_env_present or key_saved):
        return {"ok":False,"reason":"Save an encrypted API key or set "+p["key_env"]}
    if p["protocol"]=="ollama":
        from urllib.parse import urljoin
        endpoint=p["endpoint"].split("/api/chat")[0].rstrip("/")+"/api/tags"
        resp=request_json(endpoint,{},timeout=5)
        models=[x.get("name","") for x in resp.get("models",[])[:150]]
        return {"ok":True,"model_available":p["model"] in models,"installed_models":models[:30]}
    # Do not make paid inference merely to check credentials.
    return {"ok": (key_env_present or key_saved) if p["remote"] else True, "reason":"Configuration ready; model request not performed"}


def github_status() -> dict:
    token=os.environ.get("GITHUB_TOKEN","")
    if not token:
        from credential_vault import load_secret
        token=load_secret("github","default") or ""
    if not token:
        return {"configured":False,"connected":False,"instruction":"Set GITHUB_TOKEN in the Engineer process environment for private repositories. Public repositories are browsable without a key."}
    data=request_json("https://api.github.com/user",{"Accept":"application/vnd.github+json","Authorization":"Bearer "+token},timeout=10)
    return {"configured":True,"connected":True,"login":str(data.get("login","")),"url":str(data.get("html_url",""))}


def github_repos(limit=30) -> list[dict]:
    token=os.environ.get("GITHUB_TOKEN","")
    if not token:
        from credential_vault import load_secret
        token=load_secret("github","default") or ""
    headers={"Accept":"application/vnd.github+json"}
    if token:headers["Authorization"]="Bearer "+token
    endpoint="https://api.github.com/user/repos?per_page="+str(min(100,max(1,int(limit))))+"&sort=updated" if token else "https://api.github.com/repositories?per_page="+str(min(100,max(1,int(limit))))
    data=request_json(endpoint,headers,timeout=15,limit=1500000)
    if not isinstance(data,list):raise ValueError("Unexpected GitHub response")
    return [{"full_name":x.get("full_name",""),"private":bool(x.get("private")),"html_url":x.get("html_url",""),"default_branch":x.get("default_branch","")} for x in data if isinstance(x,dict)][:limit]
