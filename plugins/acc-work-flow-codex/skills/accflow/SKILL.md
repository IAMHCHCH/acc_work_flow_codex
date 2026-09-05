---
name: accflow
description: Run the repository's full accelerator-driver workflow for a user task.
---

When invoked, run `python3 workflow.py new` and `python3 workflow.py run` from the repository root. First collect the task request and selected repositories. Use `config.local.json` for the active lab environment. Do not claim hardware verification without successful evidence logs. After completion run `python3 workflow.py consolidate` and report the task id, artifacts, verification evidence, failures, and archived case ids.
