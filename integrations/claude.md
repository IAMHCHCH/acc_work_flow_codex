# Claude Code adapter

Install this repository as a project skill, then invoke:

```text
Use the accflow skill. Take the user's task as the only required input. Infer repositories from config.local.json, create a task, run every stage, and report the final evidence and case IDs.
```

The executable entry is `python3 workflow.py start --request "..."`; Claude should not replace the workflow's build, verify, rollback, or archive stages with ad-hoc commands.
