整体同意你的判断，但有几点需要确认和调整。

## 1. 关于 `CueRuntime` 不能作为 haptic 唯一输入源

你说的这一点很重要。我理解为：cue 层可能因为 GUI/console 显示优先级，只实际输出或显示最高优先级 cue；但 haptic 需求允许不同硬件同时输出。

例如同一帧可能同时有：

```text
slip_active = true
slip_reason = TRACK_BLOCKED
blocked_force_active = true
track_state = BLOCKED_X_NEG
```

这时硬件需求是：

```text
matrix:
  输出 blocked direction

vibration:
  如果配置允许，输出 slip
```

所以 haptic 层不能只消费 `CueRuntime.process_frame()` 最终返回的 cue command。
haptic 层应观察已有 `trial_result` / processed frame / existing haptic feedback state 中的状态字段，例如：

```text
slip_active
slip_reason
blocked_force_active
track_state
primary_blocked_surface
contact_state
```

但注意：这不是让 haptic 层重新判断 contact/slip/blocked。haptic 层只能消费已有状态和事件，做路由和记录，不重新实现状态机。

## 2. Matrix direction 语义

这两个功能我都需要，但默认使用 A。

请支持配置：

```json
{
  "matrix": {
    "direction_semantics": "blocked_surface"
  }
}
```

可选值：

```text
blocked_surface
correction_direction
```

语义：

```text
blocked_surface:
  使用 primary_blocked_surface / track_state
  表示撞到哪一侧边界

correction_direction:
  使用 CueCommand.direction 或等价字段
  表示应该往哪个方向退回
```

默认：

```text
direction_semantics = blocked_surface
```

也就是默认使用 `primary_blocked_surface / track_state`。

但日志中请同时保存：

```text
primary_blocked_surface
correction_direction
matrix_direction_used
matrix_direction_semantics
```

避免后续分析时混淆。

## 3. ESP32 clear / zero frame

Stage 1 先不要发送 clear / zero-channel frame。

原因：

当前 matrix 端是 HV507 / latch 结构，电脑端不应假设发送空 channel frame 就能可靠中断或清空硬件输出。
因此不要实现 misleading 的 `clear_all` / `stop_all` 语义。

Stage 1 规则：

```text
blocked start / direction changed:
  发送 direction channel frame

blocked end:
  只记录 haptic state_end
  不发送 clear

trial end / invalid / abort:
  只关闭/停止电脑端 worker 和日志
  不假设能通过 TCP payload 清空 HV507 输出
```

如果以后要实现可靠硬件清空，需要单独修改或确认 ESP32 端 parser / HV507 blanking / LE / BL 控制。请把这点写进 README TODO。

## 4. Matrix connect 和 ESP32 端启动延迟

ESP32 端连接后有硬件上电/等待延迟。电脑端应尊重 ESP32 侧延迟。

如果 `matrix.enabled=true`：

* 在 trial 开始前连接 matrix ESP32。
* 不要在 trial loop 或第一次 blocked 时才连接。
* 可以增加配置：

```json
{
  "matrix": {
    "startup_settle_seconds": 7.0
  }
}
```

连接和等待都应发生在 trial running 之前，避免第一次 blocked 反馈卡顿或延迟。

## 5. Matrix connect 失败策略

如果 matrix hardware 已启用，连接失败不能只写 warning。

默认策略应是 fail fast：

```json
{
  "matrix": {
    "enabled": true,
    "required": true
  }
}
```

如果 `matrix.enabled=true` 且 `matrix.required=true`，但 TCP 连接失败：

```text
trial 不启动
清晰报错
run_stop_reason = matrix_haptic_connect_failed
phase 停在 trial 前
summary 中记录 haptic connect error
```

只有显式配置：

```json
{
  "matrix": {
    "required": false
  }
}
```

时，才允许 degrade to logging / warning。

## 6. Slip 输出开关补充

请注意 slip 需要多层开关：

```json
{
  "vibration": {
    "enable_slip": true,
    "enable_slip_pinch_insufficient": true,
    "enable_slip_track_blocked": false,
    "enable_slip_track_blocked_in_target_region": true
  }
}
```

语义：

```text
enable_slip=false:
  所有 slip vibration 都关闭

slip_reason=PINCH_INSUFFICIENT:
  由 enable_slip_pinch_insufficient 控制

slip_reason=TRACK_BLOCKED 且 block 不在 target_region:
  由 enable_slip_track_blocked 控制

slip_reason=TRACK_BLOCKED 且 block 在 target_region:
  由 enable_slip_track_blocked_in_target_region 控制
```

这样 blocked 时 slip 可以单独关掉，不影响 pinch-insufficient slip；target_region 内的 blocked slip 可以单独开启。

target_region 必须复用 map / trial 中已有的 `target_region` 判断，不要新造距离阈值。

## 7. Vibration 端

Vibration ESP32 协议暂时还没定，所以 Stage 1 只做：

```text
contact_enter -> vibration command log, one_shot
contact_exit -> vibration command log, one_shot
slip_start -> vibration command log, state_start
slip_end -> vibration command log, state_end
```

不要真实连接 vibration ESP32，不要假设 vibration packet 格式。

## 8. 其他边界保持不变

保持：

* 不改 TrialController / BlockController。
* 不改 parser / adapter 坐标语义。
* 不改 cue log 语义。
* matrix 和 vibration 是两个 target device。
* matrix 是真实 TCP worker。
* vibration Stage 1 只记录/预留。
* replay 默认不连接真实硬件。


haptic priority 不要直接复用 cue priority。
因为 cue priority 是为了“显示一个主要提示”，但 haptic 是两个硬件并行，matrix 和 vibration 可以同时输出。更好的规则是：每个 target device 内部自己处理状态，target device 之间不要互相压制。


确认，可以按这个实现。

补充一点实现边界：

haptic runtime 可以维护上一帧的 `slip_active` / `blocked_force_active` / direction signature，用于生成：

```text
state_start
state_update
state_end
```

这不算重新实现状态机，只是对已有状态字段做边缘检测。不要重新计算 contact/slip/blocked 的成立条件。

另外请注意：

```text
haptic priority 不复用 cue display priority。
```

原因是 cue priority 是为了 GUI/console 显示一个主要提示；haptic 有两个独立 target device：

```text
matrix
vibration
```

它们可以同时生成 command。例如 `slip_reason=TRACK_BLOCKED` 时：

```text
matrix -> blocked direction
vibration -> slip
```

不应因为 blocked direction 优先级更高就压制 vibration slip。

如果需要 priority / replacement，只在同一个 target device 内部处理，例如 matrix 当前方向更新、vibration slip start/end/update，不要跨 target device 互相压制。
