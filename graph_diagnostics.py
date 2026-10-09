"""Dependency gap audit. Structural containment is never counted as code usage."""
from __future__ import annotations
from collections import Counter, defaultdict
from pathlib import PurePosixPath

MEDIA={".png",".jpg",".jpeg",".gif",".webp",".avif",".svg",".woff",".woff2",
       ".ttf",".otf",".mp4",".mp3",".ico",".pdf",".webm"}
ENTRY_NAMES={"package.json","package-lock.json","astro.config.mjs","vite.config.ts",
             "tsconfig.json","jsconfig.json","readme.md","readme.es.md","pyproject.toml",
             "requirements.txt","cargo.toml","go.mod","dockerfile","makefile",
             "manifest.webmanifest","robots.txt","sitemap.xml","settings.json"}
def audit_graph(nodes,edges):
    files=[n for n in nodes if n.get("id","").startswith("file:")]
    incoming=Counter()
    outgoing=Counter()
    unknown_targets=Counter()
    generated=Counter()
    for e in edges:
        relation=e.get("relation")
        if relation=="contains":continue
        a,b=e.get("from",""),e.get("to","")
        if relation in ("generates","produces","writes-output"):
            generated[b]+=1
            continue
        outgoing[a]+=1;incoming[b]+=1
        if b.startswith("external:"):unknown_targets[b]+=1
    classes=defaultdict(list)
    for n in files:
        key=n["id"]
        if generated[key] and incoming[key]==0:
            classes["produced_but_no_consumers_detected"].append(key)
        if incoming[key]>0:
            classes["referenced_by_other_files"].append(key)
            continue
        rel=key.removeprefix("file:")
        p=PurePosixPath(rel)
        is_entry=(p.name.lower() in ENTRY_NAMES or str(p).startswith("src/pages/") or
                  str(p).startswith("scripts/") or str(p).startswith(".github/"))
        if is_entry:
            classes["possible_entrypoints"].append(key)
        elif str(p).startswith("public/"):
            classes["unreferenced_public_assets"].append(key)
        elif p.suffix.lower() in MEDIA:
            classes["unreferenced_media"].append(key)
        else:
            classes["requires_review"].append(key)
    return {
       "explanation":"A file with zero outgoing dependencies may still be used by others; zero incoming is not proof it is unused. Structural membership excluded.",
       "indexed_files":len(files),
       "semantically_referenced_files":len(classes["referenced_by_other_files"]),
       "files_with_outgoing_references":sum(outgoing[n["id"]]>0 for n in files),
       "semantic_relationships":sum(1 for e in edges if e.get("relation") not in ("contains","generates","produces","writes-output")),
       "production_relationships":sum(1 for e in edges if e.get("relation") in ("generates","produces","writes-output")),
       "structural_relationships":sum(1 for e in edges if e.get("relation")=="contains"),
       "unresolved_external_references":dict(unknown_targets.most_common(250)),
       "categories":{k:{"count":len(v),"files":v} for k,v in classes.items()},
    }
