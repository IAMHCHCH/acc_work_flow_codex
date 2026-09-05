# 环境配置模板

`config.local.json` 用于填写实际编译机、验证机和 BMC 信息。它已被 `.gitignore` 忽略，不应提交密码、token 或私钥。

```json
{
  "repositories": {
    "kernel": {"path": "/path/to/crypto-2.6", "ref": "<固定提交或标签>", "config": "/path/to/.config"},
    "uadk": {"path": "/path/to/uadk", "ref": "<固定提交或标签>"},
    "uadk_engine": {"path": "/path/to/uadk_engine", "ref": "<固定提交或标签>"}
  },
  "profile": {
    "name": "<环境名称>",
    "environment": {
      "hardware": "<芯片/加速器型号>",
      "kernel_release": "<uname -r>",
      "arch": "aarch64",
      "build_host": "<user@build-ip>",
      "verify_host": "<user@verify-ip>",
      "bmc": "<bmc-ip>"
    },
    "deployment_required": true,
    "deployment_authorized": false,
    "preflight": [{"argv": ["ssh", "<build-user@build-ip>", "uname -a"]}],
    "baseline": [{"argv": ["ssh", "<verify-user@verify-ip>", "<reproduce-command>"], "failure_code": 1}],
    "baseline_expect": "fail",
    "build": [{"argv": ["ssh", "<build-user@build-ip>", "<build-command>"]}],
    "deploy": [{"argv": ["ssh", "<verify-user@verify-ip>", "<install-or-modprobe-command>"]}],
    "verify": [{"argv": ["ssh", "<verify-user@verify-ip>", "<verification-command>"]}],
    "rollback": [{"argv": ["ssh", "<verify-user@verify-ip>", "<rollback-command>"]}],
    "artifacts": ["<workspace-relative-artifact-pattern>"]
  }
}
```

填写步骤：

1. 为每个仓库填写固定提交；不要使用会漂移的 `HEAD`。
2. 在编译机和验证机分别运行 `uname -a`、`ip addr`、设备节点检查，把结果填入 `environment`。
3. `baseline` 写可复现的失败命令；已知正常基线则把 `baseline_expect` 改为 `pass`。
4. 填写真实构建产物、部署和回滚命令。部署默认拒绝，确认回滚可用后才将 `deployment_authorized` 改为 `true`。
5. 运行 `python3 workflow.py doctor`，再运行 `python3 workflow.py new ...` 和 `python3 workflow.py run TASK-...`。

SSH 推荐使用密钥和 `~/.ssh/config` 别名，避免把密码写入命令。不同实例只需要复制此模板并替换地址、账号、设备节点、内核配置和命令；案例会按硬件、内核版本和架构隔离，避免跨环境误复用。
