"""Regression: real file use, generator provenance, reverse impact, no unsafe cleanup."""
from pathlib import Path
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from generated_files import generated_references
from engineer import references
from impact_analysis import impact_graph, impact_from_sqlite

class ImpactRegression(unittest.TestCase):
 def setUp(self):
    self.temp=tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root=Path(self.temp.name).resolve()
    def write(path,content):
        obj=self.root/path;obj.parent.mkdir(parents=True,exist_ok=True)
        obj.write_bytes(content if isinstance(content,bytes) else content.encode("utf-8"))
    self.write=write
    write("public/images/posts/hello/Portada.webp",b"image")
    write("public/images/unused.svg",b"<svg/>")
    write("src/content/blog/hello/post.json",json.dumps({"heroImage":"/images/posts/hello/Portada.webp"}))
    write("src/content/blog/hello/index-es.mdx","# hello")
    write("src/locales/es/posts/hello.json",json.dumps({"title":"hola"}))
    write("src/pages/[lang]/blog/[slug].astro",
          "const root=process.cwd(); const postDir=path.join(root,'src','content','blog',slug);\n"
          "const metaPath=path.join(postDir,'post.json');\n"
          "const meta=JSON.parse(fs.readFileSync(metaPath,'utf8'));\n")
    write("scripts/post-service.js",
          "export async function createPost(input,{rootDir=projectRoot}={}){\n"
          "const contentDir = inside(rootDir,'src','content','blog',post.slug);\n"
          "const imagesDir = inside(rootDir,'public','images','posts',post.slug);\n"
          "await atomicWrite(inside(contentDir,'post.json'), json(meta));\n}")
    write("settings.json",json.dumps({"feature":True}))
    write("scripts/read-settings.js",
          "const cfg=fs.readFileSync(path.join(process.cwd(), 'settings.json'));\n")
    write("package.json",json.dumps({"dependencies":{"astro":"^5.0.0"}}))

 def edges(self,name):
    return set(references(self.root,name,(self.root/name).read_bytes()))

 def test_image_has_source_and_consumer(self):
    post=self.edges("src/content/blog/hello/post.json")
    self.assertTrue(any(x[0]=="file:public/images/posts/hello/Portada.webp" for x in post),post)
    page=self.edges("src/pages/[lang]/blog/[slug].astro")
    self.assertTrue(any(x[0]=="file:src/content/blog/hello/post.json" for x in page),page)
    graph=[{"from":"file:"+src,"to":dst,"relation":rel,"evidence":evidence}
           for src in ("src/content/blog/hello/post.json","src/pages/[lang]/blog/[slug].astro")
           for dst,rel,evidence in self.edges(src)]
    results=impact_graph([],graph,"file:public/images/posts/hello/Portada.webp")
    self.assertEqual(results["usage_status"],"referenced")
    self.assertIn("file:src/pages/[lang]/blog/[slug].astro",
                  [n["id"] for n in results["transitive_consumers"]])
    self.assertIsNone(results["safe_to_delete"])

 def test_generator_is_not_runtime_consumer(self):
    rels=self.edges("scripts/post-service.js")
    outputs={x[0] for x in rels if x[1]=="generates"}
    self.assertIn("file:src/content/blog/hello/post.json",outputs)
    self.assertIn("file:public/images/posts/hello/Portada.webp",outputs)
    self.assertIn("file:src/locales/es/posts/hello.json",outputs)
    evidence=[{"from":"file:scripts/post-service.js","to":x[0],
               "relation":x[1],"evidence":x[2]} for x in rels]
    impact=impact_graph([],evidence,"file:public/images/posts/hello/Portada.webp")
    self.assertEqual(impact["direct_consumer_count"],0)
    self.assertTrue(impact["producers"])
    self.assertIsNone(impact["safe_to_delete"])

 def test_settings_read_is_detected(self):
    edges=self.edges("scripts/read-settings.js")
    self.assertTrue(any(x[0]=="file:settings.json" for x in edges),edges)

 def test_package_json_manifest(self):
    deps=self.edges("package.json")
    self.assertIn(("external:npm:astro@^5.0.0","package-manifest","declared"),deps)

 def test_no_references_is_not_delete_proof(self):
    status=impact_graph([],[],"file:public/images/unused.svg")
    self.assertEqual(status["usage_status"],"unresolved")
    self.assertIsNone(status["safe_to_delete"])

 def test_sqlite_reverse_walk_no_global_materialization(self):
    db=self.root/"impact.sqlite"
    with closing(sqlite3.connect(db)) as con, con:
        con.execute("CREATE TABLE nodes(key TEXT,kind TEXT,status TEXT)")
        con.execute("CREATE TABLE edges(source TEXT,target TEXT,relation TEXT,evidence TEXT)")
        con.executemany("INSERT INTO nodes VALUES(?,?,?)",[
          (x,"file","done") for x in
          ("file:src/pages/[lang]/blog/[slug].astro",
           "file:src/content/blog/hello/post.json",
           "file:public/images/posts/hello/Portada.webp")])
        con.executemany("INSERT INTO edges VALUES(?,?,?,?)",[
          ("file:src/pages/[lang]/blog/[slug].astro",
           "file:src/content/blog/hello/post.json","computed-file-path","inferred"),
          ("file:src/content/blog/hello/post.json",
           "file:public/images/posts/hello/Portada.webp","metadata-resource","declared")])
    result=impact_from_sqlite(db,"file:public/images/posts/hello/Portada.webp")
    self.assertEqual(result["transitive_consumer_count"],2,result)
    self.assertEqual(result["direct_consumer_count"],1,result)
    self.assertIsNone(result["safe_to_delete"])

if __name__=="__main__":
 unittest.main()
