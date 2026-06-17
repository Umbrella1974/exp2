你现在要在 `exp2` 仓库当前分支上实现 Haptic TCP Stage 1。

请先阅读当前代码，不要依赖 README。重点看：

* `cue_feedback.py`
* `cue_config.py`
* `data_models.py`
* `haptic_feedback.py`，如果已有
* `run_live_integrated_session.py`
* `replay_debug_runner.py`
* `run_replay_debug_gui.py`
* `validate_session_outputs.py`
* map / target_region 相关代码
* session / trial_config / summary 输出相关代码
* 当前 tests

## 实际硬件背景

实验后续会使用两个 ESP32，均通过 TCP 与电脑端通讯。

### 1. Matrix / electrotactile ESP32

用途：

```text
blocked direction / 轨道阻挡方向反馈
```

该 ESP32 端是 TCP server，当前已知协议是：

```text
MAGIC(4B) + payload_length(1B) + payload(N<=128B) + checksum(1B)
```

其中：

```text
MAGIC = AA 55 AA 55
checksum = sum(payload) & 0xFF
```

payload 主要是 HV507 channel list。

如果 payload[0] 带 `0x80`，ESP32 端会将第一个 byte 当作 PWM control byte，后续才是 channel data。当前 haptic Stage 1 主要需要 matrix channel list，不要扩展 PWM 语义。

Matrix ESP32 有 HV507 / latch 机制。注意：

* 电脑端不要把“发送空 channel frame”描述成可靠 clear / stop_all。
* 不要假设电脑端能通过输入内容安全中断 HV507 所有输出。
* 如果需要记录 blocked 状态结束，只在电脑端日志中记录状态结束。
* 如果以后需要真正硬件级中断，应由 ESP32 端/硬件端另行实现。
* 本阶段不要写 misleading 的 `clear_all` / `stop_all` 语义。

### 2. Vibration ESP32

用途：

```text
contact / release / slip
```

但 vibration ESP32 的协议目前尚未确定。

因此本阶段：

* 预留 vibration target。
* 记录 vibration haptic command。
* 不实现真实 vibration TCP packet 编码。
* 不连接或发送未知 vibration 协议。
* 不要假设 vibration ESP32 的 payload 格式。

## 总体架构边界

必须遵守：

1. 不修改 `TrialController` / `BlockController` 核心状态机。
2. 不修改 parser / adapter 坐标语义。
3. 不改变 cue 生成语义。
4. 不在 trial loop 中直接执行阻塞式 TCP `connect` / `send` / `sendall`。
5. Matrix 和 vibration 是两个不同 IP 的 ESP32。
6. Matrix 和 vibration 应作为两个 target device；matrix 可有真实 TCP worker，vibration 本阶段只做 pending/logging target。
7. Trial loop 只提交 haptic command，不等待硬件发送完成。
8. 队列必须 bounded，不能无限增长。
9. TCP 断线、发送失败、队列满等问题只记录到 haptic log，不应拖死 trial loop。
10. Replay 默认不向真实硬件发送数据。
11. 实验条件中的“硬件输出/不输出”由后级电源或硬件开关控制，不要在电脑端 haptic 代码中承担这个实验条件控制。

## 两个 ESP32 的网络配置

两个 ESP32 是两个 IP，端口可以固定，例如：

```json
{
  "matrix": {
    "host": "192.168.x.x",
    "port": 12345
  },
  "vibration": {
    "host": "192.168.x.y",
    "port": 12345
  }
}
```

如果 matrix enabled，要求配置 matrix host。

vibration 本阶段不做真实发送，因此可以允许 vibration host 为空，或者仅记录配置，不连接。

## Haptic source：优先消费 cue-stage / existing state

当前 cue-stage 已经有这些 cue type：

```text
contact_enter
contact_exit
slip_pinch_insufficient
slip_track_blocked
blocked_directional
```

本阶段优先消费现有 `CueCommand` 和已有 frame/state 字段，不要重新实现 contact/slip/blocked 判断。

原则：

