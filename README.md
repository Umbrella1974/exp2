# Exp2 Constrained Block Experiment Engine

这个仓库实现的是一个“受约束物块交互实验”的核心引擎与离线调试工具链。当前重点不是 GUI 或真实硬件闭环，而是：

- 读取 MANUS/Vive raw JSONL 数据
- 通过 parser / adapter 转成实验输入帧
- 运行 TrialController 和 BlockController
- 输出逐帧 CSV、事件、触觉状态和 summary
- 对离线 session 做可视化分析
- 提供 MapConfig / MapGenerator 作为正式实验地图配置基础层

核心实时数据流大致是：

```text
raw JSONL
  -> JsonlRawFrameSource
  -> parse_raw_manus_vive_frame()
  -> ManusViveExperimentAdapter
  -> ExperimentInputSample
  -> TrialController
  -> BlockController
  -> frames / events / haptic state
```

当前还没有正式 GUI、真实 socket live loop、MANUS SDK、Vive SDK 或触觉硬件控制。`ManusSocketRawFrameSource` 现在只是 receiver 适配壳。

## 环境

在项目根目录运行：

```powershell
cd D:\research_history\first_one\research_code\exp2
```

安装基础依赖：

```powershell
pip install numpy pytest
```

如果要生成 PNG 图：

```powershell
pip install matplotlib
```

没有 `matplotlib` 时，核心 CSV/JSON 输出仍然可用；绘图会跳过并写入 warning。

## 测试

完整测试：

```powershell
pytest -q
```

通过数量会随新增诊断测试变化，以本地 `pytest` 输出为准。

常用局部测试：

```powershell
pytest -q tests/test_offline_replay_autocalibrated.py tests/test_offline_replay_session_output.py
pytest -q tests/test_offline_replay_map_config.py
pytest -q tests/test_offline_replay_diagnostic_map.py
pytest -q tests/test_offline_replay_map_template.py
pytest -q tests/test_batch_offline_replay_report.py
pytest -q tests/test_analyze_session.py
pytest -q tests/test_map_preview.py tests/test_map_template.py
pytest -q tests/test_map_config.py tests/test_map_generator.py
pytest -q tests/test_calibration_geometry.py tests/test_calibration_io.py tests/test_calibration_sampling.py
pytest -q tests/test_calibrate_from_raw_jsonl_table.py tests/test_offline_replay_formal_calibrated.py
pytest -q tests/test_live_raw_stream.py tests/test_run_live_raw_preview.py
pytest -q tests/test_trial_controller.py tests/test_block_controller.py
```

## 最常用流程

如果你现在拿到一包离线 JSONL 数据，建议按这个顺序跑：

1. 先用 `offline_replay_autocalibrated.py` 跑一组单样本，看 pipeline 能不能完整跑通。
2. 加 `--write-session` 生成标准 session 目录。
3. 用 `analyze_session.py` 生成 summary 和图。
4. 多组数据稳定后，把它们写进 batch cases JSON，用 `batch_offline_replay_report.py` 做回归基线。

示例：

```powershell
python offline_replay_autocalibrated.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_14.jsonl --out-dir data\offline_replay\experiment_14_block060 --max-frames 5000 --calibration-frames 100 --scene-mode wide-track --block-size 0.6 --write-session
```

然后分析 session：

```powershell
python analyze_session.py --session-dir data\offline_replay\experiment_14_block060\session --overwrite
```

## `offline_replay_autocalibrated.py`

这个脚本用于未正式标定 raw JSONL 的 post-hoc 离线 replay。它会自动估计临时 task 坐标系，自动生成临时 block/track scene，然后跑现有 parser、adapter、TrialController、BlockController。

它适合 smoke test、参数调试和回归测试，不代表正式实验分析。正式实验仍然需要在线标定和正式 scene/map 配置。

宽通道示例：

```powershell
python offline_replay_autocalibrated.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_14.jsonl --out-dir data\offline_replay\experiment_14_wide --max-frames 5000 --calibration-frames 100 --scene-mode wide-track --block-size 0.6
```

窄通道 blocked 压力测试：

```powershell
python offline_replay_autocalibrated.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_15.jsonl --out-dir data\offline_replay\experiment_15_narrow --max-frames 5000 --calibration-frames 100 --scene-mode narrow-corridor --block-size 0.6 --narrow-track-width 0.08
```

写标准 session：

```powershell
python offline_replay_autocalibrated.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_14.jsonl --out-dir data\offline_replay\experiment_14_block060 --max-frames 5000 --calibration-frames 100 --scene-mode wide-track --block-size 0.6 --write-session
```

默认 session 写到：

```text
out_dir/session
```

也可以显式指定：

```powershell
python offline_replay_autocalibrated.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_14.jsonl --out-dir data\offline_replay\experiment_14_block060 --write-session --session-dir data\offline_sessions\experiment_14_block060 --session-id exp14_block060 --subject-id S001 --notes "offline smoke test"
```

常用参数：

