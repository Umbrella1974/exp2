你现在要修改 exp2 的 stage-5 分支。

目标：
新增一个“明天可用的最小实时视觉反馈实验入口”，用于快速采几组真实 MANUS/Vive 数据，保存 session，并在实验后用 analyze_session.py 生成地图、路线、状态和 logical haptic 图。

本阶段不是完整正式实验系统。
本阶段不接真实 haptic hardware。
本阶段不做复杂 GUI。
本阶段只做一个最小 live visual preview，让受试者/实验员能看到：
- pinch cursor
- block position / footprint
- track boxes / target region
- pinch valid / insufficient
- contact / moving / slip / blocked
- logical haptic feedback state

当前已有：
- live_raw_stream.py：newline JSON TCP source
- run_live_raw_preview.py：raw stream health check
- calibration_io.py / formal_table_lines calibration
- map_config.py
- TrialController / BlockController
- SessionRecorder
- analyze_session.py
- map preview / session analyzer

不要修改：
- BlockController 核心逻辑
- TrialController 核心逻辑
- TaskCoordinateSystem 核心逻辑
- raw parser / adapter 语义
- haptic feedback 语义
- manus_vive_com 仓库代码

不要：
- 不要接真实 haptic hardware
- 不要写复杂正式 GUI
- 不要做完整 trial sequence 管理
- 不要做自动重连
- 不要破坏已有 offline replay / analyze_session / calibration 工具

============================================================
一、新增 run_live_trial_visual_preview.py
============================================================

新增脚本：
run_live_trial_visual_preview.py

目标：
从 manus_vive_com 的 newline JSON TCP stream 实时接收 raw frame，使用 calibration.json + map_config.json 创建 task space 和 track scene，实时运行 TrialController，并显示一个最小视觉反馈窗口或文本状态面板，同时保存 session。

输入参数：
- --calibration-json path，必填
- --map-config path，必填
- --host 默认 127.0.0.1
- --port 默认 8888
- --out-dir 默认 data/live_trial_preview
- --session-dir 可选
- --write-session 默认 true
- --max-frames 可选
- --duration-seconds 可选
- --print-every 默认 30
- --show-visual / --no-show-visual，默认 show
- --visual-mode，可选 matplotlib / text，默认 matplotlib
- --visual-history 默认 300，显示最近多少帧 path
- --thumb-node 默认 4
- --index-node 默认 9
- --tracker-index 默认 0
- --skeleton-index 默认 0
- --timestamp-scale 默认 0.001
- --trial-id 默认 "live_visual_preview"
- --subject-id 可选
- --notes 可选

行为：
1. 读取 calibration.json。
2. validate calibration。
3. build_task_coordinate_system_from_calibration。
4. 读取 map_config.json。
5. validate map_config。
6. compile_map_to_track_region。
7. 创建 TrialController / BlockController。
8. 启动 LiveRawStreamServer，等待 manus_vive_com 连接。
9. 每帧：
   - 保存 raw frame
   - parse_raw_manus_vive_frame
   - adapter 得到 ExperimentInputSample
   - world -> task
   - TrialController.update
   - 记录 SessionRecorder
   - 生成 dashboard snapshot
   - 更新视觉反馈或文本状态
10. 结束后写 summary.json / session/trial_summary.json。
11. session 可以直接被 analyze_session.py 读取。

重要：
- 这是真实 live preview trial，但不是正式实验 runner。
- session_meta.json 中写：
  - mode = "live_visual_preview"
  - is_live_trial = true
  - is_formal_experiment = false
  - calibration_type 来自 calibration.json
  - scene_type = "map_config"
  - haptic_hardware_enabled = false

============================================================
二、最小 DashboardSnapshot
============================================================

新增模块：
dashboard_snapshot.py

定义 DashboardSnapshot dataclass，字段至少包括：
- frame_index
- time
- tracker_valid
- hand_valid
- pinch_valid
- pinch_distance
- pinch_center_task
- block_center_task
- block_size
- contact_state
- block_motion_state
- stop_reason
- track_state
- pinch_state
- detach_state
- large_delta
- slip_active
- slip_reason
- blocked_force_active
- logical_haptic_active
- logical_haptic_label
- hardware_haptic_active = false
- map_id
- calibration_id
- processing_latency_ms

要求：
- 可 JSON 序列化
- GUI/text 只消费 snapshot，不重新计算状态机

============================================================
三、最小视觉反馈
============================================================

新增模块：
live_visual_display.py

第一版可以用 matplotlib。
如果 matplotlib 不可用或 --visual-mode text，则 fallback 到文本状态输出，不让主流程失败。

