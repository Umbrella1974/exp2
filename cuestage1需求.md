你现在要在 `exp2` 仓库的 `GUI-stage` 分支上实现下一阶段：**Cue/Haptic Abstraction + Non-hardware Cue Smoke + Visual Profile Control**。

本阶段不要接真实 haptic hardware。目标是先把“触觉/提示语义”抽象出来，用 logging / console / GUI text 方式模拟 cue，并记录 cue log，为后续 behavioral RT 分析和真实 haptic sink 做准备。

请先阅读代码，不要依赖 README。重点阅读：

* `haptic_feedback.py`
* `data_models.py`
* `block_controller.py`
* `live_trial_runner.py`
* `run_live_integrated_session.py`
* `debug_gui.py`
* `debug_view_model.py`
* `dashboard_snapshot.py`
* `timing_diagnostics.py`
* recorder / summary / session 输出相关代码
* 当前已有 GUI / timing / validation tests

## 一、核心目标

实现统一的 cue / haptic sink 抽象，但第一版只做非硬件 sink：

```text
none
logging
console
gui_text
```

不要实现真实 ESP32 / DRV2605 / serial / UDP / TCP 硬件通信。真实硬件 sink 后续单独阶段实现。

本阶段要保证：

1. cue 不阻塞 trial loop。
2. cue 不修改 TrialController / BlockController 逻辑。
3. cue 不由 GUI 决定 trial 状态。
4. cue log 可用于后续 RT 分析。
5. CLI/GUI 语言提示和未来硬件 haptic 使用同一套 command/log schema。
6. 后续换成真实 haptic sink 时，RT analyzer 不需要大改。

## 二、命名要求

当前项目中已有：

* `HapticEventType.CONTACT_ENTER`
* `HapticEventType.CONTACT_EXIT`
* `HapticFeedbackState.slip_active`
* `HapticFeedbackState.blocked_force_active`
* `force_vector_task`
* `primary_blocked_surface`
* `StopReason.TRACK_BLOCKED`
* `TrackState.BLOCKED_X_POS / X_NEG / Y_POS / Y_NEG / Z_POS / Z_NEG`

请优先复用这些现有命名。

不要发明项目里没有的状态名。
如果需要对外显示文案，可以写成人能看懂的 cue message，但内部 event/source state 必须尽量贴合已有字段。

## 三、CueCommand / CueSink 设计

新增轻量数据结构，例如：

```text
CueCommand
CueCommandResult
CueSink
CueSinkConfig
```

建议字段：

```text
cue_id
cue_type
cue_modality
source_event_type
source_state
source_frame_index
created_monotonic_ms
issued_monotonic_ms
displayed_monotonic_ms
ack_monotonic_ms
pattern
direction
intensity
duration_ms
message
success
error
is_hardware_haptic
```

第一版 `cue_modality` 可取：

```text
none
logging
console_text
gui_text
```

未来硬件可扩展：

```text
hardware_haptic
```

第一版 `is_hardware_haptic=false`。

## 四、Cue 类型

第一版至少支持以下 cue 类型：

```text
contact_enter
contact_exit
slip_pinch_insufficient
slip_track_blocked
blocked_directional
```

其中：

* contact cue 来自 `CONTACT_ENTER`
* detach / release cue 来自 `CONTACT_EXIT`，并保留 detach_state
* slip cue 来自 `HapticFeedbackState.slip_active`
* force distribution / blocked directional cue 来自 `blocked_force_active`、`primary_blocked_surface`、`force_vector_task` 或现有 blocked_info

注意：用户提到“力分布”是物块被轨迹边缘阻拦、pinch center 有离开趋势时给上下左右提示。请先查看现有代码中对应的状态名，不要强行叫新名字。如果现有语义是 `TRACK_BLOCKED` / `blocked_force_active` / `primary_blocked_surface`，就复用这些名字。

## 五、Cue sink

实现：

```text
NullCueSink
LoggingCueSink
ConsoleCueSink
GuiTextCueSink 或 GUI cue store
```

要求：

