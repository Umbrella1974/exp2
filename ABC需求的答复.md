关于 A 和 C，我想收紧一下边界。

## A. Pinch-Insufficient Slip Gate

A 第一版不要改底层 `PINCH_INSUFFICIENT slip` 定义。

也就是说：

```text
松手进入物块
pinch center 仍在物块内
pinch_state = PINCH_INSUFFICIENT
hand_delta 超过 slip_motion_threshold
```

这种情况仍然可以被底层记录为 `PINCH_INSUFFICIENT slip`。

我现在不想默认把它从 slip 语义里删掉。因为它可能仍然是有意义的“未有效捏紧但在物块内相对运动”。

第一版更合适的做法是：

```text
底层 slip_active / slip_reason 保持不变；
只在 vibration haptic 输出层增加可选 gate。
```

例如在 haptic config 里支持：

```text
pinch_insufficient_slip_policy = allow_loose_touch | requires_prior_grab
```

默认：

```text
allow_loose_touch
```

含义是保持当前行为：松手进入物块也可以触发 PINCH_INSUFFICIENT slip vibration。

如果设置为：

```text
requires_prior_grab
```

才要求必须曾经 `PINCH_VALID + INSIDE_BLOCK` 后，再变成 `PINCH_INSUFFICIENT`，才输出 vibration rough slip。

`need_pinch_active` 可以作为日志/诊断状态预留，但第一版不要强制改变底层 slip，也不要立刻新增 GUI/cue 弹窗。

## C. Boundary Lock 解锁判断

Boundary lock 的解锁不能使用单帧 `hand_delta`。

原因是 `hand_delta = current_pinch_center - previous_pinch_center` 只表示一帧内移动量。
如果受试者慢慢往回拉，例如每帧回拉 1 mm，连续 5 帧总共已经回拉 5 mm，但单帧 `hand_delta` 永远达不到 `unlock_delta_m=0.005`，会导致无法解锁。
同时，单帧 hand_delta 还会受采样率和手套抖动影响，采样率越高单帧位移越小，阈值越难达到；偶发抖动又可能误解锁。

正确做法：

```text
进入 lock 时记录 lock_entry_pinch_center；
lock 期间计算 current_pinch_center - lock_entry_pinch_center；
用这个累计位移在 escape_direction 上的投影作为 escape_progress；
escape_progress >= boundary_lock.unlock_delta_m 时才解锁。
```

也就是说：

```text
previous_pinch_center 可以每帧更新，用来避免解锁后 block 突跳；
但 previous_pinch_center / hand_delta 不能作为解锁进度来源。
```

解锁时仍然需要 reset `previous_pinch_center = current_pinch_center`，避免下一帧物块跳动。


因此，A 不默认改变 PINCH_INSUFFICIENT slip 语义。
valid_grab_required_for_slip 默认 false，或改名为可选 policy。
第一版优先只 gate vibration rough slip，不改底层 slip_active。
need_pinch_active 第一版只记录，不新增 GUI/cue 弹窗。

B 可以先做。
contact_exit + slip_end 同帧时，优先发送 release one-shot 2\n；
slip_end 仍写日志，但标记 covered_by_contact_exit。

C 可以做，但解锁必须使用累计反向位移：
current_pinch_center - lock_entry_pinch_center
不要用单帧 hand_delta。
previous_pinch_center 可以每帧更新，但它不是解锁进度来源。
boundary lock 默认关闭，通过 config 开启。
contact_tolerance_m 字段保留，默认 0.0。
surface_mode=primary 可以作为第一版，但 README 要写明是简化规则。


整体确认，可以按这个方案继续。

## A 确认

`pinch_insufficient_slip_policy` 放在 haptic config 的 `vibration` 下面：

```json
{
  "vibration": {
    "pinch_insufficient_slip_policy": "allow_loose_touch"
  }
}
```

可选值：

```text
allow_loose_touch
requires_prior_grab
```

