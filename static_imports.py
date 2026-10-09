"""Conservative literal imports for languages without a full dependency parser.

No dynamic expressions, process execution or third-party code.
"""
from __future__ import annotations
from pathlib import Path
import re

# Each pattern yields a single literal module or path; do not cross lines.
PATTERNS={
 ".swift":[(r'(?m)^[ \t]*import[ \t]+([\w.]+)',"module")],
 ".dart":[(r'(?m)^[ \t]*(?:import|export|part)[ \t]+["\x27]([^"\x27]+)["\x27]',"file")],
 ".lua":[(r'\brequire[ \t]*\(?[ \t]*["\x27]([^"\x27]+)["\x27]',"lua")],
 ".proto":[(r'(?m)^[ \t]*import[ \t]+(?:public[ \t]+|weak[ \t]+)?["\x27]([^"\x27]+)["\x27]',"file")],
 ".graphql":[(r'(?m)^[ \t]*#[ \t]*import[ \t]+["\x27]([^"\x27]+)["\x27]',"file")],
 ".gql":[(r'(?m)^[ \t]*#[ \t]*import[ \t]+["\x27]([^"\x27]+)["\x27]',"file")],
 ".sh":[(r'(?m)^[ \t]*(?:source|\.)[ \t]+["\x27]?([./\w-]+)["\x27]?', "file")],
 ".bash":[(r'(?m)^[ \t]*(?:source|\.)[ \t]+["\x27]?([./\w-]+)["\x27]?', "file")],
 ".zsh":[(r'(?m)^[ \t]*(?:source|\.)[ \t]+["\x27]?([./\w-]+)["\x27]?', "file")],
 ".ps1":[(r'(?m)^[ \t]*\.[ \t]+["\x27]([^"\x27]+)["\x27]',"file"),
          (r'(?mi)^[ \t]*Import-Module[ \t]+([\w.-]+)',"module")],
 ".rb":[(r'\brequire_relative[ \t]*\(?[ \t]*["\x27]([^"\x27]+)["\x27]',"ruby-relative"),
        (r'\brequire[ \t]*\(?[ \t]*["\x27]([^"\x27]+)["\x27]',"ruby")],
 ".php":[(r'\b(?:require_once|include_once|require|include)[ \t]*\(?[ \t]*["\x27]([^"\x27]+)["\x27]',"file")],
 ".pl":[(r'(?m)^[ \t]*use[ \t]+([\w:]+)',"perl-module")],
 ".pm":[(r'(?m)^[ \t]*use[ \t]+([\w:]+)',"perl-module")],
 ".r":[(r'\bsource[ \t]*\([ \t]*["\x27]([^"\x27]+)["\x27]',"file")],
 ".jl":[(r'\binclude[ \t]*\([ \t]*["\x27]([^"\x27]+)["\x27]',"file"),
        (r'(?m)^[ \t]*(?:using|import)[ \t]+([\w.]+)',"module")],
 ".ex":[(r'(?m)^[ \t]*(?:alias|use|require|import)[ \t]+([\w.]+)',"module")],
 ".exs":[(r'(?m)^[ \t]*(?:alias|use|require|import)[ \t]+([\w.]+)',"module")],
 ".erl":[(r'(?m)^[ \t]*-(?:include|include_lib)\([ \t]*"([^"]+)"',"file")],
 ".hrl":[(r'(?m)^[ \t]*-(?:include|include_lib)\([ \t]*"([^"]+)"',"file")],
 ".scala":[(r'(?m)^[ \t]*import[ \t]+([\w.]+)',"module")],
 ".sc":[(r'(?m)^[ \t]*import[ \t]+([\w.]+)',"module")],
 ".groovy":[(r'(?m)^[ \t]*import[ \t]+([\w.]+)',"module")],
 ".vb":[(r'(?mi)^[ \t]*Imports[ \t]+([\w.]+)',"module")],
 ".m":[(r'(?m)^[ \t]*#\s*(?:include|import)[ \t]*["<]([^">]+)',"file")],
 ".mm":[(r'(?m)^[ \t]*#\s*(?:include|import)[ \t]*["<]([^">]+)',"file")],
 ".tf":[(r'(?m)^[ \t]*source[ \t]*=[ \t]*"([^"]+)"',"terraform")],
}

def find_references(root:Path,relative:str,content:bytes):
    suffix=Path(relative).suffix.lower()
    if suffix not in PATTERNS:return []
    text=content.decode("utf-8-sig",errors="replace")
    result=[]
    origin=root/relative
    for pattern,mode in PATTERNS[suffix]:
        for match in re.finditer(pattern,text):
            spec=match.group(1).strip()
            if not spec or "$" in spec or "{" in spec or "\x00" in spec or len(spec)>700:
                continue
            if mode=="lua":
                name=spec.replace(".","/")
                variants=[name+".lua",name+"/init.lua"]
            elif mode in ("ruby","ruby-relative"):
                variants=[spec,spec+".rb"]
            elif mode=="perl-module":
                variants=[spec.replace("::","/")+".pm"]
            else:
                variants=[spec]
            local=None
            for v in variants:
                if v.startswith(("//","\\","/")):continue
                candidate=(origin.parent/v).resolve(strict=False)
                try: rel=candidate.relative_to(root).as_posix()
                except ValueError:continue
                if candidate.is_file():
                    local=rel;break
            if local:
                result.append(("file:"+local,"static-import","declared"))
            elif mode not in ("file","ruby-relative","terraform"):
                result.append(("external:module:"+spec[:320],"static-import","declared"))
            elif mode=="terraform" and not spec.startswith("."):
                result.append(("external:terraform:"+spec[:320],"module-source","declared"))
    return list(dict.fromkeys(result))
