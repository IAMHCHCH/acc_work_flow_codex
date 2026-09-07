# 受控 CPU 对比

基线工具为 `scripts/controlled_bench.c` 和 `scripts/run_lz4_matrix.py`；双设备和官方带宽追加实验使用 `scripts/lz4_ceiling_matrix.py`。
旧 `bench_lz4_uadk` 在硬件清理时调用 `numa_run_on_node()`，会扩大主线程及后续软件线程的 CPU 集合。仅在外层加 `taskset` 不足以保证公平，旧日志只用于问题定位。

## 测量合同

- 从目标机 `lscpu` 查询 `(socket, core, node)`，在 NUMA 0 选每个物理核的一个 SMT 线程，不把逻辑 CPU 数当作物理核数。
- 每个工作线程在测量前显式绑定单个 CPU；预热后和测量后检查 affinity；运行时检查全部进程线程不得超出允许集合。
- 三种实现独立进程运行，使用完全相同输入和块边界，每轮改变实现执行顺序。当前默认每点三次。
- 排除输入生成、磁盘读取、初始化、内存预热和正确性检查时间；报告是内存驻留压缩吞吐，不是文件端到端吞吐。
- 预热阶段遍历整个输入，提取 UADK 私有封装内的 LZ4 block，用原生 `LZ4_decompress_safe` 解码并逐字节比较。软件输出也回解比较。
- UADK 同步轮询和异步提交/轮询/组装均包含在计时内；异步每线程深度 8，全部提交和轮询都在该工作线程执行。
- 同步硬件完成数必须覆盖计时请求；硬件 API 错误不能伪装为成功输出。不可压缩数据的原样存储与软件回退不同。
- 使用单调墙钟、线程 CPU 时间和整个进程 CPU 时间。`cpu_cores_used = process_cpu_seconds / wall_seconds`，`CPU 秒/GB = process_cpu_seconds / input_bytes * 1e9`。
- 新 CSV 使用十进制 MB/s。UADK 输出字节数包含其私有 12 字节封装，软件为原生 LZ4 block；它不是标准 LZ4F 帧，不能用标准帧互操作性替代块级验证。
- `LZ4_UADK_HW_CONCURRENCY=0` 取消封装默认的 8 请求信号量上限；硬件队列背压通过 poll 和重试处理。所有三种实现的 CPU 限制一致。

## 复现

编译机具有统一版本的 UADK 构建目录和 LZ4/NUMA 头文件及链接库时，使用 `scripts/lz4_rebuild.sh SOURCE UADK DEPS` 构建。源码需应用报告目录中归档的补丁，测试源 `controlled_bench.c` 与 `lz4_async_regression.c` 放在源码根目录。将可执行文件和同次构建的 `libwd`、`libwd_comp`、`libhisi_zip` 放入隔离运行目录；驱动库同时放在 `.libs/uadk/`，不覆盖系统库。

在验证机执行：

```bash
python3 run_lz4_matrix.py --runtime /tmp/lz4-runtime \
  --corpus /path/to/silesia --output /tmp/lz4-results \
  --phase scaling --seconds 2 --repeats 3
```

`--phase crossing` 细测 10/12/14/15/16 核；`--phase screen` 测试三种合成输入和 Dickens/XML/Ooffice 的 4 KiB、64 KiB、256 KiB、1 MiB、4 MiB 块。需要 NUMA 0 上至少 32 个可用物理核。其他拓扑需调整矩阵核数并重新记录环境，不可沿用本机结果。

原始数据和全部执行命令写入 CSV/日志；失败立即中止，不生成“通过”的数据行。图表脚本 `scripts/lz4_report.py` 消费最终数据生成图表和统计表。环境模板见 `docs/lz4-environment.example.json`。

## 双设备与匹配窗口

基线矩阵的会话在 NUMA 0 创建，虽然两设备均分配了队列，实际压缩只进入 NUMA 0 的 ZIP。双设备方案按 CPU 所属节点显式选择会话，输入和工作缓冲区在所属 CPU 首次触碰。软件使用相同 CPU 集合及内存策略。

新增 API `lz4_uadk_create_ctx_on_node(params, node)` 和 `lz4_uadk_async_create_config(depth, threads, node, window_kb)` 支持指定节点和 4/8/16/24/32 KiB 匹配窗口；原 API 保留 32 KiB 默认值。同步 API 的 `params.window_size` 现在实际传递给 UADK。实验必须记录窗口，因为缩小窗口会改变匹配结果和压缩率。

官方工具示例（本验证机 NUMA 0 对应 `hisi_zip-1`）：

```bash
LD_LIBRARY_PATH=/tmp/accflow-lz4-unified/.libs:/usr/lib64 \
numactl --physcpubind=0,2,4,6,8,10,12,14 --membind=0 \
/tmp/accflow-lz4-unified/uadk_tool benchmark \
  --alg lz77_only --mode sva --opt 0 --sync --pktlen 65536 \
  --seconds 5 --thread 8 --ctxnum 8 --device hisi_zip-1 \
  --prefetch --complevel 8 --winsize 1
```

`--winsize` 是枚举：0/1/2/3/4 对应 4/8/16/24/32 KiB，不是 KiB 数值。`--complevel 8` 必须显式指定；默认 0 会在驱动报错后打印零吞吐并以成功退出。工具输出 KiB/s，乘 1024/1e9 转为十进制 GB/s。官方测试生成约 70% 伪随机前缀、30% 零尾部，每线程重复同一输入；未执行 LZ4 组装，不能使用其 compress data rate 作为 LZ4 压缩率。

`lz4_ceiling_matrix.py` 的 `official`、`official-fine`、`official-confirm` 阶段先测单设备，再同时启动两个进程，各自指定设备、互不重叠的物理核集合及本地内存。异步的提交和轮询线程均计入该集合。每点保存命令、亲和性采样、输出日志和两设备完成计数。返回码、非零吞吐和设备计数都必须满足要求。所有设备实验串行调度，功能测试不得与带宽采样同时进行。

`optimized` 阶段使用 32 KiB 窗口，`window` 阶段使用 8 KiB 窗口及少量窗口/块大小诊断。后者指定 `--binary controlled_bench_window`。`LZ4_BENCH_LOCAL=1` 启用 NUMA 本地策略；`LZ4_BENCH_WINDOW_KB=8` 指定封装窗口。`raw` 模式只测原始 LZ77，输出标识 `RAW_NOT_LZ4`，不参加压缩率或 LZ4 正确性统计。

当前追加矩阵的设备、CPU 编号及输入路径是本机复现实验值，换机必须依据环境模板及 `lscpu`/UACCE 映射更新，不能直接沿用。深度 32 曾触发队列超时和驱动复位，失败证据保留；默认使用已验证的深度 8，不自动重试掩盖硬件故障。

新版完整补丁为 `reports/lz4-study/patches/lz4-uadk-window-full.patch`，直接应用到报告指定的上游基线；不要与旧完整补丁叠加。追加图表由 `python3 scripts/lz4_ceiling_report.py` 生成。