matplotlib visual 要求：
- 2D task x-y 平面
- 画 track boxes
- 画 target region
- 画 current block footprint
- 画 block path 最近 N 帧
- 画 pinch cursor / pinch path 最近 N 帧
- 右侧或标题显示状态文本：
  - tracker_valid / hand_valid
  - pinch_distance
  - pinch_state
  - contact_state
  - block_motion_state
  - stop_reason
  - slip_active / slip_reason
  - blocked_force_active
  - logical_haptic_label

颜色建议：
- normal / free：蓝色或默认色
- grabbed / moving：绿色
- slip：橙色
- blocked：红色
- tracking invalid：灰色/黑色
- pinch insufficient：黄色/橙色

要求：
- GUI 慢不能改变 TrialController 逻辑
- 第一版可以在同线程中 best-effort 更新，但如果更新失败要 fallback 到 text
- 不要让绘图失败导致 session 保存失败

text mode 要求：
每 print_every 帧打印一行紧凑状态，例如：
frame=123 tracker=1 hand=1 pinch=0.021 PINCH_VALID contact=INSIDE_BLOCK motion=GRABBED_MOVING stop=NONE slip=0 blocked=0 feedback=NONE

如果 slip / blocked 出现，应明显打印：
- FEEDBACK=SLIP_PINCH_INSUFFICIENT
- FEEDBACK=SLIP_TRACK_BLOCKED
- FEEDBACK=BLOCKED_FORCE

============================================================
四、logical haptic label
============================================================

不要接真实 haptic hardware。
但要根据现有 FrameOutput / HapticFeedbackState 输出 logical label：

规则：
- if blocked_force_active: logical_haptic_label = "BLOCKED_FORCE"
- elif slip_active and slip_reason == "TRACK_BLOCKED": "SLIP_TRACK_BLOCKED"
- elif slip_active and slip_reason == "PINCH_INSUFFICIENT": "SLIP_PINCH_INSUFFICIENT"
- elif slip_active: "SLIP"
- else: "NONE"

写入：
- dashboard snapshot
- processed_frames.csv / haptic.csv 如果现有 SessionRecorder 支持
- summary 中统计 logical_haptic_label_counts

不要改变 haptic_feedback.py 语义，只做显示和统计。

============================================================
五、live metrics
============================================================

每帧记录 live metrics，至少写到：
out_dir/live_metrics.csv

字段：
- frame_index
- raw_timestamp
- receive_time_monotonic
- process_start_time_monotonic
- process_end_time_monotonic
- processing_latency_ms
- inter_frame_interval_ms
- parse_ok
- adapter_ok
- tracker_valid
- hand_valid
- pinch_valid
- pinch_distance
- sync_delta_ms
- queue_size
- dropped_frame_count
- error_message

summary.json 增加：
- total_received_frames
- total_processed_frames
- parse_error_count
- adapter_error_count
- tracker_invalid_frame_count
- hand_invalid_frame_count
- pinch_valid_frame_count
- large_delta_frame_count
- slip_active_frame_count
- blocked_frame_count
- logical_haptic_label_counts
- dropped_frame_count
- mean_processing_latency_ms
- max_processing_latency_ms
- mean_receive_fps
- stop_reason

============================================================
六、session 保存
============================================================

如果 --write-session：
- 使用 SessionRecorder
- 写 raw_frames.jsonl
- 写 device_frames.jsonl
- 写 processed_frames.csv
- 写 events.csv
- 写 haptic.csv
- 写 trial_summary.json
- 写 calibration.json
- 写 trial_config.json

要求：
- 不要伪造字段
- 能拿到的 FrameOutput / HapticFeedbackState 就记录
- 结束后该 session 能被 analyze_session.py 正常分析

============================================================
七、测试
============================================================

新增 tests/test_dashboard_snapshot.py
- DashboardSnapshot 可序列化
- logical_haptic_label 生成正确

新增 tests/test_live_visual_display.py
- text mode 可运行
- matplotlib 不可用时不失败
- snapshot 更新不改变状态

新增 tests/test_run_live_trial_visual_preview.py
使用 fake raw JSONL 或 fake LiveRawStreamServer：
- 可以跑一个短 preview
- summary.json 生成
- live_metrics.csv 生成
- --write-session 生成 session
- session 可被 analyze_session.py 读取
- logical_haptic_label_counts 存在
- 不依赖真实设备
- 不依赖 manus_vive_com
- 不接 haptic hardware

如果真实 socket 测试复杂，可以把 live source 抽象成 frame_iter，测试用 fake frame_iter。

============================================================
八、README
============================================================

README 增加一节：
“最小 live visual preview / 明天采数用”

说明：
- 这是临时 MVP，用于快速采几组数据和出图
- 它不接真实 haptic，只显示 logical haptic feedback
- 它不是完整 formal experiment runner
- 它会保存 session，实验后用 analyze_session.py 出图

示例命令：

