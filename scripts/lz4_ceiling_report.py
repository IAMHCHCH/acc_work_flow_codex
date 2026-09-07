#!/usr/bin/env python3
"""Report measured ZIP ceilings separately from validated LZ4 throughput."""
import csv
import json
import os
from collections import defaultdict
from pathlib import Path
from statistics import median

os.environ.setdefault('MPLCONFIGDIR', '/tmp/accflow-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1] / 'reports/lz4-study'
COLORS = {'sw': '#2563a6', 'sync': '#cb7515', 'async': '#168569'}
LABELS = {'sw': 'Native LZ4', 'sync': 'UADK sync', 'async': 'UADK async'}


def save(fig, name):
    for suffix in ['png', 'pdf']:
        fig.savefig(ROOT / 'figures' / (name + '.' + suffix), bbox_inches='tight')
    plt.close(fig)


def summary(rows, keys, metrics):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[k] for k in keys)].append(row)
    result = []
    for key, samples in sorted(groups.items()):
        r = dict(zip(keys, key), samples=len(samples))
        for metric in metrics:
            vals = [row[metric] for row in samples]
            r.update({metric: median(vals), metric + '_min': min(vals), metric + '_max': max(vals)})
        result.append(r)
    return result


def csv_file(name, rows):
    with (ROOT / name).open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)


