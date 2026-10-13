"""Diff privacy, Git authorship, and change-observer regression tests.

Local-only, deterministic: the test Git repositories never contact GitHub.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from change_history import format_diff, git_file_history, safe_source, text_snapshots
from change_monitor import ChangeMonitor


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.monitor = ChangeMonitor(self.root / "private" / "events.sqlite")

    def test_plain_text_diff_is_bounded_and_visible(self):
        patch_text = format_diff("alpha\nbeta\n", "alpha\ngamma\n")
        self.assertIn("-beta", patch_text)
        self.assertIn("+gamma", patch_text)
        self.assertLessEqual(len(patch_text), 20_000)

    def test_web_diff_optin_and_no_source_without_consent(self):
        t = self.monitor.add("https://example.org/page", 60, "sha256", True)
        responses = [
            {"status": 200, "body": b"<html><p>Hello world one</p></html>",
             "type": "text/html", "etag": "", "modified": ""},
            {"status": 200, "body": b"<html><p>Hello world two</p></html>",
             "type": "text/html", "etag": "", "modified": ""},
        ]
        with patch("change_monitor.fetch_public", side_effect=responses):
            self.assertEqual(self.monitor.check(t["id"])["state"], "baseline")
            result = self.monitor.check(t["id"])
        self.assertEqual(result["state"], "content")
        details = self.monitor.events()[0]["details"]
        self.assertIn("diff", details)
        import json
        self.assertIn("Hello world two", json.loads(details)["diff"])

    def test_local_diff_optin_and_secret_exclusion(self):
        root = self.root / "repo"
        root.mkdir()
        file = root / "main.py"
        file.write_text("answer = 1\n", encoding="utf-8")
        (root / ".env").write_text("SECRET=test\n", encoding="utf-8")
        target = self.monitor.add(str(root), 60, "sha256", True)
        self.assertEqual(self.monitor.check(target["id"])["state"], "baseline")
        file.write_text("answer = 2\n", encoding="utf-8")
        result = self.monitor.check(target["id"])
        self.assertEqual(result["state"], "files")
        self.assertIn("main.py", [x["file"] for x in result["details"]["diffs"]])
        patch_text = result["details"]["diffs"][0]["patch"]
        self.assertIn("-answer = 1", patch_text)
        self.assertIn("+answer = 2", patch_text)
        self.assertNotIn(".env", text_snapshots(str(root), [".env", "main.py"]))
        self.assertFalse(safe_source("X/.ENV.local"))
        self.assertFalse(safe_source("root/private.pem"))

    def test_diff_off_by_default(self):
        root = self.root / "off"
        root.mkdir()
        file = root / "file.txt"
        file.write_text("first")
        target = self.monitor.add(str(root), 60)
        self.assertFalse(target["record_diffs"])
        self.monitor.check(target["id"])
        file.write_text("second")
        result = self.monitor.check(target["id"])
        self.assertNotIn("diffs", result["details"])
        self.assertNotIn("last_texts", self.monitor.targets()[0])
        with self.monitor.connect() as db:
            self.assertIsNone(db.execute("SELECT last_texts FROM targets").fetchone()[0])

    def test_dirty_git_does_not_claim_author(self):
        if not shutil.which("git"):
            self.skipTest("Git executable unavailable")
        root = self.root / "git-repo"
        root.mkdir()
        def git(*args):
            return subprocess.run(["git", "-C", str(root), *args],
                                  check=True, capture_output=True, text=True)
        git("init")
        # Prevent runner-specific core.autocrlf from making a fresh Git commit look dirty.
        git("config", "core.autocrlf", "false")
        git("config", "core.safecrlf", "false")
        git("config", "user.name", "Test Contributor")
        git("config", "user.email", "test@example.org")
        git("remote", "add", "origin", "https://github.com/SolarisPKN/SolarisPKN-Engineer.git")
        file = root / "hello.py"
        file.write_text("version = 1\n", encoding="utf-8")
        git("add", "hello.py")
        git("commit", "-m", "First commit")
        clean = git_file_history(str(root), ["hello.py"])
        self.assertEqual(clean["files"][0]["attribution"], "last_commit")
        self.assertEqual(clean["files"][0]["last_commit"]["author"], "Test Contributor")
        self.assertEqual(clean["files"][0]["last_commit"]["message"], "First commit")
        self.assertTrue(clean["files"][0]["last_commit"]["url"].startswith(
            "https://github.com/SolarisPKN/SolarisPKN-Engineer/commit/"))
        file.write_text("version = 2\n", encoding="utf-8")
        dirty = git_file_history(str(root), ["hello.py"])
        self.assertEqual(dirty["files"][0]["attribution"], "uncommitted")
        self.assertTrue(dirty["files"][0]["working_tree_changed"])

    def test_algorithm_migration_keeps_optin(self):
        root = self.root / "repo"
        root.mkdir()
        (root / "file.py").write_text("x = 1")
        t = self.monitor.add(str(root), 60, "md5", True)
        self.assertEqual(self.monitor.check(t["id"])["state"], "baseline")
        again = self.monitor.add(str(root), 60, "sha512", True)
        self.assertTrue(again["record_diffs"])
        self.assertIsNone(again["last_sha"])
        self.assertEqual(self.monitor.check(t["id"])["state"], "baseline")


if __name__ == "__main__":
    unittest.main()
