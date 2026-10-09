"""Regression tests for colored file states and conservative encryption detection."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr, closing
from io import StringIO
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sys import path as import_path
import_path.insert(0, str(Path(__file__).resolve().parents[1]))

from encrypted_formats import encryption_reason
from engineer import build_index
from api_extensions import aggregate_status
from live_view import graph_snapshot, status_snapshot, render_live_map


class StatusColorsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.db = Path(self.temp.name) / "graph.sqlite"
        (self.root / "main.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "encrypted.bin").write_bytes(b"Salted__" + b"12345678" + b"x" * 32)

    def test_known_encrypted_marker(self):
        self.assertIn("OpenSSL", encryption_reason(self.root / "encrypted.bin"))
        self.assertIsNone(encryption_reason(self.root / "main.py"))

    def test_status_persists_and_live_view_preserves_reason(self):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            build_index(self.root, self.db, None, True, 1, False, 0)
        with closing(sqlite3.connect(str(self.db))) as db, db:
            statuses = dict(db.execute("SELECT key,status FROM nodes"))
            reason = db.execute(
                "SELECT error FROM nodes WHERE key='file:encrypted.bin'"
            ).fetchone()[0]
        self.assertEqual(statuses["file:encrypted.bin"], "encrypted")
        self.assertEqual(statuses["file:main.py"], "done")
        self.assertIn("OpenSSL", reason)
        snapshot = graph_snapshot(self.db)
        self.assertIn("encrypted", [n["status"] for n in snapshot["nodes"]])
        self.assertEqual(status_snapshot(self.db)["statuses"]["file:main.py"], "done")
        html = render_live_map(
            self.db, Path(__file__).resolve().parents[1] / "visualizer.html"
        ).decode("utf-8")
        self.assertIn("file:encrypted.bin", html)
        self.assertIn("encrypted", html)

    def test_directory_aggregates_red_with_encryption(self):
        self.assertEqual(aggregate_status({"done": 3}), "done")
        self.assertEqual(aggregate_status({"done": 4, "pending": 1}), "pending")
        self.assertEqual(aggregate_status({"done": 4, "encrypted": 1}), "encrypted")
        self.assertEqual(aggregate_status({"failed": 2}), "failed")
        self.assertEqual(aggregate_status({"external": 1}), "external")


if __name__ == "__main__":
    unittest.main()
