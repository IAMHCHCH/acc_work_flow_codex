#!/usr/bin/env python3
"""Run on the target. CPU topology, commands and raw rows remain reproducible."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import time

FIELDS = "mode,cores,block_bytes,input,wall_seconds,input_bytes,output_bytes,calls,mbps,thread_cpu_seconds,process_cpu_seconds,cpu_cores_used,output_ratio,depth,validation,fallback".split(",")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--runtime", required=True)
    p.add_argument("--corpus", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--phase", choices=["screen", "scaling", "crossing"], default="screen")
    p.add_argument("--seconds", type=float, default=1)
    p.add_argument("--repeats", type=int, default=3)
    args = p.parse_args()
    runtime = Path(args.runtime).resolve()
    out = Path(args.output).resolve(); out.mkdir(parents=True, exist_ok=True)
    corpus = Path(args.corpus)
    topology = subprocess.check_output(["lscpu", "-p=CPU,CORE,SOCKET,NODE"], universal_newlines=True)
    cpus, seen = [], set()
    for line in topology.splitlines():
        if line.startswith("#"): continue
        cpu, core, socket, node = map(int, line.split(","))
        key = (socket, core)
        if node == 0 and key not in seen and cpu in os.sched_getaffinity(0):
            seen.add(key); cpus.append(cpu)
    if len(cpus) < 32: raise RuntimeError("Need 32 distinct physical cores on node 0")
    (out / "topology.txt").write_text(topology)
    data_dir = out / "inputs"; data_dir.mkdir(exist_ok=True)
    rng = random.Random(20260907)
    # Seeded bytes without requiring Python 3.9 randbytes.
    datasets = {
        "random": rng.getrandbits(16*1024*1024*8).to_bytes(16*1024*1024, "little"),
        "repeated": b"A" * (16*1024*1024),
        "text": (b"LZ4 hardware acceleration uses queues and CPU assembly. " * 350000)[:16*1024*1024],
    }
    for name, data in datasets.items(): (data_dir / name).write_bytes(data)
    files = [data_dir / name for name in datasets] + [corpus / name for name in ["dickens", "xml", "ooffice"]]
    if args.phase == "scaling": files = [corpus / "dickens", data_dir / "text"]
    if args.phase == "crossing": files = [corpus / "dickens"]
    manifest = {str(f): {"bytes": f.stat().st_size, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()} for f in files}
    (out / (args.phase + "-inputs.json")).write_text(json.dumps(manifest, indent=2))
    jobs = []
    for repeat in range(1, args.repeats + 1):
        for f in files:
            blocks = [4096, 65536, 262144, 1048576, 4194304] if args.phase == "screen" else [65536]
            for block in blocks:
                counts = {"screen": [1], "scaling": [1, 2, 4, 8, 16, 32], "crossing": [10, 12, 14, 15, 16]}
                for n in counts[args.phase]:
                    modes = ["sw", "sync", "async"]
                    random.Random(repeat * 99 + n + block).shuffle(modes)
                    jobs.extend((repeat, f, block, n, mode) for mode in modes)
    env = dict(os.environ, LD_LIBRARY_PATH=str(runtime / ".libs") + ":/usr/lib64",
               LZ4_UADK_QUIET="1", LZ4_UADK_HW_CONCURRENCY="0")
    with (out / (args.phase + ".csv")).open("w", newline="") as stream, (out / (args.phase + ".log")).open("w") as log:
        writer = csv.writer(stream); writer.writerow(["repeat", "cpuset"] + FIELDS)
        for index, (repeat, f, block, n, mode) in enumerate(jobs, 1):
            cpuset = ",".join(map(str, cpus[:n]))
            cmd = ["taskset", "-c", cpuset, str(runtime / "controlled_bench"), mode, str(n), str(block), str(f), str(args.seconds), "8"]
            log.write(json.dumps({"command": cmd, "environment": {k: env[k] for k in ["LD_LIBRARY_PATH", "LZ4_UADK_QUIET", "LZ4_UADK_HW_CONCURRENCY"]}}) + "\n"); log.flush()
            result = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=120)
            log.write(result.stdout + result.stderr); log.flush()
            if result.returncode: raise RuntimeError("benchmark failed: " + result.stderr)
            rows = [r for r in csv.reader(result.stdout.splitlines()) if len(r) == len(FIELDS) and r[0] == mode]
            if len(rows) != 1 or rows[0][-2] != "PASS": raise RuntimeError("invalid benchmark output")
            writer.writerow([repeat, cpuset] + rows[0]); stream.flush()
            print("{}/{} {} {} {} cores {}: {} MB/s".format(index, len(jobs), args.phase, f.name, n, mode, rows[0][8]), flush=True)

if __name__ == "__main__": main()
