"""Portable regression suite modeling confirmed patterns in SolarisPKN-Labs.

All fixtures are synthetic. Tests never import or execute analyzed JS/Astro source.
"""
from __future__ import annotations
from contextlib import redirect_stdout, closing
from io import StringIO
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from sys import path as sys_path
sys_path.insert(0, str(Path(__file__).resolve().parents[1]))

from engineer import references, build_index
from graph_diagnostics import audit_graph
from graph_structure import enrich_with_directories
from project_semantics import resolve, aliases

class LabsSemantics(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        def write(path,data):
            p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True)
            if isinstance(data,str):p.write_text(data,encoding="utf-8")
            else:p.write_bytes(data)
        self.write=write
        write("astro.config.mjs","vite: { resolve: { alias: {'@styles': '/src/styles', '@locales':'/src/locales'} } }")
        write("src/styles/global.css","@font-face { src: url('/fonts/inter.woff2') format('woff2'); }")
        write("public/fonts/inter.woff2",b"synthetic font file")
        write("src/layouts/Layout.astro","""---
import '@styles/global.css';
const lang = Astro.props.lang;
const common = await import("""+chr(96)+"../locales/"+chr(36)+"{lang}/common.json"+chr(96)+""");
---
<link rel="preload" href="/fonts/inter.woff2" as="font" />
""")
        for lang in ("es","en"):
            write(f"src/locales/{lang}/common.json",'{"nav":{"inicio":"Test"}}')
            write(f"src/locales/{lang}/posts/hello.json",'{"title":"Test"}')
            write(f"src/content/blog/hello/index-{lang}.mdx","![cover](/images/posts/hello/Portada.webp)")
        write("src/content/blog/hello/post.json",json.dumps({"heroImage":"/images/posts/hello/Portada.webp"}))
        write("public/images/posts/hello/Portada.webp",b"fake image")
        write("src/pages/[lang]/blog/[slug].astro","""---
const root = process.cwd();
const postDir = path.join(root,'src','content','blog',slug);
const metaPath = path.join(postDir, 'post.json');
const localePath = path.join(root, 'src','locales',lang,'posts', """+chr(96)+chr(36)+"{slug}.json"+chr(96)+""");
const locale = await import("""+chr(96)+"../../../locales/"+chr(36)+"{lang}/common.json"+chr(96)+""");
---
""")
        write("public/images/portfolio/estudios/Udemy/Cloud Computing.webp",b"image")
        write("src/locales/es/portfolioskills.json",
              json.dumps({"certificados":[{"imagen":"/images/portfolio/estudios/Udemy/Cloud Computing.webp"}]}))
        write("src/pages/[lang]/portfolio.astro",
              '<img src={'+chr(96)+'/images/portfolio/carruseltech/'+chr(36)+'{tech}.svg'+chr(96)+'} />')
        write("public/images/portfolio/carruseltech/Astro.svg","<svg/>")
        write("public/images/portfolio/carruseltech/CSS.svg","<svg/>")
        write("public/images/unused.webp",b"intentionally unreferenced")

    def targets(self,file):
        p=self.root/file
        return set(references(self.root,file,p.read_bytes()))

    def test_fonts_and_aliases(self):
        self.assertIn(("file:src/styles/global.css","source-import","declared"),
                      self.targets("src/layouts/Layout.astro"))
        self.assertIn(("file:public/fonts/inter.woff2","style-resource","declared"),
                      self.targets("src/styles/global.css"))
        self.assertTrue(any(t=="file:public/fonts/inter.woff2" for t,_,_ in
                            self.targets("src/layouts/Layout.astro")))

    def test_blog_hero_locales_and_content(self):
        edges=self.targets("src/content/blog/hello/post.json")
        self.assertIn(("file:public/images/posts/hello/Portada.webp","metadata-resource","declared"),edges)
        self.assertTrue(any(t=="file:src/locales/es/posts/hello.json" for t,_,_ in edges))
        page=self.targets("src/pages/[lang]/blog/[slug].astro")
        self.assertTrue(any(t=="file:src/content/blog/hello/post.json" for t,_,_ in page))
        self.assertTrue(any(t=="file:src/locales/en/common.json" for t,_,_ in page))
        self.assertTrue(any(t=="file:src/locales/es/posts/hello.json" for t,_,_ in page))

    def test_spanish_certification_and_template_images(self):
        cert=self.targets("src/locales/es/portfolioskills.json")
        self.assertIn(("file:public/images/portfolio/estudios/Udemy/Cloud Computing.webp",
                       "metadata-resource","declared"),cert)
        img=self.targets("src/pages/[lang]/portfolio.astro")
        self.assertTrue(any(t=="file:public/images/portfolio/carruseltech/Astro.svg" for t,_,_ in img))
        self.assertTrue(any(t=="file:public/images/portfolio/carruseltech/CSS.svg" for t,_,_ in img))

    def test_no_fabricated_code_links_for_unused_asset(self):
        db=self.root.parent/"map.sqlite"
        with redirect_stdout(StringIO()):
            build_index(self.root,db,None,True,1,False,0)
        with closing(sqlite3.connect(db)) as cx, cx:
            nodes=[{"id":k,"type":kind,"status":state} for k,kind,state
                   in cx.execute("SELECT key,kind,status FROM nodes")]
            links=[{"from":a,"to":b,"relation":r,"evidence":e} for a,b,r,e
                   in cx.execute("SELECT source,target,relation,evidence FROM edges")]
        enriched,combined=enrich_with_directories(nodes,links)
        self.assertIn("dir:src/locales/es",{n["id"] for n in enriched})
        self.assertTrue(any(e["from"]=="dir:src/locales/es" and
                            e["to"]=="file:src/locales/es/common.json" for e in combined))
        diag=audit_graph(enriched,combined)
        self.assertIn("file:public/images/unused.webp",
                      diag["categories"]["unreferenced_public_assets"]["files"])
        self.assertFalse(any(e["to"]=="file:public/images/unused.webp" and
                             e["relation"]!="contains" for e in combined))

if __name__=="__main__":
    unittest.main()
