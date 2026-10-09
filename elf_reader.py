"""ELF DT_NEEDED dependency names, read-only (32/64-bit, little/big endian)."""
import struct


def get(data, fmt, pos):
    size = struct.calcsize(fmt)
    if pos < 0 or pos + size > len(data):
        raise ValueError("ELF offset out of bounds")
    return struct.unpack_from(fmt, data, pos)


def names(data):
    if data[:4] != b"\x7fELF" or len(data) < 52:
        return []
    bits, byteorder = data[4], data[5]
    if bits not in (1, 2) or byteorder not in (1, 2):
        return []
    prefix = "<" if byteorder == 1 else ">"
    if bits == 2:
        phoff = get(data, prefix + "Q", 32)[0]
        phentsize, phnum = get(data, prefix + "HH", 54)
        phformat = prefix + "IIQQQQQQ"
        dynformat = prefix + "QQ"
    else:
        phoff = get(data, prefix + "I", 28)[0]
        phentsize, phnum = get(data, prefix + "HH", 42)
        phformat = prefix + "IIIIIIII"
        dynformat = prefix + "II"
    loads = []
    dynamic = []
    for i in range(min(phnum, 4096)):
        fields = get(data, phformat, phoff + i * phentsize)
        if bits == 2:
            typ, flags, offset, addr, unused, filesz, memsz, align = fields
        else:
            typ, offset, addr, unused, filesz, memsz, flags, align = fields
        if typ == 1:
            loads.append((addr, filesz, offset))
        elif typ == 2:
            dynamic.append((offset, filesz))

    def file_offset(address):
        for vaddr, length, offset in loads:
            if vaddr <= address < vaddr + length:
                return offset + address - vaddr
        raise ValueError("Unmapped dynamic ELF string table")

    offsets = []
    strtab = None
    entry_size = struct.calcsize(dynformat)
    for start, length in dynamic:
        for i in range(min(4096, length // entry_size)):
            tag, value = get(data, dynformat, start + i * entry_size)
            if tag == 0:
                break
            if tag == 1:
                offsets.append(value)
            elif tag == 5:
                strtab = value
    if strtab is None:
        return []
    base = file_offset(strtab)
    out = []
    for value in offsets:
        pos = base + value
        if 0 <= pos < len(data):
            raw = data[pos:pos + 256].split(bytes(1), 1)[0]
            name = raw.decode("utf-8", errors="replace")
            if name:
                out.append(name)
    return list(dict.fromkeys(out))
