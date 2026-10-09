"""Windows tray supervisor regression tests (no real tray or child started)."""
from __future__ import annotations
from pathlib import Path
from contextlib import closing
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tray_launcher
from engineer import build_index
from server import State


class TrayTests(unittest.TestCase):
    def test_tray_controls_only_its_subprocess(self):
        manager = tray_launcher.Supervisor()
        with patch.object(tray_launcher, "port_is_busy", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "8765"):
                manager.start()
        self.assertIsNone(manager.proc)

    def test_python_entrypoint_uses_server_child(self):
        command = tray_launcher.server_command()
        self.assertIn("server.py", " ".join(command))
        self.assertIn("--no-browser", command)

    def test_stop_request_prevents_new_jobs(self):
        state = State()
        state.stop_event.set()
        with self.assertRaisesRegex(ValueError, "shutting down"):
            state.begin("test", "scan", {})

    def test_cancel_before_scan_preserves_pending_state(self):
        with tempfile.TemporaryDirectory() as root_temp:
            root = Path(root_temp)
            source = root / "source"
            source.mkdir()
            (source / "main.py").write_text("import helper\n", encoding="utf-8")
            (source / "helper.py").write_text("x = 5\n", encoding="utf-8")
            db = root / "index.sqlite"
            cancel = threading.Event()
            cancel.set()
            build_index(source, db, "main.py", False, 1, False, 8_000_000,
                        cancel_event=cancel)
            import sqlite3
            with closing(sqlite3.connect(str(db))) as connection, connection:
                states = dict(connection.execute("SELECT key,status FROM nodes"))
            self.assertEqual(states["file:main.py"], "pending")


if __name__ == "__main__":
    unittest.main()
