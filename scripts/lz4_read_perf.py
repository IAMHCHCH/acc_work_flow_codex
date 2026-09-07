#!/usr/bin/env python3
"""Read the documented ZIP performance-mode register; never write MMIO."""
import json
import mmap
import os
from pathlib import Path
import struct

result = {}
for dev in Path('/sys/class/uacce').glob('hisi_zip-*'):
    pci = (dev / 'device').resolve()
    fd = os.open(str(pci / 'resource2'), os.O_RDONLY | os.O_SYNC)
    try:
        base = 0x301208 // mmap.PAGESIZE * mmap.PAGESIZE
        with mmap.mmap(fd, mmap.PAGESIZE, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ, offset=base) as memory:
            val = struct.unpack_from('<I', memory, 0x301208 - base)[0]
        result[pci.name] = {'HZIP_HIGH_PERF_0x301208': hex(val), 'enabled': bool(val & 1),
                            'numa_node': (pci / 'numa_node').read_text().strip()}
    finally:
        os.close(fd)
print(json.dumps(result, indent=2))