```text
--raw-jsonl                  输入 JSONL，每行一个 raw dict
--out-dir                    输出目录
--max-frames                 最多处理多少帧
--thumb-node                 thumb tip node id，默认 4
--index-node                 index tip node id，默认 9
--tracker-index              Vive tracker index，默认 0
--skeleton-index             MANUS skeleton index，默认 0
--calibration-mode           initial-window 或 pca
--calibration-frames         用前多少个有效点估计临时坐标系，默认 100
--scene-mode                 wide-track / fitted-corridor / narrow-corridor
--block-size                 物块边长，单位按米理解
--track-margin               wide/fitted 场景边界余量
--track-width                fitted-corridor 宽度
--narrow-track-width         narrow-corridor 宽度
--z-tolerance                轨道 z 方向容差
--map-config                 可选，使用 MapConfig JSON 作为 block/track scene
--map-id-override            可选，只覆盖本次 replay 输出中的 map_id
--strict-map-validation      可选，MapConfig warning 也会阻止 replay
--diagnostic-map             可选，根据旧数据轨迹生成诊断 MapConfig scene
--map-template               可选，使用模板地图并按旧数据前段主方向对齐
--template-anchor-frames     模板地图用前多少个有效 task 点估计主方向，默认 100
--diagnostic-map-frames      用前多少个有效 task 点估计主方向，默认 100
--diagnostic-map-shape       cross / l_shape / t_shape，默认 l_shape
--diagnostic-map-turn        left / right，默认 left
--write-session              额外写标准 session 目录
```

重要行为：

- replay 时会强制 `subject_end=False`，避免 raw 中途结束导致后续帧无法观察。
- 离线 replay 基本关闭 trial timeout 和 too-many-detach 限制，方便完整回放一段数据。
- 临时坐标系和临时 scene 都是 post-hoc 结果，不应作为正式实验结论。
- 不传 `--map-config` / `--diagnostic-map` / `--map-template` 时，物块初始中心来自第一帧有效输入点，block/track 由 `--scene-mode` 自动生成。
- 传 `--map-config` 时，物块初始中心、物块尺寸和轨道都来自 MapConfig；`--scene-mode`、`--block-size` 等 auto scene 参数不再决定 block/track 几何。
- `--map-config`、`--diagnostic-map`、`--map-template` 三者互斥。

使用 MapConfig 跑旧 raw JSONL：

```powershell
python offline_replay_autocalibrated.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_14.jsonl --out-dir data\offline_replay\exp14_map_xoy_turn --max-frames 5000 --calibration-frames 100 --map-config maps\examples\xoy_turn.json --write-session
```

然后用 analyzer 查看多段地图可视化：

```powershell
python analyze_session.py --session-dir data\offline_replay\exp14_map_xoy_turn\session --overwrite
```

注意：这个流程使用配置地图，但 calibration 仍然是 post-hoc auto，不是正式实验。它适合检查真实数据在指定 MapConfig 地图中的 pipeline 表现和可视化，不应作为正式实验结果。

MapConfig replay 行为：

- `validation.errors` 会阻止 replay。
- 默认 `validation.warnings` 不阻止 replay，会写入 `summary.json`、`scene_auto.json`、`session/trial_config.json` 和 `session/trial_summary.json`。
- 如果传 `--strict-map-validation`，warnings 也会阻止 replay。
- `--map-id-override` 只影响本次输出，不修改原始 map JSON；输出 metadata 会保留 `original_map_id` 和 `map_id_overridden`。
- 使用 `--map-config` 时，`session_meta.json` 和 `calibration.json` 仍标记 `calibration_type=post_hoc_auto`、`is_formal_calibration=false`，`trial_config.json` 和 `scene_auto.json` 标记 `scene_type=map_config`、`is_formal_scene=false`。

MapConfig replay 会在 `summary.json`、`scene_auto.json`、`session/trial_config.json` 和 `session/trial_summary.json` 中记录：

```text
map_config_used
map_id
original_map_id
map_id_overridden
map_config_path
map_config_version
map_source_type
track_box_count
target_region_present
strict_map_validation
map_validation_errors
map_validation_warnings
```

使用 trajectory-aligned diagnostic map 跑旧 raw JSONL：

```powershell
python offline_replay_autocalibrated.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_14.jsonl --out-dir data\offline_replay\exp14_diag_lshape --max-frames 5000 --calibration-frames 100 --diagnostic-map --diagnostic-map-shape l_shape --diagnostic-map-turn left --write-session
```

然后分析：

```powershell
python analyze_session.py --session-dir data\offline_replay\exp14_diag_lshape\session --overwrite
```

`--diagnostic-map` 是旧数据诊断工具。它使用前 `--diagnostic-map-frames` 个有效 task 点估计主方向，然后生成 axis-aligned task-space MapConfig。因为当前 `TrackRegion` 是 AABB union，第一版会把主方向 snap 到最近的 `x+ / x- / y+ / y-`。默认 shape 是 `l_shape`：先沿 snapped 主方向走一段，再按 `--diagnostic-map-turn` 转向，target 放在第二段末端。

diagnostic map 输出额外看：

```text
diagnostic_map_used
diagnostic_map_id
diagnostic_map_shape
diagnostic_map_frames
diagnostic_map_main_length
diagnostic_map_perp_length
diagnostic_map_width
diagnostic_map_z_tolerance
diagnostic_map_turn
raw_main_direction
snapped_main_direction
snapped_perp_direction
snap_angle_degrees
```

注意：diagnostic map 是 data-driven post-hoc diagnostic map，不是正式实验地图，也不是正式实验标定。

使用 template map 跑旧 raw JSONL：

