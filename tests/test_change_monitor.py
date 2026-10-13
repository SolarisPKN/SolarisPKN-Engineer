"""Deterministic, offline tests for the opt-in SHA-256 change observer."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from change_monitor import ChangeMonitor, digest, local_manifest, target_kind, visible_content


class ChangeMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.monitor = ChangeMonitor(self.root / "private" / "changes.sqlite")

    def test_local_repository_baseline_modify_add_delete(self):
        repo = self.root / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main")
        (repo / "main.py").write_text("print(1)", encoding="utf-8")
        target = self.monitor.add(str(repo), 60)
        first = self.monitor.check(target["id"])
        self.assertEqual(first["state"], "baseline")
        self.assertEqual(len(self.monitor.events()), 1)
        self.assertEqual(self.monitor.check(target["id"])["state"], "unchanged")
        self.assertEqual(len(self.monitor.events()), 1)
        (repo / "main.py").write_text("print(2)", encoding="utf-8")
        (repo / "new.py").write_text("x = 1", encoding="utf-8")
        second = self.monitor.check(target["id"])
        self.assertEqual(second["state"], "files")
        self.assertEqual(second["details"]["added"], ["new.py"])
        self.assertEqual(second["details"]["modified"], ["main.py"])
        (repo / "new.py").unlink()
        third = self.monitor.check(target["id"])
        self.assertEqual(third["details"]["removed"], ["new.py"])
        manifest_hash, manifest = local_manifest(str(repo))
        self.assertNotIn(".git/HEAD", manifest)
        self.assertEqual(len(manifest_hash), 64)
        self.assertEqual(len(self.monitor.events()), 3)

    def test_public_web_sha_and_semantic_changes(self):
        target = self.monitor.add("https://example.org/index.html", 300)
        responses = [
            {"status": 200, "body": b"<html><body>Hello</body></html>",
             "type": "text/html", "etag": "v1", "modified": ""},
            {"status": 200, "body": b"<html><body>  Hello  </body></html>",
             "type": "text/html", "etag": "v2", "modified": ""},
            {"status": 200, "body": b"<html><body>Goodbye</body></html>",
             "type": "text/html", "etag": "v3", "modified": ""},
            {"status": 304}
        ]
        with patch("change_monitor.fetch_public", side_effect=responses) as fake:
            self.assertEqual(self.monitor.check(target["id"])["state"], "baseline")
            self.assertEqual(self.monitor.check(target["id"])["state"], "technical")
            result = self.monitor.check(target["id"])
            self.assertEqual(result["state"], "content")
            self.assertEqual(result["details"]["previous_excerpt"], "Hello")
            self.assertEqual(result["details"]["current_excerpt"], "Goodbye")
            self.assertEqual(self.monitor.check(target["id"])["state"], "unchanged")
        self.assertEqual(fake.call_count, 4)
        self.assertEqual(len(self.monitor.events()), 3)

    def test_database_does_not_store_web_body(self):
        target = self.monitor.add("https://example.org/", 60)
        token = "ONE_TIME_TEST_PRIVATE_DATA_DO_NOT_SAVE"
        with patch("change_monitor.fetch_public",
                   return_value={"status": 200, "body": token.encode(),
                                 "type": "text/plain", "etag": "", "modified": ""}):
            self.assertEqual(self.monitor.check(target["id"])["state"], "baseline")
        with sqlite3.connect(self.monitor.path) as db:
            row = db.execute("SELECT last_sha,last_sample FROM targets WHERE id=?", (target["id"],)).fetchone()
            self.assertEqual(row[0], hashlib.sha256(token.encode()).hexdigest())
            # Text samples are stored locally for change diagnosis: not in the public Git repo.
            self.assertEqual(row[1], token)

    def test_error_keeps_last_known_hash(self):
        target = self.monitor.add("https://example.org/", 60)
        with patch("change_monitor.fetch_public",
                   return_value={"status": 200, "body": b"ok",
                                 "type": "text/plain", "etag": "", "modified": ""}):
            self.monitor.check(target["id"])
        old_sha = self.monitor.targets()[0]["last_sha"]
        with patch("change_monitor.fetch_public", side_effect=ValueError("HTTP 503")):
            self.assertEqual(self.monitor.check(target["id"])["state"], "error")
        self.assertEqual(self.monitor.targets()[0]["last_sha"], old_sha)

    def test_removing_target_deletes_history(self):
        target = self.monitor.add(str(self.root), 60)
        self.monitor.check(target["id"])
        self.assertTrue(self.monitor.events())
        self.assertTrue(self.monitor.remove(target["id"])["removed"])
        self.assertEqual(self.monitor.events(), [])
        self.assertEqual(self.monitor.targets(), [])

    def test_reject_private_host_and_invalid_intervals(self):
        for url in ("http://localhost/", "http://127.0.0.1/",
                    "http://169.254.169.254/", "ftp://example.org/",
                    "https://user:pass@example.org/"):
            with self.assertRaises(ValueError, msg=url):
                kind, value = target_kind(url)
                if kind == "web":
                    from change_monitor import public_ip_for
                    public_ip_for(__import__("urllib.parse", fromlist=["urlsplit"]).urlsplit(value).hostname, 443)
        with self.assertRaises(ValueError):
            self.monitor.add(str(self.root), 1)
        with self.assertRaises(ValueError):
            self.monitor.add(str(self.root), 90000)

    def test_public_dns_rejects_private_records_and_pins_public(self):
        from change_monitor import public_ip_for
        with patch("change_monitor.socket.getaddrinfo",
                   return_value=[(2, 1, 6, "", ("8.8.8.8", 443)),
                                 (2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(ValueError):
                public_ip_for("example.org", 443)
        with patch("change_monitor.socket.getaddrinfo",
                   return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]):
            self.assertEqual(public_ip_for("example.org", 443), "8.8.8.8")

    def test_html_normalization(self):
        a = b"<html><style>body{color:red}</style><body> Hello   world </body></html>"
        b = b"<html><style>body{color:blue}</style><body>Hello world</body></html>"
        self.assertEqual(visible_content(a, "text/html"), visible_content(b, "text/html"))
        self.assertNotEqual(digest(a), digest(b))


if __name__ == "__main__":
    unittest.main()
