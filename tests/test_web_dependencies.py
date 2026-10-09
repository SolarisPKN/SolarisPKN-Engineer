"""Regression coverage for Astro/MDX/CSS dependencies found in SolarisPKN-Labs."""
from __future__ import annotations

from contextlib import redirect_stdout, closing
from io import StringIO
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sys import path as import_path
import_path.insert(0, str(Path(__file__).resolve().parents[1]))

from engineer import build_index
from web_dependencies import root_relative_candidate, source_references


class WebDependenciesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "website"
        self.root.mkdir()
        self.db = Path(self.temp.name) / "map.sqlite"
        for name, body in {
            "src/pages/index.astro": (
                "---\nimport Card from '../components/Card.astro';\n"
                "import '../styles/site.css';\nimport badge from '@/assets/badge.svg';\n---\n"
                "<Card />\n<img src='/images/banner.webp' />\n"
            ),
            "src/components/Card.astro": "<p>Card</p>",
            "src/styles/site.css": "body { background: url('/images/banner.webp'); }",
            "src/assets/badge.svg": "<svg/>",
            "public/images/banner.webp": "sample-not-an-image",
            "src/content/post.mdx": "import Card from '../components/Card.astro'\n<Card />",
            "src/components/Extra.tsx": "import '../styles/site.css'\nexport default function Extra(){}",
        }.items():
            f = self.root / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(body, encoding="utf-8")

    def test_astro_components_assets_and_aliases(self):
        f = self.root / "src/pages/index.astro"
        edges = set(source_references(self.root, "src/pages/index.astro", f.read_bytes()))
        expected = {
            ("file:src/components/Card.astro", "source-import", "declared"),
            ("file:src/styles/site.css", "source-import", "declared"),
            ("file:src/assets/badge.svg", "source-import", "declared"),
            ("file:public/images/banner.webp", "asset-reference", "declared"),
        }
        self.assertTrue(expected.issubset(edges), edges)

    def test_mdx_and_css(self):
        mdx = source_references(
            self.root, "src/content/post.mdx",
            (self.root / "src/content/post.mdx").read_bytes()
        )
        self.assertIn(
            ("file:src/components/Card.astro", "source-import", "declared"), mdx
        )
        css = source_references(
            self.root, "src/styles/site.css",
            (self.root / "src/styles/site.css").read_bytes()
        )
        self.assertIn(
            ("file:public/images/banner.webp", "style-resource", "declared"), css
        )

    def test_does_not_escape_project(self):
        f = self.root / "src/pages/index.astro"
        self.assertIsNone(root_relative_candidate(self.root, f, "../../../outside.py"))
        self.assertIsNone(root_relative_candidate(self.root, f, "https://example.com/js"))

    def test_recursive_index_reaches_imports(self):
        with redirect_stdout(StringIO()):
            build_index(self.root, self.db, "src/pages/index.astro",
                        False, 2, False, 0)
        with closing(sqlite3.connect(str(self.db))) as conn, conn:
            nodes = dict(conn.execute("SELECT key,status FROM nodes"))
            edges = set(conn.execute("SELECT source,target FROM edges"))
        self.assertEqual(nodes["file:src/components/Card.astro"], "done")
        self.assertIn(
            ("file:src/pages/index.astro", "file:src/styles/site.css"), edges
        )
        self.assertIn(
            ("file:src/styles/site.css", "file:public/images/banner.webp"), edges
        )


if __name__ == "__main__":
    unittest.main()
