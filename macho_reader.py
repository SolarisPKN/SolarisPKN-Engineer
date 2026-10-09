"""Mach-O dylib load command reader. Thin Mach-O only, read-only."""
import struct


def names(data):
    layouts = {
        bytes.fromhex("cefaedfe"): ("<", 28),
        bytes.fromhex("cffaedfe"): ("<", 32),
        bytes.fromhex("feedface"): (">", 28),
        bytes.fromhex("feedfacf"): (">", 32),
    }
    if len(data) < 32 or data[:4] not in layouts:
        return []
    endian, head_size = layouts[data[:4]]
    commands, size = struct.unpack_from(endian + "II", data, 16)
    offset = head_size
    end = min(len(data), head_size + size)
    result = []
    for _ in range(min(commands, 4096)):
        if offset + 8 > end:
            break
        cmd, length = struct.unpack_from(endian + "II", data, offset)
        if length < 8 or offset + length > end:
            break
        if (cmd & 0x7fffffff) in (12, 24, 31, 35) and length >= 24:
            string_off = struct.unpack_from(endian + "I", data, offset + 8)[0]
            if 24 <= string_off < length:
                name = data[offset + string_off: offset + length].split(bytes(1), 1)[0]
                value = name.decode("utf-8", errors="replace").strip()
                if value:
                    result.append(value)
        offset += length
    return list(dict.fromkeys(result))