1. `NullCueSink` 不提示，只 no-op。
2. `LoggingCueSink` 只写 cue log。
3. `ConsoleCueSink` 在 CLI 打印简短提示。
4. `GuiTextCueSink` 不直接控制 GUI 线程；可以通过线程安全 store / latest cue store 给 GUI 读取。
5. 不要在 TrialController / BlockController 里直接 print 或写 GUI。
6. 不要在 core update 里做慢 I/O。
7. cue log 第一版可以用内存 collector，run 结束后写 CSV，或轻量异步写入；不能阻塞 trial loop。

## 六、CLI 参数和 config

在 live integrated runner 中新增参数或 config 支持：

```text
--cue-sink none|logging|console|gui_text
--cue-config PATH
```

默认建议：

```text
--cue-sink logging
```

或者如果担心改变行为，默认 `none`，但仍允许显式开启 logging。

cue config 可以控制：

```text
enable_contact_cue
enable_contact_exit_cue
enable_slip_cue
enable_blocked_directional_cue
min_cue_interval_ms
repeat_policy
console_message_language
```

如果本阶段不想做完整 config，至少提供 CLI 参数和合理默认值。

## 七、cue log

live session 输出：

```text
session/cue_log.csv
```

字段至少包括：

```text
cue_id
cue_type
cue_modality
source_frame_index
source_event_type
source_state
direction
message
created_monotonic_ms
issued_monotonic_ms
displayed_monotonic_ms
ack_monotonic_ms
success
error
is_hardware_haptic
```

summary 中记录：

```text
cue_enabled
cue_sink
cue_log_path
cue_count
cue_type_counts
```

如果 cue sink 是 `none`，可以不写 cue log，或写空 log，但 summary 要清楚。

## 八、RT 相关边界

本阶段不做最终 behavioral RT 分析。

但本阶段要让 cue log 足够支持后续离线 RT：

* haptic/cue 发出时间
* cue 类型
* source frame
* source event/state
* direction
* modality
* whether hardware haptic or simulated cue

后续 RT analyzer 会基于 cue log + per-frame state + event log + timing diagnostics 计算：

```text
slip cue -> correction onset
slip cue -> slip resolved
detach cue -> recontact
blocked directional cue -> direction change
blocked directional cue -> blocked resolved
trial start -> operator e
target entry -> operator e
```

不要在本阶段把这些 RT 写死到 live trial loop 中。

## 九、Visual Profile Control

新增 GUI / display 可视化配置，使调试和实验尝试可以切换显示内容。

新增 CLI 或 config：

```text
--visual-profile debug_all|experiment_markers_when_hidden|experiment_blank
```

也可以支持 config 文件字段：

```json
{
  "visual_profile": "experiment_markers_when_hidden"
}
```

### debug_all

显示：

* track boxes
* target region
* block box
* initial block
* pinch marker
* block-pinch line
* status panel

### experiment_markers_when_hidden

用于实验尝试。

要求：

1. 不显示 track boxes。
2. 不显示 target region。
3. 不显示完整 block geometry。
4. 不显示 block-pinch line。
5. 当 `block_state.visible == false`，或 snapshot 中等价字段显示当前 block 处于 hidden/contact/grabbed 状态时：

   * 显示 block center marker
   * 显示 pinch/hand marker
6. 其他时候：

   * 不显示 block
   * 不显示 track
   * 不显示 target
   * 不显示 pinch/hand marker
7. 状态面板是否显示可以通过参数控制；第一版可以保留实验员侧状态面板，但不要显示给受试者的窗口内容。

### experiment_blank

不显示 task geometry，不显示 hand/block marker，只保留空白或极简状态。

## 十、DashboardSnapshot / ViewModel 字段

如果当前 `DashboardSnapshot` 没有直接暴露：

```text
block_visible
block_motion_state
```

请从现有 `FrameOutput.block_state.visible` / `block_state.motion_state` 读取并加入 snapshot 或 view model。

要求：

1. 只暴露已有状态。
2. 不改 BlockController 逻辑。
3. 不根据 GUI 需要反向改变 block visibility。
4. GUI rendering 根据 visual profile 决定画什么。

## 十一、测试要求

请新增或更新测试：

