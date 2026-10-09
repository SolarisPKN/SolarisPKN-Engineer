"""Extensible language/file recognition, separate from dependency extraction.

Builtin registry works offline; GitHub Linguist's *data-only* language catalog
can be refreshed explicitly. Neither downloaded data nor source is executed.
A known extension does NOT imply that references can be fully parsed.
"""
from __future__ import annotations
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.request import Request, urlopen

UPSTREAM="https://raw.githubusercontent.com/github-linguist/linguist/main/lib/linguist/languages.yml"
SOURCE="https://github.com/github-linguist/linguist/blob/main/lib/linguist/languages.yml"
DATA_VERSION=1
MAX_DOWNLOAD=4_500_000

# List format: "Language|category|implemented analyzer|extensions separated by spaces".
# inventory = format known, no trustworthy dependency parser yet.
LANGUAGE_GROUPS = """
Python|programming|python-ast|.py .pyi .pyw
Cython|programming|inventory|.pyx .pxd .pxi
JavaScript|programming|web-static|.js .jsx .mjs .cjs .jsm .javascript
TypeScript|programming|web-static|.ts .tsx .mts .cts .d.ts
Astro|markup|web-static|.astro
MDX|markup|web-static|.mdx
Vue|markup|web-static|.vue
Svelte|markup|web-static|.svelte
HTML|markup|web-static|.html .htm .xhtml .hbs .ejs .liquid .njk .pug .jade
CSS|markup|web-static|.css .scss .sass .less .styl .module.css .module.scss
C|programming|c-include|.c .h .i
C++|programming|c-include|.cc .cpp .cxx .c++ .hpp .hh .hxx .ipp .tpp .inl
Objective-C|programming|c-include|.m .mm
C#|programming|dotnet-using|.cs .csx
Java|programming|java-import|.java
Kotlin|programming|java-import|.kt .kts
Go|programming|go-import|.go
Rust|programming|rust-module|.rs
Ruby|programming|file-require|.rb .rake .gemspec .ru
PHP|programming|file-require|.php .phtml .php3 .php4 .php5 .blade.php
Bash|programming|static-imports|.sh .bash .zsh .fish .ksh .csh .tcsh
PowerShell|programming|static-imports|.ps1 .psm1 .psd1
Perl|programming|static-imports|.pl .pm .t
Lua|programming|static-imports|.lua
Dart|programming|static-imports|.dart
Swift|programming|static-imports|.swift
Scala|programming|static-imports|.scala .sc
Groovy|programming|static-imports|.groovy .gradle .gvy
Clojure|programming|inventory|.clj .cljs .cljc .edn
Elixir|programming|static-imports|.ex .exs
Erlang|programming|static-imports|.erl .hrl
Haskell|programming|inventory|.hs .lhs
OCaml|programming|inventory|.ml .mli .mll .mly
F#|programming|inventory|.fs .fsi .fsx
Visual Basic|programming|inventory|.vb .vbs .vba .bas .frm
R|programming|static-imports|.r .rmd .rproj
Julia|programming|static-imports|.jl
MATLAB|programming|inventory|.matlab .mlx .m
GNU Octave|programming|inventory|.octave .m
Fortran|programming|inventory|.f .f90 .f95 .f03 .f08 .for .f77
COBOL|programming|inventory|.cob .cbl .cpy
Pascal|programming|inventory|.pas .pp .p
Delphi|programming|inventory|.dpr .dfm .dproj .pas
Ada|programming|inventory|.ada .adb .ads
D|programming|inventory|.d .di
Zig|programming|inventory|.zig
Nim|programming|inventory|.nim .nims
Crystal|programming|inventory|.cr
V|programming|inventory|.v .vsh
Odin|programming|inventory|.odin
Assembly|programming|inventory|.asm .s .nasm .inc
WebAssembly Text|programming|inventory|.wat .wast
Solidity|programming|inventory|.sol
Vyper|programming|inventory|.vy
Move|programming|inventory|.move
Apex|programming|inventory|.apex .cls .trigger
ABAP|programming|inventory|.abap
SQL|programming|inventory|.sql .psql .pgsql .plsql .tsql
GraphQL|data|static-imports|.graphql .gql
Protocol Buffers|data|static-imports|.proto
Thrift|data|inventory|.thrift
FlatBuffers|data|inventory|.fbs
Avro|data|inventory|.avsc .avdl
JSON|data|inventory|.json .jsonc .json5 .jsonl .ndjson
YAML|data|inventory|.yaml .yml
TOML|data|inventory|.toml
XML|markup|inventory|.xml .xsl .xslt .xsd .wsdl .pom
SVG|markup|web-static|.svg
INI|data|inventory|.ini .cfg .conf .properties .inf
Markdown|prose|inventory|.md .markdown .mdown
reStructuredText|prose|inventory|.rst
AsciiDoc|prose|inventory|.adoc .asciidoc
LaTeX|markup|inventory|.tex .sty .cls
Terraform|programming|static-imports|.tf .tfvars .tf.json
HCL|data|inventory|.hcl
Nix|programming|inventory|.nix
Bicep|programming|inventory|.bicep .bicepparam
Dockerfile|data|inventory|.dockerfile .containerfile
CMake|data|inventory|.cmake
Makefile|data|inventory|.mk .make
Gradle|data|inventory|.gradle .gradle.kts
Jenkinsfile|data|inventory|.jenkinsfile
GitHub Actions|data|inventory|.yaml .yml
SaltStack SLS|data|inventory|.sls
Ansible|data|inventory|.yml .yaml
QML|programming|inventory|.qml
GDScript|programming|inventory|.gd
Godot Shader|programming|inventory|.gdshader
GLSL|programming|inventory|.glsl .vert .frag .geom .tesc .tese .comp
HLSL|programming|inventory|.hlsl .fx .fxh
WGSL|programming|inventory|.wgsl
Metal|programming|inventory|.metal
ShaderLab|programming|inventory|.shader
Unity|data|inventory|.unity .prefab .asset .meta
UnrealScript|programming|inventory|.uc
Blueprint/Unreal|data|inventory|.uasset .umap
Razor|markup|inventory|.cshtml .razor
XAML|markup|inventory|.xaml
Blazor|markup|inventory|.razor
SvelteKit|markup|web-static|.svelte
Django Template|markup|inventory|.jinja .jinja2 .j2
Jupyter|data|inventory|.ipynb
CoffeeScript|programming|inventory|.coffee
Elm|programming|inventory|.elm
PureScript|programming|inventory|.purs
Reason|programming|inventory|.re .rei
ReScript|programming|inventory|.res .resi
Racket|programming|inventory|.rkt
Scheme|programming|inventory|.scm .ss
Common Lisp|programming|inventory|.lisp .lsp .cl
Prolog|programming|inventory|.pro .prolog
Smalltalk|programming|inventory|.st
Tcl|programming|inventory|.tcl .tk
AWK|programming|inventory|.awk
Sed|programming|inventory|.sed
Batch|programming|inventory|.bat .cmd
AutoHotkey|programming|inventory|.ahk .ah2
AutoIt|programming|inventory|.au3
AppleScript|programming|inventory|.applescript .scpt
Forth|programming|inventory|.forth .fth
Brainfuck|programming|inventory|.bf
RPG|programming|inventory|.rpgle .rpg
SAS|programming|inventory|.sas
Stata|programming|inventory|.do .ado
MQL|programming|inventory|.mq4 .mq5
OpenSCAD|programming|inventory|.scad
VHDL|programming|inventory|.vhd .vhdl
Verilog|programming|inventory|.v .sv .svh .vh
SystemVerilog|programming|inventory|.sv .svh
TLA+|programming|inventory|.tla
Alloy|programming|inventory|.als
YANG|data|inventory|.yang
CSV|data|inventory|.csv .tsv
SQLite|binary|inventory|.sqlite .sqlite3 .db
PE executable|binary|pe-imports|.exe .dll .sys .ocx .scr
ELF executable|binary|elf-imports|.elf .so .o .ko
Mach-O executable|binary|macho-imports|.dylib .app .bundle
WebAssembly|binary|inventory|.wasm
Java archive|archive|inventory|.jar .war .ear
ZIP archive|archive|inventory|.zip .whl .apk
ISO image|archive|iso9660|.iso
UDF image|archive|inventory|.udf
WIM image|archive|inventory|.wim .esd
SquashFS image|archive|inventory|.squashfs
7z archive|archive|inventory|.7z
RAR archive|archive|inventory|.rar
TAR archive|archive|inventory|.tar .tar.gz .tgz .tar.xz .tar.zst
Gzip archive|archive|inventory|.gz
Package|archive|inventory|.deb .rpm .msi .msix .appx .dmg .pkg
Disk image|archive|inventory|.img .vhd .vhdx .vmdk .qcow2 .vdi
""".strip()

