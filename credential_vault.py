"""SolarisPKN-Engineer credential vault — Windows DPAPI, current-user scope.

Records are AES/OS-protected by Windows DPAPI via CryptProtectData.
The vault file only contains ciphertext, opaque identifiers and timestamps.
Secrets are not written to logs, SQL, Obsidian notes, subprocess arguments or Git.
No silent fallback to plaintext on other operating systems.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading

from workspace import app_folder

_LOCK = threading.RLock()
_ALLOWED_KINDS = frozenset(("archive", "ai-provider", "github"))
_VERSION = 1
_SAFE = re.compile(r"^[A-Za-z0-9_.:/!@+\\-]{1,2048}$")


class VaultUnavailable(RuntimeError):
    pass


class VaultError(RuntimeError):
    pass


class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _windows_dpapi():
    if os.name != "nt":
        raise VaultUnavailable(
            "This encrypted vault requires Windows DPAPI. "
            "No plaintext fallback is permitted on this operating system."
        )
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    args = [
        ctypes.POINTER(_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB),
    ]
    crypt.CryptProtectData.argtypes = args
    crypt.CryptProtectData.restype = wintypes.BOOL
    crypt.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_BLOB), ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(_BLOB), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(_BLOB),
    ]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    return crypt, kernel


def _input_blob(plain: bytes):
    if not plain:
        raise ValueError("Credential is empty")
    backing = (ctypes.c_ubyte * len(plain)).from_buffer_copy(plain)
    return _BLOB(len(plain), backing), backing


def _transform(data: bytes, entropy: bytes, *, encrypt: bool) -> bytes:
    crypt, kernel = _windows_dpapi()
    input_blob, buffer = _input_blob(data)
    entropy_blob, salt_buffer = _input_blob(entropy)
    result = _BLOB()
    try:
        if encrypt:
            ok = crypt.CryptProtectData(
                ctypes.byref(input_blob), "SolarisPKN-Engineer secret",
                ctypes.byref(entropy_blob), None, None, 1, ctypes.byref(result)
            )
        else:
            ok = crypt.CryptUnprotectData(
                ctypes.byref(input_blob), None, ctypes.byref(entropy_blob),
                None, None, 1, ctypes.byref(result)
            )
        if not ok:
            # Never surface the secret, ciphertext or sensitive contents.
            raise VaultError("Windows DPAPI could not protect or unlock this credential")
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        if result.pbData:
            ctypes.memset(result.pbData, 0, result.cbData)
            kernel.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))
        ctypes.memset(buffer, 0, len(buffer))
        ctypes.memset(salt_buffer, 0, len(salt_buffer))


def supported() -> bool:
    return os.name == "nt"


def vault_path() -> Path:
    return app_folder() / ".private" / "credentials.dpapi.json"


def _identity(kind: str, name: str) -> tuple[str, bytes]:
    if kind not in _ALLOWED_KINDS or not isinstance(name, str) or not (1 <= len(name) <= 2048) or any(ord(c) < 32 for c in name):
        raise ValueError("Invalid credential identifier")
    ref = (kind + ":" + name).encode("utf-8")
    digest = hashlib.sha256(ref).hexdigest()
    return digest, b"SolarisPKN-Engineer/v1/" + ref


def _read() -> dict:
    f = vault_path()
    if not f.is_file():
        return {"version": _VERSION, "items": {}}
    # Bounds protect from malicious or broken vault files.
    if f.stat().st_size > 8_000_000:
        raise VaultError("Encrypted vault exceeds supported metadata size")
    try:
        doc = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VaultError("Encrypted vault is invalid and was not overwritten") from None
    if not isinstance(doc, dict) or doc.get("version") != _VERSION or not isinstance(doc.get("items"), dict):
        raise VaultError("Encrypted vault format is not supported")
    return doc


def _write(doc: dict) -> None:
    folder = vault_path().parent
    folder.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        raise VaultUnavailable("Windows DPAPI is required")
    dest = vault_path()
    tmp = folder / ("credentials-" + os.urandom(6).hex() + ".tmp")
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            json.dump(doc, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, dest)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def save_secret(kind: str, name: str, secret: str) -> None:
    """Encrypt one secret for the current Windows account; never persist plaintext."""
    if not isinstance(secret, str) or not (1 <= len(secret) <= 16384):
        raise ValueError("Credential must contain 1..16384 characters")
    digest, entropy = _identity(kind, name)
    encrypted = _transform(secret.encode("utf-8"), entropy, encrypt=True)
    with _LOCK:
        doc = _read()
        doc["items"][digest] = {
            "scheme": "windows-dpapi-user-v1",
            "encrypted": base64.b64encode(encrypted).decode("ascii"),
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _write(doc)


def has_secret(kind: str, name: str) -> bool:
    digest, _ = _identity(kind, name)
    with _LOCK:
        item = _read()["items"].get(digest)
    return isinstance(item, dict) and item.get("scheme") == "windows-dpapi-user-v1"


def load_secret(kind: str, name: str) -> str | None:
    """Server-only API. Never expose returned value to a browser response."""
    digest, entropy = _identity(kind, name)
    with _LOCK:
        item = _read()["items"].get(digest)
    if item is None:
        return None
    if not isinstance(item, dict) or item.get("scheme") != "windows-dpapi-user-v1":
        raise VaultError("Unexpected vault item format")
    try:
        cipher = base64.b64decode(item["encrypted"], validate=True)
        return _transform(cipher, entropy, encrypt=False).decode("utf-8")
    except (KeyError, ValueError, UnicodeError) as ex:
        raise VaultError("Stored credential is corrupt") from None


def delete_secret(kind: str, name: str) -> bool:
    digest, _ = _identity(kind, name)
    with _LOCK:
        doc = _read()
        if digest not in doc["items"]:
            return False
        del doc["items"][digest]
        _write(doc)
        return True
