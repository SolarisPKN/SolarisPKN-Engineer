"""Regression tests of DPAPI encrypted-at-rest credential vault.

Only synthetic test values are used; no user secrets, network or API requests.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import credential_vault as vault


@unittest.skipUnless(os.name == "nt", "Requires native Windows DPAPI")
class VaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        target = Path(self.temp.name) / "credentials.dpapi.json"
        self.patch = patch.object(vault, "vault_path", return_value=target)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.target = target

    def test_secure_roundtrip_no_plaintext(self):
        # Synthetic fixture only — not an actual credential.
        password = "synthetic-test-password-1234"
        identity = "sample project:Private ZIP With Spaces.zip"
        vault.save_secret("archive", identity, password)
        self.assertTrue(vault.has_secret("archive", identity))
        raw = self.target.read_text(encoding="utf-8")
        self.assertNotIn(password, raw)
        self.assertNotIn(identity, raw)
        self.assertEqual(vault.load_secret("archive", identity), password)
        self.assertEqual(json.loads(raw)["version"], 1)
        self.assertTrue(vault.delete_secret("archive", identity))
        self.assertIsNone(vault.load_secret("archive", identity))

    def test_secrets_are_partitioned(self):
        vault.save_secret("github", "default", "synthetic-gh-token")
        vault.save_secret("ai-provider", "openai", "synthetic-api-token")
        self.assertEqual(vault.load_secret("github", "default"), "synthetic-gh-token")
        self.assertEqual(vault.load_secret("ai-provider", "openai"), "synthetic-api-token")
        self.assertIsNone(vault.load_secret("ai-provider", "gemini"))

    def test_invalid_identifier_rejected(self):
        with self.assertRaises(ValueError):
            vault.save_secret("unknown", "default", "test")
        with self.assertRaises(ValueError):
            vault.save_secret("github", "a\nb", "test")

    def test_bad_stored_file_not_overwritten(self):
        self.target.write_text("not json", encoding="utf-8")
        with self.assertRaises(vault.VaultError):
            vault.save_secret("github", "default", "synthetic")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "not json")


if __name__ == "__main__":
    unittest.main()
