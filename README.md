# HiSilicon Accelerator Auto Workflow

面向海思加速器（Linux 内核、UACCE、UADK）的可恢复开发与问题修复工作流。阶段为 intake、inspect、change、build、deploy、verify、review、consolidate；每次任务生成案例和证据，便于检索、去重和复盘。

```bash
python3 workflow.py init
python3 workflow.py run --request '修复 ZIP 异步压缩超时' --dry-run
python3 workflow.py consolidate
```

默认只做仓库状态采集。目标机部署需在适配器中显式配置并关闭 dry-run。验证清单应覆盖 UACCE/IOMMU SVA/SMMU V3/PCI PASID、`CONFIG_CRYPTO_DEV_HISI_*`、设备节点权限、同步异步、并发边界和复位恢复；UADK 构建依据其 INSTALL，内核构建使用 `.config`。

## 配置真实环境

复制 `config.json` 为 `config.local.json`（不要提交），填写固定的硬件、内核版本和架构；为每个阶段提供 `argv` 数组命令。基线故障必须设置 `baseline_expect: fail` 及对应 `failure_code`，构建必须设置 `artifacts`。需要加载 ko 或替换 so 时，同时填写备份/回滚命令，并明确设置 `deployment_authorized: true`。工作流会拒绝未配置环境、缺少回滚、空验证步骤和不完整产物。

常用命令：

```bash
python3 workflow.py doctor
python3 workflow.py new --request '...' --kind bugfix --repos kernel,uadk
python3 workflow.py run TASK-...
python3 workflow.py status TASK-...
python3 workflow.py recover TASK-...
python3 workflow.py search 'ZIP async queue'
python3 workflow.py consolidate
```

`doctor` 已显示当前内核工作树有大量未提交文件和未解决冲突；任务只从固定提交克隆隔离副本，因此不会把这些存量修改带入代理工作区。`demo` 是纯 Python 模拟，仅验证编排逻辑，不代表海思硬件通过。

自动代理使用 `codex exec --json --output-schema`，每个阶段都有独立 prompt 和结构化报告；官方文档说明该模式适合 CI、支持 JSONL、可恢复会话和显式 sandbox。参考项目：[LangGraph](https://github.com/langchain-ai/langgraph)（持久化状态/恢复）与 [Superpowers](https://github.com/obra/superpowers)（规格、复现、验证、复盘）。

验证机断电时可在编译机运行 `scripts/wait_for_target.sh`。它通过 BMC `192.168.90.209:10008` 执行 `sh /home/reset_chip.sh 0`，随后轮询验证机 SSH；脚本不保存密码。长期免手工配置 IP 的正确方案是把 IP 写入验证机 NetworkManager、systemd-networkd 或 ifcfg 配置，并配置 DHCP 保留租约；`~/ip_set.sh` 适合作为一次性修复，不能作为可靠自动化依赖。
