"""Offline semantic references for file assets, dynamic imports, metadata and framework conventions."""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote
import glob
import json
import re

ASSET_EXT={".webp",".png",".jpg",".jpeg",".gif",".avif",".svg",".ico",".woff",".woff2",".ttf",".otf",".eot",".pdf",".mp3",".mp4",".webm",".css",".scss",".js",".ts",".json",".mdx",".md"}
JSON_KEYS={"image","images","heroimage","cover","coverimage","banner","thumbnail","avatar","icon","logo","favicon","screenshot","screenshots","gallery","poster","picture","ogimage","socialimage","src","backgroundimage","featuredimage","assets","media","video","file","filename","filepath","attachment","attachments","imagen","imagenes","foto","fotos","portada","miniatura","icono","logotipo","fondo","bandera","archivo","archivos","adjunto","adjuntos","heroimagen","fuente","tipografia"}
WEB_EXT={".astro",".html",".htm",".js",".jsx",".ts",".tsx",".mjs",".cjs",".css",".scss",".sass",".less",".vue",".svelte",".svg",".mdx",".md"}
JOIN=re.compile(r'\b(?:path|nodePath)\.join\s*\(([^)\r\n]{1,900})')
JOIN_ASSIGN=re.compile(r'\b(?:const|let|var)\s+([\w$]+)\s*=\s*(?:path|nodePath)\.join\s*\(([^)\r\n]{1,900})')
TEMPLATES=re.compile(r'\b(?:import|import\.meta\.glob(?:Eager)?)\s*\(\s*([\x60"\x27])([^\x60"\x27]{1,900})\1',re.S)
DYNVAR=re.compile(r'\$\{[\w.]+\}')
MARKDOWN=re.compile(r'!\[[^\]\r\n]{0,250}\]\(\s*(?:<([^>\r\n]+)>|([^\s)\r\n]+))')
FRONTMATTER=re.compile(r'(?mi)^[ \t]*(?:heroImage|image|cover|banner|logo|thumbnail)[ \t]*:[ \t]*["\x27]?([^"\x27\s\r\n]+)')
ATTR=re.compile(r'(?i)\b(?:src|href|poster|srcset)\s*=\s*["\x27]([^"\x27\r\n{}]{1,800})["\x27]')
TEMPLATE_ATTR=re.compile(r'(?i)\b(?:src|href|poster|image)\s*=\s*\{\s*\x60([^\x60]{1,800})\x60\s*\}')
STYLE=re.compile(r'(?i)\b(?:url\(\s*["\x27]?|@import\s+["\x27])([^"\x27)\s\r\n]{1,800})')
JSON_FIELD=re.compile(r'(?i)\b(?:image|heroImage|src|cover|logo|avatar|icon|font|asset)[\w]*\s*[:=]\s*["\x27]([^"\x27\r\n]{1,800})["\x27]')
ALIAS=re.compile(r'["\x27](@[\w/-]+)["\x27]\s*:\s*["\x27](/src(?:/[\w.-]+)*)["\x27]')
TS_PATH=re.compile(r'"(@[^"]+/\*)"\s*:\s*\[\s*"([^"]+/\*)"')

@lru_cache(maxsize=32)
def aliases(root_string):
    root=Path(root_string)
    result={"@/":"src/","~/":"src/"}
    cfg=root/"astro.config.mjs"
    if cfg.is_file() and cfg.stat().st_size<400000:
        for name,target in ALIAS.findall(cfg.read_text(encoding="utf8",errors="replace")):
            result[name.rstrip("/")+"/"]=target.lstrip("/").rstrip("/")+"/"
    for fn in ("tsconfig.json","jsconfig.json"):
        cfg=root/fn
        if cfg.is_file() and cfg.stat().st_size<400000:
            for name,target in TS_PATH.findall(cfg.read_text(encoding="utf8",errors="replace")):
                result[name[:-1]]=target.removeprefix("./")[:-1]
    return result

def safe(root,path):
    try:
        value=path.resolve(strict=False)
        return value.relative_to(root).as_posix() if value.is_file() else None
    except (ValueError,OSError):return None

