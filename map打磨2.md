你现在要修改 exp2 的 stage4-offline 分支。

目标：
新增一个离线诊断地图生成能力：根据旧 raw JSONL 数据前 N 个有效点估计主方向，在 task 坐标系中生成一个主方向 + 垂直方向的 MapConfig 地图，用于观察旧数据在“按自身运动方向对齐的地图”中的物块状态、slip、blocked 和 haptic 状态。

重要定位：
这个功能只用于旧数据 offline diagnostic，不是正式实验地图生成器。
它生成的是 data-driven post-hoc diagnostic map。
不能把结果当作正式实验设计或正式实验数据分析。

当前已有：
- offline_replay_autocalibrated.py 可以从 raw JSONL 自动构造临时 TaskCoordinateSystem
- offline_replay_autocalibrated.py 支持 --map-config
- map_config.py / MapConfig / MapBoxSpec / map_config_to_trial_config
- analyze_session.py 可以画 track_boxes
- session_recorder.py

不要修改：
- BlockController 核心逻辑
- TrialController 核心逻辑
- TaskCoordinateSystem 核心逻辑
- ManusViveExperimentAdapter
- raw parser / adapter
- haptic 状态语义

新增能力：

在 offline_replay_autocalibrated.py 中新增一种 scene 来源：
1. auto scene：现有 wide-track / fitted-corridor / narrow-corridor
2. map-config scene：现有 --map-config
3. trajectory-aligned diagnostic map：本次新增

新增 CLI 参数：
- --diagnostic-map，布尔
- --diagnostic-map-frames，默认 100
- --diagnostic-map-main-length，默认 1.0，单位 m
- --diagnostic-map-perp-length，默认 0.5，单位 m
- --diagnostic-map-width，默认 0.20，单位 m
- --diagnostic-map-z-tolerance，默认 0.20，单位 m
- --diagnostic-map-shape，可选：
    cross
    l_shape
    t_shape
  默认 cross
- --diagnostic-map-id，默认 "trajectory_aligned_diagnostic_map"

互斥规则：
- --map-config 和 --diagnostic-map 不能同时使用
- --diagnostic-map 启用时，不使用 scene-mode 自动场景
- --diagnostic-map 仍然使用 post-hoc auto calibration
- --diagnostic-map 生成 MapConfig，然后走和 --map-config 类似的 compile_map_to_track_region / trial_config/session 输出逻辑

主方向估计：

使用 offline replay 已有的有效轨迹点。
优先使用 pinch_center_task。
如果 pinch_center_task 无效，可以使用 tracker fallback task point。
只使用前 --diagnostic-map-frames 个有效 task 点。

估计方法：
- origin_task = 第一个有效 task 点
- main_direction = calibration / diagnostic points 中距离 origin_task 最远的点 - origin_task
- 如果长度太短，则 fallback 到 PCA 第一主成分
- 如果仍然太短，报错退出
- main_direction 归一化
- perp_direction = 在 task x-y 平面内相对 main_direction 旋转 90°
- z 方向不参与主方向估计，地图在 x-y 平面生成
- block_initial_center_task = origin_task

注意：
这个功能生成的是 task-space map。
不要在 world space 生成地图。

地图生成规则：

根据 shape 生成 MapConfig：

1. cross：
- 主方向 corridor：
  从 origin_task 沿 +main_direction 生成一个 box，长度 diagnostic_map_main_length
- 垂直方向 corridor：
  以 origin_task 或主方向中点为中心，沿 ±perp_direction 生成一条 corridor，总长度 diagnostic_map_perp_length
- 两个 box 必须有正体积重叠，保证轨道连通

2. l_shape：
- 第一段沿 main_direction，长度 diagnostic_map_main_length
- 第二段从第一段末端沿 +perp_direction，长度 diagnostic_map_perp_length
- 两段在 junction_overlap 处有正体积重叠

3. t_shape：
- 主方向 corridor 从 origin_task 沿 +main_direction，长度 diagnostic_map_main_length
- 在主方向末端或中点生成左右两个 perp 分支，总长度 diagnostic_map_perp_length
- junction 处必须有正体积重叠

所有 corridor box：
- 使用 AABB
- 因为 Box3D / TrackRegion 当前是 axis-aligned AABB，若 main_direction 不是接近 task x 或 task y，不能生成斜向 box
- 因此需要把 main_direction snap 到最近的 task axis：
    x+, x-, y+, y-
- 在 summary / trial_config metadata 中记录：
    raw_main_direction
    snapped_main_direction
    snap_angle_degrees
- 如果 snap_angle_degrees 太大，例如 > 45°，warning

这是第一版限制：只生成 axis-aligned diagnostic map，不生成斜向 corridor。

target_region：
- 放在主方向 corridor 的末端
- target_length 默认 min(0.20, main_length * 0.25)
- 与主方向 corridor 有正体积交集
- id = "target"
- metadata.type = "target_region"