1. CueCommand 序列化。
2. NullCueSink 不依赖硬件。
3. LoggingCueSink 能生成 cue log。
4. ConsoleCueSink 不影响 trial outcome。
5. cue rate limit / min interval，如果实现了。
6. contact enter 生成 contact cue。
7. contact exit 生成 detach/release cue。
8. slip_active 生成 slip cue。
9. blocked_force_active 生成 directional cue。
10. cue sink 不修改 TrialController / BlockController。
11. visual_profile=debug_all 显示全部 debug geometry。
12. visual_profile=experiment_markers_when_hidden 在 block hidden 时只显示 markers。
13. visual_profile=experiment_markers_when_hidden 在 block visible/free 时不显示 block/track/hand。
14. visual_profile=experiment_blank 不显示 task geometry。
15. GUI close 仍不停止 trial。
16. 现有 timing / validation / live / replay tests 仍通过。

## 十二、README / known risks

更新 README，说明：

1. cue sink 只是模拟提示，不是真实 haptic hardware。
2. console/gui_text cue 可以用于无硬件预实验或调试。
3. cue log 将用于后续 RT 分析。
4. 当前 behavioral RT 不在线计算。
5. visual profile 如何切换。
6. experiment_markers_when_hidden 的显示语义。
7. 真实 haptic hardware sink 后续单独实现。

## 十三、完成后请输出

1. 修改了哪些文件。
2. 新增了哪些 cue/haptic 抽象。
3. 支持哪些 cue sink。
4. cue log 写到哪里。
5. 支持哪些 visual profile。
6. experiment_markers_when_hidden 的具体显示规则。
7. 是否修改了 DashboardSnapshot / view model。
8. 是否改动 TrialController / BlockController 核心逻辑。
9. 新增了哪些测试。
10. 测试结果。


确认以下实现决策。

1. `TRACK_BLOCKED` 同时满足 `slip_reason=TRACK_BLOCKED` 和 `blocked_force_active=true` 时，不要同一帧同时发 `slip_track_blocked` 和 `blocked_directional`。

   决策：

   * 如果有方向信息，优先只发 `blocked_directional`。
   * 如果缺少方向信息，再回退到 `slip_track_blocked`。
   * 这样避免双重提示和 RT 起点歧义。

2. slip / blocked cue 的生成策略：

   决策：

   * cue command / cue log 使用 `edge_only`。
   * 状态刚进入时生成一次 cue。
   * 状态持续不变时不重复生成新的 cue。
   * blocked surface、slip reason、direction/source_state 发生变化时，可以视为新 cue。
   * 但 GUI/CLI 文本提示可以持续显示，直到状态解除或切换到其他状态。
   * 不做周期性 repeat。

3. `blocked_directional.direction` 的语义：

   决策：

   * 同时记录：

     * `primary_blocked_surface`：例如 `X_POS`，表示受阻/撞到的边界或方向。
     * `direction`：例如 `X_NEG`，表示建议修正方向。
   * 内部先使用 task 坐标方向：`X_POS / X_NEG / Y_POS / Y_NEG / Z_POS / Z_NEG`。
   * 不要现在把它直接翻译成“左/右/上/下”，因为真实 haptic 硬件的二维方向映射取决于手握持方向和后续标定。

4. `Z_POS / Z_NEG`：

   决策：

   * 内部 cue log 中仍然生成并记录 `Z_POS / Z_NEG`。
   * 当前 haptic 硬件虽然是二维上下左右，但本阶段不接真实硬件，所以先不做二维映射。
   * 后续 hardware haptic sink 阶段再决定如何映射或忽略 Z 方向。

5. `source_event_type` 命名：

   决策：

   * 使用日志友好的 lowercase 命名，例如：

     * `contact_enter`
     * `contact_exit`
     * `slip_start`
     * `blocked_force_start`
   * 同时在 `details_json` 或显式字段里保存原始信息，例如：

     * `detach_state`
     * `slip_reason`
     * `primary_blocked_surface`
     * 原始 enum 名称
   * 不丢失原始语义，但日志字段保持可读。

6. cue log：

   决策：

   * 所有非 `none` sink 都必须写 `cue_log.csv`。
   * `logging` 只记录。
   * `console` / `gui_text` 在记录之外再显示。
   * 保持现有 `haptic.csv` 的逐帧逻辑反馈语义不变。
   * 新的 cue command 单独写入 `cue_log.csv`，不要和未来真实硬件命令混在一起。

