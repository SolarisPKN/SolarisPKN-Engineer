"""Inference of build outputs and producer provenance, NOT runtime dependency usage.

Recognizes actual writer + path construction conventions. A generated file
can be recreated, but that does not mean it can be deleted from deployments.
No code execution or synthetic edges to non-existent files.
"""
from pathlib import Path
import re

OUTPUT_WRITES=re.compile(r"\b(?:writeFile|writeFileSync|atomicWrite|copyFile|copyFileSync|outputFile|outputFileSync)\s*\(")

def _file(root,path):
    try:
        p=path.resolve(strict=False)
        return "file:"+p.relative_to(root).as_posix() if p.is_file() else None
    except (ValueError,OSError):
        return None

def generated_references(root:Path,relative:str,content:bytes):
    root=root.resolve(strict=False)
    file=root/relative
    if file.suffix.lower() not in {".js",".ts",".mjs",".cjs",".jsx",".tsx",".py"}:
        return []
    text=content.decode("utf-8-sig",errors="replace")
    # Do not claim output generation if code only refers to a file path in a reader.
    if not OUTPUT_WRITES.search(text):
        return []
    found=[]
    # Common post-generation pattern, based on *declarations found in source*.
    # Files are linked only where a literal destination prefix is present.
    if (re.search(r"['\"]src['\"]\s*,\s*['\"]content['\"]\s*,\s*['\"]blog['\"]",text)
        and "post.json" in text and re.search(r"createPost\s*\(",text)):
        base=root/"src"/"content"/"blog"
        if base.is_dir():
            for project in base.iterdir():
                if not project.is_dir() or not re.fullmatch("[a-zA-Z0-9_-]{1,160}",project.name):
                    continue
                for name in ("post.json","index-es.mdx","index-en.mdx"):
                    ref=_file(root,project/name)
                    if ref:
                        found.append((ref,"generates","inferred"))
                # A post generator may also create matching locale and assets.
                for language in ("es","en"):
                    ref=_file(root,root/"src"/"locales"/language/"posts"/(project.name+".json"))
                    if ref:
                        found.append((ref,"generates","inferred"))
                if re.search(r"['\"]images['\"]\s*,\s*['\"]posts['\"]",text):
                    assets=root/"public"/"images"/"posts"/project.name
                    if assets.is_dir():
                        for image in assets.iterdir():
                            ref=_file(root,image)
                            if ref:found.append((ref,"generates","inferred"))
    # Literal write paths (e.g. writeFileSync('./settings.json', data))
    literal_write=re.compile(r"\b(?:writeFile|writeFileSync|atomicWrite)\s*\(\s*['\"]([^'\"\n]{1,500})['\"]")
    for match in literal_write.finditer(text):
        raw=match.group(1)
        if raw.startswith(("./","../")):
            ref=_file(root,file.parent/raw)
            if ref:found.append((ref,"generates","declared"))
    return list(dict.fromkeys(found))
