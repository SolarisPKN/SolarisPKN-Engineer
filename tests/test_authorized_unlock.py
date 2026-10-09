"""Tests for owner-authorized legacy encrypted ZIP (fixture password: demo-only).

The ZIP fixture is synthetic, contains only two harmless Python files, and is
stored as base64 test data. No private user files or credentials are used.
"""
from __future__ import annotations
import base64
from contextlib import redirect_stdout, closing
from io import StringIO
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engineer import database, enqueue
from authorized_unlock import unlock_zip, safe_member_name
from encrypted_formats import encryption_reason

FIXTURE=(
"UEsDBAoACQAAAG5eSV2pgvdqGAAAAAwAAAAHABwAbWFpbi5weVVUCQADQNXIakDVyGp1"
"eAsAAQQAAAAABAAAAAA9yp78SH6abcYN148NFSigMElh1R5/gM5QSwcIqYL3ahgAAAAM"
"AAAAUEsDBAoACQAAAG5eSV3NdchqKQAAAB0AAAAHABwAdXRpbC5weVVUCQADQNXIakDV"
"yGp1eAsAAQQAAAAABAAAAAAzwreyHI18FnIqZ+g6DR6hlMgB4Ihann0zeqJf2HyE1E278"
"3n2PSUCEVBLBwjNdchqKQAAAB0AAABQSwECHgMKAAkAAABuXkldqYL3ahgAAAAMAAAAB"
"wAYAAAAAAABAAAApIEAAAAAbWFpbi5weVVUBQADQNXIanV4CwABBAAAAAAEAAAAAFBLAQIe"
"AwoACQAAAG5eSV3NdchqKQAAAB0AAAAHABgAAAAAAAEAAACkgWkAAAB1dGlsLnB5VVQFAANA"
"1chqdXgLAAEEAAAAAAQAAAAAUEsFBgAAAAACAAIAmgAAAOMAAAAAAA=="
)


class UnlockTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/"original"
        self.root.mkdir()
        self.path=self.root/"private.zip"
        self.path.write_bytes(base64.b64decode(FIXTURE))
        self.db=Path(self.tmp.name)/"index.sqlite"
        connection=database(self.db,self.root.resolve())
        enqueue(connection,"file:private.zip",0)
        connection.execute("UPDATE nodes SET status='encrypted' WHERE key='file:private.zip'")
        connection.commit()
        connection.close()

    def test_zip_unlock_and_graph(self):
        self.assertIn("password-protected",encryption_reason(self.path))
        output=StringIO()
        with redirect_stdout(output):
            bad=unlock_zip(self.root,self.db,"private.zip","wrong-secret")
            good=unlock_zip(self.root,self.db,"private.zip","demo-only")
        self.assertEqual(bad["encrypted"],2)
        self.assertEqual(good["done"],2)
        self.assertNotIn("demo-only",output.getvalue())
        with closing(sqlite3.connect(self.db)) as db, db:
            nodes=dict(db.execute("SELECT key,status FROM nodes"))
            edges=set(db.execute("SELECT source,target FROM edges"))
        self.assertEqual(nodes["file:private.zip"],"done")
        self.assertEqual(nodes["zip:private.zip!/main.py"],"done")
        self.assertIn(("zip:private.zip!/main.py","zip:private.zip!/util.py"),edges)
        self.assertFalse((self.root/"main.py").exists())
        self.assertFalse((self.root/"util.py").exists())

    def test_member_path_safety(self):
        self.assertIsNone(safe_member_name("../secrets.txt"))
        self.assertIsNone(safe_member_name("/root.txt"))
        self.assertIsNone(safe_member_name("C:/windows/system32"))
        self.assertEqual(safe_member_name("src/main.py"),"src/main.py")


if __name__=="__main__":
    unittest.main()