7. `gui_text` 与 `--gui`：

   决策：

   * 如果启动时使用 `--cue-sink gui_text` 但没有 `--gui`，直接报配置错误，不静默降级。
   * 如果 GUI 已启动但中途关闭，trial 继续；后续 cue fallback 到 console text，并继续写 cue log。
   * GUI cue 文本显示直到对应状态解除或被新的 cue/state 替换。
   * `displayed_monotonic_ms` 表示 GUI 或 fallback console 实际显示/输出的时间。
   * 如果 cue 未实际显示，则保持 `displayed_monotonic_ms=null`，并在 cue log 中记录 display status / warning。

8. 单窗口限制：

   决策：

   * 本阶段接受当前 GUI 只有一个窗口。
   * 不实现双窗口。
   * `debug_all` 默认显示状态面板。
   * `experiment_markers_when_hidden` 和 `experiment_blank` 默认隐藏状态面板。
   * 双窗口需求写进 TODO：未来需要实验员窗口和受试者窗口分离。

9. `gui_text` 显示位置：

   决策：

   * 使用独立居中的 cue text overlay。
   * 不依赖状态面板。
   * 这样在 `experiment_blank` 或隐藏状态面板时，仍然可以向受试者显示 cue 文本。

10. visual profile：

决策：

* live GUI 和 replay GUI 都支持 visual profile。
* 默认 `debug_all`。
* 这样可以先用 replay 检查实验显示效果。

11. replay cue log：

决策：

* replay 支持生成 cue log。
* 只有传入 replay `--out-dir` 时才写 `out_dir/cue_log.csv`。
* 绝不修改输入 session。
* README 中写清楚这一点。

12. 默认 cue sink：

决策：

* 默认 `--cue-sink logging`。
* 它不向受试者显示任何提示，也不改变行为，但能为后续 RT 分析留下 cue 数据。

13. cue config：

决策：

* `--cue-config PATH` 支持 JSON/YAML。
* YAML 使用 lazy import PyYAML。
* 未知字段报错，避免拼写错误被静默忽略。
* 行为尽量与 termination config 一致。

14. `min_cue_interval_ms`：

决策：

* 按 `(cue_type, direction/source_state)` 限流。
* 不使用全局限流。
* contact enter / contact exit 等离散边缘事件不应被其他 cue 抑制。
* 目的只是防止同一种 cue 因状态抖动在短时间内反复刷屏。

15. cue log 额外字段：

决策：

* 同意加入：

  * `trial_id`
  * `source_sample_time`
  * `source_trial_time`
* 这些字段对未来多 trial 和 RT join 很有价值。

16. validator：

决策：

* 如果 `cue_enabled=true` 或 `cue_sink != none`，validator 应检查 `cue_log.csv`。
* 如果 cue log 缺失，记为 ERROR。
* 如果 `cue_count > 0` 但 cue log 为空，记为 ERROR。
* 如果 cue log 存在但没有 cue 行，且 summary 中 cue_count=0，可以接受或 WARNING。
* 如果 `cue_sink=none`，不要求 cue log。


确认以下实现决策。

## 1. ConsoleCueSink 非阻塞

同意你的建议。

所有 cue 都先进入线程安全内存 collector / queue。`ConsoleCueSink` 不应在 trial loop 中直接 `print()`。

要求：

* trial loop 只提交 `CueCommand`。
* console 输出由独立 worker 或等价非阻塞机制处理。
* 如果 console worker 出错，不应导致 trial loop 崩溃。
* console 输出时间写入 `displayed_monotonic_ms`。
* 不要在 `TrialController` / `BlockController` 中直接 print。

## 2. `cue_count` 和 suppressed cue

同意你的建议。

定义：

* `cue_count` = 实际通过配置和限流、进入 sink 的 cue 行数。
* 被 `min_cue_interval_ms` 抑制的候选 cue 不计入 `cue_count`。
* 被抑制的候选 cue 不作为 RT 起点。
* summary 中可以增加：

  * `suppressed_cue_count`
  * `suppressed_cue_type_counts`

第一版不需要把每个 suppressed candidate 都写入 `cue_log.csv`。如果后续需要 debug，可以再加可选 debug log。

## 3. 时间字段定义

同意固定为：

* `created_monotonic_ms`：从 frame result / haptic feedback state 生成 `CueCommand` 的时刻。
* `issued_monotonic_ms`：sink 接受 command 的时刻。
* `displayed_monotonic_ms`：console 实际打印或 GUI 实际 render 的时刻。
* `ack_monotonic_ms`：第一版全部为 null，因为没有硬件或外部设备 acknowledgement。
* `success=true`：sink 成功接受/处理 command，不代表受试者看见，也不代表任务成功。