```powershell
python offline_replay_autocalibrated.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_14.jsonl --out-dir data\offline_replay\exp14_template_l --max-frames 5000 --calibration-frames 100 --map-template maps\templates\template_l.json --template-anchor-frames 100 --write-session
```

`--map-template` 用于旧数据诊断和地图探索。模板 JSON 写在局部 template 坐标系里，默认主方向是 `x+`；replay 会先做 post-hoc auto calibration，再用前 `--template-anchor-frames` 个有效 task 点估计主方向并 snap 到 `x+ / x- / y+ / y-`，最后把整个模板旋转和平移成标准 MapConfig。

template map 输出额外看：

```text
map_template_used
template_id
template_anchor_frames
raw_main_direction
snapped_main_direction
snap_angle_degrees
track_box_count
target_region_present
map_validation_errors
map_validation_warnings
```

注意：template map 仍是 post-hoc diagnostic map，不是正式实验地图。

## Stage 5A 正式桌面三线标定

Stage 5A 新增的是“正式 calibration 文件格式 + 离线验证工具”，不是 live calibration GUI。它解决的问题是：只用 `origin + x axis` 不足以稳定定义真实桌面实验坐标系，因为桌面可能相对 SteamVR world 有倾斜；正式流程需要同时估计桌面平面法向，也就是 task `up`，再由 `x` 和 `up` 构造正交的 `y`。

桌面三线设计：

- `origin`：静止采样，默认 5 秒，用来确定 task 原点。
- `long_axis_line`：沿桌面长边移动采样，定义 task `x_axis_world`。
- `width_axis_line`：沿桌面宽边移动采样，只做正交质量检查，不翻转 `y`。
- `diagonal_line`：沿对角线移动采样，用来检查线段是否在同一平面内，以及对角线是否既不像 x 也不像 y。

坐标系构造规则：

```text
up_axis_world = fitted table plane normal, flipped toward --up-hint
x_axis_world = long_axis_line.direction_world projected to table plane
y_axis_world = normalize(cross(up_axis_world, x_axis_world))
```

`width_axis_line` 不决定 `y` 的正负方向。即使宽边线方向和构造出的 `y_axis_world` 相反，也只记录角度/quality warning，不改变坐标系。

### 生成 `calibration.json`

`calibrate_from_raw_jsonl_table.py` 用已有 raw JSONL 模拟正式采样窗口，主要用于测试 calibration 文件格式和质量指标。它不是真正 live calibration。

```powershell
python calibrate_from_raw_jsonl_table.py --raw-jsonl path\to\raw_frames.jsonl --origin-start-frame 100 --long-line-start-frame 300 --width-line-start-frame 600 --diagonal-line-start-frame 900 --sample-duration-seconds 5 --out data\calibration\table_line_calibration.json
```

如果 raw timestamp 是毫秒，保持默认即可：

```text
--timestamp-scale 0.001
```

如果你更想按帧数截窗口，可以传：

```powershell
python calibrate_from_raw_jsonl_table.py --raw-jsonl path\to\raw_frames.jsonl --origin-start-frame 100 --long-line-start-frame 300 --width-line-start-frame 600 --diagonal-line-start-frame 900 --sample-window-frames 150 --out data\calibration\table_line_calibration.json
```

常用参数：

```text
--raw-jsonl                    输入 raw JSONL
--out                          输出 calibration JSON
--calibration-id               可选，自定义 calibration id
--origin-start-frame           origin 采样起始帧
--long-line-start-frame        长边线采样起始帧
--width-line-start-frame       宽边线采样起始帧
--diagonal-line-start-frame    对角线采样起始帧
--sample-duration-seconds      默认 5.0
--sample-window-frames         可选；传入后优先按帧数取窗口
--point-source                 tracker_position_world 或 pinch_center_world，默认 tracker_position_world
--timestamp-scale              默认 0.001
--up-hint                      默认 0,0,1
--min-samples                  默认 10
--min-line-length              默认 0.10 m
--notes                        可选备注
```

输出文件会包含：

```text
calibration_type = formal_table_lines
is_formal_calibration = true
origin / long_axis_line / width_axis_line / diagonal_line
origin_world / x_axis_world / y_axis_world / up_axis_world
task_coordinate_system
quality
warnings
metadata
```

第一版 quality 阈值：

```text
origin max deviation > 0.02 m -> warning
line fit rmse > 0.02 m -> warning
plane fit rmse > 0.02 m -> warning
long/width angle deviation from 90 deg > 10 deg -> warning
long/width angle deviation from 90 deg > 25 deg -> error
diagonal angle to x or y < 10 deg or > 80 deg -> warning
```

warning 不阻止保存；error 会阻止保存。这个 raw JSONL 版本会在 metadata 中标明它只是 replayed raw JSONL calibration test，不应当当成 live formal calibration。

### 使用正式 calibration 做离线 replay

`offline_replay_formal_calibrated.py` 使用 `calibration.json + map_config.json` 跑旧 raw JSONL。它不做 post-hoc auto calibration，但仍然是 offline replay，不是 live formal trial。

```powershell
python offline_replay_formal_calibrated.py --raw-jsonl path\to\raw_frames.jsonl --calibration-json data\calibration\table_line_calibration.json --map-config maps\examples\xoy_turn.json --out-dir data\offline_replay_formal\test --write-session
```

常用参数：

