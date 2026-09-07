# OpenCode adapter

Run `python3 workflow.py install --client opencode`; ask OpenCode to load the
`accflow` skill and complete the task. The current OpenCode agent drives the
workflow without another model process. The internal entry is:

```bash
python3 workflow.py start --request "<task>"
```

Select relevant repositories from `config.local.json`, then keep driving next,
plan, exec, checkpoint and finish. `start` returning active does not mean complete.
See `docs/workflow-guide.md` for installation, use and validation boundaries.
