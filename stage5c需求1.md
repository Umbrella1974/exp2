你现在要修改 exp2 的 stage-5 分支。

目标：
新增正式 LiveTrialRunner 架构，把 live trial 的实时控制逻辑从 run_live_integrated_session.py 这个脚本中抽离出来，为后续 GUI、正式实验 runner、多 trial 流程和 haptic sink 做准备。

背景：
当前 run_live_integrated_session.py 已经实现：
- live stream
- integrated calibration
- map_config loading
- TrialController running
- session recording
- text display
- safe summary

但它仍然是一个脚本型 integrated runner，后续如果继续在脚本里堆 GUI、trial plan、多 trial、haptic，会变复杂。

本阶段目标：
1. 抽出可复用的 LiveTrialRunner 类。
2. LiveTrialRunner 不负责 calibration 采样，不负责 GUI。
3. LiveTrialRunner 接收：
   - live frame source / LatestFrameBuffer
   - FormalCalibration 或 TaskCoordinateSystem
   - MapConfig / TrackRegion
   - EngineConfig
   - SessionRecorder
4. LiveTrialRunner 固定频率运行 TrialController，并产出 DashboardSnapshot / TrialLiveSnapshot。
5. run_live_integrated_session.py 在 calibration 完成后调用 LiveTrialRunner，而不是自己写 trial loop。
6. 为后续 GUI 订阅 snapshot 做准备。

不要修改：
- BlockController 核心逻辑
- TrialController 核心逻辑
- parser / adapter 语义
- calibration geometry 语义
- haptic feedback 语义

不要：
- 不要实现正式 GUI
- 不要接真实 haptic hardware
- 不要做多 trial plan
- 不要做完整实验 randomization

============================================================
一、新增 live_trial_runner.py
============================================================

新增模块：
live_trial_runner.py

核心类：

1. LiveTrialRunnerConfig
字段：
- trial_id
- control_rate_hz
- duration_seconds
- max_frames
- no_frame_timeout_seconds
- print_every
- timestamp_scale
- thumb_node
- index_node
- tracker_index
- skeleton_index
- engine_config overrides
- haptic_hardware_enabled = false

2. LiveTrialRunnerStats
字段：
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
- no_new_frame_count
- max_no_new_frame_gap_seconds
- processing_latency_ms mean/max
- run_stop_reason

3. LiveTrialRunnerResult
字段：
- stats
- summary
- last_snapshot
- events_count
- session_finalized

4. LiveTrialRunner
初始化参数：
- latest_frame_buffer
- task_coordinate_system
- track_region
- block_initial_center_task
- block_size
- map_config_payload / trial_config
- session_recorder
- config
- optional display callback / snapshot callback

方法：
- start_trial()
- step_once()
- run_until_done()
- request_stop(reason)
- build_summary()

行为：
每个 control tick：
1. 从 LatestFrameBuffer 获取最新未处理 raw frame。
2. parse raw frame。
3. adapter 转 ExperimentInputSample。
4. 使用 task_coordinate_system 转 task。
5. 调 TrialController.update。
6. 记录 SessionRecorder。
7. 构建 TrialLiveSnapshot / DashboardSnapshot。
8. 调用 snapshot_callback(snapshot)。
9. 更新 stats。

LiveTrialRunner 不做：
- calibration
- map validation
- GUI 绘图
- haptic hardware sending

============================================================
二、新增 live_trial_snapshot.py 或复用 dashboard_snapshot.py
============================================================

实现 TrialLiveSnapshot / DashboardSnapshot：

字段至少包括：
- frame_index
- sample_time
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
- logical_haptic_label
- main_state_label
- contact_label
- pinch_label
- motion_label
- feedback_label
- processing_latency_ms

要求：
- 可 JSON 序列化
- label 逻辑与 MVP 版本保持一致：
  CONTACT / CONTACT RELEASE / MOVING / FREE / SLIP / BLOCKED / PINCH INSUFFICIENT / TRACKING INVALID
- 不依赖 GUI

============================================================
三、重构 run_live_integrated_session.py
============================================================

修改 run_live_integrated_session.py：

流程仍然是：
1. wait stream
2. calibration
3. calibration review
4. load map
5. create TrialController scene
6. run trial
7. save session

但 trial running 部分改为调用 LiveTrialRunner。

要求：
- calibration 和 trial 仍然同一个 live session
- trial 仍然使用 in-memory FormalCalibration
- summary/session metadata 不变或向后兼容
- existing tests should still pass
- run_live_integrated_session.py 不再直接实现复杂 trial loop

============================================================
四、为 GUI 预留 snapshot subscription
============================================================

LiveTrialRunner 支持：
- snapshot_callback(snapshot)
- stats_callback(stats) 可选

第一版 run_live_integrated_session.py 只用 text callback。
后续 GUI 可以订阅 snapshot_callback，不需要改 TrialController 或 LiveTrialRunner 核心逻辑。

callback 要求：
- 如果 callback 抛异常，不应让 TrialController 失败。
- 记录 warning 或 callback_error_count。
- GUI/display 慢不能阻塞控制循环太久；第一版可以同步调用，但要记录 callback_latency_ms，后续可异步化。

============================================================
五、测试
============================================================

新增 tests/test_live_trial_runner.py

至少覆盖：
1. LiveTrialRunner 可以用 fake LatestFrameBuffer 跑一个短 trial。
2. step_once 处理一帧并生成 snapshot。
3. parse error / adapter error 不会崩溃，stats 计数增加。
4. no_new_frame_timeout 会退出。
5. request_stop 能安全停止。
6. snapshot_callback 被调用。
7. snapshot_callback 抛异常不会导致 runner 崩溃。
8. stats 中 logical_haptic_label_counts 正确。
9. session_recorder 被调用并能 finalize。

更新 tests/test_live_integrated_session.py：
- integrated runner calibration 后调用 LiveTrialRunner。
- calibration_id / task_coordinate_system 一致性仍然通过。
- Ctrl+C / timeout / map validation error 行为不变。

运行 pytest -q。

============================================================
六、README
============================================================

README 增加 LiveTrialRunner 架构说明：

- run_live_integrated_session.py 负责流程：
  calibration → map → trial
- LiveTrialRunner 负责实时 trial 控制：
  latest frame → parser → adapter → TrialController → session → snapshot
- GUI 后续只订阅 snapshot，不直接跑 TrialController
- haptic hardware 后续通过 sink 接入，不在本阶段实现

完成后报告：
1. 新增文件
2. 修改文件
3. 新增测试
4. pytest 是否通过
5. LiveTrialRunner 如何被 integrated session 调用
6. 后续 GUI 如何订阅 snapshot

这个阶段是结构重构，不是行为重写。
LiveTrialRunner 必须调用现有 TrialController.update，不要重新实现 contact/slip/blocked 规则。
优先保证 run_live_integrated_session.py 的外部行为和 summary/session 输出与重构前一致。
如果为了抽类需要改太多，先只抽 trial loop，不动 calibration 流程。

重构前后 existing integrated session tests 仍通过
新增 LiveTrialRunner 单元测试
不改变 FrameOutput / HapticFeedbackState 语义
summary 字段尽量向后兼容