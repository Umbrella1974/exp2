你现在要修改 exp2 的 stage4-offline 分支。

目标：
在已经实现 map_config.py / map_generator.py / analyze_session.py 的基础上，做一次小迭代：

1. 修 MapConfig / generator 的若干细节，让地图配置更适合正式实验和手工编辑。
2. 让 analyze_session.py 的 trajectory_track_map 支持绘制 MapConfig 多段轨道，而不是只画一个简单 track bounds 外框。

重要背景：
当前项目已有：
- map_config.py
- map_generator.py
- maps/examples/*.json
- analyze_session.py
- session_recorder.py
- offline_replay_autocalibrated.py
- BlockController / TrackRegion / Box3D

当前 MapConfig core 已经能：
- 读取 JSON map
- validate
- compile_map_to_track_region
- map_config_to_trial_config
- 规则生成 xoy 正交 corridor map

当前 analyze_session.py 已经能：
- 读取 session 目录
- 生成 analysis_summary.json
- 生成 timeseries / pinch_distance / trajectory_track_map / state_timeline / haptic_timeline
- best-effort 绘图，不重新跑 TrialController / BlockController

本次仍然不要修改核心实验逻辑。

不要修改：
- BlockController 核心逻辑
- TrialController 核心逻辑
- TaskCoordinateSystem 核心逻辑
- ManusViveExperimentAdapter
- raw parser / adapter
- haptic 状态语义

不要：
- 不要接真实设备
- 不要接 socket
- 不要接 haptic hardware
- 不要写 GUI
- 不要在 analyze_session.py 中重新实现物理规则
- 不要让绘图失败导致分析失败

============================================================
一、MapConfig 细节修复
============================================================

修改 map_config.py / map_generator.py / example maps。

1. example maps 改为 pretty JSON

把以下示例地图改成缩进格式，便于手工编辑：
- maps/examples/xoy_straight.json
- maps/examples/xoy_turn.json
- maps/examples/xoy_two_turns.json

要求：
- 使用 2 空格缩进
- 字段顺序尽量稳定
- 每个 track box 都清楚包含：
  - id
  - order
  - label
  - min
  - max
  - metadata
- metadata 中尽量包含：
  - direction 或 segment_direction
  - segment_length
  - turn_from_previous，如果适用

不要改变示例地图的基本语义，只提升可读性。

2. 加强 order 检查

在 validate_map_config(config) 中增加 ordered track box 检查。

如果任意 track box 设置了 order：
- 所有 track box 都应该设置 order，否则 warning 或 error；建议 error
- order 不允许重复；重复是 error
- order 应该从 0 开始连续到 n-1；缺失或不连续是 error
- ordered boxes 按 order 排序后，相邻段必须连续

连续性定义：
- 相邻 boxes 有正体积重叠：OK
- 相邻 boxes 面接触，并且接触面有正面积：OK
- 只有边接触或点接触：error
- 完全分离，有 gap：error

不要为了这个修改 TrackRegion 或 Box3D 的核心行为。

3. target_region 检查保持严格

target_region 如果存在：
- 必须与至少一个 track_box 有正体积交集
- 完全不相交是 error
- 只有面接触是 warning
- 只有边/点接触是 warning 或 error；建议 error

4. generator 生成独立 target_region

当前 generate_orthogonal_corridor_map 如果直接把最后一个 segment 作为 target_region，请改成生成一个独立 target_region box。

目标：
- target_region 应该位于最后一个 segment 的末端区域
- target_region 与最后一个 segment 有正体积交集
- target_region 不应简单等于整个最后一个 segment
- target_region.id = "target"
- target_region.label = "Target region"
- target_region.metadata 至少包含：
  - type = "target_region"
  - based_on_segment_id
  - target_length

新增参数：
- target_length: float | None = None

行为：
- 如果 target_length 为 None，则默认使用 min(0.20, last_segment_length * 0.25)
- target_length 必须 > 0
- target_length 不应超过最后一段长度；如果超过，则 clamp 到最后一段长度并写入 metadata warning 或 validation warning
- target_region 在 xoy 平面中沿最后一段方向放在末端
- z 范围与最后一段相同
- 横向宽度与最后一段相同

5. generator 保持第一版约束

generate_orthogonal_corridor_map 仍然只支持：
- plane = "xoy"
- initial_direction in ["x+", "x-", "y+", "y-"]
- allowed_turns 只包含 ["left", "right", "straight"]

补充明确：
- "left" 是相对当前方向逆时针 90°
- "right" 是相对当前方向顺时针 90°
- "straight" 是方向不变
- 不支持任意角度转弯
- 不支持斜向 segment
- 不支持 180° 掉头
- 每个 segment 必须是 axis-aligned AABB
- start 必须包含在第一段 track box 内
- block_initial_center_task = start

6. map_config_to_trial_config 输出保持完整

确保 map_config_to_trial_config(config) 输出中包含：
- map_config_version = 1
- map_id
- map_source_type = "manual" 或 "generated"
- description
- coordinate_space
- unit
- block_initial_center_task
- block_size
- track_boxes，完整多段 box 列表
- target_region
- metadata
- is_generated

不要只输出外包围 bounds。
后续 analyze_session.py 要依赖 track_boxes 画真实地图。

============================================================
二、analyze_session.py 支持多段地图绘制
============================================================

修改 analyze_session.py 的 trajectory_track_map 绘制逻辑。

目标：
如果 trial_config.json 中存在 MapConfig 风格的 track_boxes，则 trajectory_track_map.png 应逐个画出每个 track box 的 x-y 投影，而不是只画一个整体 bounds。

1. track_boxes 读取

analyze_session.py 应优先尝试读取：
- trial_config["track_boxes"]

每个 box 支持字段：
- min: [x, y, z]
- max: [x, y, z]
- id
- order
- label
- metadata

如果没有 track_boxes，再 fallback 到当前已有的 track_bounds / track_bounds_task / track_region / bounds / scene_auto.track_bounds 等逻辑。

2. 绘制多段 track boxes

在 trajectory_track_map.png 中：
- 对每个 track box 画 x-y 投影矩形
- 可以使用透明填充或边框
- 如果 box 有 order，按 order 排序绘制
- 可以在 box 中心标注 order 或 id，但要避免过度拥挤
- 如果 track_boxes 数量很多，只标注前若干个或只标注 order
- 不要因为某个 box 格式错误导致整张图失败；跳过该 box 并写 warning

3. 绘制 target_region

如果 trial_config 中有 target_region：
- 绘制 target_region 的 x-y 投影
- 使用和普通 track box 不同的边框或线型
- 在图例中标明 target region
- 如果 target_region 格式错误，写 warning，不要失败

4. 绘制 block_initial_center_task

如果 trial_config 中有 block_initial_center_task：
- 在 trajectory_track_map.png 中标出该点
- label 为 "configured block start" 或类似名称

5. 保留已有轨迹和事件点

trajectory_track_map.png 仍然要保留：
- pinch_center_task 轨迹
- block_center_task 轨迹
- block 起点和终点
- blocked 事件点
- slip 事件点
- haptic active 点

事件点坐标仍然从 processed_frames.csv 获取：
- 优先按 frame_index 匹配
- 缺 frame_index 时按最近 time 匹配
- 找不到则跳过并写 warning

6. summary 记录

analysis_summary.json 中增加或确保包含：
- map_id，如果 trial_config 有
- map_config_version，如果 trial_config 有
- map_source_type，如果 trial_config 有
- track_box_count
- target_region_present
- trajectory_map_used_track_boxes: true/false

如果 fallback 到 track_bounds 而不是 track_boxes，也在 warnings 中记录。

============================================================
三、测试
============================================================

更新或新增 tests/test_map_config.py

至少增加：
- duplicate order 是 error
- missing order when some boxes ordered 是 error
- non-contiguous order 是 error
- edge-only / point-only adjacent boxes 是 error
- ordered face contact with positive contact area 通过
- ordered volume overlap 通过
- generated target_region 不等于最后一个 segment
- generated target_region 与最后一个 segment 有正体积交集
- generated target_region 位于最后 segment 末端
- map_config_to_trial_config 包含完整 track_boxes 和 target_region

更新或新增 tests/test_map_generator.py

至少增加：
- target_length 默认值行为
- target_length 超过最后段长度时被安全处理
- left/right 是 90° 转向
- start 在第一段 box 内
- 不支持 xoz / yoz
- 不支持非法 initial_direction
- 不支持非法 allowed_turns

更新 tests/test_analyze_session.py

至少增加：
- fake session 的 trial_config.json 包含 track_boxes
- analyze_session.py 可以生成 trajectory_track_map，不失败
- analysis_summary.json 包含 track_box_count
- trajectory_map_used_track_boxes = true
- target_region_present = true
- 没有 track_boxes 时仍 fallback 到旧 track_bounds 逻辑
- track_boxes 中有坏 box 时只 warning，不导致整个分析失败

测试不要依赖真实 raw JSONL。
测试不要依赖 manus_vive_com。
matplotlib 不可用时测试不要失败；可以通过 monkeypatch 模拟无 matplotlib。

============================================================
四、文档 / 注释
============================================================

在 map_config.py docstring 或注释中说明：
MapConfig 是正式实验地图配置层，把 task-space JSON map 编译成现有 TrackRegion，不实现新物理，不替代 BlockController。

在 map_generator.py docstring 或注释中说明：
第一版 generator 只支持 xoy 平面正交 corridor，所有转弯都是 90°，不支持斜向、任意角度或 3D 迷宫。

在 analyze_session.py 注释中说明：
trajectory_track_map 优先使用 trial_config["track_boxes"] 绘制真实多段地图；如果不存在，再 fallback 到旧 track bounds 绘制。

============================================================
五、完成后报告
============================================================

完成后运行 pytest -q，并报告：
1. 修改文件
2. 新增/更新测试
3. pytest 是否通过
4. example maps 是否已经 pretty-format
5. 如何用 map_config_to_trial_config 生成可供 analyzer 绘制的 trial_config
6. analyze_session.py 如何显示多段 track boxes 和 target_region