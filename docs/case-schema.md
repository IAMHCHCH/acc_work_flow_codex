# 案例格式

`cases/*.json` 保存任务结论，`evidence/*.json` 保存命令输出。字段包括 `id/kind/title/fingerprint/status/symptoms/root_cause/changes/verification/lessons/supersedes`。指纹由任务类型、标题、症状和根因规范化后计算；`consolidate` 按指纹标记重复案例，保留证据文件。
