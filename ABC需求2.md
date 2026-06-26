我想把前面说的 `transition prelude` 重新定义一下。这里不要理解成“让受试者感受到的过渡刺激”，而应该理解成：

```text
Matrix output change 之前的 state-dependent hardware reset。
```

也就是：当 Matrix 从上一种正式输出切换到下一种正式输出时，先根据“上一种 Matrix 输出内容”发送一个可配置的 reset channel list，然后再发送下一种正式输出。

## 核心语义

不要实现统一的 `clear_all`、`stop_all`、`blank`。
也不要假设 empty channel frame 能可靠清空硬件。
程序不负责理解硬件复位的物理含义。

程序只负责：

```text
1. 记录上一种 Matrix 正式输出 previous_matrix_output_key
2. 准备发送下一种 Matrix 正式输出 next_matrix_output_key
3. 如果 two keys 不同，并且 reset_before_output_change.enabled=true：
      用 previous_matrix_output_key 查 reset_map
      如果找到 reset channel list，先发送 reset packet
      然后发送 next main output packet
4. 如果找不到 reset mapping，按 missing_reset_policy 处理
```

这里的 reset 内容完全由 config 指定。

## 命名

建议不要叫 `transition_prelude`，因为它容易被理解成给人的过渡刺激。
建议叫：

```text
reset_before_output_change
```

或者：

```text
state_dependent_reset
```

## 配置结构建议

在 matrix config 里加入：

```json
{
  "matrix": {
    "reset_before_output_change": {
      "enabled": false,
      "missing_reset_policy": "skip_reset",
      "reset_map": {
        "contact_valid": {
          "channel_list": [],
          "hold_ms": 0
        },
        "pinch_insufficient": {
          "channel_list": [],
          "hold_ms": 0
        },
        "blocked:X_POS": {
          "channel_list": [],
          "hold_ms": 0
        },
        "blocked:X_POS+Y_NEG": {
          "channel_list": [],
          "hold_ms": 0
        }
      }
    }
  }
}
```

`enabled=false` 时，不发送任何 reset，旧行为不变。

`missing_reset_policy` 第一版支持：

```text
skip_reset
error
```

默认：

```text
skip_reset
```

含义是：如果 previous output 没有 reset mapping，就不发送 reset，直接发送 next main output，并在 haptic log 里记录：

```text
not_sent_reason = missing_matrix_reset_mapping
```

如果以后需要更严格的实验条件，可以用：

```text
error
```

让缺失 reset mapping 时不发送 next output 或直接报错。第一版默认不要这么激进。

## Matrix output key

需要定义稳定的 `matrix_output_key`，用于 reset_map 查表。

建议：

```text
contact_valid
pinch_insufficient
blocked:<matrix_direction_used>
```

例如：

```text
contact_valid
pinch_insufficient
blocked:X_POS
blocked:X_POS+Y_NEG
blocked:X_NEG+Y_POS
```

注意：`blocked:<matrix_direction_used>` 应使用经过现有 direction_semantics、ignore_direction_axes、combination mapping 逻辑后实际用于 Matrix 的 key。

也就是说，如果 Matrix 实际输出的是：

```text
X_POS+Y_NEG
```

那么 key 就是：

```text
blocked:X_POS+Y_NEG
```

## 发送顺序

当 Matrix output 从 A 切换到 B：

```text
previous_matrix_output_key = A
next_matrix_output_key = B
```

如果 reset enabled 且 A 有 reset mapping，则发送顺序是：

```text
reset packet for A
main output packet for B
```

如果 A 没有 reset mapping，则按 `missing_reset_policy` 处理。

如果 A == B，不发送 reset。

如果只是 continuous_resend 同一个 output，也不发送 reset。

## 状态结束到 none 的情况

请也支持一个明确策略：

```json
{
  "reset_before_output_change": {
    "apply_on_transition_to_none": false
  }
}
```

默认 `false`。

如果以后我希望从某个 Matrix 输出结束到 none 时也发送 reset，可以把它设成 true。
第一版默认不要在 output -> none 时自动发送 reset，避免引入意外硬件行为。

## hold_ms

`hold_ms` 可以先保留，默认 0。

第一版如果实现复杂，可以先只支持：

```text
hold_ms = 0
```

也就是 reset packet 和 next main packet 连续入队。
如果以后硬件需要复位保持时间，再扩展支持非零 `hold_ms`。

不要为了 hold_ms 在 trial loop 里 sleep。
如果需要延迟，必须在 Matrix worker 内部处理，不能阻塞 trial loop。

## 日志

haptic log 需要记录 reset 行，不要把 reset 混在 main output 里。

建议 reset command 记录为：

```text
target_device = matrix
haptic_type = matrix_reset_before_output_change
haptic_phase = reset
previous_matrix_output_key = contact_valid
next_matrix_output_key = blocked:X_POS
channel_list = [...]
send_status = queued/sent/skipped/error
```

main output 继续记录原有 haptic_type，例如：

```text
matrix_contact_valid
matrix_pinch_insufficient
matrix_blocked_direction
```

这样后面能看清楚：

```text
先发了哪个 reset
再发了哪个正式 Matrix 输出
```

## 与新增 Matrix 状态的关系

新增 Matrix 正式输出状态包括：

```text
matrix_contact_valid
matrix_pinch_insufficient
matrix_blocked_direction
```

但最终还是要先 resolve 成一个 `next_matrix_output_key` 和一个正式 channel list。

第一版建议只允许一个 Matrix main output active。优先级：

```text
blocked_direction > pinch_insufficient > contact_valid
```

不要自动把多个状态的 channel list 相加。

reset 逻辑发生在最终 Matrix main output 切换之前，而不是分别散落在三个状态内部。

## 边界

不要修改：

```text
TrialController
BlockController
cue
vibration
ESP32 matrix 协议
processed_frames 原始状态
events.csv 原始状态
```

只修改 matrix haptic 输出层、配置、日志和测试。

README 中要明确：

```text
reset_before_output_change 不是给人的过渡刺激；
它是按 previous_matrix_output_key 查表发送的硬件复位 channel list；
程序不假设 reset channel list 的物理含义；
reset 是否有效取决于实际 Matrix ESP32/HV507 硬件配置。
```
