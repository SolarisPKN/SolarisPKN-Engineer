"""Read-only streaming ISO9660/Joliet directory explorer (2048-byte sectors).

Never mounts, writes or executes an ISO. Not a UDF reader (yet).
"""
from __future__ import annotations
from pathlib import Path
import hashlib
import re

SECTOR=2048


def _record(data: bytes) -> dict | None:
    if not data:return None
    length=data[0]
    if length<34 or length>len(data):return None
    extent=int.from_bytes(data[2:6],"little")
    size=int.from_bytes(data[10:14],"little")
    flags=data[25]
    name_length=data[32]
    if 33+name_length>length:return None
    raw=data[33:33+name_length]
    return {"extent":extent,"size":size,"directory":bool(flags & 2),"name_raw":raw}


def _descriptors(iso: Path):
    with iso.open("rb") as inp:
        for sector in range(16, 48):
            inp.seek(sector*SECTOR)
            data=inp.read(SECTOR)
            if len(data)<SECTOR or data[1:6]!=b"CD001":
                break
            if data[0]==255:
                break
            if data[0] not in (1,2):continue
            joliet=data[0]==2 and data[88:91] in (bytes.fromhex("252f40"),bytes.fromhex("252f43"),bytes.fromhex("252f45"))
            if data[0]==2 and not joliet:continue
            root=_record(data[156:190])
            if root:yield (joliet,root)
def volume(iso: Path):
    primary=None
    joliet=None
    for is_joliet,root in _descriptors(iso):
        if is_joliet:joliet=root
        else:primary=root
    if primary is None:raise ValueError("No ISO9660 primary volume descriptor; UDF-only or unsupported image")
    return joliet or primary, bool(joliet)


def list_dir(iso: Path, extent: int, length: int, joliet: bool=False):
    """Stream directory blocks with bounded buffering and sector boundary padding."""
    if length<0 or length>iso.stat().st_size:
        raise ValueError("Invalid or oversized directory extent")
    if extent*SECTOR+length>iso.stat().st_size:
        raise ValueError("Directory points outside image")
    with iso.open("rb") as inp:
        inp.seek(extent*SECTOR)
        consumed=0
        while consumed<length:
            block=inp.read(min(SECTOR,length-consumed))
            if not block:break
            consumed+=len(block)
            cursor=0
            while cursor<len(block):
                size=block[cursor]
                if size==0:break
                item=_record(block[cursor:cursor+size])
                cursor+=size
                if not item:continue
                raw=item.pop("name_raw")
                if raw in (bytes([0]),bytes([1])):continue
                name=raw.decode("utf-16-be" if joliet else "ascii",errors="replace")
                name=name.split(";",1)[0].rstrip(".")
                if not name or name in (".","..") or "/" in name or "\\" in name:
                    continue
                item["name"]=name
                yield item


def lookup(iso:Path, inner:str):
    base,joliet=volume(iso)
    if not inner:return base,joliet
    current=base
    for component in inner.split("/"):
        if not current["directory"]:raise ValueError("ISO node is not a directory")
        found=next((item for item in list_dir(iso,current["extent"],current["size"],joliet)
                    if item["name"]==component),None)
        if found is None:raise FileNotFoundError("ISO entry not found")
        current=found
    return current,joliet


def split_key(key:str):
    if not key.startswith("iso:") or "!/" not in key:raise ValueError("Invalid ISO key")
    relative,inner=key[4:].split("!/",1)
    if not relative or ".." in relative.split("/") or ".." in inner.split("/"):
        raise ValueError("Invalid ISO path")
    return relative,inner


def scan_iso(root:Path,key:str):
    relative,inner=split_key(key)
    iso=(root/relative).resolve()
    iso.relative_to(root)
    entry,joliet=lookup(iso,inner)
    if entry["directory"]:
        discovered=[]
        for child in list_dir(iso,entry["extent"],entry["size"],joliet):
            path=(inner+"/" if inner else "")+child["name"]
            discovered.append(("iso:"+relative+"!/"+path,"contains","declared"))
        h=hashlib.sha256(("ISO9660:"+str(entry["extent"])+":"+str(entry["size"])).encode()).hexdigest()
        return discovered,h
    h=hashlib.sha256()
    imports=set()
    size=entry["size"]
    with iso.open("rb") as inp:
        inp.seek(entry["extent"]*SECTOR)
        remaining=size
        carry=b""
        while remaining:
            data=inp.read(min(256*1024,remaining))
            if not data:raise IOError("Truncated embedded ISO file")
            remaining-=len(data)
            h.update(data)
            text=(carry+data).decode("utf-8",errors="replace")
            for spec in re.findall(r"(?m)^\s*(?:import|from)\s+([\w.]+)",text):
                imports.add(spec)
            carry=data[-1024:]
    # Parsing a file inside ISO uses heuristics; proper module resolution is a future stage.
    deps=[("external:iso-module:"+spec,"iso-import","heuristic") for spec in sorted(imports)]
    return deps,h.hexdigest()
