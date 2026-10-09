"""Best-effort resource policies without privileged drivers or monitoring services."""
from __future__ import annotations
import ctypes
import os
import shutil
import sys
import time
from pathlib import Path


def hardware() -> dict:
    logical = os.cpu_count() or 1
    available = None
    total = None
    if os.name == "nt":
        class MEMORYSTATUS(ctypes.Structure):
            _fields_ = [("length",ctypes.c_ulong),("load",ctypes.c_ulong),
                        ("total_physical",ctypes.c_ulonglong),("available_physical",ctypes.c_ulonglong),
                        ("total_page",ctypes.c_ulonglong),("available_page",ctypes.c_ulonglong),
                        ("total_virtual",ctypes.c_ulonglong),("available_virtual",ctypes.c_ulonglong),
                        ("extended_virtual",ctypes.c_ulonglong)]
        mem=MEMORYSTATUS();mem.length=ctypes.sizeof(mem)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
            total=mem.total_physical;available=mem.available_physical
    else:
        try:
            mem={}
            for line in Path("/proc/meminfo").read_text().splitlines():
                if ":" in line:
                    k,v=line.split(":",1);mem[k]=int(v.strip().split()[0])*1024
            total=mem.get("MemTotal");available=mem.get("MemAvailable")
        except (OSError, ValueError, IndexError):
            pass
    disk=shutil.disk_usage(Path(__file__).resolve().parent)
    return {"logical_cpus":logical,"ram_total":total,"ram_available":available,
            "free_disk":disk.free,"platform":sys.platform,
            "recommendation":{"eco":1,"balanced":max(1,logical//2),
                              "full":min(32,logical)}}


class Governor:
    """User-selectable pacing. No claim of hard CPU/memory caps."""
    def __init__(self, mode="balanced"):
        if mode not in ("eco","balanced","full"):
            raise ValueError("Mode must be eco, balanced or full")
        self.mode=mode

    def workers(self) -> int:
        cores=os.cpu_count() or 1
        return {"eco":1,"balanced":max(1,cores//2),"full":min(32,cores)}[self.mode]

    def pause(self):
        if self.mode=="eco":time.sleep(0.15)
        elif self.mode=="balanced":time.sleep(0.025)

    def priority(self):
        """Lower ONLY the current Engineer process scheduling priority."""
        if self.mode=="full":return
        if os.name=="nt":
            try:
                BELOW_NORMAL_PRIORITY_CLASS=0x00004000
                IDLE_PRIORITY_CLASS=0x00000040
                kernel=ctypes.windll.kernel32
                kernel.SetPriorityClass(kernel.GetCurrentProcess(),
                                        IDLE_PRIORITY_CLASS if self.mode=="eco" else BELOW_NORMAL_PRIORITY_CLASS)
            except (OSError, AttributeError):
                pass
        else:
            try:
                os.nice(10 if self.mode=="eco" else 5)
            except (OSError, PermissionError):
                pass
