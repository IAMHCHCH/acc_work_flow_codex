# 当前 agent 的执行协议

用户只提出任务。下列 JSON、命令和报告由当前 Codex / Claude / OpenCode agent 生成和执行，不是要求用户逐条操作。

技能安装后用技能目录的 `scripts/accflow.py` 代替下文 `workflow.py`，即可从任意工作目录运行。`start` 返回 active 表示任务已建档，agent 必须继续 `next` 指示的工作；它没有启动后台服务。只有 `finish` 返回 completed 才能宣称本流程结束。

## 建档与计划

```bash
python3 workflow.py start --request '修复 UADK 异步完成数据丢失' \
  --kind bugfix --repos uadk --environment config.local.json
python3 workflow.py next TASK-ID
```

先冻结所选仓库的提交和环境，克隆到 `.accflow/tasks/TASK-ID/workspace/`，复制并哈希内核 `.config`。源仓库的未提交改动不带入副本，agent 应检查并说明；用户明确要纳入时，先把对应 diff 导入副本并记录。任务 JSON 和证据保存在副本之外。

agent 检查代码、INSTALL、设备、环境、已有案例后编写如下计划：

```json
{
  "stages": ["inspect", "baseline", "change", "build", "verify"],
  "skip_reasons": {"deploy": "Use isolated runtime; no system installation needed"},
  "acceptance": [
    {"id": "regression", "description": "Previously failing request sequence now roundtrips with the native decoder"},
    {"id": "hardware", "description": "Expected ZIP device completes real requests without software fallback"}
  ],
  "rollback": []
}
```

```bash
python3 workflow.py plan TASK-ID --file /absolute/path/plan.json
```

计划固定后不能静默删除验收项。范围变化建立后续任务并带入失败证据。`analysis` 可省略 change/build/deploy，但必须写明 skip_reasons；不能要求纯分析凭空产出 so/ko。`bugfix` 和 `feature` 要求 inspect/baseline/change/build/verify。baseline 可验证原缺陷，也可做功能开发前的通过基线。

## 执行、证据和检查点

```bash
python3 workflow.py exec TASK-ID --stage inspect -- git -C /path/to/clone status --short
python3 workflow.py checkpoint TASK-ID --stage inspect --summary '确认路径、版本和设备映射'
python3 workflow.py exec TASK-ID --stage baseline --expect 1 --contains 'REPRODUCED' -- python3 /path/to/reproducer.py
python3 workflow.py checkpoint TASK-ID --stage baseline --summary '复现原错误，非连接或构建失败'
```

命令参数在显式 `--` 后传入，按 argv 执行，不自动拼 shell。SSH 的远程命令本身仍由远程 shell 解释，agent 负责准确引用参数、禁止泄露凭据。超时杀死本地进程组；断开的 SSH 不保证远程进程已停止，必须检查远程状态再恢复。

agent 用自己的编辑工具修改副本，并关闭 change 检查点。构建时执行真实命令，将远程产物取回任务副本或证据目录，再捕获不可变副本：

```bash
python3 workflow.py checkpoint TASK-ID --stage change --summary '修复完成；新增输入覆写回归'
python3 workflow.py exec TASK-ID --stage build --cwd /path/to/clone -- make -j8
python3 workflow.py attach TASK-ID --kind artifact --file /path/to/clone/.libs/libexample.so
python3 workflow.py checkpoint TASK-ID --stage build --summary '构建成功，产物已取回并哈希'
python3 workflow.py exec TASK-ID --stage verify --check regression --contains PASS -- ssh lab-target 'actual-test-command'
python3 workflow.py exec TASK-ID --stage verify --check hardware --contains PASS -- python3 /path/to/validate-device-results.py
python3 workflow.py checkpoint TASK-ID --stage verify --summary '两项验收均通过'
```

`exec` 返回日志绝对路径、SHA-256、时间、返回码、attempt。verify 必须带计划中的 check ID；测试应主动检测输出、fallback、设备计数等，而非只执行 `uname`。验证期间不能改变源文件。生成物应放在任务证据目录，或预先在 build 生成；不要将会在验证时写入的缓存当作源码。收尾再次检查当前源码、已捕获产物及日志哈希，旧源码测试不能为新源码背书。

## 失败与部署

失败保留原日志并阻止继续。先定位，修复后调用 `retry TASK-ID --reason '...'`；保留 inspect/baseline 检查点，change/build/deploy/verify 必须重新执行，旧轮验证不能结项。默认最多 3 轮，耗尽后返回实际阻塞证据，不能无限重复复位或扩大权限。

计划包含 deploy 时，环境需记录已有用户部署授权 `profile.deployment_authorized: true`，计划还需具体 rollback argv 数组。部署开始前标记 dirty；失败后 agent 使用 `exec --stage rollback -- ...` 按计划回滚，完成全部回滚命令才可 retry。不要为已有授权重复询问用户，也不能把普通开发请求等同于任意环境的破坏性操作授权。

中断留下 `running`。agent 核对远程命令与加载状态后运行 `session-recover TASK-ID --reason '检查所得事实'`，再回滚/重试。它不直接重启板卡；BMC 动作由当前 agent 根据环境信息及用户授权执行并留证。

## 复盘和整理

agent 按 `schemas/agent-result.json` 写 review，至少一条解决问题或能力缺口的 finding。`evidence` 必须为当前轮成功 verify 日志的绝对路径。历史案例可提出 amend、retire 或 propose：匹配环境且有当前成功证据才应用 amend/retire，跨环境仅记录 proposal。始终保留 before/after 和原日志。

```bash
python3 workflow.py finish TASK-ID --review /absolute/path/review.json --report /absolute/path/REPORT.md
```

finish 检查所有阶段、验收项、最新源码、构建产物和证据哈希；导出相对于冻结提交的补丁；调用 Library.archive/maintain 更新案例。相同根因、解决方案、环境的重复案例合并；近似项仅提议复核，不自动删证据。报告和图表可作为文件附件；审核其数值是否由真实数据生成仍是 agent 的责任，框架不能从任意自然语言断言自动证明语义正确。

## 旧批处理模式

`start --headless` 和 `new/run` 保留旧的 Runner：用户或 agent 需先提供完整 profile 命令，内部再调用 Codex CLI 或 command 适配器。这是可选模式，不是 Claude/OpenCode 技能主路径。`python3 workflow.py demo` 测旧模拟编排；`python3 -m unittest discover -s tests` 同时验证新的原生流程、失败修复、证据门槛、安装器与历史修订。