请在 README / docs 中明确 `success` 的语义。

## 4. GUI 关闭后的 modality

同意你的建议，但请同时保留 requested sink。

如果启动时是：

```text
--cue-sink gui_text
```

但 GUI 中途关闭，后续 cue fallback 到 console：

* `requested_cue_sink = gui_text`
* `cue_modality = console_text`
* `fallback_reason = gui_closed`
* `displayed_monotonic_ms` = console 实际输出时间
* `success=true` 表示 fallback console 成功输出

如果 cue 没有实际显示/输出：

* `displayed_monotonic_ms = null`
* `success=false` 或 `display_status=not_displayed`
* 记录 warning / error 字段

## 5. GUI cue 的解除与清空

同意你的建议。

edge-only command 只记录 cue 开始，不为状态解除生成新的 cue command。

要求：

* 不生成 `cue_clear` / `cue_resolved` 之类新的 cue command。
* 不把解除动作写入 `cue_log.csv`。
* GUI cue store 根据后续 frame state 清空或替换显示。
* RT analyzer 以后从 event log / per-frame state 判断 resolved time。

例如：

* slip 开始：写一条 slip cue。
* slip 持续：不重复写 cue，但 GUI/CLI 可以持续显示。
* slip 解除：GUI 清空 slip 文本；RT analyzer 后处理判断 slip resolved time。

## 6. 同一帧多个 cue 的 GUI 显示优先级

同意你的建议。

cue log 可以记录多个 cue，但 GUI overlay 同时主要显示一个。显示优先级：

```text
blocked_directional
slip_track_blocked / slip_pinch_insufficient
contact_exit
contact_enter
```

补充：

* 即使 GUI overlay 只显示最高优先级 cue，`cue_log.csv` 仍可记录同一帧的多个 cue。
* 但如果 `TRACK_BLOCKED` 同时能生成 `slip_track_blocked` 和 `blocked_directional`，仍按之前决策：有方向信息时只发 `blocked_directional`，避免双重 RT 起点。

## 7. `source_state` 的具体值

同意你的建议。

使用：

* `contact_enter`: `INSIDE_BLOCK`
* `contact_exit`: `OUTSIDE_BLOCK`
* `slip_pinch_insufficient`: `PINCH_INSUFFICIENT`
* `slip_track_blocked`: `TRACK_BLOCKED`
* `blocked_directional`: `TRACK_BLOCKED`

同时用独立字段保存：

* `detach_state`
* `slip_reason`
* `track_state`
* `primary_blocked_surface`
* `direction`
* `details_json`

不要把所有细节都塞进 `source_state`。

## 8. Cue config 默认值

同意你的建议。

默认：

```text
enable_contact_cue = true
enable_contact_exit_cue = true
enable_slip_cue = true
enable_blocked_directional_cue = true
min_cue_interval_ms = 0
console_message_language = en
```

说明：

* 第一版所有 cue 默认启用。
* `min_cue_interval_ms=0` 保留真实边缘事件，不默认限流。
* 第一版只支持 `en`。
* 中文或其他语言以后再加，不要本阶段扩展。

## 9. 保存 effective cue config

同意。

live session 写：

```text
session/cue_config.json
```

并将有效配置写入：

* `session_meta.json`
* `trial_config.json` 或等价 trial config 输出，如果当前结构适合
* `summary.json`
* `trial_summary.json`

要求：

* 写的是 effective config，而不是原始输入 config。
* 如果未传 `--cue-config`，也写默认生效配置。
* validator 后续可以检查 summary 和 effective cue config 是否一致。

## 10. visual profile 的坐标轴和网格

决策稍微调整：

* `debug_all`：必须显示坐标轴和网格。调试时不管显示不显示物块/轨道，都需要保留坐标轴。
* `experiment_markers_when_hidden` 和 `experiment_blank`：默认隐藏坐标轴、网格、轴标签和刻度。
* 是否在实验 profile 中显示坐标轴，后续再讨论。可以先不做额外开关。

如果实现成本很低，可以预留参数：

```text
--show-axes auto|show|hide
```