NAME_OVERRIDES={
 "Dockerfile":("Dockerfile","data","inventory"),
 "Containerfile":("Dockerfile","data","inventory"),
 "Makefile":("Makefile","data","inventory"),
 "GNUmakefile":("Makefile","data","inventory"),
 "CMakeLists.txt":("CMake","data","inventory"),
 "Jenkinsfile":("Jenkinsfile","data","inventory"),
 "Cargo.lock":("TOML","data","manifest"),
 "go.mod":("Go","data","go-import"),
 "go.sum":("Go","data","manifest"),
 "requirements.txt":("Python dependencies","data","manifest"),
 "pyproject.toml":("TOML","data","manifest"),
 "package.json":("npm manifest","data","manifest"),
 "package-lock.json":("npm manifest","data","manifest"),
 "Pipfile":("TOML","data","manifest"),
 "Gemfile":("Ruby","data","file-require"),
 "Rakefile":("Ruby","data","file-require"),
 ".gitignore":("Git ignore","data","inventory"),
 ".gitattributes":("Git attributes","data","inventory"),
 ".editorconfig":("EditorConfig","data","inventory"),
 "APKBUILD":("Shell","programming","static-imports"),
 "Justfile":("Just","data","inventory"),
}
SHEBANGS={
 "python":("Python","python-ast"),"node":("JavaScript","web-static"),
 "bash":("Bash","static-imports"),"sh":("Shell","static-imports"),
 "zsh":("Shell","static-imports"),"pwsh":("PowerShell","static-imports"),
 "ruby":("Ruby","file-require"),"perl":("Perl","static-imports"),
 "lua":("Lua","static-imports"),"php":("PHP","file-require"),
 "deno":("TypeScript","web-static"),"bun":("JavaScript","web-static"),
}
def builtin():
    extensions={}
    for line in LANGUAGE_GROUPS.splitlines():
        language,category,analyzer,raw=line.split("|",3)
        for extension in raw.split():
            extensions.setdefault(extension.lower(),[]).append(
                {"language":language,"category":category,"analyzer":analyzer}
            )
    return extensions