```text
--raw-jsonl           输入 raw JSONL
--calibration-json    formal_table_lines calibration JSON
--map-config          MapConfig JSON
--out-dir             输出 frames.csv / events.csv / summary.json
--max-frames          可选，最多处理多少帧
--write-session       额外写标准 session 目录
--session-dir         可选，自定义 session 目录
--session-id          可选
--subject-id          可选
--notes               可选
--thumb-node          默认 4
--index-node          默认 9
--tracker-index       默认 0
--skeleton-index      默认 0
--timestamp-scale     默认 0.001
```

输出目录包含：

```text
frames.csv
events.csv
summary.json
calibration_formal.json
scene_map_config.json
```

如果传 `--write-session`，`session_meta.json` 会明确写：

```text
mode = offline_formal_calibrated_replay
calibration_type = formal_table_lines
is_formal_calibration = true
is_live_trial = false
scene_type = map_config
is_formal_scene = false
```

这里不会写 `post_hoc_auto` warning；但会保留一句 warning：它是使用正式 calibration 文件的 offline replay，不是 live formal trial。

## Stage 5B-0 Live Raw Stream Preview

Stage 5B-0 是实时 raw stream smoke test，用来验证 `manus_vive_com` 发来的 newline-delimited combined JSON 能否被 exp2 实时接收、解析、转换并记录健康指标。它不是正式实验流程：

- 不做 calibration。
- 不启动 `TrialController`。
- 不运行 `BlockController`。
- 不接 haptic hardware。
- 不写 GUI。
- 不修改 `manus_vive_com` 仓库。

启动 exp2 receiver：

```powershell
python run_live_raw_preview.py --host 127.0.0.1 --port 8888 --out-dir data\live_raw_preview\test
```

然后启动 `manus_vive_com` 的 C++ client，让它连接 `127.0.0.1:8888` 并发送 combined JSON。协议是每行一个 JSON object，以 `\n` 结尾。

常用参数：

```text
--host                  默认 127.0.0.1
--port                  默认 8888
--duration-seconds      可选，到时停止
--max-frames            可选，处理到指定帧数停止
--thumb-node            默认 4
--index-node            默认 9
--tracker-index         默认 0
--skeleton-index        默认 0
--timestamp-scale       默认 0.001
--out-dir               默认 data/live_raw_preview
--write-session         额外写标准 session 目录
--session-dir           可选
--print-every           默认 30
--save-raw-jsonl        默认开启
--no-save-raw-jsonl     不保存 out_dir/raw_frames.jsonl
--max-queue-size        默认 300
```

输出：

```text
out_dir/raw_frames.jsonl
out_dir/live_metrics.csv
out_dir/live_summary.json
```

`live_metrics.csv` 每个合法 raw JSON object 写一行，包含：

```text
frame_index
raw_timestamp
receive_time_monotonic
receive_wall_time
parse_ok
adapter_ok
tracker_valid
hand_valid
pinch_valid
pinch_distance
skeleton_count
tracker_count
sync_delta_ms
inter_receive_interval_ms
processing_latency_ms
queue_size
dropped_frame_count
error_message
```

bad JSON 指 TCP stream 里某一行不是合法 JSON object，例如 `{bad json}`、截断 JSON 或 `[1, 2, 3]`。这类行不写入逐帧 metrics，也不保存整行大 payload；只在 summary 里计入：

```text
parse_error_count
bad_json_line_count
last_parse_error_message
last_bad_json_preview
```

队列满时默认丢弃最旧帧，保留最新实时状态：

```text
queue_drop_policy = drop_oldest_when_full
```

client 断开后第一版直接结束，不自动重连。`live_summary.json` 会记录：

```text
stop_reason = client_disconnected / max_frames / duration_reached / keyboard_interrupt / socket_error
```

如果使用 `--write-session`，session 会写：

```text
session_meta.json
raw_frames.jsonl
device_frames.jsonl
trial_summary.json
live_summary.json
```

`SessionRecorder` 会创建空的 `processed_frames.csv / events.csv / haptic.csv`，但 live raw preview 不会伪造 TrialController 输出。`session_meta.json` 会明确记录：

```text
mode = live_raw_preview
is_live_trial = false
trial_controller_started = false
processed_frames_are_trial_outputs = false
```

## `analyze_session.py`

这个脚本只读取已记录的 session 目录，生成 `analysis_summary.json` 和可选 PNG 图。它不会重新运行 TrialController / BlockController，也不会写回 `events.csv`。

运行：

```powershell
python analyze_session.py --session-dir data\offline_replay\experiment_14_block060\session --overwrite
```

只生成 summary，不画图：

```powershell
python analyze_session.py --session-dir data\offline_replay\experiment_14_block060\session --no-plots --overwrite
```

常用参数：

```text
--session-dir          必填，session 目录
--out-dir              可选，只改变 PNG 输出目录
--no-plots             不生成 PNG，只写 analysis_summary.json
--event-label-limit    图中最多标注多少事件文字，默认 40
--max-footprint-overlays slip/blocked footprint 最多各抽样多少个，默认 20
--overwrite            覆盖 analysis_summary.json 和本脚本生成的 PNG
--time-column          sample_time / trial_time / raw_timestamp
--relative-time        使用相对时间轴，默认开启
--absolute-time        使用原始时间列作为图像横轴
```