```text
contact_enter -> vibration target，one-shot
contact_exit -> vibration target，one-shot
slip_pinch_insufficient -> vibration target，continuous slip state
slip_track_blocked -> vibration target，continuous slip state
blocked_directional -> matrix target，blocked direction state
```

如果 haptic 层需要知道 slip 何时结束，可以使用已有 `slip_active` / `slip_start` / `slip_end` / cue runtime state，不要自己重新判断 slip。

如果 haptic 层需要知道 blocked 何时结束，可以使用已有 `blocked_force_active` / `blocked_force_start` / `blocked_force_end` / `track_state` / cue runtime state，不要自己重新判断 blocked。

## Matrix feedback：blocked direction

Matrix ESP32 第一版只处理：

```text
blocked_directional
```

方向来源使用 cue command 或 existing state 中已有字段：

```text
direction
primary_blocked_surface
track_state
```

不要从连续 `force_vector_task` 推断方向。

不要把 `X_NEG / X_POS / Y_NEG / Y_POS / Z_NEG / Z_POS` 翻译成 left/right/up/down。

第一版只使用 task-coordinate direction，并通过配置映射到 HV507 channel list：

```json
{
  "direction_channel_map": {
    "X_NEG": [1, 2, 3],
    "X_POS": [4, 5, 6],
    "Y_NEG": [7, 8, 9],
    "Y_POS": [10, 11, 12],
    "Z_NEG": [],
    "Z_POS": []
  }
}
```

如果 direction 没有配置 channel list：

* 不发送 matrix packet。
* haptic log 中记录 skipped / no_channel_mapping。
* 不中止 trial。

## Matrix 发送模式

Matrix 需要支持两种发送策略：

```text
latched_once
continuous_resend
```

默认：

```text
matrix_feedback_mode = latched_once
```

### latched_once

语义：

```text
blocked_directional start -> 发送一次当前 direction channel frame
blocked direction changed -> 发送一次新的 direction channel frame
blocked 持续且 direction 不变 -> 不重复发送
blocked end -> 记录状态结束，不假设可以 clear 硬件输出
```

注意：不要在 blocked end 时自动发送所谓 `clear_all`，除非配置显式要求实验性 zero-frame。即使实现 zero-frame，也只能命名为 `send_zero_channel_frame`，并在 README 中说明它不是可靠硬件级清空。

### continuous_resend

语义：

```text
blocked_directional active -> 按固定周期重复发送当前 direction channel frame
blocked direction changed -> 立刻发送新 direction channel frame
blocked end -> 停止重复发送，记录状态结束
```

默认 resend interval 可设为：

```text
100 ms
```

但默认模式仍然是 `latched_once`。

## Matrix packet encoder

新增独立 encoder，例如：

```python
encode_matrix_packet(payload: bytes) -> bytes
```

编码规则：

```text
packet = MAGIC + length + payload + checksum
length = len(payload)
checksum = sum(payload) & 0xFF
```

约束：

* payload 长度必须 <= 128。
* channel 必须是 0-127 的整数。
* channel list 中每个值编码为一个 byte。
* 无效 channel 清晰报错或 skipped，不发送坏包。
* packet encoder 必须有单元测试。

本阶段不要修改 ESP32 端代码。

## Vibration target：状态预留，不真实发送

Vibration ESP32 的协议暂未确定，所以本阶段只做 haptic command 路由和日志。

状态语义：

```text
contact_enter:
  one-shot vibration command

contact_exit:
  one-shot vibration command

slip:
  continuous state
  slip_start -> vibration slip start command
  slip_end -> vibration slip end command
```

本阶段不要实现 vibration TCP packet encoder。vibration command 记录为：

```text
target_device = vibration
send_status = protocol_pending / not_implemented
sent_monotonic_ms = null
```

## Slip 输出开关

需要支持多层 slip 配置。

建议 haptic config 中加入：

```json
{
  "vibration": {
    "enabled": false,
    "enable_contact": true,
    "enable_release": true,
    "enable_slip": true,
    "enable_slip_pinch_insufficient": true,
    "enable_slip_track_blocked": true,
    "enable_slip_track_blocked_in_target_region": true
  }
}
```

