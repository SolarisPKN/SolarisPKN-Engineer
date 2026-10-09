"""Conservative read-only detection of recognized encrypted file envelopes.

No password cracking and no entropy-based guesses: unsupported != encrypted.
Only emit an encrypted result when a recognizable encryption marker is present.
"""
from __future__ import annotations

from pathlib import Path
import zipfile


class EncryptedContentError(ValueError):
    """A recognizable encrypted container that the scanner cannot inspect."""


def encryption_reason(path: Path, header: bytes | None = None) -> str | None:
    """Identify known encrypted content without loading the complete file."""
    if header is None:
        with path.open("rb") as inp:
            header = inp.read(8192)
    if header.startswith(b"Salted__"):
        return "OpenSSL salted encrypted envelope"
    if header.startswith(b"age-encryption.org/v1\n"):
        return "age encrypted file"
    if header.startswith(b"-----BEGIN PGP MESSAGE-----"):
        return "OpenPGP armored encrypted message"
    if header.startswith(b"\x00GITCRYPT\x00"):
        return "git-crypt encrypted source"
    # ZIP: ordinary headers are not encryption evidence; check member flags.
    if header.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        try:
            with zipfile.ZipFile(path) as archive:
                protected = sum(bool(item.flag_bits & 0x1) for item in archive.infolist())
                if protected:
                    return f"ZIP container with {protected} password-protected entries"
        except (zipfile.BadZipFile, OSError, ValueError):
            pass
    # Encrypted PDF has an explicit /Encrypt dictionary reference.
    if header.startswith(b"%PDF-"):
        try:
            with path.open("rb") as inp:
                inp.seek(max(0, path.stat().st_size - 262144))
                tail = inp.read(262144)
            if b"/Encrypt" in tail:
                return "PDF encryption dictionary detected"
        except OSError:
            pass
    # Office encrypted packages store both marker streams in OLE compound files.
    if header.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        try:
            with path.open("rb") as inp:
                chunk = inp.read(256 * 1024)
            if b"EncryptedPackage" in chunk or "EncryptedPackage".encode("utf-16le") in chunk:
                return "Encrypted Office package marker"
        except OSError:
            pass
    return None