时间列选择规则：

```text
优先使用 --time-column 指定列
如果该列缺失或全为空，则 fallback:
sample_time -> trial_time -> raw_timestamp -> frame_index
```

输出：

```text
session/analysis_summary.json
session/plots/timeseries_xyz_with_events.png
session/plots/pinch_distance_with_events.png
session/plots/trajectory_track_map.png
session/plots/trajectory_track_map_with_block_footprint.png
session/plots/state_timeline.png
session/plots/haptic_timeline.png
```

默认情况下，图像横轴使用相对时间：

```text
selected_time - first_finite_selected_time
```

`analysis_summary.json` 会记录：

```text
time_column_used
time_axis_mode
time_zero
time_axis_label
```

如果需要保留旧的绝对时间横轴，使用：

```powershell
python analyze_session.py --session-dir data\offline_replay\experiment_14_block060\session --absolute-time --overwrite
```

`trajectory_track_map.png` 会优先使用 `trial_config.json` 里的 `track_boxes` 画多段轨道，并额外画：

```text
target_region
block_initial_center_task
pinch path
block path
blocked / slip / haptic event points
```

`trajectory_track_map_with_block_footprint.png` 在同一张地图基础上额外画物块 footprint：

```text
configured block initial footprint
block start footprint
block end footprint
sampled slip frame block footprints
sampled blocked frame block footprints
```

footprint 由 `block_center_task + block_size` 重建 x-y AABB。当前 `TrackRegion` 的语义是 `block_center_feasible_region`：轨道限制的是物块中心可行区域，不代表完整物块 footprint 必须完全在轨道里。`analysis_summary.json` 会记录 `track_region_semantics`、`block_footprint_overlay_count`、`slip_footprint_overlay_count`、`blocked_footprint_overlay_count`。

Analyzer 还会做一个只读的 slip consistency diagnostic：对 `slip_active=True` 且几何字段齐全的帧，检查 `pinch_center_task` 是否在重建的 block AABB 内。结果写入：

```text
slip_frame_count
slip_frames_with_geometry_check
slip_frames_pinch_inside_block_count
slip_frames_pinch_outside_block_count
```

Analyzer 还会做 `block end diagnostic`，用于解释图里的 `block end` 为什么停在那个位置。这里的 `block end` 不是事件，而是 `processed_frames.csv` 中最后记录的 block center；诊断会找最后一次真实移动帧，以及之后第一帧不再移动的直接原因。常看字段：

```text
block_end_frame_index
block_end_position_task
block_last_moved_frame_index
block_first_stop_frame_index
block_end_reason
block_end_subreason
block_end_explanation
block_end_nearby_frames
block_end_movement_epsilon
block_end_secondary_signals
```

`block_end_reason` 常见值包括 `contact_exit`、`pinch_insufficient`、`track_blocked`、`tracking_invalid`、`recording_ended_while_moving`、`no_block_movement_detected`。`block_end_explanation` 是 analyzer 根据已有 CSV 字段做的 reconstructed diagnostic，不代表重新运行状态机。`block_end_secondary_signals` 第一版是可选补充字段，可能为空；主结论只以 `block_end_reason`、`block_end_subreason` 和 `block_end_explanation` 为准。

如果 session 没有 `track_boxes`，会回退到旧的 bounds 字段，例如：

```text
track_bounds_task
track_bounds
track_region
bounds
scene_auto.track_bounds
```

识别不了边界时只画轨迹并写 warning，不会让分析失败。

## `map_preview.py`

这个脚本不依赖 replay，也不读取 raw JSONL。它直接读取一个 MapConfig JSON，验证后输出 x-y 地图预览图，用来检查地图结构、track_boxes、target_region 和 configured block 是否符合预期。

```powershell
python map_preview.py --map-config maps\examples\xoy_turn.json --out data\map_preview\xoy_turn.png --summary-out data\map_preview\xoy_turn_summary.json
```

常用参数：

```text
--map-config               必填，MapConfig JSON
--out                      输出 PNG，默认 map_preview.png
--summary-out              可选，输出 validation summary JSON
--show-target-region       默认 true，可用 --no-show-target-region 关闭
--show-box-labels          默认 true，可用 --no-show-box-labels 关闭
--show-box-order           默认 true，可用 --no-show-box-order 关闭
--show-configured-block    默认 true，可用 --no-show-configured-block 关闭
--annotate-centers         可选，标出 box 中心点
--padding                  图像边界留白比例，默认 0.1
--title                    可选标题
```

## `batch_offline_replay_report.py`

这个脚本用于批量跑多组 raw JSONL，并生成稳定的 regression 报告。它内部通过 subprocess 调用 `offline_replay_autocalibrated.py`，尽量不改变 replay 脚本本身。

运行示例：

```powershell
python batch_offline_replay_report.py --cases data\offline_replay_batch\cases_example.json --out-dir data\offline_replay_batch --overwrite
```

如果希望一旦 FAIL/ERROR 就返回非零退出码：

```powershell
python batch_offline_replay_report.py --cases data\offline_replay_batch\cases_example.json --out-dir data\offline_replay_batch --overwrite --stop-on-fail
```

输出：

```text
data/offline_replay_batch/batch_summary.csv
data/offline_replay_batch/batch_summary.json
data/offline_replay_batch/<case_id>/summary.json
data/offline_replay_batch/<case_id>/frames.csv
data/offline_replay_batch/<case_id>/events.csv
```

