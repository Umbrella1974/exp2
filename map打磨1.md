你现在要修改 exp2 的 stage4-offline 分支。

目标：
继续打磨 MapConfig replay / analyze_session 的可解释性和稳健性。
本次是小迭代，不修改核心实验逻辑。

当前已有：
- map_config.py
- map_generator.py
- offline_replay_autocalibrated.py 支持 --map-config
- analyze_session.py 支持绘制 trial_config["track_boxes"]
- session_recorder.py
- maps/examples/*.json

不要修改：
- BlockController 核心逻辑
- TrialController 核心逻辑
- TaskCoordinateSystem 核心逻辑
- ManusViveExperimentAdapter
- raw parser / adapter
- haptic 状态语义

本次修改内容：

1. analyze_session.py 增加 valid/skipped track box 统计

在读取 trial_config["track_boxes"] 并绘制 trajectory_track_map 时：
- track_box_count = 原始 track_boxes 数量
- valid_track_box_count = 成功解析并用于绘制的 box 数量
- skipped_track_box_count = 因格式错误跳过的 box 数量

写入 analysis_summary.json：
- track_box_count
- valid_track_box_count
- skipped_track_box_count
- trajectory_map_used_track_boxes

如果存在 skipped box，要在 warnings 中说明。

注意：
不要因为单个坏 box 导致整张图失败。
不要修改 trial_config.json。

2. haptic 命名解释更清楚

analysis_summary.json 中保留现有：
- haptic_active_frame_count
- haptic_event_count
- slip_active_frame_count
- blocked_frame_count

新增：
- logical_slip_feedback_frame_count = slip_active_frame_count
- logical_blocked_feedback_frame_count = blocked_force_active true 的帧数
- hardware_haptic_active_frame_count = haptic_active_frame_count
- hardware_haptic_event_count = haptic_event_count

目的：
明确区分逻辑反馈状态和真实硬件 haptic command。
当前 offline replay 通常没有真实 hardware command，所以 hardware_haptic_active_frame_count 可能为 0，但 slip_active_frame_count 可以大于 0。

不要改变原有字段含义，只新增别名/解释性字段。

3. relative time 可读性

analyze_session.py 增加参数：
- --relative-time，布尔，默认 false 或 true 由你判断，但建议默认 true
- --absolute-time，布尔，可选，用于关闭 relative time

如果使用 relative time：
- 图像横轴使用 selected_time - first_valid_time
- xlabel 写 "time since session start (s)"
- analysis_summary.json 写：
  - time_column_used
  - time_axis_mode = "relative"
  - time_zero

如果使用 absolute time：
- 保持现有行为
- time_axis_mode = "absolute"

不要改变 CSV 原始数据，只影响 analyzer 图像和 summary。

4. README 补充一段简短说明

说明：
- MapConfig replay 可以用旧 raw JSONL + configured map 进行 post-hoc 检测
- calibration 仍是 post-hoc auto，不是正式实验
- analyze_session.py 默认使用相对时间更方便看图
- haptic_active_frame_count 表示硬件/命令层 active，不等于 slip_active_frame_count

测试：

更新 tests/test_analyze_session.py，至少覆盖：
- analysis_summary.json 包含 valid_track_box_count / skipped_track_box_count
- 有坏 track box 时不失败，skipped_track_box_count > 0
- relative time summary 字段存在
- haptic alias 字段存在

运行 pytest -q。
报告：
1. 修改文件
2. 新增/更新测试
3. pytest 是否通过

同意这个 relative time 定义。

请按以下方式实现：

1. --relative-time 默认开启。
   time_zero 取当前选中的 time-column 中第一个非空、有限值。
   所有图像横轴和事件标注都使用 selected_time - time_zero。

2. --absolute-time 关闭 relative time，保持旧横轴。
   CSV 原始数据不做任何修改。

3. analysis_summary.json 中记录：
   - time_column_used
   - time_axis_mode = "relative" 或 "absolute"
   - time_zero
   - time_axis_label

4. 如果当前选中的时间列不存在或全为空，继续沿用已有 fallback：
   sample_time -> trial_time -> raw_timestamp -> frame_index。
   如果最终用 frame_index，需要 warnings 说明。

5. valid_track_box_count / skipped_track_box_count 即使 --no-plots 也要写入 summary。
   坏 box 只 warning，不让分析失败。

6. haptic 新字段只做解释性别名：
   - logical_slip_feedback_frame_count = slip_active_frame_count
   - logical_blocked_feedback_frame_count = blocked_force_active true 的帧数
   - hardware_haptic_active_frame_count = haptic_active_frame_count
   - hardware_haptic_event_count = haptic_event_count
   不改变旧字段含义。