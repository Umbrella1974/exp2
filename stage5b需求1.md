你现在要修改 exp2 的 stage-5 分支。

目标：
新增 Stage 5C：integrated live session runner，把 live calibration 加入整个实验流程中。

核心问题：
之前流程是：

1. 单独运行 calibration 程序，生成 calibration.json
2. 再运行实验/preview 程序，加载 calibration.json
3. 如果坐标原点或地图位置不对，MVP 里又用 --anchor-current-pinch 临时把 map 平移到当前手部位置

这个流程容易出现：

* calibration 和 trial 不在同一个 live session 中
* calibration 文件保存后，实验程序实际使用的坐标系/地图位置可能和现场不一致
* --anchor-current-pinch 虽然能让物块出现在手附近，但它不是正式 calibration，容易掩盖坐标系错误
* 标定、地图、trial、session 的关系不够清楚

本阶段目标：
实现一个 integrated live session runner，让正式流程变成：

WAITING_FOR_STREAM
→ STREAM_HEALTH_CHECK
→ CALIBRATE_ORIGIN
→ CALIBRATE_LONG_LINE
→ CALIBRATE_WIDTH_LINE
→ CALIBRATE_DIAGONAL_LINE
→ CALIBRATION_REVIEW
→ LOAD_MAP
→ READY_FOR_TRIAL
→ TRIAL_RUNNING
→ TRIAL_ENDED
→ SAVE_SESSION

关键原则：
calibration 和 trial 必须在同一个 live session 内连续完成。
calibration 完成后要保存 calibration.json，但 trial 必须直接使用刚刚生成的 in-memory FormalCalibration 对象，不要求用户重启程序再加载 calibration 文件。
禁止在 trial 开始时重新做 post-hoc auto calibration。
禁止默认使用 first pinch / current pinch 作为 task origin。
如果保留 map anchor，它只能是显式 debug option，不能默认启用，并且必须在 session metadata 中明确标记。

不要修改：

* BlockController 核心逻辑
* TrialController 核心逻辑
* TaskCoordinateSystem 核心逻辑
* raw parser / adapter 语义
* haptic feedback 语义
* manus_vive_com 仓库代码

不要：

* 不要接真实 haptic hardware
* 不要做完整正式 GUI
* 不要做复杂 trial sequence/randomization
* 不要把 --anchor-current-pinch 当成正式 calibration
* 不要破坏现有 offline replay / analyze_session / calibration 工具

============================================================
一、复用现有模块
========

必须复用现有：

* calibration_io.py
* calibration_geometry.py
* calibration_sampling.py
* live_raw_stream.py
* map_config.py
* session_recorder.py
* dashboard_snapshot.py 如果可用
* run_live_trial_visual_preview.py 中可复用的 snapshot/label 逻辑
* TrialController / BlockController

不要复制粘贴大量重复逻辑。
如果需要抽公共 helper，可以新增模块：

* live_session_runner.py
* live_session_state.py
* latest_frame_buffer.py

============================================================
二、新增 LatestFrameBuffer / 接收策略
=============================

新增模块：
latest_frame_buffer.py

目标：
解决 MVP 中 GUI/处理卡顿和队列积压问题。

实现 LatestFrameBuffer：

* 接收线程/LiveRawStreamServer 每收到 raw frame，只保留最新帧
* 控制线程固定频率取最新帧
* 如果同一帧已经处理过，不重复处理
* 统计 overwritten_frame_count / dropped_old_frame_count
* 保留 last_frame_index / last_receive_time

不要追历史帧。
正式 live 交互以当前状态为准。

保留 raw logging：

* raw frame 仍然可以写 raw_frames.jsonl
* 但控制线程不应被写文件阻塞

============================================================
三、新增 integrated live session 状态机
================================

新增模块：
live_session_state.py

定义 LiveSessionPhase：

* WAITING_FOR_STREAM
* WAITING_FOR_VALID_TRACKER
* WAITING_FOR_VALID_PINCH
* READY_FOR_CALIBRATION
* CALIBRATING_ORIGIN
* CALIBRATING_LONG_LINE
* CALIBRATING_WIDTH_LINE
* CALIBRATING_DIAGONAL_LINE
* CALIBRATION_REVIEW
* CALIBRATION_FAILED
* READY_FOR_TRIAL
* TRIAL_RUNNING
* TRIAL_ENDED
* SAVING
* ERROR
* STOPPED

定义 LiveSessionStatus dataclass：