`--overwrite` 只会清理当前 `case_id` 对应的输出子目录，不会删除整个 `out_dir`。

cases JSON 示例结构：

```json
{
  "cases": [
    {
      "id": "positive_moving_experiment_14",
      "raw_jsonl": "D:/download_edge/dataansys/raw_frames_15_parts/experiment_14.jsonl",
      "description": "Positive moving sample",
      "args": {
        "calibration_mode": "initial-window",
        "calibration_frames": 100,
        "scene_mode": "wide-track",
        "block_size": 0.6
      },
      "expectations": [
        {"metric": "moving_frame_count", "op": ">=", "value": 1},
        {"metric": "large_delta_frame_count", "op": "==", "value": 0}
      ]
    }
  ]
}
```

支持的 expectation 操作：

```text
==
!=
>
>=
<
<=
between
```

`case_id` 只能包含字母、数字、下划线、短横线和点，避免误删路径。

注意：当前 batch runner 的 case args 还没有透传 `map_config`、`map_template`、`map_id_override`、`strict_map_validation`。如果要批量跑 MapConfig/template replay，需要先扩展 `batch_offline_replay_report.py` 的 supported case args；单次地图 replay 请直接使用 `offline_replay_autocalibrated.py --map-config` 或 `--map-template`。

## `run_live_preview.py`

这个脚本是轻量 preview/smoke 工具，用于快速检查一个 JSONL 文件能否跑通 parser/adapter/trial/block pipeline。它不是批量离线评估工具。

```powershell
python run_live_preview.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_15.jsonl --max-frames 1000 --print-every 10 --trial-id smoke_001
```

常用参数：

```text
--raw-jsonl         输入 JSONL
--max-frames       最多处理多少帧
--print-every      每隔多少帧打印一次
--trial-id         trial id，默认 preview
--timestamp-scale  timestamp 转秒比例，默认 0.001
--calibration      可选 calibration JSON
```

文件输入可以用 `for frame in source`，因为 JSONL 有 EOF。以后真实 socket/live source 不要用 `for`，因为 socket 的 `None` 表示“当前没有新帧”，不是数据结束；实时流应该持续调用 `next_frame()`。

## Session 输出

`offline_replay_autocalibrated.py --write-session` 会生成：

```text
session_meta.json
calibration.json
trial_config.json
raw_frames.jsonl
device_frames.jsonl
processed_frames.csv
events.csv
haptic.csv
trial_summary.json
plots/
```

`raw_frames.jsonl` 保存原始 raw dict，不额外写 `frame_index`，不改字段。

`device_frames.jsonl` 保存 parser 后的 DeviceFrame 摘要，包括 timing 字段：

```text
combined_monotonic_ms
skeleton_receive_monotonic_ms
tracker_receive_monotonic_ms
sync_delta_ms
skeleton_callback_index
tracker_callback_index
tracker_last_update_time
```

`processed_frames.csv` 是最常用的逐帧状态表，常看字段：

```text
sample_time
trial_time
tracker_valid
hand_valid
pinch_valid
input_source
pinch_distance
pinch_center_world_*
pinch_center_task_*
block_center_task_*
contact_state
pinch_state
block_motion_state
stop_reason
track_state
detach_state
slip_active
slip_reason
blocked_force_active
large_delta
```

离线 replay 中如果前几帧在 trial start time 之前，`trial_time` 可能为负数。这通常表示 pre-roll 数据，不是状态机错误。

## 怎么看结果

优先看 `summary.json` 或 `analysis_summary.json`：

```text
valid_input_frames
valid_pinch_frames
tracker_invalid_frame_count
invalid_input_frame_count
generated_contact_enter_count
generated_contact_exit_count
moving_frame_count
slip_active_frame_count
blocked_frame_count
large_delta_frame_count
haptic_active_frame_count
haptic_event_count
logical_slip_feedback_frame_count
logical_blocked_feedback_frame_count
slip_reason_counts
logical_slip_due_to_pinch_insufficient_count
logical_slip_due_to_track_blocked_count
blocked_force_active_count
hardware_haptic_active_frame_count
hardware_haptic_event_count
pinch_distance_min / mean / max
task_trajectory_range
block_displacement_task
warnings
```

当前字段可以区分“逻辑上的 pinch insufficient slip”和“逻辑上的 track blocked slip”：看 `slip_active=True` 帧里的 `slip_reason`。`PINCH_INSUFFICIENT` 表示 pinch 距离不足导致的逻辑 slip；`TRACK_BLOCKED` 表示轨道约束阻挡导致的逻辑 slip。`blocked_force_active=True` 只对应 track blocked 的逻辑反馈。限制是：当前字段能说明状态机给出的逻辑原因，但没有记录连续的 boundary distance，所以不能单靠字段证明“离边界几厘米”。

`haptic_active_frame_count` / `hardware_haptic_active_frame_count` 表示命令或硬件层 active，不等于逻辑 slip。离线 replay 中常见情况是 `logical_slip_feedback_frame_count` 大于 0，但没有真实硬件发送记录。`slip_active`、`slip_reason`、`blocked_force_active` 是逻辑反馈状态；`haptic_state`、`haptic_reason`、`command_type` 更接近 haptic 命令/记录层。

