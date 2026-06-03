确认以下实现决策。

## 1. visual profile 的核心规则更新

我确认修改实验 profile 的显示语义。

不要采用之前的：

```text
block_visible=false 时显示 block center marker + pinch marker
block_visible=true 时不显示 block
```

新的实验显示规则是：

```text
block_visible=false:
  什么都不显示，包括 block center marker、pinch marker、hand marker、track、target、block-pinch line

block_visible=true:
  显示物块位置
  显示 pinch 点位置
  不显示 track
  不显示 target
  不显示 block-pinch line
```

这样 contact enter / contact exit 可以通过物块消失 / 重新出现表达：

```text
contact enter -> block_visible=false -> 物块和 pinch 点都消失
contact exit / detach / release -> block_visible=true -> 物块和 pinch 点重新出现
```

由于这个语义已经不符合 `experiment_markers_when_hidden` 这个名字，建议新增更准确的 visual profile 名称：

```text
experiment_visibility_feedback
```

可以保留 `experiment_markers_when_hidden` 作为兼容 alias，但 README 中应标记为 deprecated 或说明它映射到新的 `experiment_visibility_feedback` 规则。

对应测试也需要更新：不再测试 visible/free 时不显示 block，而是测试 visible/free 时显示 block + pinch，hidden/contact/grabbed/blocked 时不显示任何 task marker。

## 2. Replay cue config

同意你的建议。

replay cue config 优先级：

1. 显式 `--cue-config` 优先。
2. 使用 `--session-dir` 且存在 `session/cue_config.json` 时，默认加载该 effective config。
3. 否则使用默认 cue config。
4. replay 输出目录中写一份 effective `cue_config.json`。
5. replay 绝不修改输入 session。

## 3. Replay cue timing 标记

同意。

replay cue log 和 summary 中增加：

```text
mode = replay
is_live_cue_timing = false
```

live 中则为：

```text
mode = live
is_live_cue_timing = true
```

后续 RT analyzer 默认只接受 `is_live_cue_timing=true` 作为真实受试者反应时分析输入。replay cue timing 只能用于调试 cue 触发逻辑和日志结构，不能解释为真实 RT。

## 4. `success` 与实际显示状态

同意你的建议。

固定语义：

```text
success=true
```

表示 sink 成功接受或处理 command，不代表受试者看见，也不代表任务成功。

新增或使用：

```text
display_status=displayed
display_status=not_displayed
display_status=not_applicable
display_status=not_displayed_lower_priority
```

具体规则：

* logging sink: `success=true`, `display_status=not_applicable`, `displayed_monotonic_ms=null`
* console sink: 实际打印后 `display_status=displayed`
* gui_text sink: 实际 render 后 `display_status=displayed`
* GUI 接受但从未 render: `success=true`, `display_status=not_displayed`
* 低优先级 cue 被高优先级 cue 挤掉: `display_status=not_displayed_lower_priority`

README 中要明确：`success` 不是“受试者看到提示”。

## 5. 同一帧多个 cue 的低优先级 cue

同意。

同一帧多个不同 cue 可以都写入 `cue_log.csv`。

GUI overlay 只显示最高优先级 cue。未显示的低优先级 cue 记录为：

```text
displayed_monotonic_ms = null
display_status = not_displayed_lower_priority
```

这些未显示 cue 不能作为视觉 cue RT 起点。

## 6. 低优先级 cue 是否在高优先级 cue 解除后重新出现

同意你的建议：不重新显示旧 cue。

只有新的 cue edge 才能再次显示。不要因为高优先级 cue 解除，就把之前被压制的旧 cue 重新显示出来。否则会产生没有真实新 cue command 的视觉 RT 起点。

## 7. `console_message_language`

建议改名为：

```text
message_language
```

它同时控制 console 和 GUI cue message。

第一版只支持：

```text
message_language = en
```

如果为了兼容已有配置，也可以暂时接受 `console_message_language` 作为 alias，但 effective config 中建议统一写 `message_language`。

## 8. `repeat_policy`

同意写入 effective cue config。

第一版固定：

```text
repeat_policy = edge_only
```

只接受 `edge_only`。其他值清晰报错，为未来扩展保留接口。

## 9. Validator 检查 cue config 一致性

同意。

规则：