python run_live_trial_visual_preview.py ^
  --calibration-json data\calibration\table_line_calibration.json ^
  --map-config maps\examples\xoy_turn.json ^
  --host 127.0.0.1 ^
  --port 8888 ^
  --out-dir data\live_trial_preview\debug_01 ^
  --write-session ^
  --show-visual

实验后：

python analyze_session.py ^
  --session-dir data\live_trial_preview\debug_01\session ^
  --overwrite

============================================================
九、完成后报告
============================================================

完成后运行 pytest -q，并报告：
1. 新增文件
2. 修改文件
3. 新增测试
4. pytest 是否通过
5. 如何启动最小 live visual preview
6. 如何实验后生成 analyze_session 图

补充修改：实时视觉反馈必须使用“颜色 + 英文状态词”，不能只靠颜色。

目标：
run_live_trial_visual_preview.py / live_visual_display.py 中，所有关键状态都必须同时用：
1. 颜色
2. 大号英文 label
3. 程序内部状态名括号说明

来显示，方便实验员和受试者理解。

不要只用颜色表达状态。

============================================================
一、显式显示 contact / release / detach
============================================================

DashboardSnapshot 中增加或确认以下字段：
- contact_label
- release_label
- interaction_label
- feedback_label
- status_line

这些 label 用于 GUI/text 显示，不改变核心状态机。

建议 label 规则：

1. 如果 contact_state 表示 pinch center 在 block 内：
   显示：
   CONTACT (INSIDE_BLOCK)

2. 如果 contact_state 表示 outside block，或 detach_state 不是 NONE：
   显示：
   CONTACT RELEASE (FREE / OUTSIDE_BLOCK)
   或：
   CONTACT RELEASE (UNEXPECTED_DETACH)

3. 如果 block_motion_state 表示 grabbed/moving：
   显示：
   GRABBED / MOVING (GRABBED_MOVING)

4. 如果 block_motion_state 表示 free visible：
   显示：
   FREE (FREE_VISIBLE)

5. 如果 pinch_state 表示 pinch insufficient：
   显示：
   PINCH INSUFFICIENT (PINCH_INSUFFICIENT)

6. 如果 tracking invalid：
   显示：
   TRACKING INVALID

注意：
如果实验语义和程序状态有重叠，要用括号显示程序内部状态名。
例如：
- CONTACT RELEASE (FREE_VISIBLE)
- CONTACT RELEASE (OUTSIDE_BLOCK)
- SLIP: PINCH INSUFFICIENT (PINCH_INSUFFICIENT)
- BLOCKED: TRACK WALL (TRACK_BLOCKED / BLOCKED_X_POS)

============================================================
二、反馈状态 label
============================================================

不要只显示 haptic_active=true/false。
即使不接真实 haptic hardware，也要显示 logical feedback label。

建议 feedback_label 规则：

优先级：
1. 如果 blocked_force_active=True：
   feedback_label = "FEEDBACK: BLOCKED FORCE (TRACK_BLOCKED)"

2. elif slip_active=True 且 slip_reason == TRACK_BLOCKED：
   feedback_label = "FEEDBACK: SLIP / TRACK BLOCKED (TRACK_BLOCKED)"

3. elif slip_active=True 且 slip_reason == PINCH_INSUFFICIENT：
   feedback_label = "FEEDBACK: SLIP / PINCH INSUFFICIENT (PINCH_INSUFFICIENT)"

4. elif slip_active=True：
   feedback_label = "FEEDBACK: SLIP"

5. else:
   feedback_label = "FEEDBACK: NONE"

summary.json 中继续统计 logical_haptic_label_counts，但 label 要可读。

============================================================
三、GUI 显示要求
============================================================

matplotlib visual 中必须有一个清晰的状态文本区域，至少显示：

- MAIN STATE:
  例如 MOVING / FREE / CONTACT RELEASE / BLOCKED / TRACKING INVALID

- CONTACT:
  例如 CONTACT (INSIDE_BLOCK) 或 CONTACT RELEASE (OUTSIDE_BLOCK)

- PINCH:
  例如 PINCH VALID, distance=0.023 m
  或 PINCH INSUFFICIENT, distance=0.115 m

- MOTION:
  例如 GRABBED / MOVING (GRABBED_MOVING)
  或 FREE (FREE_VISIBLE)

- STOP:
  例如 NONE / TRACK_BLOCKED / PINCH_INSUFFICIENT / LARGE_DELTA

- FEEDBACK:
  例如 FEEDBACK: SLIP / PINCH INSUFFICIENT (PINCH_INSUFFICIENT)
  或 FEEDBACK: BLOCKED FORCE (TRACK_BLOCKED)

这些文字要每帧更新。
颜色只作为辅助，不是唯一信息来源。