* phase
* message
* frame_index
* tracker_valid
* hand_valid
* pinch_valid
* calibration_id
* map_id
* trial_id
* warnings
* errors

所有 CLI/text/GUI 显示都应该显示当前 phase 和 message。
不要因为一开始没有数据就快速退出。
只有用户 quit、明确 timeout 或致命错误才退出。

============================================================
四、新增 run_live_integrated_session.py
===================================

新增脚本：
run_live_integrated_session.py

目标：
一个 live session 内完成：

1. 等待 stream
2. 采桌面三线 calibration
3. 立即用该 FormalCalibration 进入 trial
4. 运行 TrialController
5. 保存 session
6. 后续可用 analyze_session.py 出图

输入参数：

基本：

* --host 默认 127.0.0.1
* --port 默认 8888
* --out-dir 默认 data/live_integrated_session
* --session-dir 可选
* --overwrite-session
* --subject-id 可选
* --trial-id 默认 live_integrated_trial
* --notes 可选

map：

* --map-config path，必填
* --strict-map-validation，可选

calibration：

* --calibration-id 可选
* --point-source tracker_position_world / pinch_center_world，默认 tracker_position_world
* --sample-duration-seconds 默认 5.0
* --min-samples 默认 10
* --min-line-length 默认 0.10
* --up-hint 默认 "0,0,1"
* --confirm-calibration / --no-confirm-calibration，默认需要确认保存/继续
* --allow-calibration-warnings，默认 true
* --recalibrate，如果 session dir 已有 calibration 时强制重新采

trial/control：

* --control-rate-hz 默认 60
* --max-frames 可选
* --duration-seconds 可选
* --pinch-grab-threshold 可选
* --pinch-release-threshold 可选
* --slip-motion-threshold 可选
* --ignore-task-z 可选，debug only
* --task-z-half-extent 默认 5.0

display：

* --display-mode text / none，默认 text
* --print-every 默认 30
* 不要在本阶段做 matplotlib 主 GUI
* matplotlib 只保留给 analyze_session 离线图

debug-only anchor：

* --anchor-current-pinch-debug，可选，默认 false
* 如果启用，必须在 session_meta、trial_config、summary 中写：
  map_anchor_mode = current_pinch_debug
  is_formal_experiment = false
  warning = "Map was translated to current pinch for debugging; this is not a formal calibrated trial."

注意：
默认不启用 anchor。
正式路径是 calibration 决定 task coordinate system，MapConfig 决定 block/track 位置。

============================================================
五、运行流程
======

1. 启动 LiveRawStreamServer / LatestFrameBuffer。
2. phase = WAITING_FOR_STREAM。
3. 等待连接和有效 raw frame。
4. 等到 tracker_valid 后，phase = READY_FOR_CALIBRATION。
5. calibration 阶段：

   * 提示用户准备 origin
   * 按 Enter 后采样 sample_duration_seconds
   * 提示 long line
   * 按 Enter 后采样 sample_duration_seconds
   * 提示 width line
   * 按 Enter 后采样 sample_duration_seconds
   * 提示 diagonal line
   * 按 Enter 后采样 sample_duration_seconds
6. 使用 calibration_sampling / calibration_geometry 构造 FormalCalibration。
7. validate_calibration。
8. 如果 errors：

   * phase = CALIBRATION_FAILED
   * 打印错误
   * 允许用户 retry calibration 或 quit
9. 如果 warnings：

   * 打印 warnings 和 quality summary
   * 如果 confirm-calibration=true，询问是否继续
10. 保存 calibration.json 到 session/calibration.json 和 out_dir/calibration.json。
11. 关键：不要重新加载 calibration 文件作为 trial 输入。
    直接使用内存中的 FormalCalibration object：
    task_system = build_task_coordinate_system_from_calibration(calibration)
12. 读取 MapConfig。
13. validate map。
14. compile_map_to_track_region。
15. 创建 TrialController / BlockController。
16. phase = READY_FOR_TRIAL。
17. 用户按 Enter 开始 trial。
18. phase = TRIAL_RUNNING。
19. 控制线程按 --control-rate-hz 固定频率取 LatestFrameBuffer 最新帧：

    * parse raw
    * adapter
    * world_to_task
    * TrialController.update
    * SessionRecorder record
    * build DashboardSnapshot
    * text display
20. 结束条件：

    * 用户按 q 或 Ctrl+C
    * max_frames
    * duration_seconds
    * TrialController ended
21. phase = TRIAL_ENDED / SAVING。
22. finalize session。
23. 写 summary.json。

