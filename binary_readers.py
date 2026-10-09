"""Read-only binary dependency inspection. Python standard library."""
import re
from pe_reader import names as pe_import_names
from elf_reader import names as elf_import_names
from macho_reader import names as macho_import_names


def inspect_binary(path, data):
    """Report probable library references, never execute binaries."""
    pattern = rb"[A-Za-z0-9_.+-]{2,90}\.(?:dll|so(?:\.[0-9]+)*|dylib)"
    try:
        if data[:2] == b'MZ':
            direct = pe_import_names(data)
        elif data[:4] == b'\x7fELF':
            direct = elf_import_names(data)
        else:
            direct = macho_import_names(data)
        if direct:
            return [(name, 'declared') for name in direct]
    except (ValueError, IndexError, OverflowError):
        pass
    found = set()
    for match in re.finditer(pattern, data[:8000000], re.I):
        found.add(match.group().decode('ascii', errors='ignore'))
        if len(found) >= 128:
            break
    return [(name, 'heuristic') for name in sorted(found)]