建议颜色：
- NORMAL / FREE: blue or gray
- CONTACT / GRABBED / MOVING: green
- CONTACT RELEASE / FREE_VISIBLE: gray
- PINCH INSUFFICIENT: orange
- SLIP: orange
- BLOCKED / TRACK_BLOCKED: red
- TRACKING INVALID: black or dark gray
- LARGE_DELTA: purple

但注意：
颜色之外必须显示英文 label。

============================================================
四、text mode 显示要求
============================================================

text mode 每 print_every 帧打印一行紧凑状态，但要包含可读 label。

示例：

frame=123
MAIN=MOVING
CONTACT=CONTACT(INSIDE_BLOCK)
PINCH=PINCH_VALID dist=0.023m
MOTION=GRABBED_MOVING
STOP=NONE
FEEDBACK=NONE

如果发生 release：

frame=361
MAIN=CONTACT_RELEASE
CONTACT=CONTACT_RELEASE(OUTSIDE_BLOCK)
PINCH=PINCH_VALID dist=0.021m
MOTION=FREE_VISIBLE
STOP=NONE
FEEDBACK=NONE

如果发生 slip：

frame=420
MAIN=SLIP
CONTACT=CONTACT(INSIDE_BLOCK)
PINCH=PINCH_INSUFFICIENT dist=0.115m
MOTION=FREE_VISIBLE
STOP=PINCH_INSUFFICIENT
FEEDBACK=SLIP_PINCH_INSUFFICIENT(PINCH_INSUFFICIENT)

如果发生 blocked：

frame=510
MAIN=BLOCKED
CONTACT=CONTACT(INSIDE_BLOCK)
PINCH=PINCH_VALID dist=0.020m
MOTION=GRABBED_BLOCKED
STOP=TRACK_BLOCKED
FEEDBACK=BLOCKED_FORCE(TRACK_BLOCKED)

============================================================
五、主状态 main_state_label
============================================================

DashboardSnapshot 中新增：
- main_state_label

建议优先级：
1. tracking invalid -> "TRACKING INVALID"
2. large_delta -> "LARGE DELTA"
3. blocked_force_active or stop_reason == TRACK_BLOCKED -> "BLOCKED"
4. slip_active -> "SLIP"
5. contact release / outside block / detach -> "CONTACT RELEASE"
6. grabbed/moving -> "MOVING"
7. pinch insufficient -> "PINCH INSUFFICIENT"
8. otherwise -> "FREE"

这个 label 只用于显示，不改变状态机。

============================================================
六、测试
============================================================

更新 tests/test_dashboard_snapshot.py：

至少测试：
- contact_state=INSIDE_BLOCK 时 contact_label 包含 CONTACT
- contact_state=OUTSIDE_BLOCK 或 detach_state!=NONE 时 contact_label 包含 CONTACT RELEASE
- slip_reason=PINCH_INSUFFICIENT 时 feedback_label 包含 SLIP / PINCH INSUFFICIENT
- stop_reason=TRACK_BLOCKED 或 blocked_force_active=True 时 feedback_label 包含 BLOCKED
- main_state_label 按优先级生成
- snapshot JSON 序列化包含这些 label

更新 tests/test_live_visual_display.py：

至少测试：
- text mode 输出包含英文 label
- matplotlib visual 的状态文本函数能生成包含 CONTACT / PINCH / MOTION / STOP / FEEDBACK 的文本
- 不依赖颜色判断状态


同意，可以按以下补充语义实现：

1. --write-session 默认 true，但同时提供 --no-write-session。
   关闭 session 时仍写 out_dir/summary.json 和 out_dir/live_metrics.csv。

2. DashboardSnapshot.stop_reason 明确表示每帧 controller/frame output 的 stop_reason。
   程序整体退出原因不要叫 stop_reason，summary 中使用 run_stop_reason，例如 keyboard_interrupt / max_frames / duration_reached / client_disconnected。

3. 不改 SessionRecorder.haptic.csv 表头。
   logical_haptic_label 写入 haptic.csv 的 details_json。
   summary.json 中统计 logical_haptic_label_counts。

4. 为现场快速调参，增加最小阈值 CLI：
   - --pinch-grab-threshold
   - --pinch-release-threshold
   - --slip-motion-threshold

   未传则使用 EngineConfig 默认值。
   传入后只覆盖 EngineConfig 对应字段。
   summary.json 和 session/trial_config.json 记录最终使用的阈值。

5. matplotlib visual 必须 best-effort。
   如果 matplotlib 不可用或窗口更新失败，自动 fallback 到 text mode。
   绘图失败不能影响 TrialController update 和 session 保存。

6. 分支名使用 mvp-live-visual-preview。
   本分支目标是明天可用的最小 live visual pilot，不做完整正式实验系统，不接真实 haptic hardware，不做复杂 GUI。

   