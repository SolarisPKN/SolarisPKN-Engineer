"""No-network tests of AI commentary, incremental cache and project workspaces."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import workspace
from ai_analysis import AIConfig, prepare_file, annotate_database, decode_response
from engineer import build_index
from export_graph import export_graph


class AIWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.temp = Path(self.tmp.name)
        self.root = self.temp / "source"
        self.root.mkdir()
        (self.root / "main.py").write_text("import helper\nprint(helper.answer())\n", encoding="utf-8")
        (self.root / "helper.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        self.db = self.temp / "graph.sqlite"

    def index(self):
        build_index(self.root, self.db, "main.py", False, 1, False, 8 * 1024 * 1024)

    @staticmethod
    def mock_model(cfg, data):
        return {
            "summary": "Archivo analizado: " + data["file"],
            "responsibilities": ["Explicar propósito"],
            "important_symbols": ["answer"], "inputs_outputs": "Devuelve un valor",
            "notes": "Documento generado por una prueba simulada.",
            "limitations": "No se ejecutó el archivo",
        }

    def test_local_project_workspace(self):
        with patch.object(workspace, "app_folder", return_value=self.temp):
            a = workspace.register(str(self.root / "main.py"))
            b = workspace.register(str(self.root / "main.py"))
            self.assertEqual(a, b)
            self.assertEqual(a["entry"], "main.py")
            self.assertEqual(len(workspace.list_projects()), 1)
            self.assertFalse((self.root / "proyecto.json").exists())
            info, dbpath, output, log = workspace.paths(a["id"])
            self.assertTrue("proyectos" in str(dbpath))
            self.assertEqual(output.parent, dbpath.parent)

    def test_local_only_by_default(self):
        AIConfig().validate()
        with self.assertRaises(ValueError):
            AIConfig(endpoint="https://example.com/v1/chat/completions").validate()
        AIConfig(provider="openai", endpoint="https://example.com/v1/chat/completions",
                 allow_remote=True).validate()
        with self.assertRaises(ValueError):
            AIConfig(provider="openai", endpoint="http://example.com/v1/chat/completions",
                     allow_remote=True).validate()

    def test_secrets_excluded(self):
        (self.root / ".env").write_text("ACCESS_TOKEN=abc", encoding="utf-8")
        with self.assertRaises(PermissionError):
            prepare_file(self.root.resolve(), "file:.env", [], 3000)
        (self.root / "sensitive.py").write_text(
            "password = 'THIS_IS_AN_EXAMPLE_LONG_VALUE_1234567890'\n", encoding="utf-8")
        with self.assertRaises(PermissionError):
            prepare_file(self.root.resolve(), "file:sensitive.py", [], 3000)

    def test_ai_incremental_cache_and_obsidian_index(self):
        self.index()
        cfg = AIConfig()
        first = annotate_database(self.db, cfg, max_files=1, responder=self.mock_model)
        self.assertEqual(first["done"], 1)
        second = annotate_database(self.db, cfg, responder=self.mock_model)
        self.assertEqual(second["done"], 1)
        self.assertGreaterEqual(second["cached"], 1)
        third = annotate_database(self.db, cfg, responder=self.mock_model)
        self.assertEqual(third["done"], 0)
        output = self.temp / "exports"
        export_graph(self.db, output)
        listing = (output / "Obsidian" / "Indice-IA.md").read_text(encoding="utf-8")
        self.assertIn("Archivo analizado:", listing)
        self.assertIn("Comentario de IA", "\n".join(p.read_text(encoding="utf-8")
                          for p in (output / "Obsidian" / "Archivos").glob("*.md")))
        graph = json.loads((output / "mapa.json").read_text(encoding="utf-8"))
        self.assertEqual(len([x for x in graph["nodes"] if "ai" in x]), 2)

    def test_llm_response_json(self):
        payload = {"message": {"content": json.dumps(self.mock_model(None, {"file": "x.py"}))}}
        self.assertTrue(decode_response(payload, "ollama")["summary"])
        with self.assertRaises(ValueError):
            decode_response({"message": {"content": "non JSON"}}, "ollama")


if __name__ == "__main__":
    unittest.main()