============================================================
六、退出与 Ctrl+C
============

必须修复 MVP 中“需要狂按 Ctrl+C 才结束”的问题。

要求：

* 单次 Ctrl+C：

  * 设置 run_stop_reason = keyboard_interrupt
  * phase = SAVING
  * 停止接收/控制循环
  * finalize session
  * 写 summary.json
  * 正常退出
* 不要在 except KeyboardInterrupt 中重新 raise，除非文件已保存后 main 决定返回非零码。
* 如果正在 input() 等待 Enter，Ctrl+C 也必须进入同一套安全收尾逻辑。
* 如果 live source 线程阻塞，stop_event 必须能让它退出。
* summary.json 记录：

  * run_stop_reason
  * phase_at_stop
  * session_finalized true/false

============================================================
七、记录与 session
=============

如果 session 写入启用，session 目录至少包含：

* session_meta.json
* calibration.json
* trial_config.json
* raw_frames.jsonl
* device_frames.jsonl
* processed_frames.csv
* events.csv
* haptic.csv
* trial_summary.json

session_meta.json 必须包含：

* mode = live_integrated_session
* is_live_trial = true
* is_formal_experiment = false  第一版仍然 false，避免过度声明
* calibration_type = formal_table_lines
* is_formal_calibration = true
* calibration_id
* calibration_collection_mode = live_stream_integrated
* map_id
* scene_type = map_config
* map_anchor_mode = none 或 current_pinch_debug
* haptic_hardware_enabled = false

trial_config.json 必须包含：

* map_config_to_trial_config 输出
* calibration_id
* calibration_type
* task_coordinate_system
* map_anchor info
* engine thresholds
* control_rate_hz
* warnings

summary.json 必须包含：

* mode
* run_stop_reason
* phase_at_stop
* total_received_frames
* total_processed_frames
* dropped_or_overwritten_frame_count
* tracker_invalid_frame_count
* hand_invalid_frame_count
* pinch_valid_frame_count
* slip_active_frame_count
* blocked_frame_count
* logical_haptic_label_counts
* processing_latency_ms mean/max
* calibration_quality
* calibration_warnings
* map_warnings
* session_dir

============================================================
八、text display
==============

第一版只做 text display，不做正式 GUI。

每 print_every 帧打印：

* PHASE
* frame
* tracker/hand/pinch valid
* pinch distance
* CONTACT label
* MOTION label
* STOP reason
* FEEDBACK label
* block center
* pinch task

如果 phase 变化，立即打印一行明显提示。

显示用 DashboardSnapshot 的英文 label：

* CONTACT
* CONTACT RELEASE
* MOVING
* FREE
* SLIP
* BLOCKED
* PINCH INSUFFICIENT
* TRACKING INVALID
* FEEDBACK: ...

============================================================
九、不要让 calibration 和 trial 坐标不一致
===============================

硬性要求：

1. trial 使用的 TaskCoordinateSystem 必须来自同一个内存 FormalCalibration 对象。
2. 保存的 session/calibration.json 必须是这个对象的序列化结果。
3. trial_config.json 中的 task_coordinate_system 必须和该 calibration 一致。
4. 禁止 trial 阶段调用 post-hoc auto calibration。
5. 禁止默认 anchor_current_pinch。
6. 如果用户启用 debug anchor，只能平移 MapConfig，不能修改 calibration；必须清楚记录。

新增测试要检查：

* integrated runner 中 calibration object 的 calibration_id 与 session_meta/trial_config/calibration.json 一致。
* trial_config task_coordinate_system 与 calibration.json 一致。
* 默认 map_anchor_mode == none。
* 启用 debug anchor 时 session metadata 明确 warning。

============================================================
十、测试
====

新增 tests/test_live_integrated_session.py

使用 fake live source / fake frame iterator 测试，不依赖真实设备，不依赖 manus_vive_com。

至少覆盖：

1. integrated session 可以完成 calibration + trial。
2. calibration 由 live frames 采样得到，不是从外部 calibration-json 加载。
3. trial 使用同一个 in-memory FormalCalibration。
4. session/calibration.json、session_meta.json、trial_config.json 的 calibration_id 一致。
5. 默认不启用 anchor_current_pinch。
6. Ctrl+C / stop request 能 finalize summary 和 session。
7. calibration error 时进入 CALIBRATION_FAILED，不启动 TrialController。
8. map validation error 时不启动 trial。
9. summary 中 run_stop_reason、phase_at_stop、session_finalized 存在。
10. text display 不影响控制循环。
11. LatestFrameBuffer 只保留最新帧，覆盖旧帧计数正确。

