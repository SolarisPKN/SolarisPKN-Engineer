"""Static dependency discovery for Astro, MDX, HTML, JS/TS and CSS.

This is a conservative source resolver. Literal imports/assets are reported;
runtime expressions and template variables stay unresolved. No code execution.
"""
from __future__ import annotations
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
from project_semantics import semantic_references, resolve
from generated_files import generated_references

# Import-from covers JS/TS, Astro frontmatter and MDX ESM declarations.
ESM_FROM = re.compile(r"""(?m)^[ \t]*(?:import|export)[ \t]+(?:[^\r\n;]{0,280}?\s+from[ \t]*)?["']([^"'\\\n]+)["']""")
ESM_BARE = re.compile(r"""(?m)^[ \t]*import[ \t]*["']([^"'\\\n]+)["']""")
ESM_DYNAMIC = re.compile(r"""\b(?:require|import)\s*\(\s*["']([^"'\\\n]+)["']\s*\)""")
HTML_ATTR = re.compile(r"""\b(?:src|href|poster)\s*=\s*["']([^"'\\\n{}]+)["']""", re.IGNORECASE)
CSS_IMPORT = re.compile(r"""@import\s+(?:url\(\s*)?["']?([^"'\s)]+)""", re.IGNORECASE)
CSS_URL = re.compile(r"""url\(\s*["']?([^"')]+)""", re.IGNORECASE)
NEW_URL = re.compile(r"""new\s+URL\s*\(\s*["']([^"'\\\n]+)["']\s*,\s*import\.meta\.url""")
JS_WORKER = re.compile(r"""\bnew\s+Worker\s*\(\s*["']([^"'\\\n]+)["']""")
RESOURCE_SUFFIXES = {
    ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico",
    ".webm", ".mp4", ".mp3", ".woff", ".woff2", ".ttf",
    ".css", ".scss", ".sass", ".less", ".js", ".ts", ".jsx", ".tsx",
    ".astro", ".mdx", ".md", ".json", ".yaml", ".yml", ".html", ".xml",
}
COMPONENT_EXT = ("", ".astro", ".mdx", ".js", ".jsx", ".ts", ".tsx",
                 ".mjs", ".cjs", ".css", ".scss", ".sass", ".less",
                 ".svg", ".json", ".md", ".html")


def root_relative_candidate(root: Path, origin: Path, spec: str) -> str | None:
    """Resolve path under root; no glob expansion or paths outside root."""
    spec = spec.strip()
    if not spec or len(spec) > 700 or "\x00" in spec or "\\" in spec:
        return None
    spec = spec.split("?", 1)[0].split("#", 1)[0].strip()
    if not spec or spec.startswith(("#", "data:", "mailto:", "javascript:", "//")):
        return None
    if "://" in spec or "{" in spec or "}" in spec:
        return None
    spec = unquote(spec)
    if spec.startswith("@/"):
        search = [root / "src" / spec[2:]]
    elif spec.startswith("~/"):
        search = [root / "src" / spec[2:]]
    elif spec.startswith("/"):
        search = [root / "public" / spec.lstrip("/"), root / spec.lstrip("/")]
    elif spec.startswith("."):
        search = [origin.parent / spec]
    else:
        return None  # bare npm/package specifier: not a source file
    for stem in search:
        for ext in COMPONENT_EXT:
            candidate = Path(str(stem) + ext) if ext else stem
            for p in (candidate, candidate / "index.astro", candidate / "index.ts",
                      candidate / "index.js", candidate / "index.mdx"):
                try:
                    relative = p.resolve(strict=False).relative_to(root).as_posix()
                except ValueError:
                    continue
                if p.is_file():
                    return relative
    return None


def source_references(root: Path, relative: str, content: bytes) -> list[tuple[str, str, str]]:
    file = root / relative
    ext = file.suffix.lower()
    text = content.decode("utf-8-sig", errors="replace")
    results = []
    seen = set()

    def add(spec: str, relation: str, *, package: bool = False):
        ref = root_relative_candidate(root, file, spec) or resolve(root.resolve(strict=False), file, spec)
        if ref:
            result = ("file:" + ref, relation, "declared")
        elif package and spec and not spec.startswith((".", "/", "@/", "~/")):
            # Package specifiers are declared dependencies. Query string is ignored.
            result = ("external:package:" + spec[:350], relation, "declared")
        else:
            return
        if result not in seen:
            seen.add(result)
            results.append(result)

    if ext in (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".astro", ".mdx", ".vue", ".svelte"):
        for match in ESM_FROM.finditer(text):
            add(match.group(1), "source-import", package=True)
        for match in ESM_BARE.finditer(text):
            add(match.group(1), "source-import", package=True)
        for match in ESM_DYNAMIC.finditer(text):
            add(match.group(1), "source-import", package=True)
        for match in NEW_URL.finditer(text):
            add(match.group(1), "file-resource")
        for match in JS_WORKER.finditer(text):
            add(match.group(1), "worker-reference")
    if ext in (".astro", ".mdx", ".html", ".htm", ".vue", ".svelte", ".svg"):
        for match in HTML_ATTR.finditer(text):
            spec = match.group(1)
            if Path(spec.split("?", 1)[0]).suffix.lower() in RESOURCE_SUFFIXES:
                add(spec, "asset-reference")
    if ext in (".css", ".scss", ".sass", ".less", ".astro", ".html", ".vue", ".svelte"):
        for match in CSS_IMPORT.finditer(text):
            add(match.group(1), "style-import")
        for match in CSS_URL.finditer(text):
            add(match.group(1), "style-resource")
    results.extend(semantic_references(root, relative, content))
    results.extend(generated_references(root, relative, content))
    return list(dict.fromkeys(results))
