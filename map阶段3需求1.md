你现在要修改 exp2 的 stage4-offline 分支。

目标：
围绕地图可视化与地图配置，做一轮聚焦改进。当前不要改核心控制器逻辑，也不要先继续打磨 diagnostic L-shape 规则。重点是：

1. 保留当前 slip / haptic 语义不变，但增强可视化：
   - 保留现有 trajectory_track_map.png
   - 新增一张带物块 footprint / block AABB 的地图图
   - 用于观察 slip / blocked / pinch 点与物块、轨道之间的关系

2. 支持“直接生成并可视化地图”，不依赖 replay 轨迹：
   - 给一个 map JSON
   - 直接输出地图预览图
   - 用来检查地图结构是否正确

3. 在 2 的基础上，新增一种“模板式地图生成”能力：
   - 主方向由轨迹前若干个有效点估计
   - 其余区域的相对位置 / 旋转 / 平移关系由 JSON 模板定义
   - 这样可以用真实数据对齐第一段方向，再由 JSON 决定整体地图结构

4. 帮我检查并明确：
   - 当前系统里，边界附近导致的 slip
   - 和 pinch-distance / pinch insufficient 导致的 slip
   在 haptic 状态或记录字段上是否可以区分
   - 如果可以，请指出具体字段和当前语义
   - 如果不可以，请明确说明当前哪些字段不足

重要：
本轮优先做“可视化、地图预览、模板地图、字段诊断”。
不要先改 slip 判定逻辑。
不要先改 BlockController / TrialController 的核心行为。

============================================================
一、不要修改的内容
============================================================

不要修改：
- BlockController 核心逻辑
- TrialController 核心逻辑
- TaskCoordinateSystem 核心逻辑
- ManusViveExperimentAdapter
- raw parser / adapter
- haptic 状态机核心语义
- 当前 slip / blocked / contact 的底层判定逻辑

不要：
- 不要接 socket
- 不要接真实 haptic hardware
- 不要写 GUI
- 不要重新实现物理规则

============================================================
二、增强 analyze_session.py：新增带物块 footprint 的地图图
============================================================

当前已有 trajectory_track_map.png。
要求：

1. 保留现有 trajectory_track_map.png，不要破坏当前输出。
   - 这张图仍然只画：
     - pinch path
     - block path
     - track boxes / bounds
     - target region
     - block start/end
     - slip / blocked / haptic 事件点
   - 不额外画物块 footprint

2. 新增一张图：
   plots/trajectory_track_map_with_block_footprint.png

这张图在现有 trajectory_track_map 的基础上，额外可视化：
- configured block initial footprint
- block start footprint
- block end footprint
- slip 帧对应的 block footprint（抽样）
- blocked 帧对应的 block footprint（抽样）

要求：
- footprint 使用 block center + block_size 重建 x-y AABB
- 为避免太乱，新增 CLI 参数：
  --max-footprint-overlays 默认 20
- 对 slip_active=True 的帧，最多画 N 个 footprint
- 对 blocked_force_active=True 或 stop_reason 包含 TRACK_BLOCKED 的帧，最多画 N 个 footprint

block_size 来源优先级：
1. trial_config["block_size"]
2. trial_config["block_size_task"]
3. processed_frames.csv 中如果有 block_size 字段则使用
4. 找不到则 warning，不让分析失败

analysis_summary.json 增加：
- block_footprint_overlay_count
- slip_footprint_overlay_count
- blocked_footprint_overlay_count
- track_region_semantics = "block_center_feasible_region"

注意：
当前 TrackRegion 的语义是 block center feasible region，不是完整物块 footprint 必须完全在轨道内。
不要修改核心控制器逻辑，只把这个语义通过图像和 summary 更清楚地表达出来。

============================================================
三、增强 analyze_session.py：新增 slip consistency diagnostic
============================================================

新增纯分析性质的检查，不改变任何核心逻辑。

对于每个 slip_active=True 的帧：
- 如果该帧有 pinch_center_task、block_center_task、block_size
- 检查 pinch_center_task 是否在该帧重建的 block AABB 内

统计并写入 analysis_summary.json：
- slip_frame_count
- slip_frames_with_geometry_check
- slip_frames_pinch_inside_block_count
- slip_frames_pinch_outside_block_count

