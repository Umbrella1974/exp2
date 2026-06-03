整体同意你的 9 点建议。补充确认如下。

## 关于 behavioral RT 的阶段划分

本阶段先不要计算受试者 behavioral reaction time。

当前 Stage 4 的目标应限定为：

* system timing diagnostics
* session output validation
* 为后续 haptic / behavioral RT 分析准备可靠时间戳和日志

不要在本阶段实现：

* haptic cue -> movement onset RT
* haptic cue -> corrective movement onset RT
* haptic cue -> event resolved time
* subject foot pedal RT
* behavioral RT analyzer

原因是 behavioral RT 依赖真实 haptic cue time、event/cue log、per-frame behavioral state，以及后续可能加入的 subject foot pedal marker。当前 haptic 尚未接入，强行计算 RT 会导致定义不稳定。第一版只需要保证系统时序和日志结构足够支持后续离线 RT 分析。

后续 behavioral RT 应单独作为下一阶段或 haptic 接入后的阶段处理，主要在 offline analysis 中完成，而不是在线 trial loop 中实时判定。

## 1. Session validation 如何定位 summary.json

同意。

新增：

```powershell
python validate_session_outputs.py --session-dir <session_dir> [--summary-json <path>]
```

默认尝试：

```text
session_dir.parent / summary.json
```

不要修改现有输出布局。validator 只读文件，不移动、不复制、不重写 session。

## 2. Validation 的 ERROR / WARNING 边界

基本同意。

建议：

ERROR：

* 必需文件缺失
* 必需字段缺失
* summary.json / trial_summary.json 无法解析
* trial_outcome 缺失
* end_reason 缺失
* effective termination config 缺失
* GUI 启用但 gui_diagnostics.csv 缺失
* timing 启用但 timing_diagnostics.csv 缺失

WARNING：

* raw_frames.jsonl 存在但为空
* 末帧 block / pinch / target 诊断为 null
* target_region 缺失导致 target diagnostics 为 null
* GUI diagnostics 存在但行数很少
* timing diagnostics 存在但部分可选字段为空

可以预留 `--strict`，以后把 raw empty 等 warning 升级为 error，但第一版不强制。

## 3. Debug duration reason 是否保持兼容

同意。

继续使用：

```text
trial_outcome = DURATION_REACHED
end_reason = duration_reached
```

不要改成 `debug_duration_reached`。

但 README / docs 必须明确：

* `duration_reached` 来自 `--duration-seconds`
* 它是 debug runner 外层 stop
* 它不是 protective trial timeout
* protective timeout 应继续使用 `FAILED_TIMEOUT / trial_timeout`

## 4. Operator 提示是否在 --display-mode none 下打印

同意保持静默。

`--display-mode none` 应尽量真正静默。operator command 提示只在 text display 或其他明确可见模式下打印。

错误、崩溃、缺依赖等关键错误仍可输出 stderr。

## 5. 不同 monotonic 时钟域不能相减

强烈同意。

发送方 monotonic 字段：

```text
combined_monotonic_ms
skeleton_receive_monotonic_ms
tracker_receive_monotonic_ms
```

只能在发送方时钟域内部比较。

可以计算：

```text
abs(skeleton_receive_monotonic_ms - tracker_receive_monotonic_ms)
```

也可以记录 combined / skeleton / tracker 的发送方字段。

但绝对不要计算：

```text
Python time.monotonic() - sender monotonic
```

因为发送方和 Python 接收方不是同一个 monotonic 时钟域。

Python 内部 latency 只能使用 Python `time.monotonic()` 产生的字段相减。

## 6. Stage 4 缺少 parser / adapter 耗时字段

同意增加。

请加入：

```text
parse_start_monotonic_ms
parse_end_monotonic_ms
parse_duration_ms

adapter_start_monotonic_ms
adapter_end_monotonic_ms
adapter_duration_ms
```

这些字段用于区分 parser、adapter、trial update 各自耗时。

如果某条路径暂时无法记录某字段，写 null，不要伪造。

## 7. Timing 指标定义

同意以下定义：

```text
raw_to_frame_publish = socket receive -> LatestFrameBuffer.put
frame_wait_age = buffer put -> runner consume
frame_to_trial_update = buffer put -> TrialController.update start
trial_update_duration = TrialController.update start -> end
trial_update_to_snapshot = update end -> DashboardSnapshot 创建完成
snapshot_to_gui = snapshot publish -> 该 frame 首次 GUI render
operator_command_to_stop = 检测到 e/q -> request_stop
```

补充：

* GUI 重复绘制最后一帧时，只记录首次 render。
* snapshot_to_gui 需要通过 frame_index 或 snapshot id 避免重复统计。
* operator_command_to_stop 只使用 Python monotonic 时钟域。
* timing 字段缺失时写 null，不要硬算。

## 8. Replay timing 输出位置

同意。

live integrated：

```text
session/timing_diagnostics.csv
```

replay：

* 只有传 `--out-dir` 时才写 timing diagnostics
* 写到 replay 输出目录
* 绝不修改输入 session
* 必须标记：

```text
mode = replay
is_live_latency = false
```

replay 下 parser / adapter / trial update duration 有参考价值，但 replay 的等待时间和 GUI latency 不应解释为真实 live latency。

## 9. 非阻塞写入策略

同意。

第一版采用线程安全的内存 collector：

* 控制 loop 只更新内存 collector
* GUI render 只更新内存 collector
* 不在每帧 callback 中做文件 I/O
* 不在 GUI render 中做文件 I/O
* run 结束后一次性写 CSV

接受代价：如果进程硬崩溃，timing log 可能丢失。

后续如果需要更稳，可以再做周期性 flush，但不是本阶段目标。

## 本阶段额外边界

本阶段不要实现 behavioral RT 计算。只需要确保 timing/event/session 日志未来足够支持 RT 后处理。

后续 behavioral RT 阶段再处理：

* slip cue -> correction onset
* slip cue -> slip resolved
* detach cue -> recontact
* directional / blocked cue -> direction change
* haptic cue -> event resolved
* subject foot pedal done time
* target entry -> subject done
* trial start -> subject done

这些应主要放在 offline analysis 中，阈值和判定规则应通过后处理配置调整，不要写死进 live trial loop。