def main():
    official, controlled = [], []
    for phase in ['official', 'official-fine', 'official-confirm']:
        rows = json.loads((ROOT / 'raw' / phase / 'summary.json').read_text())
        for r in rows:
            r['phase'] = phase
            r['device_count'] = len(r['nodes'])
            r['window_kb'] = [4, 8, 16, 24, 32][r.get('window', 4)]
            r['nodes'] = ','.join(map(str, r['nodes']))
            assert r['gbps'] > 0 and all(v >= 0 for v in r['completed_descriptors'].values())
            official.append(r)
    csv_file('official-all.csv', [{k: r[k] for k in ['phase', 'name', 'repeat', 'mode', 'block',
              'window_kb', 'cores', 'nodes', 'threads_per_device', 'contexts_per_device', 'seconds', 'gbps']}
              for r in official])
    osummary = summary([r for r in official if r['seconds'] == 5],
                       ['block', 'window_kb', 'nodes', 'mode'], ['gbps'])
    assert all(r['samples'] == 3 for r in osummary)
    csv_file('official-summary.csv', osummary)
    for phase in ['optimized', 'window']:
        rows = json.loads((ROOT / 'raw' / phase / 'summary.json').read_text())
        for r in rows:
            r['phase'] = phase
            r.setdefault('window_kb', 32); r.setdefault('block', 65536); r.setdefault('dataset', 'dickens')
            r['gbps'] = r['mbps']/1000
            r['output_pct'] = r['output_bytes']/r['input_bytes']*100
            r['compression_factor'] = r['input_bytes']/r['output_bytes']
            r['cpu_seconds_per_gb'] = r['cpu_cores_used']/r['gbps']
            assert r['input_bytes'] > 0 and r['calls'] > 0
            assert r['validation'] == ('RAW_NOT_LZ4' if r['mode'] == 'raw' else 'PASS')
            controlled.append(r)
    csv_file('optimization-all.csv', [{k: r[k] for k in ['phase', 'name', 'repeat', 'mode', 'cores',
              'window_kb', 'block', 'dataset', 'depth', 'seconds', 'input_bytes', 'output_bytes', 'calls',
              'gbps', 'output_pct', 'compression_factor', 'cpu_cores_used', 'cpu_seconds_per_gb', 'validation']}
              for r in controlled])
    regular = [r for r in controlled if r['seconds'] in [1, 2] and r['depth'] == 8 and r['mode'] != 'raw']
    csummary = summary(regular, ['phase', 'dataset', 'block', 'window_kb', 'cores', 'mode'],
                       ['gbps', 'output_pct', 'compression_factor', 'cpu_seconds_per_gb'])
    assert all(r['samples'] == 3 for r in csummary)
    csv_file('optimization-summary.csv', csummary)
    lookup = {(r['phase'], r['dataset'], r['cores'], r['mode']): r for r in csummary}
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False,
                         'figure.dpi': 150, 'savefig.dpi': 180})
    crossings = {}
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8), constrained_layout=True)
    for ax, phase, title in zip(axes, ['optimized', 'window'], ['32 KiB match window', '8 KiB match window']):
        points = sorted({r['cores'] for r in csummary if r['phase'] == phase and r['dataset'] == 'dickens'})
        for mode in ['sw', 'sync', 'async']:
            data = [lookup[(phase, 'dickens', n, mode)] for n in points]
            vals = np.array([r['gbps'] for r in data])
            ax.errorbar(points, vals, yerr=[vals-[r['gbps_min'] for r in data],
                                           [r['gbps_max'] for r in data]-vals],
                        color=COLORS[mode], label=LABELS[mode], marker='o', markersize=3, capsize=2)
        cross = {}
        for mode in ['sync', 'async']:
            cross[mode] = next(([a,b] for a,b in zip(points, points[1:])
                if lookup[(phase,'dickens',a,mode)]['gbps'] > lookup[(phase,'dickens',a,'sw')]['gbps']
                and lookup[(phase,'dickens',b,mode)]['gbps'] <= lookup[(phase,'dickens',b,'sw')]['gbps']), None)
        crossings[phase] = cross
        if cross['async']: ax.axvspan(*cross['async'], alpha=.15, color='#555555')
        ax.set(title=title + '; async crossing ' + str(cross['async']), xlabel='Physical CPU cores',
               ylabel='Validated LZ4 input throughput (GB/s)')
        ax.set_xticks([1, 8, 16, 24, 32, 48, 64]); ax.grid(alpha=.2); ax.legend(fontsize=9)
    fig.suptitle('Both ZIP devices active; identical CPU budgets; median and min/max of 3 runs')
    save(fig, 'dual-scaling')

    fig, ax = plt.subplots(figsize=(9,5), constrained_layout=True)
    pts = sorted({r['cores'] for r in csummary if r['phase']=='window' and r['dataset']=='dickens'})
    ns = np.linspace(1,64,256)
    for mode in ['sw','sync','async']:
        rate = lookup[('window','dickens',1,mode)]['gbps']
        plateau = max(lookup[('window','dickens',n,mode)]['gbps'] for n in pts)
        y = ns*rate if mode=='sw' else np.minimum(ns*rate,plateau)
        ax.plot(ns,y,'--',color=COLORS[mode],label=LABELS[mode]+' fitted model')
        ax.scatter(pts,[lookup[('window','dickens',n,mode)]['gbps'] for n in pts],s=18,color=COLORS[mode])
    ax.set(title='8 KiB window: measured points and fitted saturation model',
           xlabel='Physical CPU cores',ylabel='Input throughput (GB/s)')
    ax.grid(alpha=.2); ax.legend(fontsize=9,loc='upper left')
    save(fig,'dual-expected-model')

    fig, axes = plt.subplots(1,2,figsize=(13,4.8),constrained_layout=True)
    for mode in ['sync','async']:
        data = [r for r in official if r['phase']=='official' and r['nodes']=='0' and
                r['name'] in ['official-{:03d}'.format(i) for i in range(1,9)] and r['mode']==mode]
        axes[0].plot([r['block']/1024 for r in data],[r['gbps'] for r in data],
                     'o-',color=COLORS[mode],label='One device, '+mode)
    dual = [r for r in official if r['phase']=='official-fine' and r['block']!=65536 and
            r['block']!=4096 and r['nodes']=='0,1']
    dual += [r for r in official if r['phase']=='official' and r['name'] in ['official-033','official-034','official-035']]
    dual += [r for r in official if r['name']=='official-019']
    dual.sort(key=lambda r:r['block'])
    axes[0].plot([r['block']/1024 for r in dual],[r['gbps'] for r in dual],'s-',color='#8056a1',
                 label='Two devices, saturated scan')
    axes[0].set_xscale('log',base=2)
    axes[0].set(xlabel='Input block size (KiB)',ylabel='Raw LZ77 input GB/s',title='Fixed 32 KiB match window')
    window_rows = [r for r in official if r['phase']=='official-fine' and r['block']==65536]
    window_rows += [r for r in official if r['name']=='official-019']
    window_rows.sort(key=lambda r:r['window_kb'])
    axes[1].plot([r['window_kb'] for r in window_rows],[r['gbps'] for r in window_rows],'o-',color='#8056a1')
    axes[1].set(xlabel='Match window (KiB)',ylabel='Raw LZ77 input GB/s',title='Fixed 64 KiB input block; two devices')
    axes[1].set_xticks([4,8,16,24,32])
    for ax in axes:
        ax.axhline(15,linestyle='--',color='#555555',label='User reference: 15 GB/s')
        ax.grid(alpha=.2); ax.legend(fontsize=8)
    fig.suptitle('Official uadk_tool, level 8, perf_mode=1; synthetic input, not assembled LZ4')
    save(fig,'official-ceiling')

    with (ROOT/'summary.csv').open() as f:
        baseline=list(csv.DictReader(f))
    fig,axes=plt.subplots(1,2,figsize=(13,4.8),constrained_layout=True)
    datasets=['dickens','xml','ooffice','random','repeated','text']
    x=np.arange(len(datasets))
    compression=[]
    for i,(phase,mode,label,color) in enumerate([('window','sw','Native LZ4',COLORS['sw']),
                ('baseline','async','UADK 32 KiB window','#777777'),('window','async','UADK 8 KiB window',COLORS['async'])]):
        values=[]; speeds=[]
        for d in datasets:
            if phase=='baseline':
                r=next(r for r in baseline if r['phase']=='screen' and r['dataset']==d and r['block_bytes']=='65536' and r['mode']==mode)
                output_pct=float(r['output_ratio'])*100; speed=float(r['mbps'])/1000
            else:
                r=lookup[(phase,d,1,mode)]; output_pct=r['output_pct']; speed=r['gbps']
            values.append(output_pct); speeds.append(speed)
            compression.append(dict(dataset=d,implementation=label,output_pct=output_pct,gbps=speed))
        axes[0].bar(x+(i-1)*.24,values,width=.24,label=label,color=color)
        axes[1].bar(x+(i-1)*.24,speeds,width=.24,label=label,color=color)
    for ax in axes:
        ax.set_xticks(x); ax.set_xticklabels(datasets,rotation=20); ax.grid(axis='y',alpha=.2); ax.legend(fontsize=8)
    axes[0].set(title='64 KiB blocks: compressed output',ylabel='Output / input (%)')
    axes[1].set(title='Same one-core CPU budget',ylabel='LZ4 input throughput (GB/s)')
    save(fig,'window-tradeoff'); csv_file('window-compression.csv',compression)
    table=['| Cores | Native GB/s | UADK sync GB/s | UADK async GB/s | Async / native |',
           '|---:|---:|---:|---:|---:|']
    for n in pts:
        sw,sy,asy=[lookup[('window','dickens',n,m)]['gbps'] for m in ['sw','sync','async']]
        table.append('| {} | {:.3f} | {:.3f} | {:.3f} | {:.2f}x |'.format(n,sw,sy,asy,asy/sw))
    (ROOT/'window-scaling-table.md').write_text('\n'.join(table)+'\n')
    stats=dict(official_experiments=len(official),controlled_experiments=len(controlled),
               validated_lz4_runs=sum(r['mode']!='raw' for r in controlled),
               raw_diagnostic_runs=sum(r['mode']=='raw' for r in controlled),crossings=crossings,
               official_peak=max(official,key=lambda r:r['gbps']),
               lz4_peak=max((r for r in controlled if r['mode'] in ['sync','async']),key=lambda r:r['gbps']))
    (ROOT/'ceiling-statistics.json').write_text(json.dumps(stats,indent=2))
    print(json.dumps(stats,indent=2))


if __name__=='__main__': main()