如果 slip_frames_pinch_outside_block_count > 0：
- 在 warnings 中写：
  "Some slip_active frames have pinch_center_task outside the reconstructed block AABB; inspect contact/slip recording semantics."

注意：
这只是 analyzer 层的 consistency diagnostic。
不要修改 slip 逻辑，只做检查与报告。

============================================================
四、直接地图预览：新增 map_preview.py
============================================================

新增脚本：
map_preview.py

目标：
不依赖 replay，不依赖 raw JSONL，直接把 map JSON 渲染成一张地图预览图。

输入参数：
- --map-config path，必填
- --out path，可选，默认 map_preview.png
- --show-target-region，默认 true
- --show-box-labels，默认 true
- --show-box-order，默认 true
- --show-configured-block，默认 true
- --padding 默认 0.1
- --title 可选
- --annotate-centers，默认 false

功能：
1. 读取 map_config.py 的 MapConfig
2. validate_map_config
3. 如果 validation.errors 非空，则清晰失败
4. warnings 写到终端，也可写入一个 sidecar summary JSON（可选）
5. 在 x-y 平面渲染：
   - 所有 track_boxes
   - target_region
   - configured block_initial_center_task
   - configured block footprint（如果有 block_size）
   - 可选显示 id / order / label
6. 输出 png

要求：
- 这是独立地图预览工具
- 不依赖 replay session
- 不导入运动轨迹

可选输出：
- --summary-out path
  写一个简单 JSON，包括：
  - map_id
  - track_box_count
  - target_region_present
  - validation_errors
  - validation_warnings

五、模板式地图生成：绝对模板坐标 + 主方向对齐

新增模块：
map_template.py

目标：
支持一种 template map：
- JSON 里直接写完整地图在“模板局部坐标系”下的绝对坐标
- 模板默认主方向为 x+，也可通过 anchor_direction 指定
- replay 时根据旧 raw 数据前 N 个有效点估计主方向
- 将模板地图整体旋转和平移到 task 坐标系
- 输出标准 MapConfig

这个能力用于旧数据诊断和地图探索，不是正式实验标定。

Template JSON 结构建议：
- template_id: str
- description: str | None
- coordinate_space: "template"
- unit: "m"
- anchor_direction: "x+" / "x-" / "y+" / "y-"，默认 "x+"
- block_initial_center_template: list[float] length 3，默认 [0,0,0]
- block_size: list[float] length 3
- track_boxes: list[MapBoxSpec-like dict]
- target_region: MapBoxSpec-like dict | None
- metadata: dict

新增函数：
- load_map_template(path) -> MapTemplateConfig
- estimate_main_direction_from_points(points, n_frames) -> direction info
- transform_template_to_map_config(template, origin_task, snapped_main_direction) -> MapConfig

变换规则：
1. 估计 raw_main_direction。
2. snap 到 x+ / x- / y+ / y-。
3. 根据 template.anchor_direction 和 snapped_main_direction 计算 90° 倍数旋转 R。
4. 对 block_initial_center_template、track_boxes、target_region 做整体旋转。
5. 平移，使 block_initial_center_template 对齐到 origin_task：
   p_task = origin_task + R @ (p_template - block_initial_center_template)
6. 对每个 box：
   - 用 min/max 生成 8 个角点
   - 角点变换到 task space
   - 重新取 axis-aligned min/max
7. 输出标准 MapConfig。
8. metadata 中记录：
   - generated = true
   - generator_name = "template_aligned_to_trajectory"
   - template_id
   - template_anchor_direction
   - raw_main_direction
   - snapped_main_direction
   - rotation_degrees
   - origin_task
   - post_hoc = true
   - diagnostic = true

限制：
- 第一版只支持 x-y 平面上的 90° 倍数旋转。
- 不支持任意角度旋转。
- 不支持斜向 corridor。
- z 只平移，不参与主方向估计。

============================================================
六、把模板地图接到离线 replay（最小接入）
============================================================

在 offline_replay_autocalibrated.py 中新增可选路径：

新增参数：
- --map-template path，可选
- --template-anchor-frames 默认 100

互斥规则：
- --map-config 与 --map-template 不能同时使用
- --diagnostic-map 与 --map-template 不能同时使用