语义：

```text
enable_slip = false:
  所有 slip vibration 都关闭

enable_slip = true:
  再根据 slip reason / target region 判断
```

具体规则：

```text
slip_reason = PINCH_INSUFFICIENT:
  由 enable_slip_pinch_insufficient 控制

slip_reason = TRACK_BLOCKED 且 block 不在 target_region:
  由 enable_slip_track_blocked 控制

slip_reason = TRACK_BLOCKED 且 block 在 target_region:
  由 enable_slip_track_blocked_in_target_region 控制
```

这样应满足：

* blocked 时 slip 可以单独关闭。
* 关闭 blocked slip 不影响 pinch-insufficient slip。
* target_region 内的 blocked slip 可以单独开启。
* global `enable_slip=false` 时，所有 slip 都关闭。

`target_region` 必须使用 map / task 中已有的 target_region 判断，不要新发明“距离 target 多近”的阈值。若当前已有 target diagnostic 字段，例如 block center in target region，应复用；否则实现一个小工具函数，使用与现有 target 判断一致的几何定义。

## 同时输出规则

如果同一时刻既有 blocked direction，又有 slip：

```text
matrix:
  输出 blocked direction

vibration:
  如果对应 slip 开关允许，则记录/输出 slip
```

也就是说：

* `slip_reason=TRACK_BLOCKED` 时，可以同时有 matrix blocked direction 和 vibration slip。
* 但 vibration slip 可以由 `enable_slip_track_blocked=false` 关闭。
* target_region 内是否允许 vibration slip，由 `enable_slip_track_blocked_in_target_region` 单独控制。
* 不要因为存在 matrix blocked feedback 就自动压制 vibration slip，除非配置关闭。

## Haptic command / log

新增 haptic command record，字段至少包括：

```text
haptic_sequence_index
haptic_id
cue_id
cue_sequence_index
trial_id
target_device
source_frame_id
source_frame_index
source_trial_time
cue_type
haptic_type
haptic_phase
direction
channel_list
created_monotonic_ms
queued_monotonic_ms
sent_monotonic_ms
success
send_status
not_sent_reason
error
mode
is_live_haptic_timing
details_json
```

其中：

```text
target_device = matrix | vibration
haptic_phase = one_shot | state_start | state_update | state_end
```

示例：

```text
contact_enter -> target_device=vibration, haptic_phase=one_shot
contact_exit -> target_device=vibration, haptic_phase=one_shot
slip_start -> target_device=vibration, haptic_phase=state_start
slip_end -> target_device=vibration, haptic_phase=state_end
blocked_directional start -> target_device=matrix, haptic_phase=state_start
blocked direction changed -> target_device=matrix, haptic_phase=state_update
blocked end -> target_device=matrix, haptic_phase=state_end, no hardware clear assumed
```

`sent_monotonic_ms` 表示电脑端 socket send 完成时间，不代表受试者感受到触觉，也不代表真实硬件物理 onset。

## Haptic config

新增或扩展 haptic config。默认必须是 disabled。

示例：

```json
{
  "enabled": false,
  "matrix": {
    "enabled": false,
    "host": "",
    "port": 12345,
    "connect_timeout_s": 3.0,
    "send_timeout_s": 0.05,
    "max_queue_size": 8,
    "latest_only": true,
    "feedback_mode": "latched_once",
    "resend_interval_ms": 100,
    "send_zero_channel_frame_on_end": false,
    "direction_channel_map": {
      "X_NEG": [],
      "X_POS": [],
      "Y_NEG": [],
      "Y_POS": [],
      "Z_NEG": [],
      "Z_POS": []
    }
  },
  "vibration": {
    "enabled": false,
    "host": "",
    "port": 12345,
    "protocol": "pending",
    "enable_contact": true,
    "enable_release": true,
    "enable_slip": true,
    "enable_slip_pinch_insufficient": true,
    "enable_slip_track_blocked": true,
    "enable_slip_track_blocked_in_target_region": true
  }
}
```

