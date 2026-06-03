你现在要在 `exp2` 仓库的 `GUI-stage` 分支上新增 timing / latency instrumentation 和后处理分析。

背景：

当前系统已经基本形成预实验单 trial 闭环。下一步要接 haptic，但在 haptic 接入前，需要先把现有 live/replay 链路中的关键时间戳和延迟记录清楚。否则后续无法判断延迟来自 raw stream、parser/adapter、trial update、GUI，还是 haptic 通讯。

本阶段目标：

1. 记录关键时间点。
2. 计算关键时间差。
3. 输出 timing log。
4. 提供一个离线 timing summary / analysis 工具。
5. 不接 haptic 硬件。
6. 不改变 trial 判定逻辑。

请先阅读代码，不要依赖 README。重点看：

* `raw_manus_vive_parser.py`
* `device_frame_models.py`
* `live_raw_stream.py`
* `latest_frame_buffer.py`
* `latest_frame_pump.py`
* `live_trial_runner.py`
* `run_live_integrated_session.py`
* `debug_gui.py`
* GUI diagnostics log
* raw frame / summary / session 输出结构

## 目标 1：定义 TimingRecord / TimingDiagnostics

新增轻量 timing 数据结构，例如：

```text
TimingRecord
TimingDiagnostics
TimingLogRow
```

字段命名按项目风格即可，但至少支持记录：

```text
frame_index
raw_receive_monotonic_ms
combined_monotonic_ms
skeleton_receive_monotonic_ms
tracker_receive_monotonic_ms
skeleton_tracker_sync_delta_ms
frame_published_monotonic_ms
frame_consumed_monotonic_ms
trial_update_start_monotonic_ms
trial_update_end_monotonic_ms
snapshot_created_monotonic_ms
snapshot_published_monotonic_ms
gui_render_monotonic_ms
operator_command_monotonic_ms
trial_end_monotonic_ms
```

如果某些字段当前拿不到，不要伪造。写 null / empty，并在文档中说明。

优先使用 monotonic time 计算延迟。wall-clock time 可以用于日志可读性，但不要用 wall-clock 做精确延迟计算。

## 目标 2：记录关键延迟

尽量计算以下指标：

```text
skeleton_tracker_sync_delta_ms
raw_to_frame_publish_latency_ms
frame_wait_age_ms
trial_update_duration_ms
frame_to_trial_update_latency_ms
trial_update_to_snapshot_latency_ms
snapshot_publish_to_gui_render_latency_ms
operator_command_to_trial_stop_latency_ms
```

如果 GUI 未启用，GUI 相关字段为 null。

如果 replay 模式没有真实 receive time，请标记 mode=replay，并避免把 replay timing 误解成真实 live latency。

## 目标 3：输出 timing log

live integrated runner 应输出一个 timing log，例如：

```text
session/timing_diagnostics.csv
```

或 JSONL。

要求：

1. 不阻塞控制 loop。
2. 如果每帧写入影响实时性，使用缓冲 / 降频 / 轻量写入。
3. 不要在 GUI render callback 内做重日志写入。
4. timing log 应和 session 一起归档。
5. summary 中记录 timing log 路径。

## 目标 4：离线 timing summary

新增或扩展一个后处理工具，例如：

```bash
python analyze_timing.py --session-dir <session_dir>
```

输出 summary，例如：

```text
frame_count
median_skeleton_tracker_sync_delta_ms
p95_skeleton_tracker_sync_delta_ms
median_raw_to_update_latency_ms
p95_raw_to_update_latency_ms
median_trial_update_duration_ms
p95_trial_update_duration_ms
median_snapshot_to_gui_latency_ms
p95_snapshot_to_gui_latency_ms
max_no_frame_gap_ms
operator_command_to_stop_latency_ms
```

可以输出 JSON summary，也可以打印表格。优先 JSON + 简洁打印。

## 目标 5：README / known risks

更新 README，说明：

1. 哪些 timing 字段来自真实 live。
2. 哪些字段 replay 下不代表真实延迟。
3. 如何运行 timing analysis。
4. timing 结果如何用于后续 haptic 调试。

## 约束

* 不接 haptic。
* 不改变 trial outcome / termination 逻辑。
* 不改变 parser / adapter 的语义。
* 不让 timing log 阻塞控制 loop。
* 不为了 timing 大重构 LiveTrialRunner。
* 不伪造缺失时间戳。
* 不让 GUI 直接参与 trial 控制。

## 测试要求

请新增或更新测试覆盖：

1. TimingRecord 能安全处理缺失字段。
2. sync delta 计算正确。
3. latency 计算使用 monotonic time。
4. replay 模式不会被误标为真实 live latency。
5. timing log 能写出并被 analyze_timing 读取。
6. analyze_timing 能输出 median / p95 / max 等指标。
7. GUI 未启用时 GUI latency 字段为 null。
8. timing instrumentation 不改变现有 trial outcome 测试。
9. 全量测试通过。

## 完成后请输出

1. 新增了哪些 timing 字段。
2. 哪些字段当前拿不到，如何表示。
3. timing log 写到哪里。
4. analyze_timing 如何运行。
5. live 和 replay timing 如何区分。
6. 是否影响控制 loop。
7. 测试结果。
