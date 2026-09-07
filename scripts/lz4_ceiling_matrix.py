#!/usr/bin/env python3
"""Target-side, serialized ceiling experiments with per-device evidence."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import subprocess
import time

from lz4_device_probe import counters


def run_job(commands, out, name, metadata, env):
    before = counters()
    started = time.time()
    processes, streams = [], []
    for i, cmd in enumerate(commands):
        folder = out / (name + "-" + str(i))
        folder.mkdir()
        stream = (folder / "output.log").open("w")
        streams.append((stream, folder))
        processes.append(subprocess.Popen(cmd, cwd=str(folder), env=env,
                                          stdout=stream, stderr=subprocess.STDOUT))
    observations = []
    deadline = time.monotonic() + 120
    while any(p.poll() is None for p in processes):
        if time.monotonic() > deadline:
            for p in processes:
                p.kill()
            for p in processes:
                p.wait()
            raise RuntimeError("benchmark timeout: " + name)
        snapshot = []
        for p in processes:
            masks = set()
            try:
                executable = Path('/proc/{}/exe'.format(p.pid)).resolve().name
            except OSError:
                executable = ''
            # numactl/taskset apply the mask before exec; inspect the benchmark itself.
            task_paths = Path('/proc/{}/task'.format(p.pid)).glob('*') if executable == 'uadk_tool' or executable.startswith('controlled_bench') else []
            for task in task_paths:
                try:
                    masks.add(tuple(sorted(os.sched_getaffinity(int(task.name)))))
                except ProcessLookupError:
                    pass
            snapshot.append({"pid": p.pid, "masks": sorted(masks)})
        observations.append(snapshot)
        time.sleep(.2)
    after = counters()
    logs = []
    for stream, folder in streams:
        stream.close()
        logs.append((folder / "output.log").read_text())
    delta = {dev: sum((after[dev][core]['HZIP_DONE_BD_NUM'] -
                       before[dev][core]['HZIP_DONE_BD_NUM']) % 2**32
                      for core in before[dev]) for dev in before}
    record = dict(metadata, name=name, commands=commands, started=started,
                  ended=time.time(), returncodes=[p.returncode for p in processes],
                  environment={k: env[k] for k in env if k.startswith(('LD_', 'LZ4_'))},
                  completed_descriptors=delta, before=before, after=after,
                  affinity_samples=observations, stdout=logs)
    (out / (name + '.json')).write_text(json.dumps(record, indent=2))
    if any(record['returncodes']):
        raise RuntimeError("benchmark failed: " + name)
    for i, samples in enumerate(zip(*observations)):
        allowed = set(metadata['cpus'][i])
        if not any(sample['masks'] for sample in samples):
            raise RuntimeError("no benchmark affinity observations: " + name)
        if any(not set(mask).issubset(allowed) for sample in samples for mask in sample['masks']):
            raise RuntimeError("affinity escaped: " + name)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--phase', choices=['official', 'official-fine', 'official-confirm', 'optimized', 'window'], required=True)
    parser.add_argument('--binary', default='controlled_bench_numa')
    parser.add_argument('--include-depth32', action='store_true',
                        help='Opt into the previously failing depth-32 hardware stress point')
    args = parser.parse_args()
    runtime = Path(args.runtime).resolve()
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, LD_LIBRARY_PATH=str(runtime / '.libs') + ':/usr/lib64',
               LZ4_UADK_QUIET='1', LZ4_UADK_HW_CONCURRENCY='0')
    jobs = []
    if args.phase == 'official':
        # Scan one device; repeat a saturated setting and test both devices together.
        for block in [4096, 65536, 262144, 1048576]:
            for mode, threads, contexts, cores in [('sync', 8, 8, 8), ('async', 4, 4, 8)]:
                jobs.append((1, block, mode, threads, contexts, cores, [0], 3))
        for mode, threads, contexts, cores in [('sync', 1, 1, 1), ('sync', 4, 4, 4),
                                              ('sync', 16, 16, 16), ('async', 1, 1, 2),
                                              ('async', 8, 8, 16), ('async', 8, 16, 24)]:
            jobs.append((1, 65536, mode, threads, contexts, cores, [0], 3))
        for repeat in range(1, 4):
            for nodes in [[0], [1], [0, 1]]:
                for mode in ['sync', 'async']:
                    jobs.append((repeat, 65536, mode, 8 if mode == 'sync' else 4,
                                 8 if mode == 'sync' else 4, 8, nodes, 5))
        for block in [4096, 262144, 1048576]:
            jobs.append((1, block, 'async', 4, 4, 8, [0, 1], 3))
    elif args.phase == 'official-fine':
        for repeat in range(1, 4):
            for nodes in [[0], [1], [0, 1]]:
                for mode in ['sync', 'async']:
                    jobs.append((repeat, 4096, mode, 8 if mode == 'sync' else 4,
                                 8 if mode == 'sync' else 4, 8, nodes, 5))
        for block in [8192, 16384, 24576, 32768]:
            jobs.append((1, block, 'sync', 8, 8, 8, [0, 1], 3))
        for window in range(4):
            jobs.append((1, 65536, 'sync', 8, 8, 8, [0, 1], 3, window))
    elif args.phase == 'official-confirm':
        for repeat in range(1, 4):
            for nodes in [[0], [1], [0, 1]]:
                for window in [0, 1]:
                    jobs.append((repeat, 65536, 'sync', 8, 8, 8, nodes, 5, window))
        jobs.append((1, 65536, 'sync', 8, 8, 8, [0, 1], 15, 1))
    elif args.phase == 'window':
        env['LZ4_BENCH_LOCAL'] = '1'
        for repeat in range(1, 4):
            for cores in [1, 8, 16, 24, 32, 48, 52, 56, 64]:
                for mode in ['sw', 'sync', 'async']:
                    jobs.append((repeat, cores, mode, 8, 2, 8, 65536, 'dickens'))
            for dataset in ['xml', 'ooffice', 'random', 'repeated', 'text']:
                for mode in ['sw', 'sync', 'async']:
                    jobs.append((repeat, 1, mode, 8, 1, 8, 65536, dataset))
        for window in [4, 8, 16, 24, 32]:
            for mode in ['raw', 'async']:
                jobs.append((1, 32, mode, 8, 3, window, 65536, 'dickens'))
        for block in [4096, 8192, 16384]:
            for mode in ['sw', 'async', 'raw']:
                jobs.append((1, 32, mode, 8, 3, 32, block, 'dickens'))
        jobs.append((1, 32, 'async', 8, 30, 8, 65536, 'dickens'))
    else:
        env['LZ4_BENCH_LOCAL'] = '1'
        for repeat in range(1, 4):
            for cores in [1, 2, 4, 8, 16, 24, 28, 29, 30, 32, 64]:
                for mode in ['sw', 'sync', 'async']:
                    jobs.append((repeat, cores, mode, 8, 2))
        for depth in [1, 4, 16] + ([32] if args.include_depth32 else []):
            jobs.append((1, 32, 'async', depth, 3))
        jobs.append((1, 32, 'raw', 8, 5))
    rows = []
    for index, job in enumerate(jobs, 1):
        name = '{}-{:03d}'.format(args.phase, index)
        if args.phase.startswith('official'):
            repeat, block, mode, threads, contexts, cores, nodes, seconds = job[:8]
            window = job[8] if len(job) == 9 else 4
            cpus = [list(range(node * 64, node * 64 + cores * 2, 2)) for node in nodes]
            commands = [['numactl', '--physcpubind=' + ','.join(map(str, cpuset)),
                         '--membind=' + str(node), str(runtime / 'uadk_tool'), 'benchmark',
                         '--alg', 'lz77_only', '--mode', 'sva', '--opt', '0', '--' + mode,
                         '--pktlen', str(block), '--seconds', str(seconds), '--thread', str(threads),
                         '--ctxnum', str(contexts), '--device', 'hisi_zip-' + str(1-node),
                         '--prefetch', '--complevel', '8', '--winsize', str(window)]
                        for cpuset, node in zip(cpus, nodes)]
            meta = dict(repeat=repeat, block=block, mode=mode, threads_per_device=threads,
                        contexts_per_device=contexts, cores=cores*len(nodes), nodes=nodes,
                        seconds=seconds, cpus=cpus, window=window)
            record = run_job(commands, out, name, meta, env)
            matches = [re.search(r'lz77_only\s+\d+Bytes\s+([\d.]+)KiB/s', log) for log in record['stdout']]
            if not all(matches) or any(float(m.group(1)) <= 0 for m in matches):
                raise RuntimeError('zero or missing official throughput: ' + name)
            gbps = sum(float(m.group(1)) for m in matches) * 1024 / 1e9
            expected = {'0000:33:00.0' if node == 0 else '0000:31:00.0' for node in nodes}
            if any((count > 0) != (dev in expected) for dev, count in record['completed_descriptors'].items()):
                raise RuntimeError('unexpected device traffic: ' + name)
            row = dict(meta, name=name, gbps=gbps, completed_descriptors=record['completed_descriptors'])
        else:
            repeat, cores, mode, depth, seconds = job[:5]
            window, block, dataset = job[5:] if len(job) > 5 else (32, 65536, 'dickens')
            input_path = ('/root/hch/silesia/' if dataset in ['dickens', 'xml', 'ooffice'] else
                          '/tmp/accflow-lz4-final/inputs/') + dataset
            env['LZ4_BENCH_WINDOW_KB'] = str(window)
            cpuset = [cpu for pair in zip(range(0, 64, 2), range(64, 128, 2)) for cpu in pair][:cores]
            commands = [['taskset', '-c', ','.join(map(str, cpuset)), str(runtime / args.binary),
                         mode, str(cores), str(block), input_path, str(seconds), str(depth)]]
            meta = dict(repeat=repeat, cores=cores, mode=mode, depth=depth, seconds=seconds,
                        cpus=[cpuset], window_kb=window, block=block, dataset=dataset)
            record = run_job(commands, out, name, meta, env)
            parsed = [r for r in csv.reader(record['stdout'][0].splitlines()) if len(r) == 16 and r[0] == mode]
            expected_validation = 'RAW_NOT_LZ4' if mode == 'raw' else 'PASS'
            if len(parsed) != 1 or parsed[0][-2] != expected_validation or int(parsed[0][-1]):
                raise RuntimeError('invalid controlled output: ' + name)
            r = parsed[0]
            row = dict(meta, name=name, mbps=float(r[8]), input_bytes=int(r[5]), output_bytes=int(r[6]),
                       calls=int(r[7]), cpu_cores_used=float(r[11]), validation=r[-2],
                       completed_descriptors=record['completed_descriptors'])
        rows.append(row)
        (out / 'summary.json').write_text(json.dumps(rows, indent=2))
        print('{}/{} {} {:.3f} GB/s'.format(index, len(jobs), name, row.get('gbps', row.get('mbps', 0)/1000)), flush=True)


if __name__ == '__main__':
    main()