MapConfig replay 额外看：

```text
map_config_used
map_id
original_map_id
map_id_overridden
map_config_path
map_config_version
map_source_type
track_box_count
target_region_present
strict_map_validation
map_validation_errors
map_validation_warnings
```

Diagnostic map replay 额外看：

```text
diagnostic_map_used
diagnostic_map_id
diagnostic_map_shape
diagnostic_map_turn
raw_main_direction
snapped_main_direction
snapped_perp_direction
snap_angle_degrees
```

Template map replay 额外看：

```text
map_template_used
template_id
raw_main_direction
snapped_main_direction
snap_angle_degrees
```

判断物块是否真的移动，优先看 `frames.csv` 或 `processed_frames.csv`：

```text
pinch_state
contact_state
block_motion_state
stop_reason
block_center_task_x/y/z
```

常见组合：

```text
PINCH_VALID + INSIDE_BLOCK + GRABBED_MOVING
```

表示抓取有效、接触物块、物块正在移动。

```text
PINCH_INSUFFICIENT + INSIDE_BLOCK + GRABBED_PINCH_INSUFFICIENT
```

表示手在物块里，但 pinch 距离不满足抓取阈值。

```text
PINCH_VALID + OUTSIDE_BLOCK
```

表示 pinch 有效，但没有接触物块，不应移动物块。

```text
GRABBED_BLOCKED 或 stop_reason=TRACK_BLOCKED
```

表示通道约束阻挡了移动。

`task_trajectory_range` 是输入点在 task 坐标系下的范围，不是物块轨迹。物块轨迹看 `block_center_task_x/y/z` 或 `block_displacement_task`。

## MapConfig 和 MapGenerator

MapConfig 是正式实验地图配置的基础层。它把手写 JSON 或规则生成的多段 axis-aligned box 轨道编译成 BlockController 已经支持的 `TrackRegion`。

示例地图：

```text
maps/examples/xoy_straight.json
maps/examples/xoy_turn.json
maps/examples/xoy_two_turns.json
```

加载并验证：

```python
from map_config import load_map_config, validate_map_config

config = load_map_config("maps/examples/xoy_two_turns.json")
result = validate_map_config(config)
print(result.is_valid, result.errors, result.warnings)
```

编译成 `TrackRegion`：

```python
from map_config import compile_map_to_track_region

track_region, block_center, block_size = compile_map_to_track_region(config)
```

写入 session/trial config：

```python
from map_config import map_config_to_trial_config

trial_config = map_config_to_trial_config(config)
```

`trial_config` 会包含：

```text
map_config_version
map_id
map_source_type
description
coordinate_space
unit
block_initial_center_task
block_size
track_boxes
target_region
metadata
is_generated
```

地图校验规则：

- `coordinate_space` 必须是 `task`。
- `unit` 必须是 `m`。
- `block_initial_center_task` 必须在至少一个 track box 内。
- 如果任意 `track_box.order` 存在，则所有 track box 都必须有 order。
- order 不能重复，必须是连续的 `0..n-1`。
- 相邻 ordered boxes 必须有正体积重叠，或至少是有正面积的面接触。
- gap、仅边接触、仅点接触都会报错。
- `target_region` 必须和至少一个 track box 有正体积相交。
- `target_region` 仅面接触会 warning，仅边/点接触会 error。

规则生成 x-y 平面正交轨道：

```python
from map_generator import generate_orthogonal_corridor_map

config = generate_orthogonal_corridor_map(
    map_id="demo_corridor",
    seed=7,
    num_segments=4,
    start=[0.0, 0.0, 0.0],
    initial_direction="x+",
    segment_length_range=(0.4, 0.8),
    track_width=0.2,
    z_tolerance=0.1,
    allowed_turns=["left", "right", "straight"],
    target_length=None,
)
```

当前 generator 只支持 `plane="xoy"`，只支持 `left` / `right` / `straight`，转弯都是 90 度。不支持斜向、任意角度、180 度掉头或 3D maze。

生成器会生成独立的 `target_region`，默认长度是：

```text
min(0.20, last_segment_length * 0.25)
```

如果传入的 `target_length` 超过最后一段长度，会 clamp 并在 metadata warning 中记录。

注意：`offline_replay_autocalibrated.py --map-config` 可以把旧 raw JSONL 跑在 MapConfig 地图中，但 calibration 仍然是 post-hoc auto，所以这个流程仍是调试/可视化用途，不是正式实验结果。

### Template Map

`map_template.py` 支持一种诊断用模板地图：JSON 里写 template 局部坐标系下的完整结构，默认主方向为 `x+`。离线 replay 时用旧数据前若干有效 task 点估计第一段主方向，snap 到 task 坐标轴，然后把模板整体做 90 度倍数旋转和平移，生成标准 MapConfig。

最小模板结构：

```json
{
  "template_id": "template_l",
  "coordinate_space": "template",
  "unit": "m",
  "anchor_direction": "x+",
  "block_initial_center_template": [0.0, 0.0, 0.0],
  "block_size": [0.2, 0.2, 0.2],
  "track_boxes": [],
  "target_region": null,
  "metadata": {}
}
```

限制：第一版只支持 x-y 平面上的 `x+ / x- / y+ / y-` 方向和 90 度倍数旋转，不支持斜向 corridor 或任意角度旋转。