def source_candidates(root,source,reference):
    if reference.startswith("/"):return [root/"public"/reference.lstrip("/"),root/reference.lstrip("/")]
    for prefix,dest in sorted(aliases(str(root)).items(),key=lambda x:-len(x[0])):
        if reference.startswith(prefix):return [root/dest/reference[len(prefix):]]
    if reference.startswith("."):return [source.parent/reference]
    if reference.startswith(("src/","public/")):return [root/reference]
    return []

def normalized(value):
    if not isinstance(value,str) or len(value)>1200 or "\x00" in value:return None
    value=unquote(value.split("?",1)[0].split("#",1)[0].strip())
    if not value or value.startswith(("//","http:","https:","data:","javascript:","mailto:","node:")) or "://" in value or "\\" in value:return None
    return value

def resolve(root,source,reference):
    value=normalized(reference)
    if not value or ("$"+"{") in value or "{" in value:return None
    for stem in source_candidates(root,source,value):
        for suffix in ("",".astro",".mdx",".js",".ts",".jsx",".tsx",".json",".css",".svg"):
            p=Path(str(stem)+suffix) if suffix else stem
            candidate=safe(root,p)
            if candidate:return candidate
            if not suffix:
                for name in ("index.astro","index.js","index.ts","index.mdx"):
                    candidate=safe(root,p/name)
                    if candidate:return candidate
    return None

def expand(root,source,expression):
    value=normalized(expression)
    if not value:return
    value=DYNVAR.sub("*",value)
    if ("$"+"{") in value:return
    has_pattern=any(c in value for c in "*?[")
    for stem in source_candidates(root,source,value):
        # Resolve literal ../ components before glob expansion: Astro uses
        # literal bracketed directories such as [lang] and [slug].
        normalized_path=stem.resolve(strict=False)
        try:normalized_path.relative_to(root)
        except ValueError:continue
        if not has_pattern:
            candidate=resolve(root,source,value)
            if candidate:yield candidate
        else:
            for file in glob.iglob(str(normalized_path),recursive=True):
                candidate=safe(root,Path(file))
                if candidate:yield candidate

def json_references(root,source,text):
    try:obj=json.loads(text)
    except (ValueError,TypeError):return []
    edges=[]
    def descend(v,key="",level=0):
        if level>60:return
        if isinstance(v,dict):
            for k,w in v.items():descend(w,str(k).lower().replace("_","").replace("-",""),level+1)
        elif isinstance(v,list):
            for w in v:descend(w,key,level+1)
        elif isinstance(v,str) and key in JSON_KEYS:
            ref=normalized(v)
            if ref and Path(ref).suffix.lower() in ASSET_EXT:
                found=resolve(root,source,v)
                if found:edges.append(("file:"+found,"metadata-resource","declared"))
    descend(obj)
    return edges

def text_references(root,source,text,suffix):
    edges=[]
    if suffix in (".mdx",".md"):
        for x,y in MARKDOWN.findall(text):
            found=resolve(root,source,x or y)
            if found:edges.append(("file:"+found,"markdown-image","declared"))
        for image in FRONTMATTER.findall(text[:20000]):
            found=resolve(root,source,image)
            if found:edges.append(("file:"+found,"frontmatter-resource","declared"))
    for pat,relation in ((ATTR,"asset-reference"),(TEMPLATE_ATTR,"template-asset"),(STYLE,"style-resource"),(JSON_FIELD,"data-asset")):
        for m in pat.finditer(text):
            spec=m.group(1)
            values=[x.strip().split(" ")[0] for x in spec.split(",")] if "srcset" in m.group(0).lower() else [spec]
            for value in values:
                if ("$"+"{") in value:
                    for found in expand(root,source,value):
                        edges.append(("file:"+found,relation,"inferred"))
                else:
                    raw=normalized(value)
                    if raw and Path(raw).suffix.lower() in ASSET_EXT:
                        found=resolve(root,source,value)
                        if found:edges.append(("file:"+found,relation,"declared"))
    return edges

