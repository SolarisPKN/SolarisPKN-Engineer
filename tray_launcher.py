"""SolarisPKN-Engineer Windows system tray, stdlib-only.

The tray owns and supervises ONLY the Engineer localhost server process.
Controls: Open browser, Status, Restart, Close Engineer.
Never touches SolarisPKN-IA, Solaris Bridge, or unrelated user processes.

Windows-only: Win32 Shell_NotifyIconW via ctypes. Python 3.10+.
The frozen EXE re-executes itself with --server-child.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import multiprocessing
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
from urllib.request import Request, build_opener, ProxyHandler
import webbrowser


PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"
CONTROL_ENV = "ENGINEER_TRAY_CONTROL_TOKEN"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
MB_OK = 0
MB_ICONINFORMATION = 0x40
MB_ICONWARNING = 0x30
MB_ICONERROR = 0x10
MB_ICONQUESTION = 0x20
MB_YESNO = 0x04
IDYES = 6
WM_COMMAND = 0x0111
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_CONTEXTMENU = 0x007B
WM_APP = 0x8000
TRAY_CALLBACK = WM_APP + 41
MF_STRING = 0
MF_SEPARATOR = 0x0800
TPM_RIGHTBUTTON = 0x0002
NIM_ADD = 0
NIM_DELETE = 2
NIF_MESSAGE = 1
NIF_ICON = 2
NIF_TIP = 4
MENU_OPEN = 101
MENU_STATUS = 102
MENU_RESTART = 103
MENU_EXIT = 104
TASKKILL_GRACE_SECONDS = 15.0


def app_folder():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def app_log():
    directory = app_folder() / ".private"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "server-process.log"


def port_is_busy():
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.35):
            return True
    except OSError:
        return False


def server_command():
    if getattr(sys, "frozen", False):
        return [sys.executable, "--server-child"]
    return [sys.executable, "-u", str(Path(__file__).with_name("server.py")), "--no-browser"]


def post_control(token, path, *, payload=None, timeout=2.0):
    headers = {"X-Engineer-Tray-Token": token}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = Request(
        URL.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers=headers, method="POST" if payload is not None else "GET",
    )
    with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
        return json.loads(response.read(6000).decode("utf-8"))


def win_dialog(message, title="SolarisPKN-Engineer", style=MB_OK | MB_ICONINFORMATION, hwnd=0):
    user = ctypes.windll.user32
    user.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR,
                                 wintypes.LPCWSTR, wintypes.UINT]
    return user.MessageBoxW(hwnd, message, title, style)


def terminate_owned_process_tree(proc):
    """Windows taskkill tree targets ONLY our tracked child PID and its descendants."""
    if proc is None or proc.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"],
            creationflags=CREATE_NO_WINDOW, timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


class Supervisor:
    """Strict ownership: only subprocess.Popen returned by this instance."""
    def __init__(self):
        self.lock = threading.RLock()
        self.proc = None
        self.process_log = None
        self.token = None

    def status(self):
        with self.lock:
            proc, token = self.proc, self.token
        if not proc or proc.poll() is not None:
            return {"running": False, "busy": False}
        try:
            info = post_control(token, "/__tray/status", timeout=1.8)
            return {"running": True, **info}
        except (OSError, ValueError, TimeoutError):
            return {"running": True, "busy": None}

    def start(self):
        with self.lock:
            if self.proc is not None and self.proc.poll() is None:
                return True
            if self.proc is not None and self.proc.poll() is not None:
                self.close_handles()
            if port_is_busy():
                raise RuntimeError(
                    "El puerto 8765 ya está ocupado.\n\n"
                    "Si Engineer está abierto desde INICIAR_ENGINEER.cmd antiguo, "
                    "cerrá esa instancia primero. No se tocarán procesos ajenos."
                )
            log = app_log().open("ab", buffering=0)
            token = secrets.token_urlsafe(32)
            env = dict(os.environ)
            env[CONTROL_ENV] = token
            try:
                child = subprocess.Popen(
                    server_command(), cwd=str(app_folder()), env=env,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    creationflags=CREATE_NO_WINDOW, close_fds=True,
                )
            except Exception:
                log.close()
                raise
            self.proc = child
            self.process_log = log
            self.token = token
        return True

    def await_ready(self, seconds=14):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            with self.lock:
                p, token = self.proc, self.token
            if p is None or p.poll() is not None:
                return False
            try:
                result = post_control(token, "/__tray/status", timeout=0.6)
                if result.get("managed"):
                    return True
            except (OSError, ValueError, TimeoutError):
                pass
            time.sleep(0.35)
        return False

    def stop(self, *, hwnd=0):
        """Request checkpointed exit; if too slow, explicitly ask before force-kill."""
        with self.lock:
            p, token = self.proc, self.token
        if p is None:
            return True
        if p.poll() is not None:
            self.close_handles()
            return True
        try:
            post_control(token, "/__tray/shutdown", payload={}, timeout=2.2)
        except (OSError, ValueError, TimeoutError):
            pass
        deadline = time.monotonic() + TASKKILL_GRACE_SECONDS
        while time.monotonic() < deadline:
            if p.poll() is not None:
                self.close_handles()
                return True
            time.sleep(.3)
        answer = win_dialog(
            "Engineer sigue terminando una tarea.\n\n"
            "¿Forzar el cierre de SUS procesos ahora?\n"
            "Los archivos ya confirmados quedan en SQLite; las tareas "
            "interrumpidas se podrán reanudar después.",
            style=MB_YESNO | MB_ICONWARNING, hwnd=hwnd
        )
        if answer != IDYES:
            return False
        terminate_owned_process_tree(p)
        self.close_handles()
        return True

    def close_handles(self):
        with self.lock:
            self.proc = None
            self.token = None
            if self.process_log:
                self.process_log.close()
            self.process_log = None


def windows_types():
    if os.name != "nt":
        raise RuntimeError("The tray requires Windows")

    Callback = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
        ctypes.c_size_t, ctypes.c_ssize_t
    )

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT), ("lpfnWndProc", Callback),
            ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HCURSOR), ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
        ]

    class GUID(ctypes.Structure):
        _fields_ = [
            ("a", wintypes.DWORD), ("b", wintypes.WORD),
            ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
            ("version", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID),
            ("hBalloonIcon", wintypes.HICON),
        ]
    return Callback, WNDCLASSW, NOTIFYICONDATAW


class WindowsTray:
    def __init__(self):
        self.supervisor = Supervisor()
        self.control_lock = threading.Lock()
        self.hwnd = None
        self.icon_data = None
        self.mutex = None
        self.window_proc = None
        self.registered_message = 0

    def _api(self):
        user = ctypes.windll.user32
        kernel = ctypes.windll.kernel32
        shell = ctypes.windll.shell32
        kernel.GetModuleHandleW.restype = wintypes.HINSTANCE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        user.RegisterClassW.argtypes = [ctypes.c_void_p]
        user.RegisterClassW.restype = wintypes.ATOM
        user.CreateWindowExW.restype = wintypes.HWND
        user.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE,
            ctypes.c_void_p
        ]
        user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                      ctypes.c_size_t, ctypes.c_ssize_t]
        user.DestroyWindow.argtypes = [wintypes.HWND]
        user.SetForegroundWindow.argtypes = [wintypes.HWND]
        user.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                     wintypes.UINT, wintypes.UINT]
        user.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                        ctypes.c_size_t, ctypes.c_ssize_t]
        user.DefWindowProcW.restype = ctypes.c_ssize_t
        user.CreatePopupMenu.restype = wintypes.HMENU
        user.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT,
                                     ctypes.c_size_t, wintypes.LPCWSTR]
        user.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        wintypes.HWND, ctypes.c_void_p]
        user.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        user.LoadIconW.restype = wintypes.HICON
        shell.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]
        shell.Shell_NotifyIconW.restype = wintypes.BOOL
        return user, kernel, shell

    def menu(self):
        user, _, _ = self._api()
        menu = user.CreatePopupMenu()
        if not menu:
            return
        for code, label in [
            (MENU_OPEN, "Abrir panel"),
            (MENU_STATUS, "Estado del motor"),
            (MENU_RESTART, "Reiniciar Engineer"),
            (0, None),
            (MENU_EXIT, "Cerrar Engineer por completo"),
        ]:
            user.AppendMenuW(menu, MF_SEPARATOR if code == 0 else MF_STRING,
                             code, label)
        point = wintypes.POINT()
        user.GetCursorPos(ctypes.byref(point))
        user.SetForegroundWindow(self.hwnd)
        user.TrackPopupMenu(menu, TPM_RIGHTBUTTON, point.x, point.y, 0,
                            self.hwnd, None)
        user.PostMessageW(self.hwnd, 0, 0, 0)
        user.DestroyMenu(menu)

    def _callback(self, hwnd, msg, wparam, lparam):
        user = ctypes.windll.user32
        if msg == self.registered_message:
            self.add_icon()
            return 0
        if msg == TRAY_CALLBACK:
            if lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self.menu()
            elif lparam == WM_LBUTTONDBLCLK:
                webbrowser.open(URL)
            return 0
        if msg == WM_COMMAND:
            self.action(int(wparam) & 0xFFFF)
            return 0
        if msg == WM_CLOSE:
            user.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            self.remove_icon()
            user.PostQuitMessage(0)
            return 0
        return user.DefWindowProcW(hwnd, msg, wparam, lparam)

    def add_icon(self):
        user, _, shell = self._api()
        if self.icon_data is None:
            return
        shell.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.icon_data))

    def remove_icon(self):
        if self.icon_data:
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE,
                                                    ctypes.byref(self.icon_data))

    def action(self, command):
        if command == MENU_OPEN:
            webbrowser.open(URL)
            return
        if command == MENU_STATUS:
            info = self.supervisor.status()
            text = (
                "Estado: " + ("Servidor activo" if info["running"] else "Servidor detenido")
                + "\nTarea: " + (
                    "En ejecución: " + str(info.get("project") or "proyecto")
                    if info.get("busy") else "Sin trabajo activo"
                ) + "\n\nEl tray solo gestiona SolarisPKN-Engineer."
            )
            win_dialog(text, hwnd=self.hwnd)
            return
        if command not in (MENU_RESTART, MENU_EXIT):
            return
        if not self.control_lock.acquire(blocking=False):
            return

        def worker():
            try:
                current = self.supervisor.status()
                if current.get("busy"):
                    ans = win_dialog(
                        "Hay un análisis en curso.\n\n"
                        "¿Solicitar su parada y guardar el progreso antes de "
                        ("reiniciar" if command == MENU_RESTART else "cerrar") + " Engineer?",
                        style=MB_YESNO | MB_ICONQUESTION, hwnd=self.hwnd,
                    )
                    if ans != IDYES:
                        return
                if not self.supervisor.stop(hwnd=self.hwnd):
                    return
                if command == MENU_RESTART:
                    try:
                        self.supervisor.start()
                        if not self.supervisor.await_ready():
                            raise RuntimeError("El servidor no respondió al reinicio. Consultá .private/server-process.log.")
                        webbrowser.open(URL)
                    except Exception as ex:
                        win_dialog(str(ex), style=MB_OK | MB_ICONERROR, hwnd=self.hwnd)
                else:
                    ctypes.windll.user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            finally:
                self.control_lock.release()
        threading.Thread(target=worker, name="engineer-tray-control", daemon=True).start()

    def run(self):
        user, kernel, shell = self._api()
        self.mutex = kernel.CreateMutexW(None, False, "Local\\SolarisPKN_Engineer_Tray")
        if not self.mutex:
            raise RuntimeError("No se pudo crear el control de instancia única.")
        if kernel.GetLastError() == 183:
            webbrowser.open(URL)
            kernel.CloseHandle(self.mutex)
            self.mutex = None
            return

        Callback, WNDCLASSW, NOTIFYICONDATAW = windows_types()
        self.window_proc = Callback(self._callback)
        instance = kernel.GetModuleHandleW(None)
        class_name = "SolarisPKN_Engineer_Tray_Window"
        wc = WNDCLASSW()
        wc.lpfnWndProc = self.window_proc
        wc.hInstance = instance
        wc.lpszClassName = class_name
        if not user.RegisterClassW(ctypes.byref(wc)):
            raise RuntimeError("No se pudo registrar la ventana oculta del tray.")
        self.hwnd = user.CreateWindowExW(
            0, class_name, "SolarisPKN-Engineer Tray", 0,
            0, 0, 0, 0, None, None, instance, None
        )
        if not self.hwnd:
            raise RuntimeError("No se pudo crear la ventana del tray.")
        icon = user.LoadIconW(None, ctypes.cast(ctypes.c_void_p(32512), wintypes.LPCWSTR))
        self.icon_data = NOTIFYICONDATAW()
        self.icon_data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self.icon_data.hWnd = self.hwnd
        self.icon_data.uID = 1
        self.icon_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self.icon_data.uCallbackMessage = TRAY_CALLBACK
        self.icon_data.hIcon = icon
        self.icon_data.szTip = "SolarisPKN-Engineer | Abrir / Reiniciar / Cerrar"
        self.registered_message = user.RegisterWindowMessageW("TaskbarCreated")
        self.add_icon()

        def boot():
            try:
                self.supervisor.start()
                if not self.supervisor.await_ready():
                    raise RuntimeError(
                        "No se pudo confirmar el arranque de Engineer.\n"
                        "Revisá .private/server-process.log."
                    )
                webbrowser.open(URL)
            except Exception as ex:
                win_dialog(str(ex), style=MB_OK | MB_ICONWARNING, hwnd=self.hwnd)

        threading.Thread(target=boot, name="engineer-tray-start", daemon=True).start()
        message = wintypes.MSG()
        try:
            while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user.TranslateMessage(ctypes.byref(message))
                user.DispatchMessageW(ctypes.byref(message))
        finally:
            # Window close is only permitted after supervisor.stop returned true,
            # except unexpected interpreter shutdown.
            if self.supervisor.proc and self.supervisor.proc.poll() is None:
                terminate_owned_process_tree(self.supervisor.proc)
            self.supervisor.close_handles()
            self.remove_icon()
            if self.mutex:
                kernel.CloseHandle(self.mutex)


def main():
    multiprocessing.freeze_support()
    if "--server-child" in sys.argv[1:]:
        sys.argv = ["engineer-server", "--no-browser"]
        from server import main as serve
        serve()
        return
    if os.name != "nt":
        from server import main as serve
        serve()
        return
    try:
        WindowsTray().run()
    except Exception as ex:
        win_dialog(
            "El tray no pudo iniciarse:\n" + str(ex),
            style=MB_OK | MB_ICONERROR,
        )


if __name__ == "__main__":
    main()