Unknown fields should error clearly.

## TCP worker

Implement a real TCP worker only for matrix in this stage.

Requirements:

* connect before trial if matrix enabled.
* do not connect in the trial loop.
* use worker thread / queue.
* queue must be bounded.
* queue full must not block trial loop.
* latest_only replacement should be logged.
* send failure should be logged, not crash the trial loop unless configured as fail-fast during startup.
* if matrix is disabled, old flow unchanged.
* vibration target should not start a real TCP worker yet unless protocol is implemented later.

## Live outputs

If haptic stage is enabled, live session should write:

```text
session/haptic_command_log.csv
session/haptic_config.json
```

summary / session_meta / trial_config / trial_summary should include:

```text
haptic_enabled
matrix_haptic_enabled
vibration_haptic_enabled
haptic_mode = live
is_live_haptic_timing = true
haptic_count
haptic_type_counts
effective_haptic_config
haptic_command_log_path
```

If haptic disabled:

* no haptic log required.
* summary should still record `haptic_enabled=false`.

## Replay behavior

Replay default:

```text
haptic_enabled = false
```

Replay must not automatically connect to real hardware.

If later replay logging is added, mark:

```text
haptic_mode = replay
is_live_haptic_timing = false
```

Do not replay-send real ESP32 packets unless the user explicitly opts in with a clearly named hardware option.

## Validator

Update validator only for live haptic artifacts:

* old sessions without haptic fields should not fail.
* if haptic enabled, check `haptic_config.json`.
* if haptic enabled, check `haptic_command_log.csv`.
* check CSV header.
* check haptic_count and haptic_type_counts.
* check haptic_id uniqueness.
* check effective_haptic_config consistency.

## Tests

Add tests for:

1. matrix packet encoder magic / length / checksum.
2. channel range validation.
3. blocked_directional -> matrix haptic command.
4. direction -> channel list mapping.
5. missing channel map -> skipped, no send.
6. matrix latched_once sends only on start / direction change.
7. matrix continuous_resend repeats according to interval.
8. matrix end does not assume hardware clear.
9. vibration contact_enter/contact_exit command records are one-shot.
10. vibration slip_start/slip_end records are continuous state start/end.
11. slip global disable disables all slip vibration.
12. `enable_slip_track_blocked=false` disables only TRACK_BLOCKED slip outside target_region.
13. disabling blocked slip does not disable PINCH_INSUFFICIENT slip.
14. `enable_slip_track_blocked_in_target_region=true` allows slip in target_region.
15. target_region logic uses existing map target_region, not new distance threshold.
16. matrix and vibration target devices are independent.
17. matrix TCP worker does not block trial loop.
18. replay default does not connect hardware.
19. haptic disabled keeps old flow unchanged.
20. full test suite passes.

## README / docs

Document clearly:

* Haptic Stage 1 implements matrix TCP magic protocol only.
* Vibration target is reserved/logged but protocol is pending.
* Matrix feedback is HV507/latch style; computer-side zero frame is not a guaranteed hardware clear.
* Default matrix mode is `latched_once`.
* Optional `continuous_resend` exists.
* contact/release are one-shot vibration states.
* slip is continuous from start to end.
* blocked direction and slip can occur simultaneously.
* blocked slip, pinch-insufficient slip, and target-region slip have separate enable flags.
* `target_region` uses map-defined target_region.
* timing fields are computer-side command/send timing, not true perceptual onset.
* replay does not send real hardware by default.

## 完成后请输出

1. 修改了哪些文件。
2. Matrix ESP32 TCP packet 协议如何编码。
3. Matrix `latched_once` 和 `continuous_resend` 如何实现。
4. 为什么没有实现真实 hardware clear。
5. Vibration target 本阶段记录了哪些状态，哪些还没有实现。
6. slip enable flags 的具体语义。
7. target_region slip 如何判断。
8. 两个 ESP32 target 如何独立，如何避免阻塞 trial loop。
9. live 输出哪些 haptic 文件。
10. replay 默认是否会发硬件。
11. 新增测试和测试结果。