def join_value(text,known):
    parts=[s.strip() for s in text.split(",")]
    result=[]
    for i,part in enumerate(parts):
        if part in known:result.append(known[part])
        elif part in ("root","process.cwd()"):result.append("")
        elif len(part)>1 and part[0] in ("'","\"",chr(96)) and part[-1]==part[0]:result.append(part[1:-1])
        elif re.fullmatch(r"[\w.]+",part) and i>0:result.append("*")
        else:return None
    return "/".join(p.strip("/") for p in result if p)

def direct_file_reads(root,source,text):
    """Literal config reads, and basename resolution only when file exists."""
    edges=[]
    direct=re.compile(r'\b(?:readFile|readFileSync|readJson|loadJson|fetch)\s*\(\s*["\x27]([^"\x27\n]{1,600})["\x27]')
    for match in direct.finditer(text):
        name=match.group(1)
        item=resolve(root,source,name)
        if not item and not name.startswith((".","/","@")) and "/" not in name:
            item=safe(root,source.parent/name) or safe(root,root/name)
        if item:
            heuristic=match.group(0).lstrip().startswith("fetch") or not name.startswith((".","/","@","src/","public/"))
            edges.append(("file:"+item,"file-read","inferred" if heuristic else "declared"))
    # Common Node.js root-anchored config load:
    # fs.readFileSync(path.join(process.cwd(), 'settings.json'))
    root_join=re.compile(
        r'\b(?:readFile|readFileSync|readJson)\s*\(\s*'
        r'path\.join\s*\(\s*process\.cwd\(\)\s*,\s*["\x27]([^"\x27\n]{1,250})["\x27]\s*\)')
    for match in root_join.finditer(text):
        item=safe(root,root/match.group(1))
        if item:edges.append(("file:"+item,"file-read","declared"))
    return list(dict.fromkeys(edges))

def dynamic_references(root,source,text):
    edges=[]
    for match in TEMPLATES.finditer(text):
        s=match.group(2)
        if ("$"+"{") in s or "*" in s:
            for rel in expand(root,source,s):edges.append(("file:"+rel,"dynamic-import-pattern","inferred"))
    vars={"root":"","cwd":""}
    defs=list(JOIN_ASSIGN.finditer(text))
    for _ in range(4):
        for match in defs:
            value=join_value(match.group(2),vars)
            if value is not None:vars[match.group(1)]=value
    for m in JOIN.finditer(text):
        spec=join_value(m.group(1),vars)
        if not spec or not spec.startswith(("src/","public/")):continue
        if Path(spec).suffix.lower() not in ASSET_EXT:continue
        for rel in expand(root,source,spec):
            edges.append(("file:"+rel,"computed-file-path","inferred" if "*" in spec else "declared"))
    return edges

def paired_references(root,source):
    parent=source.parent
    if source.name not in ("post.json","project.json") or parent.parent.parent.name!="content" or parent.parent.name not in ("blog","projects"):
        return []
    edges=[]
    for sibling in parent.glob("index-*.mdx"):
        rel=safe(root,sibling)
        if rel:edges.append(("file:"+rel,"localized-content","inferred"))
    if parent.parent.name=="blog":
        for target in (root/"src"/"locales").glob("*/posts/"+parent.name+".json"):
            rel=safe(root,target)
            if rel:edges.append(("file:"+rel,"localized-metadata","inferred"))
    return edges

def semantic_references(root,relative,content):
    root=root.resolve(strict=False)
    origin=root/relative
    ext=origin.suffix.lower()
    if ext not in WEB_EXT|{".json",".jsonc",".webmanifest"}:return []
    text=content.decode("utf-8-sig",errors="replace")
    edges=[]
    if ext in (".json",".webmanifest"):edges.extend(json_references(root,origin,text))
    if ext not in (".json",".jsonc",".webmanifest"):
        edges.extend(text_references(root,origin,text,ext))
    if ext in {".astro",".mdx",".vue",".svelte",".js",".jsx",".ts",".tsx",".mjs",".cjs"}:
        edges.extend(direct_file_reads(root,origin,text))
        edges.extend(dynamic_references(root,origin,text))
    if ext==".json":edges.extend(paired_references(root,origin))
    return list(dict.fromkeys(edges))