BUILTIN=builtin()

def catalog_path() -> Path:
    from workspace import app_folder
    return app_folder()/".private"/"linguist_catalog.json"

def parse_linguist(text: str) -> dict:
    """Parse ONLY top-level language, type, extensions and filenames fields.

    No arbitrary YAML constructors or remote code. Invalid tokens are skipped.
    """
    if not isinstance(text,str) or len(text)>MAX_DOWNLOAD:
        raise ValueError("Linguist catalog too large")
    lang=None;section=None;category=None;exts=[];names=[];collected={}
    def flush():
        if not lang or not category:
            return
        if category not in ("programming","markup","data","prose"):
            return
        for ext in exts:
            if not re.fullmatch(r"\.[A-Za-z0-9_+.-]{1,35}",ext):
                continue
            collected.setdefault(ext.lower(),[]).append(
                {"language":lang,"category":category,"analyzer":"inventory"}
            )
    for line in text.splitlines()+["__END__:"]:
        if line and not line.startswith((" ","#","-")) and line.endswith(":"):
            flush();lang=line[:-1].strip("'\"");category=None;section=None;exts=[];names=[]
            continue
        if line.startswith("  type:"):
            category=line.split(":",1)[1].strip().strip('"\'')
            section=None
        elif line.strip() in ("extensions:","filenames:") and line.startswith("  "):
            section=line.strip()[:-1]
        elif line.startswith("  - ") and section=="extensions":
            val=line[4:].strip().strip("'\"")
            if val:exts.append(val)
        elif line.startswith("  ") and not line.startswith("    ") and ":" in line:
            if not line.startswith("  - "):section=None
    if len(collected)<120:
        raise ValueError("Linguist data does not contain enough extensions")
    return collected

def upstream_catalog():
    p=catalog_path()
    if not p.is_file():return None
    try:
        if p.stat().st_size>MAX_DOWNLOAD:return None
        doc=json.loads(p.read_text(encoding="utf-8"))
        if doc.get("version")!=DATA_VERSION:return None
        if not isinstance(doc.get("extensions"),dict):return None
        return doc
    except (OSError,ValueError,TypeError):
        return None

@lru_cache(maxsize=1)
def merged_catalog():
    combined={k:list(v) for k,v in BUILTIN.items()}
    remote=upstream_catalog()
    if remote:
        for ext,values in remote["extensions"].items():
            if not isinstance(ext,str) or not re.fullmatch(r"\.[A-Za-z0-9_+.-]{1,35}",ext):continue
            if not isinstance(values,list):continue
            for x in values[:20]:
                if not isinstance(x,dict):continue
                language=x.get("language","")
                category=x.get("category","")
                if not isinstance(language,str) or len(language)>100 or category not in ("programming","markup","data","prose"):continue
                if not any(y["language"]==language for y in combined.get(ext,[])):
                    combined.setdefault(ext,[]).append({"language":language,"category":category,"analyzer":"inventory"})
    return combined