但本阶段不是必须。若实现，默认：

* `debug_all`: show
* experiment profiles: hide

## 11. experiment visual profile 的视图范围

同意你的建议。

experiment profile 仍使用隐藏的 scene/map bounds 计算固定视图范围，但不绘制这些 geometry。

要求：

* 不要只按可见 marker 自动缩放。
* 画面不应随着手或 marker 移动不断缩放。
* 使用 scene/map bounds 固定 view range，避免给受试者额外视觉线索。

## 12. “pinch/hand marker” 的具体含义

第一版接受只显示 pinch marker。

原因：

* 当前 `DashboardSnapshot` 已有 pinch center。
* 不要求新增 tracker marker、wrist marker 或 hand skeleton marker。
* 不为了显示 hand marker 修改 parser / adapter / DeviceFrame。
* README 中说明：本阶段 “hand marker” 实际指 pinch center marker；真实 hand/tracker marker 后续再加。

## 13. 状态面板控制参数

同意新增：

```text
--status-panel auto|show|hide
```

默认：

* `auto`

`auto` 语义：

* `debug_all`：显示状态面板。
* `experiment_markers_when_hidden`：隐藏状态面板。
* `experiment_blank`：隐藏状态面板。

`show/hide` 可以强制覆盖，用于调试。

## 14. CueCommand 的 pattern / intensity / duration

同意你的建议。

第一版没有真实硬件，因此：

* `pattern = cue_type`
* `intensity = null`
* `duration_ms = null`
* `ack_monotonic_ms = null`
* `is_hardware_haptic = false`

GUI 文本持续时间由状态解除或新 cue 替换控制，不伪造固定硬件时长。

## 15. 物块脱离手后的可见状态

这一点本阶段可以实现，但必须只在 GUI rendering / visual profile 层实现，不要修改 `BlockController` 核心逻辑。

当前已有 `BlockState.visible`，并且根据现有逻辑：

* contact / grabbed / blocked / pinch insufficient 等状态下，block 可以是 hidden。
* contact exit / detach / release 后，block 可以回到 visible/free 状态。

因此在 visual profile 中增加明确规则：

### debug_all

显示全部 debug geometry，包括：

* track
* target
* block geometry
* pinch marker
* status panel
* axes/grid

### experiment_markers_when_hidden

建议用于实验尝试。

显示规则：

* 不显示 track。
* 不显示 target。
* 默认不显示完整 block geometry。
* 不显示 block-pinch line。
* 当 `block_visible == false`：

  * 显示 block center marker。
  * 显示 pinch marker。
* 当 `block_visible == true`，即 block 处于 visible/free/detached 状态：

  * 可以显示 block 的可见状态，用于表示 contact exit / detach / release。
  * 此时不显示 track / target。
  * 是否显示 pinch marker：第一版建议不显示，避免额外手部视觉线索。
* contact enter 可以通过 block 从 visible/free 状态变为 hidden/marker 状态来表达。
* contact exit 可以通过 block 重新出现来表达。

如果你认为这会和原先 `experiment_markers_when_hidden` 名称不完全一致，可以新增更清楚的 profile 名称，例如：

```text
experiment_block_visibility
```

但不要删除原 profile。也可以先让 `experiment_markers_when_hidden` 按上述规则实现，并在 README 解释清楚。

### experiment_blank

不显示 block、track、target、pinch marker，只显示 cue overlay 或空白。

## 16. 单窗口限制 / TODO

本阶段接受当前 GUI 只有一个窗口，不实现双窗口。

但请写入 TODO / README known limitations：

* 当前实验员和受试者共用一个 GUI 窗口。
* experiment profiles 会隐藏状态面板和调试 geometry，但不是真正的双屏/双窗口实验界面。
* 后续正式实验 GUI 需要区分 experimenter view 和 participant view。

## 17. validator

同意：

* 如果 `cue_enabled=true` 或 `cue_sink != none`，validator 应检查 `cue_log.csv`。
* 如果 cue log 缺失，记为 ERROR。
* 如果 `cue_count > 0` 但 cue log 为空，记为 ERROR。
* 如果 cue log 存在但没有 cue 行，且 summary 中 `cue_count=0`，可以接受或 WARNING。
* 如果 `cue_sink=none`，不要求 cue log。
