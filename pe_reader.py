"""Dependency names from Portable Executable import tables, without executing PE files."""
import struct


def at(data, fmt, off):
    if off < 0 or off + struct.calcsize(fmt) > len(data):
        raise ValueError("PE offset out of range")
    return struct.unpack_from(fmt, data, off)


def names(data):
    if len(data) < 0x40 or data[:2] != b"MZ":
        return []
    pe = at(data, "<I", 0x3c)[0]
    if data[pe:pe + 4] != b"PE\x00\x00":
        return []
    coff = pe + 4
    sections = at(data, "<H", coff + 2)[0]
    opt_size = at(data, "<H", coff + 16)[0]
    opt = coff + 20
    magic = at(data, "<H", opt)[0]
    if magic not in (0x10b, 0x20b):
        return []
    dir_off = opt + (96 if magic == 0x10b else 112)
    if dir_off + 16 > opt + opt_size:
        return []
    import_rva, import_size = at(data, "<II", dir_off + 8)
    if not import_rva:
        return []
    section_start = opt + opt_size
    sec = []
    for idx in range(min(sections, 96)):
        pos = section_start + idx * 40
        if pos + 40 > len(data):
            break
        virtual_size, va, raw_size, raw_ptr = at(data, "<IIII", pos + 8)
        sec.append((va, max(virtual_size, raw_size), raw_ptr))
    size_headers = at(data, "<I", opt + 60)[0]

    def offset(rva):
        if 0 <= rva < size_headers:
            return rva
        for va, span, raw in sec:
            if va <= rva < va + span:
                return raw + rva - va
        raise ValueError("Unmapped PE RVA")

    start = offset(import_rva)
    results = []
    for i in range(min(4096, max(1, import_size // 20 + 4))):
        entry = start + i * 20
        if entry + 20 > len(data) or data[entry:entry + 20] == bytes(20):
            break
        name_rva = at(data, "<I", entry + 12)[0]
        try:
            pos = offset(name_rva)
            if pos >= len(data):
                continue
            raw = data[pos:pos + 256].split(bytes(1), 1)[0]
            value = raw.decode("ascii", errors="ignore").strip()
            if value:
                results.append(value)
        except ValueError:
            continue
    return list(dict.fromkeys(results))
