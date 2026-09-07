#!/usr/bin/env python3
"""Locate the configured checkout after skill copies or caching."""
import json
from pathlib import Path
import runpy
import sys

skill = Path(__file__).resolve().parents[1]
marker = skill / 'accflow-location.json'
if marker.exists():
    root = Path(json.loads(marker.read_text())['root'])
else:
    root = next((p for p in skill.parents if (p / 'workflow.py').is_file()), None)
if root is None or not (root / 'workflow.py').is_file():
    sys.exit('accflow checkout unavailable; run python3 workflow.py install from the checkout')
sys.path.insert(0, str(root))
sys.argv[1:1] = ['--root', str(root)]
runpy.run_path(str(root / 'workflow.py'), run_name='__main__')