行为：
1. 如果传 --map-template：
   - 仍然先做 post-hoc auto calibration
   - 提取前 N 个有效 task 点
   - 估计 main_direction
   - snap 到 task x/y 轴
   - load_map_template()
   - generate_map_from_template_and_direction()
   - 得到标准 MapConfig
   - 后续流程和 --map-config 一样：
     - validate
     - compile_map_to_track_region
     - replay
     - session
     - trial_config.json 写完整 track_boxes / target_region / metadata
2. session / summary 中明确写：
   - scene_type = "map_template_generated"
   - is_formal_scene = false
   - calibration_type = "post_hoc_auto"
   - is_formal_calibration = false
   - map_template_used = true
   - template_id
   - raw_main_direction
   - snapped_main_direction
   - snap_angle_degrees

============================================================
七、检查 haptic / slip 是否可区分：先做“代码与记录语义审计”
============================================================

请检查当前代码与现有 session 字段，明确以下问题，并把结论写入 README 小节，同时可在 analysis_summary.json 中加入诊断字段：

问题：
1. “边界附近导致的 slip”
   是否能通过现有字段识别？
   可能候选字段包括但不限于：
   - slip_active
   - slip_reason
   - blocked_force_active
   - stop_reason
   - contact_state
   - block_motion_state
   - haptic_state
   - haptic_reason
   - command_type

2. “pinch-distance / pinch insufficient 导致的 slip”
   是否能通过现有字段识别？

要求：
- 不要猜
- 直接读代码和现有字段定义
- 给出明确结论：
  - 可以区分：列出字段和当前语义
  - 只能部分区分：列出限制
  - 不能区分：说明缺什么

如果当前系统里已有：
- slip_reason = PINCH_INSUFFICIENT / TRACK_BLOCKED
或类似字段，
请在 README 和 analysis_summary.json 中明确说明：
- logical slip caused by pinch insufficient
- logical slip caused by track blocked
是否能区分

如果 analysis_summary.json 容易加入，建议新增：
- slip_reason_counts
- logical_slip_due_to_pinch_insufficient_count
- logical_slip_due_to_track_blocked_count
- blocked_force_active_count

前提：
不要改核心语义，只统计已有字段。

============================================================
八、测试
============================================================

新增或更新测试：

1. tests/test_analyze_session.py
至少覆盖：
- trajectory_track_map.png 仍然生成
- trajectory_track_map_with_block_footprint.png 生成
- analysis_summary.json 包含：
  - block_footprint_overlay_count
  - slip_footprint_overlay_count
  - blocked_footprint_overlay_count
  - track_region_semantics
  - slip consistency diagnostic 字段
- block_size 缺失时不失败，只 warning

2. tests/test_map_preview.py
至少覆盖：
- example map 可预览
- validation error 时清晰失败
- target region、configured block 可被渲染
- summary-out 可选生成

3. tests/test_map_template.py
至少覆盖：
- load_map_template
- 从简单模板生成 MapConfig
- 不同 snapped_main_direction 下生成不同布局
- 输出 validate 通过
- target_region 模板可生成

4. tests/test_offline_replay_map_template.py
至少覆盖：
- --map-template 可跑 fake raw JSONL
- session/trial_config.json 包含生成后的 track_boxes
- summary.json 包含：
  - map_template_used
  - template_id
  - raw_main_direction
  - snapped_main_direction
- --map-config / --map-template 互斥
- --diagnostic-map / --map-template 互斥

不要依赖真实设备。
不要依赖 manus_vive_com。
matplotlib 不可用时测试不要失败。

============================================================
九、README
============================================================

README 增加三部分简短说明：

1. trajectory_track_map.png vs trajectory_track_map_with_block_footprint.png 的区别
2. map_preview.py 的使用方式
3. map_template.py / --map-template 的用途：
   - 第一段方向来自轨迹前若干点
   - 其余结构由 JSON 模板定义
   - 用于诊断和探索，不是正式实验地图

同时补充 haptic/slip 语义审计结果：
- 当前字段是否能区分 pinch insufficient slip 与 track blocked slip
- 哪些字段是逻辑反馈状态，哪些字段代表硬件 haptic 命令层

============================================================
十、完成后报告
============================================================

完成后运行 pytest -q，并报告：
1. 修改文件
2. 新增/更新测试
3. pytest 是否通过
4. 如何直接预览一个 map JSON
5. 如何用 map template + 旧 raw JSONL 生成地图并 replay
6. 当前代码里，pinch insufficient slip 与 track blocked slip 是否可区分；如果可区分，具体看哪些字段

