#!/usr/bin/env python3
"""Generate reproducible scientific plots from completed target CSVs."""
import argparse
import csv
from collections import defaultdict
import json
import os
from pathlib import Path
import statistics

os.environ.setdefault("MPLCONFIGDIR", "/tmp/accflow-matplotlib")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

MODES = ["sw", "sync", "async"]
LABEL = {"sw": "Native LZ4", "sync": "UADK sync", "async": "UADK async (depth 8)"}
COLOR = {"sw": "#2563a6", "sync": "#d97706", "async": "#168569"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", default="reports/lz4-study")
    args = parser.parse_args()
    root = Path(args.report_dir)
    plots = root / "figures"; plots.mkdir(exist_ok=True)
    rows = []
    expected = {"scaling":108, "crossing":45, "screen":270}
    for phase, count in expected.items():
        with (root / "raw" / (phase + ".csv")).open() as f:
            data = list(csv.DictReader(f))
        if len(data) != count: raise ValueError("Incomplete {}: {} != {}".format(phase, len(data), count))
        unique = set()
        for row in data:
            if row["validation"] != "PASS" or row["fallback"] != "0": raise ValueError("Unvalidated row")
            row["phase"] = phase; row["dataset"] = Path(row["input"]).name
            for field in ["cores", "block_bytes", "repeat", "input_bytes", "output_bytes", "calls"]: row[field] = int(row[field])
            cpus = row["cpuset"].split(",")
            if len(set(cpus)) != row["cores"]: raise ValueError("Invalid cpuset")
            key = tuple(row[k] for k in ["dataset", "block_bytes", "cores", "mode", "repeat"])
            if key in unique: raise ValueError("Duplicate sample")
            unique.add(key)
            for field in ["mbps", "process_cpu_seconds", "thread_cpu_seconds", "output_ratio", "cpu_cores_used"]:
                row[field] = float(row[field])
            if row["input_bytes"] <= 0 or row["output_bytes"] <= 0:
                raise ValueError("Nonpositive byte counts")
            row["output_ratio"] = row["output_bytes"] / row["input_bytes"]
            row["compression_factor"] = row["input_bytes"] / row["output_bytes"]
            row["space_saved_pct"] = 100 * (1 - row["output_ratio"])
            overhead = 12 * row["calls"] if row["mode"] != "sw" else 0
            row["payload_output_ratio"] = (row["output_bytes"] - overhead) / row["input_bytes"]
            row["cpu_seconds_per_gb"] = row["process_cpu_seconds"] * 1e9 / row["input_bytes"]
        rows.extend(data)
    with (root / "lz4-controlled.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    groups = defaultdict(list)
    for row in rows:
        key = (row["phase"], row["dataset"], row["block_bytes"], row["cores"], row["mode"])
        groups[key].append(row)
    summary = []
    for key, samples in groups.items():
        item = dict(zip(["phase", "dataset", "block_bytes", "cores", "mode"], key))
        item["samples"] = len(samples)
        if item["samples"] != 3: raise ValueError("Need three samples")
        for metric in ["mbps", "output_ratio", "compression_factor", "space_saved_pct", "payload_output_ratio", "cpu_seconds_per_gb", "cpu_cores_used"]:
            values = [s[metric] for s in samples]
            item[metric] = statistics.median(values)
            item[metric + "_min"] = min(values); item[metric + "_max"] = max(values)
        summary.append(item)
    with (root / "summary.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0])); writer.writeheader(); writer.writerows(summary)
    lookup = {(r["phase"], r["dataset"], r["block_bytes"], r["cores"], r["mode"]): r for r in summary}
    def get(dataset, n, mode, block=65536, phase="scaling"):
        return lookup[(phase, dataset, block, n, mode)]
    plt.rcParams.update({"font.size":11, "axes.spines.top":False, "axes.spines.right":False,
                         "figure.dpi":150, "savefig.dpi":180})
    def save(fig, name):
        fig.savefig(plots / (name + ".png"), bbox_inches="tight")
        fig.savefig(plots / (name + ".pdf"), bbox_inches="tight")
        plt.close(fig)
    cores = [1,2,4,8,10,12,14,15,16,32]
    def scaling(n, mode):
        return get("dickens", n, mode, phase="crossing" if n in [10,12,14,15] else "scaling")
    crossings = {}
    for mode in ["sync", "async"]:
        crossings[mode] = next(([a,b] for a,b in zip(cores,cores[1:])
            if scaling(a,mode)["mbps"] > scaling(a,"sw")["mbps"] and
               scaling(b,mode)["mbps"] <= scaling(b,"sw")["mbps"]), None)
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.7), constrained_layout=True)
    for mode in MODES:
        vals = [scaling(n, mode) for n in cores]
        y = np.array([r["mbps"] for r in vals]) / 1000
        low = y - np.array([r["mbps_min"] for r in vals]) / 1000
        high = np.array([r["mbps_max"] for r in vals]) / 1000 - y
        axes[0].errorbar(cores, y, yerr=[low, high], marker="o", markersize=4,
                         color=COLOR[mode], label=LABEL[mode], capsize=3)
    for mode in ["sync", "async"]:
        axes[1].plot(cores, [scaling(n, mode)["mbps"] / scaling(n, "sw")["mbps"] for n in cores],
                     "o-", color=COLOR[mode], label=LABEL[mode], markersize=4)
    axes[1].axhline(1, color="#555555", linestyle="--", linewidth=1)
    for ax in axes:
        if crossings["async"]: ax.axvspan(*crossings["async"], color="#b9c5c9", alpha=.35)
        ax.set_xlabel("Physical CPU cores (one SMT thread per core)"); ax.grid(alpha=.2)
        ax.set_xticks([1,4,8,12,16,24,32]); ax.legend(fontsize=9)
    axes[0].set_ylabel("Input throughput (GB/s, decimal)")
    axes[1].set_ylabel("UADK / native LZ4 throughput")
    axes[0].set_title("Dickens, 64 KiB blocks: measured throughput")
    axes[1].set_title("Measured crossover: {}".format(crossings["async"] or "none observed"))
    fig.suptitle("Same CPU budget; median of 3 runs per point; bars show min/max")
    save(fig, "scaling")

    fig, ax = plt.subplots(figsize=(8.5, 4.8), constrained_layout=True)
    model_n = np.linspace(1,32,250)
    for mode in MODES:
        single = scaling(1, mode)["mbps"]
        plateau = max(scaling(n, mode)["mbps"] for n in cores)
        model = single * model_n if mode == "sw" else np.minimum(single * model_n, plateau)
        ax.plot(model_n, model/1000, "--", color=COLOR[mode], label=LABEL[mode] + " model")
        ax.scatter(cores, [scaling(n, mode)["mbps"]/1000 for n in cores], color=COLOR[mode], s=18)
    ax.set(xlabel="Physical CPU cores", ylabel="Input throughput (GB/s)",
           title="Illustrative saturation model fitted to measured endpoints")
    ax.text(.02,.97,"Dashed: min(single-core rate x cores, observed plateau)\nDots: measurements. Model is not an independent prediction.",
            transform=ax.transAxes, va="top", fontsize=9)
    ax.grid(alpha=.2); ax.legend(loc="lower right", fontsize=9); save(fig, "expected-model")

    fig, axes = plt.subplots(1,2,figsize=(13,4.7),constrained_layout=True)
    for mode in MODES:
        axes[0].plot(cores, [scaling(n,mode)["cpu_seconds_per_gb"] for n in cores], "o-", color=COLOR[mode], label=LABEL[mode], markersize=4)
        axes[1].plot(cores, [scaling(n,mode)["cpu_cores_used"]/n for n in cores], "o-", color=COLOR[mode], label=LABEL[mode], markersize=4)
    for ax in axes: ax.set_xlabel("Physical CPU cores"); ax.grid(alpha=.2); ax.legend(fontsize=9)
    axes[0].set(ylabel="Process CPU seconds per input GB",title="Dickens: CPU cost per unit of work")
    axes[1].set(ylabel="Process CPU time / (wall time x allowed cores)",ylim=(0,1.1),title="Saturated runs still consume the assigned CPUs")
    save(fig,"cpu-cost")

    datasets=["random","repeated","text","dickens","xml","ooffice"]
    blocks=[4096,65536,262144,1048576,4194304]
    fig,axes=plt.subplots(1,2,figsize=(12,5.3),constrained_layout=True)
    for ax,mode in zip(axes,["sync","async"]):
        values=np.array([[get(d,1,mode,b,"screen")["mbps"]/get(d,1,"sw",b,"screen")["mbps"] for b in blocks] for d in datasets])
        im=ax.imshow(values,cmap="RdYlGn",vmin=0,vmax=2,aspect="auto")
        for i in range(len(datasets)):
            for j in range(len(blocks)): ax.text(j,i,"{:.2f}x".format(values[i,j]),ha="center",va="center",color="#111111",fontsize=10)
        ax.set_xticks(range(5)); ax.set_xticklabels(["4 KiB","64 KiB","256 KiB","1 MiB","4 MiB"])
        ax.set_yticks(range(6)); ax.set_yticklabels(datasets); ax.set_title(LABEL[mode]+" / native LZ4")
    fig.colorbar(im,ax=axes,label="Throughput speedup (1.0 = equal)",shrink=.8)
    fig.suptitle("Single physical core: input content and block size determine advantage")
    save(fig,"scenarios")

    fig,ax=plt.subplots(figsize=(9,4.5),constrained_layout=True)
    x=np.arange(len(datasets))
    for i,mode in enumerate(MODES):
        ax.bar(x+(i-1)*.24,[get(d,1,mode,65536,"screen")["output_ratio"]*100 for d in datasets],width=.24,color=COLOR[mode],label=LABEL[mode])
    ax.set_xticks(x); ax.set_xticklabels(datasets); ax.set(ylabel="Output / input (%)",title="64 KiB blocks: output size (lower is smaller)")
    ax.legend(fontsize=9); ax.grid(axis="y",alpha=.2); save(fig,"compression")

    comparison = []
    for dataset in datasets:
        for block in blocks:
            sw = get(dataset, 1, "sw", block, "screen")
            for mode in MODES:
                r = get(dataset, 1, mode, block, "screen")
                comparison.append(dict(dataset=dataset, block_bytes=block, mode=mode,
                    mbps=r["mbps"], output_pct=100*r["output_ratio"],
                    compression_factor=r["compression_factor"], space_saved_pct=r["space_saved_pct"],
                    payload_output_pct=100*r["payload_output_ratio"],
                    output_mb_per_input_gb=1000*r["output_ratio"],
                    throughput_vs_sw=r["mbps"]/sw["mbps"],
                    output_reduction_vs_sw_pct=100*(1-r["output_ratio"]/sw["output_ratio"])))
    with (root/"compression-comparison.csv").open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=list(comparison[0])); writer.writeheader(); writer.writerows(comparison)
    ratio_table=["| 输入 | 实现 | MB/s | 输出/输入 | 压缩倍数 | 空间节省率 | 较软件输出减少 |",
                 "|---|---|---:|---:|---:|---:|---:|"]
    for r in comparison:
        if r["block_bytes"]==65536:
            ratio_table.append("| {dataset} | {mode} | {mbps:.1f} | {output_pct:.3f}% | {compression_factor:.3f}x | {space_saved_pct:.3f}% | {output_reduction_vs_sw_pct:.2f}% |".format(**r))
    (root/"compression-table.md").write_text("\n".join(ratio_table)+"\n")

    fig,axes=plt.subplots(2,3,figsize=(13,7),constrained_layout=True)
    for ax,dataset in zip(axes.flat,datasets):
        for mode in MODES:
            ax.plot(range(5),[get(dataset,1,mode,b,"screen")["compression_factor"] for b in blocks],
                    "o-",color=COLOR[mode],label=LABEL[mode],markersize=4)
        ax.set_title(dataset); ax.set_xticks(range(5)); ax.set_xticklabels(["4K","64K","256K","1M","4M"])
        ax.set_xlabel("Block size (binary bytes)"); ax.set_ylabel("Input / output (x, higher is better)"); ax.grid(alpha=.2)
    axes[0,0].legend(fontsize=8)
    fig.suptitle("Compression factor by input and block size; median of 3 runs")
    save(fig,"compression-blocks")

    fig,ax=plt.subplots(figsize=(9,5.5),constrained_layout=True)
    for mode,marker in [("sync","o"),("async","^")]:
        for dataset in datasets:
            r=next(r for r in comparison if r["mode"]==mode and r["dataset"]==dataset and r["block_bytes"]==65536)
            x=r["throughput_vs_sw"]; y=1-r["output_reduction_vs_sw_pct"]/100
            ax.scatter(x,y,marker=marker,color=COLOR[mode],s=55)
            ax.annotate(dataset,(x,y),xytext=(5,5),textcoords="offset points",fontsize=8)
        ax.scatter([],[],marker=marker,color=COLOR[mode],label=LABEL[mode])
    ax.axvline(1,color="#777777",linestyle="--"); ax.axhline(1,color="#777777",linestyle="--")
    ax.set(xlabel="Throughput / native LZ4 (higher is faster)",ylabel="Output bytes / native LZ4 (lower is smaller)",
           title="64 KiB, one core: speed and compressed size together")
    ax.grid(alpha=.2); ax.legend(fontsize=9); save(fig,"compression-tradeoff")

    table=["| 物理核数 | 原生 LZ4 MB/s | UADK 同步 MB/s | UADK 异步 MB/s | 异步/软件 |", "|---:|---:|---:|---:|---:|"]
    for n in cores:
        sw,sy,asy=[scaling(n,m)["mbps"] for m in MODES]
        table.append("| {} | {:.1f} | {:.1f} | {:.1f} | {:.2f}x |".format(n,sw,sy,asy,asy/sw))
    (root/"scaling-table.md").write_text("\n".join(table)+"\n")
    (root/"statistics.json").write_text(json.dumps({"validated_runs":len(rows),"groups":len(summary),
        "single_core_dickens":{m:scaling(1,m) for m in MODES},
        "crossover_cores":crossings},indent=2))
    print("Generated {} validated runs, {} groups and 7 PNG/PDF charts".format(len(rows),len(summary)))

if __name__=="__main__": main()
