# 环境填写方式

新环境以 [examples/environment.json](../examples/environment.json) 为模板，填写仓库路径、编译机、验证机和恢复方式，保存为被 Git 忽略的 `config.local.json`。也可以保存到其他位置，再告诉 agent：

```text
使用 accflow，环境文件是 /absolute/path/lab-b.json，
目标仓库为 kernel 和 uadk。请完成：……
```

| 字段 | 填写内容 |
|---|---|
| `repositories.<name>.path` | 本机源码仓库绝对路径 |
| `repositories.<name>.ref` | 要使用的提交、标签或 HEAD；start 会解析并冻结实际提交 |
| `repositories.kernel.config` | 编译内核使用的 .config 路径；任务创建时复制并哈希 |
| `profile.environment.build_host` | 编译机 SSH 别名或 user@host |
| `profile.environment.verify_host` | 验证机 SSH 别名或 user@host |
| `hardware / kernel_release / arch` | 已知芯片、内核版本和架构；未知值由 agent 探测补齐 |
| `devices` | 已知 PCI、NUMA、设备节点映射；动态编号每次现场核对 |
| `bmc` | 跳板机、控制台地址及端口、重启命令、等待时间、网络恢复方法 |
| `runtime_strategy` | 临时独立动态库、指定安装目录等运行约束 |
| `profile.deployment_authorized` | 是否已有本次范围内的部署授权；agent 应复用已有用户授权 |

不用预先填写 build、verify 命令或 artifacts。这些由 agent 检查 INSTALL、源码与任务需求后制定；需要部署时还会给出具体回滚命令。纯性能分析不强制生成 so/ko。

SSH 凭据由本机 SSH 配置或客户端的凭据机制管理，不写进 JSON、日志或案例。示例 IP、端口、CPU0 复位方式均需替换为实际环境。可直接给 agent 环境文字说明，让其代填 JSON 并探测缺失信息。

每次开始前应探测当前内核、设备、NUMA、perf_mode 和产物。重启可能清空 /tmp、改变设备编号或模块参数；旧配置是线索，不能代替现场证据。已创建任务的配置不可静默更换，目标环境改变时建立后续任务并关联旧证据。

旧 `start --headless` 批处理仍要求完整逐阶段命令和产物配置，可参考仓库的 `config.json` 与 [agent 协议](agent-protocol.md)。普通三端 Skill 使用上述环境事实模板。
