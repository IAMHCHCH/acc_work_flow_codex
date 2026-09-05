# OpenCode adapter

Add `prompts/develop.md` and `prompts/review.md` to project instructions and expose `workflow.py` as the task runner. The single user-facing command is:

```bash
python3 workflow.py start --request "<task>"
```

Repository selection is inferred from `config.local.json`; the workflow owns evidence, retries, deployment gates, rollback, and case maintenance.
