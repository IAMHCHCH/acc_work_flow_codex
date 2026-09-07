# 三端集成入口

在本工程运行一次 `python3 workflow.py install --client all`，将同一个 accflow Skill 安装到三个客户端的发现目录。也可用 `--client codex`、`--client claude` 或 `--client opencode` 单独安装。

| 客户端 | 用户级目录 | 提出任务 |
|---|---|---|
| Codex | `~/.agents/skills/accflow` | `$accflow 完成……` |
| Claude Code | `~/.claude/skills/accflow` | `/accflow 完成……` |
| OpenCode | `~/.config/opencode/skills/accflow` | `加载 accflow skill，完成……` |

`--target /path/to/project` 可安装到项目目录。技能定位文件记录本工程绝对路径，允许从其他工作目录调用；移动源码目录后重新安装。更新源码后运行 install 同步技能，`install --check` 检查是否同步。客户端可能需要重新打开会话才能发现新技能。

主路径由当前客户端 agent 使用自己的工具执行，不启动第二个模型。用户提供自然语言目标与环境信息；agent 生成计划、执行命令、开发和验证，框架管理证据、检查点和案例修订。

- [图解、完整用法与能力边界](workflow-guide.md)
- [环境填写方式](environment-template.md)
- [agent 内部协议](agent-protocol.md)
- [本轮实际验证记录](../reports/workflow-validation/REPORT.md)

旧 `new/run` 与 `start --headless` 是可选批处理，要求完整命令配置并调用所配置的 CLI agent。它们不是三端 Skill 的默认入口。原生 `start` 创建会话后由当前 agent 继续，不是独立后台服务。
