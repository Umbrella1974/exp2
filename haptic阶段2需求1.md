你现在要在 `exp2` 仓库当前分支上实现 Haptic TCP Stage 2。

目标：在现有 Haptic TCP Stage 1 基础上，接入 vibration ESP32 的 TCP line-int 协议，并且让 matrix ESP32 和 vibration ESP32 可以**独立启用、独立连接、独立测试、独立 required/fail-fast**。

不要改 TrialController / BlockController / parser / adapter / GUI cue / cue log 语义。

## 背景

现在有两个 ESP32，两个不同 IP，两个不同硬件反馈：

### 1. Matrix ESP32

用途：

```text
blocked direction / matrix electrotactile feedback
```

协议：

```text
MAGIC + length + payload + checksum
```

这是现有 Haptic TCP Stage 1 已经实现的 matrix magic protocol。

继续保持：

* matrix 用于 blocked direction。
* matrix 默认 `latched_once`。
* matrix 不实现可靠 hardware clear。
* matrix 不要使用 vibration 的 line-int 协议。

### 2. Vibration ESP32

用途：

```text
contact / release / slip
```

协议是 TCP server + newline-delimited integer command。

电脑端发送：

```text
1\n  -> contact effect
2\n  -> release effect
3\n  -> start rough slip
4\n  -> stop rough slip
```

ESP32 端口：

```text
12346
```

vibration ESP32 当前语义：

```text
cmd 1: play EFFECT_CMD_1
cmd 2: play EFFECT_CMD_2
cmd 3: start_slip()
cmd 4: stop_slip()
```

电脑端第一版映射：

```text
contact_enter -> 1
contact_exit  -> 2
slip_start    -> 3
slip_end      -> 4
```

## 总体边界

必须遵守：

1. 不修改 `TrialController` / `BlockController` 核心逻辑。
2. 不修改 parser / adapter 坐标语义。
3. 不改变 cue 生成、GUI cue、cue log 语义。
4. 不在 trial loop 中直接执行阻塞式 TCP connect/send。
5. matrix 和 vibration 是两个独立 target device。
6. matrix 和 vibration 必须可以分开启用、分开连接、分开测试。
7. 禁用的 target 不应启动 worker，不应连接 ESP32，不应因为连接失败阻止实验。
8. replay 默认不连接任何真实硬件。
9. haptic timing 仍然只是电脑端 command/send timing，不代表真实触觉 onset 或受试者感知。

## 独立启停要求

这是本阶段最重要的要求。

配置必须允许：

```text
只启用 matrix
只启用 vibration
matrix 和 vibration 都启用
matrix 和 vibration 都禁用
```

例如：

```json
{
  "enabled": true,
  "matrix": {
    "enabled": true,
    "transport": "tcp_magic_v1",
    "required": true
  },
  "vibration": {
    "enabled": false,
    "transport": "disabled",
    "required": false
  }
}
```

或者：

```json
{
  "enabled": true,
  "matrix": {
    "enabled": false,
    "transport": "disabled",
    "required": false
  },
  "vibration": {
    "enabled": true,
    "transport": "tcp_line_int_v1",
    "required": true
  }
}
```

规则：

* `matrix.enabled=false`：不启动 matrix worker，不连接 matrix ESP32。
* `vibration.enabled=false`：不启动 vibration worker，不连接 vibration ESP32。
* `matrix.enabled=true` 且 `matrix.required=true`：matrix 连接失败则 trial 不启动。
* `vibration.enabled=true` 且 `vibration.required=true`：vibration 连接失败则 trial 不启动。
* 如果某 target `required=false`，连接失败可以降级为 log/warning，但不能拖死 trial loop。
* 一个 target 失败不应影响另一个 target，除非失败 target 是 enabled+required。

## Haptic config 扩展

在现有 haptic config 基础上扩展 vibration。

建议结构：