新增 tests/test_latest_frame_buffer.py：

* put 多帧后 get_latest 返回最后一帧
* 同一帧不会重复消费，除非显式允许
* overwritten count 正确

============================================================
十一、README
=========

README 增加 Stage 5C integrated live session 说明：

说明：

* live_calibrate_table.py 是 calibration-only 工具
* run_live_integrated_session.py 是 calibration + trial 连续流程
* 正式开发应优先使用 integrated runner，避免“先标定文件、再运行实验程序”导致坐标不一致
* --anchor-current-pinch-debug 只是调试手段，不是正式 calibration

示例：

python run_live_integrated_session.py ^
--map-config maps\examples\xoy_turn.json ^
--host 127.0.0.1 ^
--port 8888 ^
--out-dir data\live_integrated_session\debug_01 ^
--subject-id S001 ^
--trial-id trial_001

流程：

1. 先启动该脚本。
2. 再启动 manus_vive_com，让它连接 127.0.0.1:8888。
3. 按提示依次采 origin / long / width / diagonal。
4. 查看 calibration quality。
5. 确认后进入 trial。
6. trial 结束后用 analyze_session.py 复盘。

完成后运行：
python analyze_session.py --session-dir data\live_integrated_session\debug_01\session --overwrite

============================================================
十二、禁止事项
=======

不要：

* 不要修改 BlockController / TrialController 核心逻辑
* 不要修改 parser / adapter 语义
* 不要接真实 haptic hardware
* 不要做复杂 GUI
* 不要默认 anchor 到当前 pinch
* 不要把 integrated session 标记成正式实验完成版
* 不要破坏现有 live_calibrate_table.py、offline_replay_formal_calibrated.py、run_live_raw_preview.py

完成后运行 pytest -q，并报告：

1. 新增文件
2. 修改文件
3. 新增测试
4. pytest 是否通过
5. 如何运行 integrated live session
6. 如何验证 calibration.json、trial_config.json、session_meta.json 使用同一个 calibration
7. Ctrl+C 如何安全退出并保存 session


补充 calibration 轴方向规则：

1. x_axis_world 的正方向必须由 long_axis_line 的采样运动方向决定：
   - 使用 long_axis_line 的 first valid point 和 last valid point
   - motion_direction = normalize(last_point - first_point)
   - fit_line_3d 得到 fitted_direction 后，如果 dot(fitted_direction, motion_direction) < 0，则翻转 fitted_direction
   - 最终 x_axis_world 使用翻转后的 long_line.direction_world 投影到 plane 上

2. up_axis_world 来自三条线点云拟合出的 plane normal。
   plane normal 的正方向用 --up-hint 决定：
   - 如果 dot(plane_normal, up_hint) < 0，则翻转 plane_normal。

3. y_axis_world 不直接由 width line 的采样方向决定。
   y_axis_world = normalize(cross(up_axis_world, x_axis_world))。
   width_axis_line 只用于质量检查：
   - 检查它是否接近 y_axis_world 或 -y_axis_world
   - 检查它是否与 x_axis_world 近似 90°
   - 不因为 width line 方向相反而翻转 y_axis_world。

4. diagonal_line 不决定坐标轴，只用于质量检查。

5. calibration.json quality 中记录：
   - long_line_motion_direction_world
   - long_line_fitted_direction_world
   - long_line_direction_flipped_to_match_motion: true/false
   - width_line_dot_y_axis
   - width_line_angle_to_y_axis_degrees
   - width_line_direction_matches_y_positive: true/false

同意你的 8 点默认选择。关于 MVP 代码，不要整文件复制 run_live_trial_visual_preview.py / live_visual_display.py。

可以参考并选择性移植：
- DashboardSnapshot 字段
- 英文状态 label 生成逻辑
- logical_haptic_label 规则
- text status line 格式
- logical_haptic_label_counts 统计

但请在 stage-5 中重新实现为更小的 dashboard_snapshot.py 或 live_status_labels.py，不要引入 MVP 的 matplotlib 实时显示结构，也不要照搬 MVP 的单脚本 runner。

正式 integrated runner 的核心应是：
- LatestFrameBuffer
- LiveSessionPhase
- calibration + trial 同一 live session
- text display first
- GUI later as subscriber

不要默认启用 anchor-current-pinch。若保留，只能作为 --anchor-current-pinch-debug，并明确写入 session metadata。