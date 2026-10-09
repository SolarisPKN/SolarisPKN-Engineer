"""Public sample export must discard private/untracked paths and source content."""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from labs_public_example import sanitized_snapshot

class PublicExampleTests(unittest.TestCase):
    def test_only_public_tracked_paths_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/"test.sqlite"
            with closing(sqlite3.connect(str(db))) as conn:
                with conn:
                    conn.execute("CREATE TABLE nodes(key TEXT,status TEXT)")
                    conn.execute("CREATE TABLE edges(source TEXT,target TEXT,relation TEXT,evidence TEXT)")
                    conn.executemany("INSERT INTO nodes VALUES (?,?)",[
                       ("file:src/pages/home.astro","done"),
                       ("file:public/images/banner.webp","done"),
                       ("file:private-local/settings.json","done")])
                    conn.executemany("INSERT INTO edges VALUES (?,?,?,?)",[
                       ("file:src/pages/home.astro","file:public/images/banner.webp","asset-reference","declared"),
                       ("file:private-local/settings.json","file:public/images/banner.webp","file-read","declared")])
            snapshot,audit=sanitized_snapshot(db,
                {"src/pages/home.astro","public/images/banner.webp"},
                "a"*40)
            wire=json.dumps(snapshot,ensure_ascii=False)
            self.assertNotIn("private-local",wire)
            self.assertNotIn(str(tmp),wire)
            self.assertEqual(sum(x["relation"]=="asset-reference" for x in snapshot["edges"]),1)
            self.assertIsNone(next(x for x in snapshot["nodes"] if x["id"]=="file:public/images/banner.webp")["usage"]["safe_to_delete"])

if __name__=="__main__":
    unittest.main()
