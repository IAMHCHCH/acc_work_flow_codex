#!/usr/bin/env python3
"""Capture read-only target provenance alongside benchmark results."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

runtime, output = map(Path, sys.argv[1:])
runtime = runtime.resolve()
env = dict(os.environ, LD_LIBRARY_PATH=str(runtime / ".libs") + ":/usr/lib64")
def command(argv):
    r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, env=env)
    return {"argv": argv, "returncode": r.returncode, "output": r.stdout}

files = [runtime / name for name in ["controlled_bench", "test_lz4_uadk", "lz4_async_regression",
         "controlled_bench_numa", "controlled_bench_window", "uadk_tool", "lz4_numa_regression"]]
files += list((runtime / ".libs").glob("*.so.2.11.0"))
files += list((runtime / ".libs/uadk").glob("*.so.2.11.0"))
files += [Path("/usr/lib64/liblz4.so.1")]
files += [runtime / '.libs/libwd_crypto.so.2']
metadata = {"uname": command(["uname", "-a"]), "lscpu": command(["lscpu"]),
            "ldd": command(["ldd", str(runtime / "controlled_bench")]),
            "uptime": command(["uptime"]),
            "hashes": {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in files if f.exists()}}
metadata["zip_parameters"] = {f.name: f.read_text().strip() for f in Path("/sys/module/hisi_zip/parameters").glob("*")}
metadata["devices"] = {}
for d in Path("/sys/class/uacce").glob("hisi_zip*"):
    metadata["devices"][d.name] = {str(f.relative_to(d)): f.read_text().strip() for f in
        [d / "device/numa_node", d / "available_instances", d / "algorithms"] if f.exists()}
metadata["loaded_runtime_maps"] = []
for process in Path("/proc").glob("[0-9]*"):
    try:
        cmdline = (process / "cmdline").read_bytes()
        if b"controlled_bench" in cmdline or b"uadk_tool" in cmdline:
            lines = (process / "maps").read_text().splitlines()
            metadata["loaded_runtime_maps"] += sorted({line.split()[-1] for line in lines
                if any(x in line for x in ["libhisi_zip", "libwd", "liblz4"])})
    except (OSError, PermissionError):
        pass
output.write_text(json.dumps(metadata, indent=2))
print(json.dumps(metadata, indent=2))