```json
{
  "enabled": true,
  "matrix": {
    "enabled": false,
    "transport": "tcp_magic_v1",
    "required": true,
    "host": "",
    "port": 12345,
    "startup_settle_seconds": 7.0,
    "connect_timeout_s": 3.0,
    "send_timeout_s": 0.05,
    "feedback_mode": "latched_once",
    "direction_semantics": "blocked_surface",
    "ignore_direction_axes": [],
    "direction_channel_map": {},
    "combination_channel_map": {},
    "missing_combination_policy": "skip"
  },
  "vibration": {
    "enabled": false,
    "transport": "tcp_line_int_v1",
    "required": true,
    "host": "",
    "port": 12346,
    "connect_timeout_s": 3.0,
    "send_timeout_s": 0.05,
    "startup_settle_seconds": 0.0,

    "enable_contact": true,
    "enable_release": true,
    "enable_slip": true,
    "enable_slip_pinch_insufficient": true,
    "enable_slip_track_blocked": true,
    "enable_slip_track_blocked_in_target_region": true,

    "command_map": {
      "contact_enter": 1,
      "contact_exit": 2,
      "slip_start": 3,
      "slip_end": 4
    },

    "one_shot_interrupts_slip": true,
    "reassert_slip_after_one_shot": true
  }
}
```

说明：

* `transport="tcp_line_int_v1"` 表示发送 `b"1\n"` 这种整数换行命令。
* `transport="disabled"` 或 `enabled=false` 时不连接。
* unknown fields 要清晰报错。
* `command_map` 必须允许以后修改，不要把 1/2/3/4 写死在 runtime 里。
* `port` 默认 12346。

## Vibration TCP worker

新增或扩展 worker，例如：

```text
VibrationTcpLineWorker
```

要求：

* 和 MatrixTcpWorker 独立。
* 只在 `vibration.enabled=true` 且 `transport=tcp_line_int_v1` 时启动。
* trial 前连接，不在第一次 contact/slip 时才连接。
* 使用 bounded queue。
* trial loop 只 enqueue command，不直接 socket send。
* 发送 payload 是 ASCII integer + newline，例如：

  ```python
  b"3\n"
  ```
* `sent_monotonic_ms` 是电脑端 send 完成时间。
* 当前 ESP32 没有 ack，`ack_monotonic_ms=null` 或不使用 ack。
* send 失败写 haptic log，不应阻塞 trial loop。
* shutdown 时，如果 slip 当前 active，应尽量发送 `4\n`，然后关闭 socket。
* 如果 socket 断开，ESP32 端会 stop slip，但电脑端仍应记录 connection lost。

## Vibration 状态映射

haptic runtime 使用已有状态，不重新判断 contact/slip。

映射：

```text
contact_enter -> vibration one_shot -> command 1
contact_exit  -> vibration one_shot -> command 2
slip false -> true -> vibration state_start -> command 3
slip true -> false -> vibration state_end -> command 4
```

要求：

* contact/release 是 one-shot。
* slip 是 continuous state，从 slip_start 到 slip_end。
* 不要每帧重复发送 `3\n`。
* 只在 slip edge 发送：

  ```text
  false -> true: 3
  true -> false: 4
  ```
* tracking invalid / trial end / abort / source stop / haptic shutdown 时，如果 slip active，应尝试发送 `4\n`。
* 不要用 vibration one-shot 代替 slip continuous state。

## Slip 开关语义

保留之前确认的多层开关：

```text
enable_slip=false:
  关闭全部 slip vibration

slip_reason=PINCH_INSUFFICIENT:
  由 enable_slip_pinch_insufficient 控制

slip_reason=TRACK_BLOCKED 且 block 不在 target_region:
  由 enable_slip_track_blocked 控制

slip_reason=TRACK_BLOCKED 且 block 在 target_region:
  由 enable_slip_track_blocked_in_target_region 控制
```

要求：

