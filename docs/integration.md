# 在 Codex、Claude、OpenCode 中使用

本工程由两层组成：`workflow.py` 是与模型无关的执行器，`plugins/acc-work-flow-codex` 是 Codex 插件/技能入口。Claude 和 OpenCode 使用同一个 CLI，或读取同目录的 `prompts/develop.md` 与 `prompts/review.md`。

## Codex

在 Codex 插件目录安装该插件后，在工程根目录调用：

```bash
python3 workflow.py new --request '任务描述' --kind feature --repos kernel,uadk,uadk_engine
python3 workflow.py run TASK-...
```

代理通过 `codex exec --json --output-schema` 被工作流调用；它只修改隔离副本，构建、部署、验证和归档由工作流控制。

## Claude Code

把仓库作为工作目录，安装本地 skill 后让 Claude 执行：

```text
使用 accflow 技能完成：<任务描述>
先创建任务，再执行全流程；不要跳过验证、复盘或案例整理。
```

## OpenCode

将 `prompts/develop.md` 和 `prompts/review.md` 注册为 project instructions，或在 agent 配置中把 `python3 workflow.py` 作为工具入口。OpenCode 也可以执行：

```bash
python3 workflow.py new --request '<任务>' --repos kernel,uadk
python3 workflow.py run TASK-...
```

模型只负责分析、修改和结构化报告；环境命令、证据哈希、失败重试、回滚和案例维护由 `accflow` 执行，保证不同模型得到相同的验收边界。

每个实例复制 `docs/environment-template.md` 为 `config.local.json`，填入仓库固定提交、编译机、验证机、BMC、构建命令、部署命令、回滚命令和验证命令。不要把密码、token 或私钥放入配置。
