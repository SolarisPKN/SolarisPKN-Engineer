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

    @unittest.skipUnless(os.name == "nt", "Win32 tray structure requires Windows")
    def test_win32_cursor_handle_and_tray_selftest(self):
        import ctypes
        from ctypes import wintypes
        # WNDCLASSW.hCursor is a HANDLE even if ctypes.wintypes lacks HCURSOR.
        _, window_class, notify_data = tray_launcher.windows_types()
        field_types = dict(window_class._fields_)
        self.assertIs(field_types["hCursor"], wintypes.HANDLE)
        self.assertEqual(ctypes.sizeof(field_types["hCursor"]), ctypes.sizeof(ctypes.c_void_p))
        self.assertGreaterEqual(ctypes.sizeof(notify_data), 700)
        tray_launcher.tray_selftest()

    def test_restart_confirmation_constructs_text_without_type_error(self):
        tray = tray_launcher.WindowsTray()
        class InlineThread:
            def __init__(self, target, **kwargs):
                self.target = target
            def start(self):
                self.target()
        with (patch.object(tray_launcher.threading, "Thread", InlineThread),
              patch.object(tray.supervisor, "status", return_value={"running": True, "busy": True}),
              patch.object(tray.supervisor, "stop", return_value=False),
              patch.object(tray_launcher, "win_dialog", return_value=tray_launcher.IDYES) as dialog):
            tray.action(tray_launcher.MENU_RESTART)
            self.assertIn("reiniciar Engineer?", dialog.call_args.args[0])

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