其中 `requires_prior_grab` 只 gate vibration 的 `slip_start/3\n`，不改变底层：

```text
slip_active
slip_reason
events.csv 的 slip_start/slip_end
processed_frames.csv 的原始 slip 状态
cue
matrix
```

`need_pinch_active` 第一版先不要进 `processed_frames.csv`，避免扩大 A 的范围。可以先记录在：

```text
haptic_command_log.csv details_json
summary count
```

例如：

```text
not_sent_reason = need_pinch_requires_valid_grab
```

或者 details 里记录：

```json
{
  "need_pinch_active": true,
  "pinch_insufficient_slip_policy": "requires_prior_grab"
}
```

以后如果需要逐帧分析 need-pinch，再单独扩展 processed_frames。

## B 确认

如果同一帧 `contact_exit + slip_end`，并且 `enable_release=true` 且 `contact_exit/2\n` 成功 queue：

```text
slip_end/4\n 不发送
slip_end 仍记录一行
send_status = skipped
not_sent_reason = covered_by_contact_exit
```

如果 `contact_exit/2\n` 因为 disabled / not_connected / queue_full / send unavailable 等原因没有成功 queue，则 `slip_end/4\n` 仍按 stop slip 逻辑尝试发送。

如果：

```text
enable_release=false
```

则不能用 contact_exit 覆盖 slip_end，应该继续发送或尝试发送 `4\n`。

## C 确认

`boundary_lock` config 第一版可以是：

```json
{
  "boundary_lock": {
    "enabled": false,
    "unlock_delta_m": 0.005,
    "contact_tolerance_m": 0.0,
    "surface_mode": "primary"
  }
}
```

`contact_tolerance_m` 第一版实现，但默认 0.0。

注意：`contact_tolerance_m` 只在 `boundary_lock_active=true` 时用于维持 lock/contact，不要改变普通 contact_enter/contact_exit 判定。

Boundary lock 需要进入逐帧日志，否则后续无法解释行为。至少写入 `processed_frames.csv` / snapshot 相关字段：

```text
boundary_lock_active
boundary_lock_surface
boundary_lock_escape_progress
boundary_lock_unlock_delta_m
```

如果方便，也增加：

```text
boundary_lock_event
```

例如：

```text
none
lock_enter
locked
unlock
```

## 实施顺序

同意按：

```text
1. B
2. A vibration gate
3. C boundary lock + processed_frames diagnostics
```

但 A 先做小：只影响 vibration output gate 和日志，不新增 GUI cue，不改底层 slip 定义，不改 processed_frames。


整体确认，可以进入实现。现在边界已经比较清楚，但我再补充几条防漏要求。

## A. `requires_prior_grab` 的历史判定

`requires_prior_grab` 的定义可以按 haptic runtime 自己观察到的历史来做，不污染底层状态机。

判定条件：

```text
contact_state == INSIDE_BLOCK
pinch_state == PINCH_VALID
```

一旦本次连续接触期间出现过这个组合，就认为：

```text
has_valid_grab_history = true
```

之后如果出现 `PINCH_INSUFFICIENT slip`，则允许 vibration rough slip 输出。

但这个历史不应只在 `contact_exit` 时清空，也应在以下情况清空：

```text
trial start
trial end
active_release
forced_detach
unexpected_detach
tracking invalid / input invalid
session abort
source stop
```

最终边界是：

```text
底层 slip_active / slip_reason 照旧记录；
requires_prior_grab 只决定 vibration 的 3\n 是否发送；
不影响 cue；
不影响 matrix；
不影响 events.csv；
不影响 processed_frames.csv 原始 slip 字段。
```

## B. `contact_exit + slip_end` 同帧处理

同意当前策略：

```text
如果 contact_exit/2\n 成功 queue：
    slip_end/4\n 不发送
    slip_end 仍记录一行
    send_status = skipped
    not_sent_reason = covered_by_contact_exit
```

这里的“成功”指的是成功进入 vibration worker queue，不是 ESP32 一定已经收到或播放。可以在 `details_json` 里写清楚：