def refresh_linguist():
    """An explicit user operation. Fetch data-only YAML from a fixed GitHub URL."""
    req=Request(UPSTREAM,headers={"User-Agent":"SolarisPKN-Engineer/0.4","Accept":"text/plain"})
    with urlopen(req,timeout=25) as stream:
        raw=stream.read(MAX_DOWNLOAD+1)
    if len(raw)>MAX_DOWNLOAD:raise ValueError("Linguist catalog exceeds size limit")
    parsed=parse_linguist(raw.decode("utf-8"))
    doc={"version":DATA_VERSION,"source":SOURCE,
         "updated":datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "sha256":hashlib.sha256(raw).hexdigest(),"extensions":parsed}
    p=catalog_path();p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(doc,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
        os.replace(tmp,p)
        merged_catalog.cache_clear()
    finally:
        tmp.unlink(missing_ok=True)
    return status()

def classify_file(file: Path, header: bytes | None = None):
    """Report confidence and parser coverage. No reading beyond a 4KiB header."""
    p=Path(file)
    if header is None:
        try:
            with p.open("rb") as reader:header=reader.read(4096)
        except OSError:header=b""
    signatures=[
        (header[:2]==b"MZ",("PE executable","binary","pe-imports")),
        (header[:4]==bytes.fromhex("7f454c46"),("ELF executable","binary","elf-imports")),
        (header[:4] in (bytes.fromhex("cefaedfe"),bytes.fromhex("cffaedfe"),bytes.fromhex("feedface"),bytes.fromhex("feedfacf")),("Mach-O executable","binary","macho-imports")),
        (header[:4]==bytes.fromhex("0061736d"),("WebAssembly","binary","inventory")),
        (header[:16]==b"SQLite format 3\x00",("SQLite","binary","sqlite-database")),
        (header[:4]==b"PK\x03\x04",("ZIP container","archive","zip-container")),
    ]
    for found,descriptor in signatures:
        if found:
            return {"language":descriptor[0],"category":descriptor[1],"analyzer":descriptor[2],
                    "evidence":"magic","candidates":[descriptor[0]],"coverage":descriptor[2]!="inventory"}
    if p.name in NAME_OVERRIDES:
        a=NAME_OVERRIDES[p.name]
        return {"language":a[0],"category":a[1],"analyzer":a[2],
                "evidence":"filename","candidates":[a[0]],"coverage":a[2]!="inventory"}
    if header.startswith(b"#!"):
        first=header.splitlines()[0].decode("utf-8",errors="replace").lower()
        for interpreter,(name,parser) in SHEBANGS.items():
            if re.search(r"(?:/|\s)"+re.escape(interpreter)+r"(?:[0-9.\s]|$)",first):
                return {"language":name,"category":"programming","analyzer":parser,
                        "evidence":"shebang","candidates":[name],"coverage":parser!="inventory"}
    filename=p.name.lower()
    registry=merged_catalog()
    matching=[ext for ext in registry if filename.endswith(ext)]
    if matching:
        ext=max(matching,key=len);items=registry[ext]
        # Builtin analyzer, if present, wins. Multiple candidates stay explicit.
        prioritized=sorted(items,key=lambda x:x["analyzer"]=="inventory")
        selected=prioritized[0]
        return {"language":selected["language"],"category":selected["category"],
                "analyzer":selected["analyzer"],"evidence":"extension:"+ext,
                "candidates":list(dict.fromkeys(x["language"] for x in items)),
                "coverage":selected["analyzer"]!="inventory",
                "ambiguous":len(items)>1}
    if b"\x00" in header:
        return {"language":"Unknown binary","category":"binary","analyzer":"inventory",
                "evidence":"content","candidates":[],"coverage":False}
    return {"language":"Unknown","category":"unknown","analyzer":"inventory",
            "evidence":"unrecognized","candidates":[],"coverage":False}

def status():
    combined=merged_catalog()
    remote=upstream_catalog()
    return {"extensions":len(combined),
            "language_labels":len({r["language"] for recs in combined.values() for r in recs}),
            "implemented_analyzers":sorted({r["analyzer"] for recs in BUILTIN.values() for r in recs if r["analyzer"]!="inventory"}),
            "upstream_loaded":bool(remote),"upstream_source":SOURCE,
            "upstream_updated":remote.get("updated") if remote else None,
            "warning":"Recognition is not dependency parsing. Ambiguous extensions need content/context; inventory does not mean understood."}