* blocked 时 slip 可以单独关闭。
* 关闭 blocked slip 不影响 pinch-insufficient slip。
* target_region 内的 blocked slip 可以单独打开。
* target_region 使用已有 map target_region，不新造距离阈值。
* matrix blocked feedback 和 vibration slip 可以同时发生，不互相压制。

## contact/release 与 slip 的同设备冲突

注意 vibration ESP32 是单个 DRV2605 输出。ESP32 代码里 `play_effect_once()` 会停止 rough slip。

因此电脑端需要有清晰规则。

第一版建议：

```text
one-shot contact/release 优先；
如果 one-shot 发生时 slip 仍 active，并且 reassert_slip_after_one_shot=true，
则在下一次 runtime update 或一个短延迟后重新发送 slip_start。
```

要求：

* 不要无限重复发送 slip_start。
* haptic log 中记录 one-shot 打断 slip 的情况，例如：

  ```text
  details_json: {"interrupted_slip": true}
  ```
* README 说明：当前 vibration ESP32 的 one-shot effect 和 rough slip 不是叠加关系，one-shot 会打断 slip；电脑端可以根据配置重新启动 slip。

如果实现短延迟会让 worker 复杂化，可以先采用“下一次 runtime update 若 slip_active 仍为 true，则重新发送 slip_start”的方案。

## Haptic target independence

不要跨 target 复用 cue display priority。

规则：

```text
matrix 和 vibration 可以同一帧都生成 command。
matrix 的 blocked direction 不应压制 vibration slip。
vibration slip 也不应影响 matrix blocked direction。
```

target 内部可以自己处理 state update/replacement，例如：

* matrix direction changed -> matrix state_update
* vibration slip active -> no repeated start
* vibration one-shot may interrupt/reassert slip

但 target 之间不要互相压制。

## Haptic log 字段

确保 haptic log 能区分 target 和协议。

建议增加或填充：

```text
target_device
target_transport
vibration_command
vibration_command_label
sent_payload
haptic_phase
send_status
not_sent_reason
error
details_json
```

示例：

```text
target_device=vibration
target_transport=tcp_line_int_v1
vibration_command=3
vibration_command_label=slip_start
sent_payload=3\n
haptic_phase=state_start
```

matrix 继续记录：

```text
target_device=matrix
target_transport=tcp_magic_v1
matrix_direction_used
channel_list
```

## Startup / fail-fast behavior

在 live trial 启动前：

1. 根据 config 启动 enabled targets。
2. matrix enabled 时连接 matrix ESP32。
3. vibration enabled 时连接 vibration ESP32。
4. 只连接 enabled targets。
5. disabled target 不连接，不等待，不报错。
6. enabled+required target 连接失败：trial 不启动，清晰报错。
7. summary 中记录：

   ```text
   run_stop_reason = matrix_haptic_connect_failed
   ```

   或：

   ```text
   run_stop_reason = vibration_haptic_connect_failed
   ```
8. 如果两个都启用且其中一个失败，按失败 target 的 required 策略处理。

## Separate smoke tests

新增或扩展 smoke test 脚本，使两个硬件能分开测试。

建议：

```text
run_matrix_haptic_smoke.py
run_vibration_haptic_smoke.py
```

或者一个统一脚本：

```text
run_haptic_smoke.py --target matrix
run_haptic_smoke.py --target vibration
```

必须支持：

### matrix smoke

```text
连接 matrix ESP32
发送指定 channel list
不连接 vibration ESP32
```

### vibration smoke

```text
连接 vibration ESP32
发送 1 / 2 / 3 / 4
不连接 matrix ESP32
```

vibration smoke 示例：

```text
python run_haptic_smoke.py --target vibration --host 192.168.x.y --port 12346 --command 1
python run_haptic_smoke.py --target vibration --host 192.168.x.y --port 12346 --command 3
python run_haptic_smoke.py --target vibration --host 192.168.x.y --port 12346 --command 4
```