## YAML 现在怎么用

`config_example.yaml` 目前只是模板和记录文件，代码不会自动读取它。修改 YAML 不会自动影响：

```text
run_live_preview.py
offline_replay_autocalibrated.py
batch_offline_replay_report.py
analyze_session.py
```

现在需要手动把 YAML 里的值对应到命令行参数，或者写进 batch cases JSON / map JSON。

例如 YAML 里的：

```yaml
preview:
  raw_jsonl: "D:/download_edge/dataansys/raw_frames_15_parts/experiment_01.jsonl"
  trial_id: "smoke_001"
  max_frames: 1000
  print_every: 10
  timestamp_scale: 0.001
```

对应命令行：

```powershell
python run_live_preview.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_01.jsonl --trial-id smoke_001 --max-frames 1000 --print-every 10 --timestamp-scale 0.001
```

如果想改离线 replay 的物块大小，当前改命令行：

```powershell
--block-size 0.6
```

如果想改正式地图里的物块初始位置和大小，应改 map JSON：

```json
{
  "block_initial_center_task": [0.0, 0.0, 0.0],
  "block_size": [0.2, 0.2, 0.2]
}
```

如果使用 `offline_replay_autocalibrated.py --map-config path/to/map.json`，则 replay 的 block 初始位置、物块尺寸和轨道来自该 MapConfig；否则仍使用自动 scene。

## Calibration JSON

`run_live_preview.py --calibration` 接收 JSON，不是 YAML。格式由 `calibration_io.py` 定义：

```json
{
  "origin_world": [0.0, 0.0, 0.0],
  "x_axis_world": [1.0, 0.0, 0.0],
  "y_axis_world": [0.0, 1.0, 0.0],
  "z_axis_world": [0.0, 0.0, 1.0],
  "metadata": {
    "up_axis_world": [0.0, 0.0, 1.0]
  }
}
```

上面是旧的轻量 task calibration 格式，主要给 preview/smoke 工具用。Stage 5A 新增的正式桌面三线 calibration 也是 JSON，但字段更多，`calibration_type=formal_table_lines`，包含 origin、三条采样线、plane fit 和 quality 指标。生成它请使用：

```powershell
python calibrate_from_raw_jsonl_table.py --raw-jsonl path\to\raw_frames.jsonl --origin-start-frame 100 --long-line-start-frame 300 --width-line-start-frame 600 --diagonal-line-start-frame 900 --out data\calibration\table_line_calibration.json
```

`offline_replay_autocalibrated.py` 不读 calibration JSON。它会根据 raw 数据前一段有效输入点自动估计临时 task 坐标系：

- `initial-window`：以前若干有效点中的最远点估计 x 方向。
- `pca`：用前若干有效点的 PCA 第一主方向估计 x 方向。

如果要用正式 calibration JSON 跑旧数据，请使用 `offline_replay_formal_calibrated.py --calibration-json ... --map-config ...`。

## 常见问题

### 一直 timeout

优先检查 timestamp 单位。`run_live_preview.py` 默认 `--timestamp-scale 0.001`，也就是把 raw timestamp 当毫秒。如果 raw timestamp 已经是秒，应使用：

```powershell
--timestamp-scale 1.0
```

`offline_replay_autocalibrated.py` 当前基本关闭 trial timeout，通常不应因为正常 replay 中途 timeout。

### `tracker_valid=False` 很多

检查 raw 中是否有 `trackers`，`tracker_index` 是否选错，tracker 是否有 `position`，以及 valid 字段是否为 false。

### `hand_valid=True` 但 pinch 无效

检查 `thumb_node`、`index_node`、`skeleton_index`，以及对应 node 是否有 3D `position`。

### `PINCH_INSUFFICIENT` 很多

当前默认阈值在 `config.py`：

```text
pinch_grab_threshold = 0.025
pinch_release_threshold = 0.035
```

单位按米理解。如果真实 pinch distance 常见值远大于 0.035，需要重新评估 node、坐标尺度或阈值。

### 图里轨迹为什么同一数据不同参数会不一样

`offline_replay_autocalibrated.py` 的 `task_trajectory_xyz.png` 画的是输入点在临时 task 坐标系下的轨迹。不同 `calibration_mode`、`calibration_frames`、scene 参数可能让 task 坐标系或物块行为不同，所以结果可能不一样。

如果你看的是 session analyzer 的 `trajectory_track_map.png`，它同时画 pinch path、block path、轨道、target 和事件点。物块路径当然会随 block size / track width / scene mode 改变。

## 当前限制

```text
没有正式 GUI
没有真实 socket live loop
没有触觉硬件控制
没有自动读取 YAML 配置
没有完整 MANUS/Vive rotation fusion
没有正式在线 calibration GUI/实时采样流程；Stage 5A 只有离线格式测试和 formal replay
offline_replay_autocalibrated.py 的 MapConfig replay 仍使用 post-hoc auto calibration，不能标记成正式实验
offline_replay_formal_calibrated.py 使用 formal calibration，但仍是 offline replay，不是 live formal trial
run_live_raw_preview.py 只是实时 raw stream smoke test，不启动正式 trial
MapTemplate replay 是旧数据诊断/探索工具，不是正式实验地图
```

比较自然的下一步是：把 MapConfig 接入正式在线 trial/session 配置路径，并实现正式受试者标定流程。