MapConfig metadata：
- generated = true
- generator_name = "trajectory_aligned_diagnostic_map"
- diagnostic = true
- post_hoc = true
- source = "raw_jsonl_valid_points"
- diagnostic_map_frames
- raw_main_direction
- snapped_main_direction
- snap_angle_degrees
- shape
- main_length
- perp_length
- width
- z_tolerance
- warning: "This map is generated from replay data and must not be treated as a formal experimental map."

输出：
- summary.json / trial_summary.json 增加：
  - diagnostic_map_used = true
  - diagnostic_map_id
  - diagnostic_map_shape
  - diagnostic_map_frames
  - diagnostic_map_main_length
  - diagnostic_map_perp_length
  - diagnostic_map_width
  - diagnostic_map_z_tolerance
  - raw_main_direction
  - snapped_main_direction
  - snap_angle_degrees
- scene_auto.json 内容可以使用 map_config_to_trial_config(generated_config)，并标明:
  - scene_type = "diagnostic_map"
  - is_formal_scene = false

session/trial_config.json：
- 包含完整 track_boxes 和 target_region
- scene_type = "diagnostic_map"
- is_formal_scene = false
- calibration_type 仍为 post_hoc_auto
- is_formal_calibration = false

测试：

新增或更新 tests/test_offline_replay_diagnostic_map.py

至少覆盖：
- --diagnostic-map 可以用 fake raw JSONL 跑完整 replay
- --diagnostic-map 与 --map-config 同时传入时报错
- summary.json 包含 diagnostic_map_used = true
- session/trial_config.json 包含 track_boxes 和 target_region
- analyze_session.py 可以读取该 session，并 trajectory_map_used_track_boxes = true
- main_direction 会 snap 到 x/y axis
- shape cross / l_shape / t_shape 都能生成 valid MapConfig
- 如果有效点太少或主方向太短，清晰失败
- 不依赖真实设备，不依赖 manus_vive_com

README：
补充说明：
- --diagnostic-map 是旧数据诊断工具
- 它根据前 N 个有效点估计主方向，并生成 axis-aligned task-space map
- 因为 TrackRegion 是 AABB union，第一版会把主方向 snap 到 x/y axis
- 它不是正式实验地图，也不是正式实验标定

运行 pytest -q。
报告：
1. 修改文件
2. 新增/更新测试
3. pytest 是否通过
4. 如何用旧 raw JSONL 跑 --diagnostic-map
5. 如何 analyze_session.py 查看生成地图和状态

继续实现 trajectory-aligned diagnostic map 功能。

要求：
1. 在 offline_replay_autocalibrated.py 中新增 --diagnostic-map。
2. --diagnostic-map 与 --map-config 互斥。
3. 仍然使用 post-hoc auto calibration，不是正式标定。
4. 使用前 --diagnostic-map-frames 个有效 task 点估计主方向。
5. 主方向估计后 snap 到最近 task x/y 轴，因为 TrackRegion 仍是 axis-aligned AABB union。
6. 支持 shape:
   - cross
   - l_shape
   - t_shape
7. 生成 MapConfig 风格的 track_boxes 和 target_region。
8. scene_auto.json / session/trial_config.json 中保留完整 track_boxes 和 metadata。
9. summary.json / trial_summary.json 写入 diagnostic_map_used、raw_main_direction、snapped_main_direction、snap_angle_degrees 等字段。
10. analyze_session.py 不需要大改，因为它已经支持 track_boxes。
11. 新增 tests/test_offline_replay_diagnostic_map.py。
12. 不修改 BlockController / TrialController / TaskCoordinateSystem / adapter 核心逻辑。

我倾向于第一版以 L 型为主，而不是 cross/T。

请按下面口径调整：

1. --diagnostic-map-shape 默认改为 l_shape。
   cross 和 t_shape 可以保留为可选，但本轮重点保证 l_shape 正确。

2. l_shape 定义：
   - 第一段从 origin_task 开始，沿 snapped_main_direction 延伸 diagnostic_map_main_length。
   - 第二段从第一段末端开始，沿 snapped_perp_direction 延伸 diagnostic_map_perp_length。
   - 两段在 junction 处必须有 junction_overlap 的正体积重叠。
   - target_region 放在第二段末端。

3. 新增参数：
   --diagnostic-map-turn，可选 left / right，默认 left。
   left 表示相对 snapped_main_direction 逆时针 90°。
   right 表示相对 snapped_main_direction 顺时针 90°。

4. 对于 cross：
   如果保留，垂直 corridor 可以以 origin_task 为中心。
   但 cross 不是默认。

5. 对于 t_shape：
   如果保留，垂直分支放在主方向末端。
   但 t_shape 不是默认。

6. 其他规则保持：
   - --diagnostic-map 与 --map-config 互斥
   - 仍然 post-hoc auto calibration
   - farthest point 优先，太短再 PCA
   - main direction snap 到 x/y axis
   - 记录 raw/snapped/snap_angle
   - 生成 MapConfig 风格 track_boxes + target_region + metadata
   - 不改核心 controller / adapter / parser