用于一个一个测试硬件。

## Live outputs

如果任一 haptic target enabled，live session 写：

```text
session/haptic_command_log.csv
session/haptic_config.json
```

summary / session_meta / trial_config / trial_summary 中记录：

```text
haptic_enabled
matrix_haptic_enabled
vibration_haptic_enabled
matrix_transport
vibration_transport
haptic_mode=live
is_live_haptic_timing=true
haptic_count
haptic_type_counts
effective_haptic_config
haptic_command_log_path
```

如果全部 disabled：

* 不要求 haptic log。
* summary 记录 `haptic_enabled=false`。

## Replay behavior

Replay 默认：

```text
matrix.enabled=false
vibration.enabled=false
```

Replay 不自动连接真实硬件。

即使 replay session 中原来保存了 hardware config，也不要自动连接 ESP32。

只有用户显式启用 replay hardware output 时才允许连接，并且要有清晰提示。第一版可以不实现 replay hardware output。

## Validator

更新 validator：

* 旧 session 没有 haptic 字段不报错。
* 任一 target enabled 时，检查 haptic artifacts。
* 检查 haptic_config 与 effective_haptic_config 一致。
* 检查 haptic log header。
* 检查 `target_device` 合法：matrix / vibration。
* 检查 vibration command 字段合法。
* 检查 haptic_count / haptic_type_counts。
* `haptic_count=0` 的 header-only log 可以接受。

## Tests

新增或更新测试：

1. vibration TCP line encoder：1 -> `b"1\n"`。
2. invalid vibration command 清晰报错。
3. vibration disabled 不连接、不发送。
4. vibration enabled+required 连接失败 -> startup fail-fast。
5. matrix enabled、vibration disabled：只启动 matrix worker。
6. vibration enabled、matrix disabled：只启动 vibration worker。
7. 两个都 enabled：启动两个独立 worker。
8. contact_enter -> vibration command 1。
9. contact_exit -> vibration command 2。
10. slip false->true -> vibration command 3。
11. slip true->true -> 不重复发送 3。
12. slip true->false -> vibration command 4。
13. trial end / invalid / abort 时，如果 slip active，尝试发送 4。
14. enable_slip=false 禁止全部 slip。
15. enable_slip_track_blocked=false 不影响 pinch-insufficient slip。
16. enable_slip_track_blocked_in_target_region=true 允许 target_region 内 track-blocked slip。
17. matrix blocked 和 vibration slip 同帧可以同时生成 command。
18. one-shot 打断 slip 时，按配置 reassert slip。
19. smoke test target=matrix 不连接 vibration。
20. smoke test target=vibration 不连接 matrix。
21. replay 默认不连接硬件。
22. full test suite passes。

## README

更新 README，说明：

* matrix 和 vibration 是两个 ESP32、两个 IP、两个独立 target。
* matrix 使用 magic packet protocol，默认端口 12345。
* vibration 使用 TCP newline integer protocol，默认端口 12346。
* 两个硬件可以单独启用和单独 smoke test。
* contact_enter/contact_exit 是 one-shot vibration。
* slip 是 continuous vibration，从 start 到 end。
* one-shot 和 rough slip 在当前 ESP32 上不是叠加关系，one-shot 会中断 slip。
* matrix blocked direction 和 vibration slip 可以同时发生。
* replay 默认不连接真实硬件。

## 完成后请输出

1. 修改了哪些文件。
2. vibration TCP line protocol 如何编码。
3. matrix / vibration 如何独立启用和独立连接。
4. 单独测试 matrix / vibration 的命令。
5. contact/release/slip 如何映射到 1/2/3/4。
6. slip start/end 如何避免重复发送。
7. one-shot 与 slip 冲突如何处理。
8. trial end / invalid / abort 时如何停止 slip。
9. replay 默认是否连接硬件。
10. 新增测试和测试结果。

