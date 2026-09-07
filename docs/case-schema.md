# 案例格式

实际存储为 `cases/library.json`，包含 `cases`、`events` 和 `maintenance`。案例字段为 id、title、component、symptom、root_cause、resolution、lessons、failed_attempts、evidence、scope、revision、status、occurrences、source_commits、patch_hashes。

指纹基于规范化的 component、root_cause、resolution 和完整 scope。完全重复的活动案例标为 merged，证据、经验和出现任务合入主案例；相似度较高的同组件根因仅成为候选。不会物理删除证据。

amend 更新 resolution/lessons 并递增 revision；retire 标为 retired；事件保存 before、after、reason。只有新证据来自当前成功验收且 scope 完全相同才自动应用；否则保留 proposal。重复提交同一修订具有幂等键。完整字段校验见 `schemas/agent-result.json`，执行入口见 `accflow/cases.py`。
