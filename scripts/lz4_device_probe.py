#!/usr/bin/env python3
"""Run one NUMA experiment and record per-device completed descriptor deltas."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time

def counters():
    result = {}
    for device in Path('/sys/kernel/debug/hisi_zip').glob('*'):
        result[device.name] = {}
        for core in device.glob('comp_core*/regs'):
            text = core.read_text()
            result[device.name][core.parent.name] = {
                name: int(value, 16) for name, value in re.findall(r'(HZIP_\w+)\s*=\s*0x([0-9a-fA-F]+)', text)
            }
    return result

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', required=True)
    p.add_argument('--local', action='store_true')
    p.add_argument('command', nargs=argparse.REMAINDER)
    args = p.parse_args()
    cmd = args.command
    if cmd and cmd[0] == '--': cmd = cmd[1:]
    env = dict(os.environ)
    env.pop('LZ4_BENCH_LOCAL', None)
    if args.local: env['LZ4_BENCH_LOCAL'] = '1'
    before = counters()
    start = time.time()
    run = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=180)
    after = counters()
    delta = {}
    for device in before:
        delta[device] = sum((after[device][core]['HZIP_DONE_BD_NUM'] - before[device][core]['HZIP_DONE_BD_NUM']) % 2**32 for core in before[device])
    result = dict(command=cmd, local=args.local, start=start, end=time.time(),
        returncode=run.returncode, stdout=run.stdout, stderr=run.stderr,
        completed_descriptors=delta, before=before, after=after)
    Path(args.output).write_text(json.dumps(result, indent=2))
    print(run.stdout, end=''); print(run.stderr, end='')
    print(json.dumps(delta))
    raise SystemExit(run.returncode)

if __name__ == '__main__': main()