整体确认，可以按你这 6 点实现。

## 1. one-shot 重新启动 slip

同意第一版采用“下一次 runtime update”方案，不加复杂延迟线程。

README 中需要明确：

```text
one-shot 和 rough slip 不是叠加关系；
contact/release 会中断 slip；
如果 reassert_slip_after_one_shot=true，电脑端会在后续 frame 中重新发送 slip_start；
这不保证 one-shot 完整播放结束后才恢复 slip。
```

另外请注意，reassert 不要变成每帧重复发送 `3\n`。建议用一个 pending 标记：

```text
pending_slip_reassert_after_one_shot = true
```

下一次 runtime update 时，如果 `slip_active=true` 且 slip 配置仍允许，则发送一次 `3\n`，然后清掉该标记。

## 2. vibration queue

同意。

Vibration worker 使用 FIFO bounded queue，不使用 latest-only replacement。

原因：

```text
3 = start slip
4 = stop slip
```

是状态命令，尤其 `4\n` 不能被随便丢弃或替换。

如果队列满：

```text
send_status = not_sent
not_sent_reason = queue_full
```

但 trial loop 仍不能阻塞。

trial end / invalid / abort / shutdown 时，如果 slip active，应尽力发送 `4\n`。如果 `4\n` 发送失败，记录：

```text
not_sent_reason = stop_slip_send_failed
```

或对应 error，并关闭 socket，让 ESP32 端 disconnect handler 作为 stop slip 兜底。

## 3. `sent_payload` CSV 格式

同意。

CSV 中不要写真实换行。写转义字符串：

```text
sent_payload = "3\\n"
```

可以额外记录：

```text
payload_hex = 330a
```

这样后处理和肉眼检查都更安全。

## 4. `command_map` 数值范围

同意。

config validation 可以允许正整数，例如：

```text
1..255
```

但 README 需要写清楚：当前 ESP32 固件只实现：

```text
1, 2, 3, 4
```

未知命令即使发到 ESP32，也只会打印 unknown，不会产生反馈。

## 5. `one_shot_interrupts_slip=false`

同意你的解释。

当前 ESP32 固件中，one-shot 实际上总会中断 rough slip。
因此：

```text
one_shot_interrupts_slip=false
```

不能表示硬件可以叠加播放。它只能表示电脑端不做 reassert/记录逻辑，或者不把 one-shot interruption 作为特殊事件处理。

README 里必须说明这一点。

## 6. startup connected 字段

同意增加更清晰的 startup 字段：

```text
matrix_haptic_startup_connected
vibration_haptic_startup_connected
matrix_haptic_connect_error
vibration_haptic_connect_error
```

避免 session 结束后 worker stopped 导致 `*_connected=false` 被误解为“启动时没连上”。

## 7. 实现路线

同意你的实现路线：

```text
保留现有 run_matrix_haptic_smoke.py
新增 run_vibration_haptic_smoke.py
新增 VibrationTcpLineWorker
扩展 haptic_config.py 的 vibration.required / transport / command_map
在 HapticRuntime 中把 contact/release/slip edge 映射到 1/2/3/4
README 明确写 one-shot 会打断 slip，以及 reassert 的限制
```

继续保持：

```text
不改 TrialController
不改 BlockController
不改 GUI cue
不改 cue log 语义
matrix 和 vibration 可独立启用和独立测试
replay 默认不连接硬件
```
4\n 的优先级要高。trial end、invalid、abort、shutdown 时，如果 slip active，应该尽力发送 4\n；如果发不出去，也要记录 stop_slip_send_failed，并关闭 socket，让 ESP32 端走 disconnect stop 兜底。
reassert_slip_after_one_shot 的实现不要变成每帧刷 3\n。只能在“one-shot 打断过 slip，并且后续 frame 仍 slip_active”的情况下补发一次 3\n，然后清除 pending reassert 标记。