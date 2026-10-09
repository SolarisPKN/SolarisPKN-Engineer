"""Self-tests for dependency recursion, deduplication, persistence and export."""
from __future__ import annotations

from contextlib import redirect_stdout, closing
from io import StringIO
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engineer import build_index, references
from export_graph import export_graph


def write(root, name, contents):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(contents, encoding="utf-8")


class EngineerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.root = self.work / "target"
        self.root.mkdir()
        self.db = self.work / "index.sqlite"
        # Explicitly exercises A -> B,C; B -> E,F; F -> H; C -> H,I.
        write(self.root, "a.py", "import b\nimport c\n")
        write(self.root, "b.py", "import e\nimport f\n")
        write(self.root, "c.py", "import h\nimport i\n")
        write(self.root, "e.py", "x = 1\n")
        write(self.root, "f.py", "import h\n")
        write(self.root, "h.py", "x = 2\n")
        write(self.root, "i.py", "x = 3\n")

    def run_index(self, workers=1, refresh=False):
        with redirect_stdout(StringIO()):
            build_index(self.root, self.db, "a.py", False, workers, refresh, 2_000_000)

    def test_recursive_dag_no_duplicate(self):
        self.run_index()
        with closing(sqlite3.connect(self.db)) as db, db:
            count = db.execute("SELECT count(*) FROM nodes WHERE kind='file'").fetchone()[0]
            edges = set(db.execute("SELECT source,target FROM edges"))
            pending = db.execute("SELECT count(*) FROM nodes WHERE status!='done'").fetchone()[0]
        self.assertEqual(count, 7)
        self.assertEqual(len(edges), 7)  # A2 + B2 + C2 + F1
        self.assertEqual(pending, 0)
        self.assertIn(("file:c.py", "file:h.py"), edges)
        self.assertIn(("file:f.py", "file:h.py"), edges)

    def test_multiple_workers(self):
        self.run_index(workers=4)
        with closing(sqlite3.connect(self.db)) as db, db:
            self.assertEqual(db.execute("SELECT count(*) FROM nodes WHERE status='done'").fetchone()[0], 7)

    def test_cycle_terminates(self):
        write(self.root, "h.py", "import a\n")
        self.run_index()
        with closing(sqlite3.connect(self.db)) as db, db:
            self.assertEqual(db.execute("SELECT count(*) FROM nodes").fetchone()[0], 7)
            self.assertIsNotNone(db.execute("SELECT 1 FROM edges WHERE source='file:h.py' AND target='file:a.py'").fetchone())

    def test_resume_does_not_repeat_finished_files(self):
        self.run_index()
        self.run_index()
        with closing(sqlite3.connect(self.db)) as db, db:
            self.assertEqual(db.execute("SELECT count(*) FROM edges").fetchone()[0], 7)

    def test_refresh_replaces_old_edges(self):
        self.run_index()
        write(self.root, "b.py", "import e\n")
        self.run_index(refresh=True)
        with closing(sqlite3.connect(self.db)) as db, db:
            self.assertIsNone(db.execute("SELECT 1 FROM edges WHERE source='file:b.py' AND target='file:f.py'").fetchone())

    def test_external_reference_is_not_scanned(self):
        write(self.root, "e.py", "import missing_package_xyz\n")
        self.run_index()
        with closing(sqlite3.connect(self.db)) as db, db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM nodes WHERE key='external:python:missing_package_xyz'").fetchone())

    def test_export(self):
        self.run_index()
        output = self.work / "out"
        with redirect_stdout(StringIO()):
            export_graph(self.db, output)
        self.assertTrue((output / "mapa.json").is_file())
        self.assertTrue((output / "mapa.html").is_file())
        self.assertTrue((output / "Obsidian" / "Inicio.md").is_file())
        self.assertIn("file:a.py", (output / "mapa.json").read_text(encoding="utf-8"))

    def test_binary_heuristics_only(self):
        from binary_readers import inspect_binary
        result = inspect_binary(self.root / "sample.exe", b"\x00USER32.dll\x00some random bytes")
        self.assertIn(("USER32.dll", "heuristic"), result)


if __name__ == "__main__":
    unittest.main()
