# Claude Code adapter

Run `python3 workflow.py install --client claude`, then invoke `/accflow <task>`.
The current Claude agent performs the work; no Codex subprocess is required.
See `docs/workflow-guide.md` and `docs/agent-protocol.md`.

```text
Use the accflow skill. Take the user's task as the only required input. Infer repositories from config.local.json, create a task, run every stage, and report the final evidence and case IDs.
```

The executable entry is the installed skill's `scripts/accflow.py start --request "..."`.
An active response is a checkpoint, not completion: continue through plan, exec,
checkpoint, review and finish. Claude writes task-specific commands itself and
records their output through the workflow.