* 新 cue-aware live session 缺少 `cue_config.json`：ERROR。
* `cue_config.json` 与 summary / trial summary 中的 effective cue config 不一致：ERROR。
* 旧 session 没有 cue 字段：不强制，避免误伤历史数据。
* replay session 只有在 replay out-dir 中写了 cue outputs 时才检查对应 replay cue config。

## 10. Visual profile 写入 session 和 summary

同意。

保存：

```text
visual_profile
status_panel
effective_status_panel_visible
show_axes / effective_axes_visible，如果实现
show_grid / effective_grid_visible，如果实现
```

用于复现实验显示条件。

## 11. `--cue-sink gui_text` 与 replay `--headless`

同意作为配置错误。

如果使用：

```text
--cue-sink gui_text
```

但 replay 是：

```text
--headless
```

则清晰失败，不静默降级。

同理，live 中 `--cue-sink gui_text` 但没有 `--gui`，也应清晰失败。

## 12. 坐标轴 / 网格规则

调试时必须保留坐标轴，不管是否显示物块或轨道。

建议：

```text
debug_all:
  显示坐标轴和网格

experiment_visibility_feedback:
  默认隐藏坐标轴、网格、轴标签和刻度
  但后续是否在实验中显示坐标轴可再讨论

experiment_blank:
  默认隐藏坐标轴、网格、轴标签和刻度
```

如果实现成本低，可以提供：

```text
--show-axes auto|show|hide
--show-grid auto|show|hide
```

其中 `auto` 默认：

```text
debug_all -> show
experiment profiles -> hide
```

但本阶段必须保证 `debug_all` 保留坐标轴。

## 13. 视图范围

同意。

experiment profile 仍然使用隐藏的 scene / map bounds 计算固定视图范围，但不绘制这些 geometry。

不要只按当前可见 marker 自动缩放，避免画面随手移动而不断缩放，给受试者额外视觉线索。

## 14. pinch / hand marker

第一版只显示 pinch marker，不新增 tracker marker、wrist marker 或 hand skeleton marker。

当前 “hand marker” 在 README 中应解释为 pinch center marker。不要为了 hand marker 修改 parser / adapter / DeviceFrame。真实手部 / tracker marker 后续再加。




确认以下实现决策。

## 1. `block_visible=true` 时显示形式

同意采用：

```text
block_visible=false:
  不显示任何 task marker
  不显示完整 block
  不显示 block center marker
  不显示 pinch marker
  不显示 hand marker
  不显示 track / target / block-pinch line

block_visible=true:
  显示完整 block geometry
  显示 pinch marker
  不显示 track
  不显示 target
  不显示 block-pinch line
```

这用于 `experiment_visibility_feedback`。语义是：

* contact enter / grabbed / blocked / pinch insufficient 等 hidden 状态：画面中物块和 pinch 都消失。
* contact exit / detach / release / visible-free 状态：物块重新出现，并显示 pinch marker。

对应测试需要更新：不要再测试 visible/free 时不显示 block；应测试 visible/free 时显示完整 block geometry + pinch marker，hidden/contact/grabbed/blocked 时不显示 task marker。

## 2. visual profile alias 落盘方式

同意你的建议。

如果用户传入：

```text
experiment_markers_when_hidden
```

则：

* CLI 继续接受该 alias。
* 内部 effective `visual_profile` 统一规范化为 `experiment_visibility_feedback`。
* 额外记录 `requested_visual_profile=experiment_markers_when_hidden`。
* summary / session meta / trial summary 中写：

  * `requested_visual_profile`
  * `visual_profile`
  * `effective_visual_profile`
* README 标记旧名称 deprecated，并说明其映射到 `experiment_visibility_feedback`。

## 3. `cue_config.json` 是否包含 `cue_sink`

同意你的建议。

`cue_config.json` 只保存 cue 生成行为配置，例如：

* enable flags
* min cue interval
* repeat policy
* message language
* rate limit settings

`cue_sink` 是运行时显示/输出方式，不写入 `cue_config.json`。

`cue_sink` 单独保存到：

* summary
* session meta
* trial summary / trial config，如果当前结构适合

Replay 加载原 session cue config 时，不应被迫复用原来的 `gui_text` 或 `console`。Replay 的 sink 由 replay CLI `--cue-sink` 决定。

## 4. 状态签名变化生成 cue

同意你的建议。

不要伪造 `blocked_force_start` 或 `slip_start` 事件。

新增字段：

```text
trigger_reason = edge_start | state_signature_changed
```

