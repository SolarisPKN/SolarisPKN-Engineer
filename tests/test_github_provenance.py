"""Offline GitHub compare/blame/deploy correlation tests and local Git line blame."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from github_provenance import (github_api, github_compare, github_blame,
                               github_deployment_evidence, _matches_deployment,
                               repo_name, ref_name, file_name)
from change_history import local_git_blame
from change_monitor import ChangeMonitor


SHA_A = "a" * 40
SHA_B = "b" * 40


class GitHubRemoteTests(unittest.TestCase):
    def test_input_validation_blocks_bad_repo_ref_file(self):
        for repo in ("https://github.com/org/repo", "org/repo/extra", "org/../repo",
                     "org/repo?token=x", "org//repo", "localhost/x"):
            with self.assertRaises(ValueError, msg=repo):
                repo_name(repo)
        for ref in ("", "../main", "main/../secrets", "a\nb", ".hidden", "-main"):
            with self.assertRaises(ValueError, msg=ref):
                ref_name(ref)
        for file in ("../.env", "/etc/passwd", ".git/config", r"..\..\win",
                     "src//code.py", "a\nb.py"):
            with self.assertRaises(ValueError, msg=file):
                file_name(file)

    def test_compare_sanitizes_and_bounds_patches(self):
        sample = {"total_commits": 1, "ahead_by": 1, "behind_by": 0, "status": "ahead",
                  "commits": [{"sha": SHA_A, "commit": {"message": "feat: change",
                        "author": {"name": "Ada", "date": "2026-10-13T00:00:00Z"}},
                        "author": {"login": "ada"}}],
                  "files": [{"filename": "src/main.py", "status": "modified",
                             "additions": 1, "deletions": 1, "patch": "@@\n-old\n+new\n"}]}
        with patch("github_provenance.github_api", return_value=sample) as fetch:
            answer = github_compare("SolarisPKN/Engineer", "main", SHA_B)
            self.assertEqual(answer["files"][0]["patch"], "@@\n-old\n+new\n")
            self.assertEqual(answer["commits"][0]["author"], "Ada")
            self.assertTrue(answer["commits"][0]["url"].endswith(SHA_A))
            self.assertIn("/compare/main..." + SHA_B, fetch.call_args.args[0])

    def test_graphql_blame_requires_auth_and_filters_requested_lines(self):
        result = {"data": {"repository": {"object": {"isBinary": False, "blame": {
          "ranges": [
            {"startingLine": 1, "endingLine": 12, "commit": {"oid": SHA_A,
                "messageHeadline": "initial", "author": {"name": "Alex",
                    "date": "2026-01-01T00:00:00Z", "user": {"login": "alex"}}}},
            {"startingLine": 13, "endingLine": 60, "commit": {"oid": SHA_B,
                "messageHeadline": "new", "author": {"name": "Sam",
                    "date": "2026-10-01T00:00:00Z", "user": {"login": "sam"}}}}
          ]
        }}}}}
        with patch("github_provenance.github_api", return_value=result) as fetch:
            data = github_blame("SolarisPKN/Engineer", "main", "src/main.py", 10, 25)
            self.assertEqual([(x["start"], x["end"]) for x in data["ranges"]],
                             [(10, 12), (13, 25)])
            self.assertEqual(data["ranges"][0]["github_login"], "alex")
            self.assertTrue(fetch.call_args.kwargs["require_auth"])
            variables = fetch.call_args.kwargs["payload"]["variables"]
            self.assertEqual(variables["expr"], "main:src/main.py")
        with self.assertRaises(ValueError):
            github_blame("SolarisPKN/Engineer", "main", "src/main.py", 1, 121)

    def test_match_deployment_exact_scheme_host_and_path_prefix(self):
        self.assertTrue(_matches_deployment("https://project.example.com/app/page",
                                           "https://project.example.com/app/"))
        self.assertTrue(_matches_deployment("https://project.example.com/",
                                           "https://project.example.com"))
        for mismatched in ("https://project.example.com.evil.net",
                           "http://project.example.com",
                           "https://other.example.com",
                           "https://project.example.com/application"):
            self.assertFalse(_matches_deployment(mismatched + "/page",
                                                "https://project.example.com/app"))

    def test_successful_deployment_is_evidence_not_content_proof(self):
        deployment = [{"id": 42, "sha": SHA_A, "environment": "production"}]
        status = [{"state": "success", "created_at": "2026-10-13T10:00:00Z",
                   "environment_url": "https://site.example.com/"}]
        with patch("github_provenance.github_api", side_effect=[deployment, status]):
            out = github_deployment_evidence("SolarisPKN/Engineer",
                                             "https://site.example.com/index.html")
        self.assertEqual(out["matches"][0]["sha"], SHA_A)
        self.assertFalse(out["verified_content"])
        self.assertIn("NO verifica", out["note"])

    def test_failed_deploy_is_not_attributed(self):
        with patch("github_provenance.github_api",
                   side_effect=[[{"id": 42, "sha": SHA_A}], [
                       {"state": "failure", "environment_url": "https://site.example.com/"}]]):
            result = github_deployment_evidence("SolarisPKN/Engineer",
                                                "https://site.example.com/")
        self.assertEqual(result["matches"], [])

    def test_https_transport_never_follows_redirect_and_hides_token(self):
        class FakeResponse:
            status = 302
            def getheader(self, key, default=None):
                return default
        conn = MagicMock()
        conn.getresponse.return_value = FakeResponse()
        with patch("github_provenance.http.client.HTTPSConnection", return_value=conn), \
             patch("github_provenance._token", return_value="TEST_PRIVATE_TOKEN"):
            with self.assertRaisesRegex(ValueError, "redirigió"):
                github_api("/repos/owner/repo")
        self.assertEqual(conn.request.call_args.args[1], "/repos/owner/repo")
        self.assertEqual(conn.request.call_args.kwargs["headers"]["Authorization"],
                         "Bearer TEST_PRIVATE_TOKEN")
        self.assertEqual(conn.close.call_count, 1)


class LocalBlameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_line_blame_and_uncommitted_warning(self):
        if not shutil.which("git"):
            self.skipTest("git executable not available")
        def git(*args):
            return subprocess.run(["git", "-C", str(self.root), *args],
                                  check=True, capture_output=True, text=True)
        git("init")
        git("config", "core.autocrlf", "false")
        git("config", "user.name", "Review Tester")
        git("config", "user.email", "review@example.com")
        source = self.root / "main.py"
        source.write_text("first\nsecond\n", encoding="utf-8")
        git("add", "main.py")
        git("commit", "-m", "Initial content")
        blame = local_git_blame(str(self.root), "main.py", 1, 2)
        self.assertEqual(len(blame["lines"]), 2)
        self.assertEqual(blame["lines"][0]["author"], "Review Tester")
        source.write_text("first\nlocally changed\n", encoding="utf-8")
        dirty = local_git_blame(str(self.root), "main.py", 1, 2)
        self.assertTrue(dirty["lines"][1]["uncommitted"])
        self.assertIsNone(dirty["lines"][1]["author"])
        with self.assertRaises(ValueError):
            local_git_blame(str(self.root), "../secret", 1, 2)

    def test_monitor_github_link_only_for_web_https(self):
        monitor = ChangeMonitor(self.root / "private" / "monitor.sqlite")
        with self.assertRaises(ValueError):
            monitor.add("http://example.com/", 60, "sha256", False, "owner/repo")
        with self.assertRaises(ValueError):
            monitor.add(str(self.root), 60, "sha256", False, "owner/repo")
        web = monitor.add("https://example.com/", 60, "sha256", False, "owner/repo")
        self.assertEqual(web["github_repo"], "owner/repo")
        from unittest.mock import patch
        responses = [
            {"status": 200, "body": b"before", "type": "text/plain", "etag": "", "modified": ""},
            {"status": 200, "body": b"after", "type": "text/plain", "etag": "", "modified": ""}
        ]
        with patch("change_monitor.fetch_public", side_effect=responses), \
             patch("change_monitor.github_deployment_evidence",
                   return_value={"matches": [], "verified_content": False, "note": "None"}) as dep:
            monitor.check(web["id"])
            result = monitor.check(web["id"])
        self.assertIn("deployment", result["details"])
        dep.assert_called_once_with("owner/repo", "https://example.com/")


if __name__ == "__main__":
    unittest.main()
