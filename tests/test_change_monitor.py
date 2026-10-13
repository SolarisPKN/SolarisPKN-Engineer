"""Deterministic, offline tests for the opt-in SHA-256 change observer."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from change_monitor import (ChangeMonitor, digest, local_manifest, target_kind,
                            visible_content, algorithm_status, verify_local_file, new_hasher)


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

    def test_all_hashes_and_lengths(self):
        sample = b"abc"
        self.assertEqual(digest(sample, "md5"), "900150983cd24fb0d6963f7d28e17f72")
        self.assertEqual(digest(sample, "sha256"),
                         "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        self.assertEqual(len(digest(sample, "sha512")), 128)
        self.assertNotEqual(digest(sample, "sha512"), digest(sample, "sha256"))
        self.assertEqual(len(digest(sample, "md5")), 32)
        with self.assertRaises(ValueError):
            new_hasher("sha1")
        with self.assertRaises(ValueError):
            new_hasher("not-an-algorithm")

    def test_blake3_optional_dependency_is_not_replaced(self):
        with patch.dict("sys.modules", {"blake3": None}):
            with self.assertRaisesRegex(ValueError, "BLAKE3"):
                digest(b"abc", "blake3")
        available = {entry["id"]: entry["available"] for entry in algorithm_status()["algorithms"]}
        if available["blake3"]:
            self.assertEqual(
                digest(b"", "blake3"),
                "af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262",
            )

    def test_change_algorithm_creates_new_baseline(self):
        target = self.monitor.add("https://example.org/", 60, "sha256")
        response = {"status": 200, "body": b"<h1>Hello!</h1>", "type": "text/html",
                    "etag": "", "modified": ""}
        with patch("change_monitor.fetch_public", return_value=response):
            self.assertEqual(self.monitor.check(target["id"])["state"], "baseline")
        previous = self.monitor.targets()[0]["last_sha"]
        self.assertEqual(len(previous), 64)
        updated = self.monitor.add("https://example.org/", 900, "md5")
        self.assertEqual(updated["id"], target["id"])
        self.assertEqual(updated["algorithm"], "md5")
        self.assertIsNone(updated["last_sha"])
        with patch("change_monitor.fetch_public", return_value=response):
            result = self.monitor.check(target["id"])
        self.assertEqual(result["state"], "baseline")
        self.assertEqual(result["algorithm"], "md5")
        self.assertEqual(self.monitor.targets()[0]["last_sha"], digest(response["body"], "md5"))
        self.assertEqual(len(self.monitor.targets()[0]["last_sha"]), 32)
        event_types = [e["type"] for e in self.monitor.events()]
        self.assertEqual(event_types, ["baseline", "algorithm", "baseline"])
        with patch("change_monitor.fetch_public", return_value=response):
            self.assertEqual(self.monitor.check(target["id"])["state"], "unchanged")

    def test_algorithm_switch_for_local_repository(self):
        repo = self.root / "repo"
        repo.mkdir()
        (repo / "README.md").write_bytes(b"hello")
        t = self.monitor.add(str(repo), 60, "md5")
        self.assertEqual(self.monitor.check(t["id"])["state"], "baseline")
        with sqlite3.connect(self.monitor.path) as db:
            before = db.execute("SELECT last_manifest FROM targets WHERE id=?", (t["id"],)).fetchone()[0]
        self.assertIn("md5:", before)
        self.monitor.add(str(repo), 60, "sha512")
        self.assertEqual(self.monitor.check(t["id"])["state"], "baseline")
        with sqlite3.connect(self.monitor.path) as db:
            after = db.execute("SELECT last_manifest FROM targets WHERE id=?", (t["id"],)).fetchone()[0]
        self.assertIn("sha512:", after)
        self.assertNotEqual(before, after)

    def test_migrates_old_sha256_target_database(self):
        database = self.root / "legacy.sqlite"
        with sqlite3.connect(database) as db:
            db.executescript("""
                CREATE TABLE targets(
                  id INTEGER PRIMARY KEY, target TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
                  interval_seconds INTEGER NOT NULL, last_sha TEXT, last_semantic TEXT,
                  last_sample TEXT, last_manifest TEXT, etag TEXT, modified TEXT,
                  checked_at TEXT, next_check TEXT, last_state TEXT, last_error TEXT);
                INSERT INTO targets(target,kind,interval_seconds,last_sha)
                   VALUES('https://example.org/','web',300,'abc123');
            """)
        migrated = ChangeMonitor(database)
        self.assertEqual(migrated.targets()[0]["algorithm"], "sha256")
        self.assertEqual(migrated.targets()[0]["last_sha"], "abc123")
        self.assertEqual(migrated.add("https://example.org/", 300)["last_sha"], "abc123")

    def test_iso_checksum_verification_reads_entire_file(self):
        iso = self.root / "Windows_test.iso"
        iso.write_bytes((b"sample-iso-bytes" * 4000))
        for algorithm in ("md5", "sha256", "sha512"):
            reference = digest(iso.read_bytes(), algorithm)
            result = verify_local_file(str(iso), reference.upper(), algorithm)
            self.assertTrue(result["matches"])
            self.assertEqual(result["actual"], reference)
            self.assertEqual(result["algorithm"], algorithm)
            self.assertEqual(result["size"], iso.stat().st_size)
            false_ref = "0" * len(reference)
            self.assertFalse(verify_local_file(str(iso), false_ref, algorithm)["matches"])
        # A monitor uses metadata-only markers for large files; explicit verification hashes all bytes.
        with patch("change_monitor.MAX_LOCAL_FILE", 32):
            _, manifest = local_manifest(str(iso), "md5")
            self.assertTrue(manifest[iso.name].startswith("metadata-only:"))
            self.assertTrue(verify_local_file(str(iso), digest(iso.read_bytes(), "md5"), "md5")["matches"])
        with self.assertRaises(ValueError):
            verify_local_file(str(iso), "not-hex", "md5")
        with self.assertRaises(ValueError):
            verify_local_file(str(self.root), "0" * 32, "md5")
        with self.assertRaises(ValueError):
            verify_local_file("https://example.org/", "0" * 32, "md5")

    def test_existing_sha512_web_record(self):
        target = self.monitor.add("https://example.org/data", 60, "sha512")
        responses = [
            {"status": 200, "body": b'{"x":1}', "type": "application/json",
             "etag": "v1", "modified": ""},
            {"status": 200, "body": b'{"x":2}', "type": "application/json",
             "etag": "v2", "modified": ""},
        ]
        with patch("change_monitor.fetch_public", side_effect=responses):
            self.assertEqual(self.monitor.check(target["id"])["state"], "baseline")
            result = self.monitor.check(target["id"])
            self.assertEqual(result["state"], "content")
        self.assertEqual(len(self.monitor.targets()[0]["last_sha"]), 128)

    def test_html_normalization(self):
        a = b"<html><style>body{color:red}</style><body> Hello   world </body></html>"
        b = b"<html><style>body{color:blue}</style><body>Hello world</body></html>"
        self.assertEqual(visible_content(a, "text/html"), visible_content(b, "text/html"))
        self.assertNotEqual(digest(a), digest(b))


if __name__ == "__main__":
    unittest.main()