规则：

* 状态刚进入时：`trigger_reason=edge_start`
* 持续状态中 blocked direction / blocked surface / slip reason / source signature 发生变化时：`trigger_reason=state_signature_changed`
* `source_event_type` 可以为空或 null
* 原始状态写入：

  * `source_state`
  * `slip_reason`
  * `track_state`
  * `primary_blocked_surface`
  * `direction`
  * `details_json`

这样不会伪造不存在的 Trial event，也能保留 cue 触发原因。

## 5. Console cue 的持续显示语义

同意你的建议。

`ConsoleCueSink` 每个 edge 只异步打印一次。Console 无法像 GUI overlay 一样持续显示/清空，所以 README 中应明确：

* console cue 是一次性文本提示。
* GUI text overlay 可以持续显示，并在状态解除或新 cue 替换时清空。
* console cue 的 displayed time 是实际打印时刻。

## 6. GUI 接受但尚未 render，随后 GUI 关闭

同意你的建议。

不要把这条旧 cue fallback 到 console。

处理方式：

* 这条旧 cue 记录为 `display_status=not_displayed`
* `displayed_monotonic_ms=null`
* 记录 warning/fallback detail，例如 `not_displayed_reason=gui_closed_before_render`
* GUI 关闭后新产生的 cue 才 fallback 到 console

这样避免延迟显示已经过期的提示，避免产生错误 RT 起点。

## 7. Console worker 积压时是否允许显示过期 cue

同意你的建议。

`ConsoleCueSink` 使用 bounded / latest-only 显示队列，避免终端卡顿后输出已经失效的提示。

要求：

* 所有通过配置和限流的 command 仍写入 cue log。
* 如果某条 cue 因为被更新的 cue 替代而没有实际打印：

  * `displayed_monotonic_ms=null`
  * `display_status=not_displayed`
  * 可记录 `not_displayed_reason=console_queue_replaced`
* 不要让 console 输出阻塞 trial loop。

## 8. `cue_enabled` 定义

同意固定为：

```text
cue_enabled = cue_sink != none
```

规则：

* `cue_sink=none`：不生成 cue log，`cue_count=0`。
* `cue_sink!=none` 但所有 enable flag 都关闭：仍写空 `cue_log.csv`，`cue_count=0`。
* `cue_count` 表示实际进入 sink 的 cue 行数。
* `suppressed_cue_count` 表示被限流或配置抑制的候选 cue 数。

## 9. Contact cue 是否受同类型 rate limit

同意。

contact cue 不应被 slip / blocked / other cue 抑制，但可以被自身同类型限流。

也就是：

* `contact_enter` 只按自身 `(cue_type, source_state)` 限流。
* `contact_exit` 只按自身 `(cue_type, source_state)` 限流。
* slip/blocked cue 不会压制 contact cue。
* contact cue 也不会压制 slip/blocked cue。

## 10. Replay cue output 的 validator 范围

同意你的建议。

本阶段 `validate_session_outputs.py` 仍面向标准 live integrated session 目录。

规则：

* live integrated session 中，如果 `cue_enabled=true` 或 `cue_sink!=none`，强制检查 cue artifacts。
* replay `--out-dir` 不是标准 session，不要用标准 session validator 强制检查。
* replay cue outputs 通过 replay tests 检查。
* 如果以后需要验证 replay out-dir，再新增独立 replay-output validation 模式。

## 补充：README / TODO

请在 README / known limitations 中写清楚：

* 当前是单窗口 GUI，不是正式双窗口实验界面。
* `experiment_visibility_feedback` 是一次实验尝试 profile。
* 旧 `experiment_markers_when_hidden` 名称保留为 alias，但 deprecated。
* console cue 是一次性提示，GUI cue 才能持续 overlay。
* replay cue timing 不是 live RT。
* 真实 haptic hardware sink 后续单独实现。



确认这 4 个实现决策。

## 1. Console 同一帧多个 cue 的显示规则

同意。

ConsoleCueSink 和 GUI 使用同一套 cue priority。

如果同一帧有多个 cue：

* cue log 仍记录所有通过配置和限流的 cue。
* Console 实际只输出最高优先级 cue。
* GUI overlay 实际只显示最高优先级 cue。
* 低优先级 cue 记录：

```text
displayed_monotonic_ms = null
display_status = not_displayed_lower_priority
```

