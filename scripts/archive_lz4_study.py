#!/usr/bin/env python3
"""Archive the completed study through the repository's deduplicating case API."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from accflow.cases import Library

report_dir = ROOT / "reports/lz4-study"
stats = json.loads((report_dir / "statistics.json").read_text())
assert stats["validated_runs"] == 423
review = json.loads((report_dir / "review.json").read_text())
evidence = sorted({item for finding in review["findings"] + review["revisions"] for item in finding["evidence"]})
for item in evidence:
    assert (ROOT / item).is_file(), item
patches = ["reports/lz4-study/patches/lz4-uadk-fixes.patch",
           "reports/lz4-study/patches/lz4-uadk-window-full.patch"]
task = {"id": "LZ4-CONTROLLED-20260907", "commits": {
    "lz4_uadk": "4603e887faea33e64a888d559b23f7836ed72296",
    "uadk_local_source": "f1d6df2b5fdb82ec61b46980502634bd18db8d55"},
    "config": {"profile": {"environment": {
        "hardware": "Kunpeng 920 7270Z, ZIP perf_mode=1 pf_q_num=64 uacce_mode=1",
        "kernel_release": "7.0.0+ #15", "arch": "aarch64",
        "uadk": "2.11.0 isolated runtime; identities in environment.json",
        "cpu_policy": "NUMA 0, one SMT thread per physical core"}}}}
library = Library(ROOT / "cases")
result = library.archive(task, review, evidence, {patch: hashlib.sha256((ROOT / patch).read_bytes()).hexdigest() for patch in patches})
print(json.dumps(result, ensure_ascii=False, indent=2))