```json
{
  "covered_by_contact_exit": true,
  "coverage_basis": "queued"
}
```

如果 `contact_exit/2\n` 因为 disabled、not_connected、queue_full、send unavailable 等原因没有成功 queue，则 `slip_end/4\n` 仍按 stop slip 逻辑尝试发送。

如果：

```text
enable_release = false
```

则不能用 contact_exit 覆盖 slip_end，应该继续发送或尝试发送 `4\n`。因为没有 release one-shot，就不能假设它帮 ESP32 stop slip。

## C. Boundary lock 配置和日志

`boundary_lock_event` 在 CSV 中统一使用：

```text
none
lock_enter
locked
unlock
```

不要一会儿空字符串一会儿 `none`。建议统一写 `none`。

`boundary_lock` 第一版配置可以是：

```json
{
  "boundary_lock": {
    "enabled": false,
    "unlock_delta_m": 0.005,
    "contact_tolerance_m": 0.0,
    "surface_mode": "primary"
  }
}
```

`boundary_lock.enabled` 默认关闭。

`contact_tolerance_m` 第一版可以实现，但默认是 `0.0`。注意：这个 tolerance 只在：

```text
boundary_lock_active = true
```

时用于维持 lock/contact，不要改变普通 contact_enter/contact_exit 判定。

## C. 解锁逻辑

Boundary lock 解锁必须使用累计反向位移，不要使用单帧 `hand_delta`。

进入 lock 时记录：

```text
lock_entry_pinch_center
escape_direction
```

lock 期间计算：

```text
current_pinch_center - lock_entry_pinch_center
```

再把这个累计位移投影到 `escape_direction` 上：

```text
escape_progress
```

当：

```text
escape_progress >= boundary_lock.unlock_delta_m
```

才解锁。

`previous_pinch_center` 可以在 lock 期间每帧更新，用来避免解锁后 block 突然跳动；但 `previous_pinch_center / hand_delta` 不能作为解锁进度来源。

解锁时仍然需要：

```text
previous_pinch_center = current_pinch_center
```

避免下一帧物块跳动。

## C. 逐帧诊断字段

Boundary lock 必须进入逐帧日志，否则后续无法解释行为。

至少写入 `processed_frames.csv` / snapshot 相关字段：

```text
boundary_lock_active
boundary_lock_surface
boundary_lock_escape_progress
boundary_lock_unlock_delta_m
boundary_lock_event
```

其中 `boundary_lock_event` 使用：

```text
none
lock_enter
locked
unlock
```

## C. surface_mode 说明

`surface_mode = primary` 是第一版预实验简化规则。

如果角落或多方向 blocked，例如：

```text
X_POS + Y_POS
```

boundary lock 第一版只锁 primary surface。这个和 haptic matrix 的多方向组合反馈不是同一层逻辑。

请在 README 中说明：

```text
boundary_lock surface_mode=primary 时，角落/多方向 blocked 只锁 primary surface；
这不是完整多方向锁定，只是第一版用于稳定边界行为的简化规则。
```

## interaction_config 复现要求

如果新增 `interaction_config`，需要写清楚配置优先级，并把 effective config 写入 session 输出。

建议优先级：

```text
默认值
interaction_config 文件
CLI 显式参数
```

最终 effective interaction config 需要写入：

```text
session summary
trial_config
或者单独的 effective_interaction_config.json
```

否则后续无法复现实验当时是否开启 boundary lock、unlock_delta_m 是多少。

## 实施顺序

同意按：

```text
1. B
2. A vibration gate
3. C boundary lock + processed_frames diagnostics
```

但 A 先做小：只影响 vibration output gate 和日志，不新增 GUI cue，不改底层 slip 定义，不改 processed_frames 原始 slip 字段。

B 和 A 是 haptic 层策略，风险较低；C 是核心交互逻辑修改，需要默认关闭、单独测试、单独实机验证。