这样视觉/文本 cue 的 RT 起点更明确，避免同一帧多个提示都被误当成受试者实际看到的 cue。

优先级继续使用：

```text
blocked_directional
slip_track_blocked / slip_pinch_insufficient
contact_exit
contact_enter
```

## 2. Contact cue overlay 的清空规则

同意。

规则：

* `contact_enter` 在 `INSIDE_BLOCK` 状态持续显示。
* `contact_exit` 在 `OUTSIDE_BLOCK` 状态持续显示。
* 如果 contact cue 被 slip / blocked 等更高优先级 cue 替换，旧 contact cue 不再在高优先级 cue 解除后重新出现。
* 只有下一次新的 contact edge 才创建新的 contact cue。
* 不为 cue 解除生成新的 cue command。
* RT analyzer 后续根据 event log / per-frame state 判断 resolved time，而不是根据 overlay 清空动作。

## 3. `cue_sink=none` 时是否仍写并验证 `cue_config.json`

同意。

新 live integrated session 始终写 effective：

```text
session/cue_config.json
```

即使：

```text
cue_sink = none
```

也要写入 effective cue config，便于复现实验配置。

Validator 规则：

* 新 live integrated session 始终检查 `cue_config.json` 是否存在，以及它与 summary / trial_summary 中的 effective cue config 是否一致。
* 只有 `cue_sink != none` 或 `cue_enabled=true` 时，才强制要求 `cue_log.csv`。
* `cue_sink=none` 时，不要求 cue log，`cue_count=0`。
* 如果 `cue_sink!=none` 但所有 enable flag 都关闭，仍写空 `cue_log.csv`，`cue_count=0`。

## 4. Replay 默认 `--cue-sink`

同意。

Replay 默认也使用：

```text
--cue-sink logging
```

与 live 保持一致。

规则：

* replay 有 `--out-dir` 时，写 `out_dir/cue_log.csv` 和 effective `cue_config.json`。
* replay 没有 `--out-dir` 时，可以在内存中生成 cue 和 summary，但绝不修改输入 session。
* replay cue log / summary 必须标记：

```text
mode = replay
is_live_cue_timing = false
```

* 可显式传：

```text
--cue-sink none
```

关闭 cue 生成。


确认这 3 个实现决策。

## 1. `summary.mode` 与 cue mode

同意。

不要覆盖现有 summary 的 `mode` 字段。

必须保留：

```text
live summary: mode = live_integrated_session
replay summary: mode = replay_debug_gui
```

cue 相关模式单独记录：

```text
summary.cue_mode = live | replay
cue_log.csv: mode = live | replay
cue_log.csv: is_live_cue_timing = true | false
```

这样不会破坏现有 validator、summary 兼容性和 replay 逻辑。

## 2. GUI / Console 英文提示文本

同意第一版使用稳定、状态导向、坐标方向明确的英文文本：

```text
contact_enter -> CONTACT
contact_exit -> CONTACT EXIT
slip_pinch_insufficient -> PINCH INSUFFICIENT
slip_track_blocked -> TRACK BLOCKED
blocked_directional -> MOVE X_NEG / MOVE X_POS / MOVE Y_NEG / MOVE Y_POS / MOVE Z_NEG / MOVE Z_POS
```

不要在本阶段翻译成 left/right/up/down，因为这依赖显示朝向、手握持方向和后续 haptic 映射标定。

第一版 `message_language=en` 即可。

## 3. tracking invalid / recovery 时的旧 GUI cue

同意。

当 tracking invalid 或输入无效导致当前 frame 不可信时：

* 不生成新的 cue。
* 立即清空旧 GUI overlay。
* 不写 `cue_clear` 日志。
* 不把 tracking invalid 作为 cue RT 起点。
* 从下一张正常有效帧恢复 edge 判断。

这样可以避免跟踪失效期间继续显示过期提示，也避免产生虚假的 RT 起点。

## 4. cue priority 生命周期

同意将 cue priority 用于整个 overlay 生命周期，而不只同一帧。

规则：

* 高优先级 cue 活跃显示时，后来的低优先级 cue 仍可写入 `cue_log.csv`。
* 但低优先级 cue 不显示，记录：

```text
display_status = not_displayed_lower_priority
displayed_monotonic_ms = null
```

* 高优先级 cue 解除后，不补显示之前被压制的低优先级 cue。
* 只有新的 cue edge / state_signature_changed 才能生成新的显示候选。
