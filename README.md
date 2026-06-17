# Exp2 Constrained Block Experiment Engine

这个仓库实现的是一个“受约束物块交互实验”的核心引擎、离线分析工具链和第一版 live 调试流程。当前已经可以：

- 读取 MANUS/Vive raw JSONL，或接收 `manus_vive_com` 发送的 newline-delimited combined JSON 实时流
- 通过 parser / adapter 生成实验输入帧，并运行 `TrialController` / `BlockController`
- 使用正式桌面三线标定、MapConfig 地图和 integrated live session 连续完成单次 trial
- 输出标准 session、逐帧状态、事件、逻辑 haptic、timing diagnostics 和 summary
- 对 session 做离线可视化、批量 replay、输出校验和 block end / slip 诊断
- 使用 replay/live debug GUI、visual profile 和非硬件 cue sink 调试视觉与提示语义

离线数据流大致是：

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

live integrated 数据流大致是：

```text
manus_vive_com combined JSON stream
  -> LiveRawStreamServer
  -> LatestFrameBuffer
  -> live calibration / LiveTrialRunner
  -> TrialController / BlockController
  -> SessionRecorder + DashboardSnapshot + CueRuntime
```

当前定位仍是研究开发和实机调试系统，不是正式实验产品：还没有正式 experiment lifecycle GUI、真实触觉硬件控制、完整实验序列，也不直接依赖 MANUS/Vive SDK。`ManusSocketRawFrameSource` 仍只是 receiver 适配壳；当前真实 TCP 接收流程使用 `LiveRawStreamServer`。

## 环境

在项目根目录运行：

```powershell
cd D:\research_history\first_one\research_code\exp2
```

安装基础依赖：

```powershell
pip install numpy pytest
```

如果要读取 YAML cue / termination config：

```powershell
pip install PyYAML
```

如果要生成 PNG 图：

```powershell
pip install matplotlib
```

没有 `matplotlib` 时，核心 CSV/JSON 输出仍然可用；绘图会跳过并写入 warning。

如果要打开 replay/live debug GUI：

```powershell
pip install PySide6 pyqtgraph
```

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
pytest -q tests/test_latest_frame_buffer.py tests/test_live_trial_runner.py tests/test_live_integrated_session.py
pytest -q tests/test_latest_snapshot_store.py tests/test_debug_view_model.py tests/test_replay_debug_runner.py
pytest -q tests/test_cue_config.py tests/test_cue_feedback.py
pytest -q tests/test_timing_diagnostics.py tests/test_analyze_timing.py tests/test_validate_session_outputs.py
pytest -q tests/test_trial_controller.py tests/test_block_controller.py
```

## 最常用流程

### 离线 JSONL 回归

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

### 实机 integrated trial

实机调试时，优先使用同一个进程内连续完成标定和 trial 的 integrated runner，避免重新连接或重新加载标定带来的坐标系不一致：

```powershell
python run_live_integrated_session.py ^
  --map-config maps\examples\xoy_turn.json ^
  --host 127.0.0.1 ^
  --port 8888 ^
  --out-dir data\live_integrated_session\debug_01 ^
  --subject-id S001 ^
  --trial-id trial_001
```

然后启动 `manus_vive_com` 发送端连接 `127.0.0.1:8888`。如果要观察 task view，增加 `--gui`；如果要调试非硬件提示，使用 `--cue-sink logging|console|gui_text`。

### Session 复盘与校验

live integrated session 生成后，推荐依次运行：

```powershell
python validate_session_outputs.py --session-dir data\live_integrated_session\debug_01\session
python analyze_session.py --session-dir data\live_integrated_session\debug_01\session --overwrite
python analyze_timing.py --session-dir data\live_integrated_session\debug_01\session
```

offline session 通常只需要运行 `analyze_session.py`。`validate_session_outputs.py` 主要用于 live integrated session 一致性校验；`analyze_timing.py` 只适用于存在 `timing_diagnostics.csv` 的 session。

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

## Stage 5B-1 Live Table-Line Calibration

`live_calibrate_table.py` 是命令行版 live calibration runner。它只采集四段标定动作并生成 `formal_table_lines` 的 `calibration.json`，不启动 `TrialController`，不运行 `BlockController`，不创建地图，也不接 haptic hardware。

四段动作固定为：

```text
origin             静止 sample_duration_seconds
long_axis_line     沿长边 / x 方向移动 sample_duration_seconds
width_axis_line    沿宽边移动 sample_duration_seconds
diagonal_line      沿对角线移动 sample_duration_seconds
```

当前推荐先用 raw JSONL simulated live mode 测完整流程。这个模式按文件流顺序和 raw timestamp 切四段：前一段是 origin，接着是 long、width、diagonal。因此 JSONL 本身要已经按这个动作顺序录好；如果你想从任意帧段截取，请继续用 `calibrate_from_raw_jsonl_table.py` 的显式 start-frame 参数。

无设备，用旧 raw JSONL 模拟 live calibration：

```powershell
python live_calibrate_table.py --raw-jsonl path\to\raw_frames.jsonl --simulate-live --auto-advance --no-confirm-save --out data\calibration\simulated_live_table_calibration.json
```

常用参数：

```text
--sample-duration-seconds   默认 5.0，每段采样时长
--min-samples               默认 10，每段最少有效点
--min-line-length           默认 0.10，线段最短长度，单位 m
--point-source              tracker_position_world / pinch_center_world
--timestamp-scale           默认 0.001，raw timestamp 毫秒转秒
--replay-real-time          JSONL 按 timestamp 间隔回放；默认 false
--speed                     replay-real-time 的速度倍率
--auto-advance              不等待每段 Enter，测试时方便
--no-confirm-save           有 warning 也直接保存，测试时方便
```

注意：即使 `--replay-real-time=false`，JSONL simulated live 仍然用 raw timestamp / frame time 切段，不用墙钟时间。如果 raw timestamp 缺失或不能单调形成 frame time，工具会失败并提示改用 `calibrate_from_raw_jsonl_table.py`。

真实 stream calibration：

```powershell
python live_calibrate_table.py --use-live-stream --live-host 127.0.0.1 --live-port 8888 --out data\calibration\live_table_calibration.json
```

然后启动发送端，把 newline-delimited combined JSON 发到 `127.0.0.1:8888`。真实 stream 模式每段开始采样前会清空当前 queue，避免按 Enter 前积压的旧帧混入当前 segment；`metadata.queue_cleared_before_segment` 会记录这个行为。

生成的文件是正式 calibration JSON：

```text
calibration_type = formal_table_lines
is_formal_calibration = true
metadata.collection_mode = raw_jsonl_simulated_live / live_stream
```

## Stage 5C Integrated Live Session

`run_live_integrated_session.py` 是第一版“同一个 live session 内完成 calibration + trial”的命令行 runner。它和 `live_calibrate_table.py` 的区别是：`live_calibrate_table.py` 只生成 calibration 文件；integrated runner 会在采完四段桌面标定后，直接使用内存中的同一个 `FormalCalibration` 对象创建 `TaskCoordinateSystem` 并进入 trial，不要求用户先退出程序再加载 `calibration.json`。

推荐正式开发优先用这个连续流程，避免“先保存 calibration 文件、再启动实验程序”造成现场坐标系和 trial 坐标系不一致。当前它仍然标记为 `is_formal_experiment=false`，因为现有 GUI 只是 debug display，尚没有正式 experiment lifecycle GUI、haptic hardware 和完整实验序列。

运行示例：

```powershell
python run_live_integrated_session.py ^
  --map-config maps\examples\xoy_turn.json ^
  --host 127.0.0.1 ^
  --port 8888 ^
  --out-dir data\live_integrated_session\debug_01 ^
  --subject-id S001 ^
  --trial-id trial_001
```

建议流程：

```text
1. 先启动 run_live_integrated_session.py。
2. 再启动 manus_vive_com，让它连接 127.0.0.1:8888。
3. 按提示依次采 origin / long_axis_line / width_axis_line / diagonal_line。
4. 查看 calibration quality；如果需要确认，按提示继续。
5. 按 Enter 进入 trial。
6. 结束后用 analyze_session.py 复盘 session。
```

分析结果：

```powershell
python analyze_session.py --session-dir data\live_integrated_session\debug_01\session --overwrite
```

常用参数：

```text
--map-config                     必填，MapConfig JSON
--out-dir                        默认 data/live_integrated_session
--session-dir                    可选；不传默认 out_dir/session
--overwrite-session              覆盖已有 session 目录
--subject-id                     可选
--trial-id                       默认 live_integrated_trial
--sample-duration-seconds        默认 5.0，每段标定采样时长
--min-samples                    默认 10
--min-line-length                默认 0.10 m
--point-source                   tracker_position_world / pinch_center_world
--confirm-calibration / --no-confirm-calibration
--control-rate-hz                默认 60
--max-frames                     可选，调试时常用
--duration-seconds               可选
--pinch-grab-threshold           可选，覆盖 EngineConfig 默认值
--pinch-release-threshold        可选，覆盖 EngineConfig 默认值
--slip-motion-threshold          可选，覆盖 EngineConfig 默认值
--ignore-task-z                  debug only，放宽 z 方向地图判定
--task-z-half-extent             默认 5.0，仅配合 --ignore-task-z
--display-mode                   text / none，默认 text
--print-every                    默认 30
--gui                            debug display，在 trial running 阶段打开 GUI
--gui-fps                        默认 30，GUI 轮询/刷新频率
--cue-sink                       none / logging / console / gui_text，默认 logging
--cue-config                     可选，JSON/YAML cue 生成配置
--visual-profile                 debug_all / experiment_visibility_feedback / experiment_blank
--status-panel                   auto / show / hide，默认 auto
--show-axes                      auto / show / hide，默认 auto
--show-grid                      auto / show / hide，默认 auto
--termination-config             可选，JSON/YAML 保护性 trial 终止配置
--anchor-current-pinch-debug     debug only，默认关闭
--pinch-position-mode            nodes_world / tracker_plus_local，默认 nodes_world
--stream-wait-timeout-seconds    默认 60，等不到任何 raw frame 会安全退出
--valid-tracker-timeout-seconds  默认 60，等不到 tracker_valid=True 会安全退出
--valid-pinch-timeout-seconds    默认 60，第一版只记录到 config/summary
--no-frame-timeout-seconds       默认 5，trial running 中长时间无新帧会退出
```

`--pinch-position-mode` 用来解释 MANUS skeleton node 的 `position`：

```text
nodes_world          node position 已经是 world 坐标，实机 MANUS/Vive live stream 默认用这个
tracker_plus_local   旧假设：node position 是 tracker-local 小偏移，需要加 tracker world position
```

如果实机 GUI 里手/物块相对标定原点整体跑飞，优先检查这个字段。2026-06-02 的实机数据表明发送方给的 MANUS node position 是米级 world 坐标，因此 live integrated runner 默认使用 `nodes_world`。

第一次实机测试建议显式加一个总时长和 text display，避免人还在调发送端时误以为程序卡死：

```powershell
python run_live_integrated_session.py ^
  --map-config maps\examples\xoy_turn.json ^
  --host 127.0.0.1 ^
  --port 8888 ^
  --out-dir data\live_integrated_session\debug_01 ^
  --duration-seconds 60 ^
  --display-mode text
```

如果要同时打开 live debug GUI，先安装可选 GUI 依赖：

```powershell
pip install PySide6 pyqtgraph
```

然后加 `--gui`：

```powershell
python run_live_integrated_session.py ^
  --map-config maps\examples\xoy_turn.json ^
  --host 127.0.0.1 ^
  --port 8888 ^
  --out-dir data\live_integrated_session\debug_gui_01 ^
  --duration-seconds 60 ^
  --display-mode text ^
  --gui ^
  --gui-fps 30
```

这个 GUI 是 debug display，不是正式实验 lifecycle GUI。它只在 trial running 阶段订阅 `DashboardSnapshot`，calibration / waiting 阶段不伪造 snapshot；窗口刚打开但还没有 trial snapshot 时只显示 waiting 文本。

当前 trial 结束逻辑是人工完成，不做自动 target success。受试者认为物块到终点后，实验员在终端按 `e` 结束当前 trial；程序会记录手动结束时的 block / pinch / target 诊断状态，但不会因为 block center 进入 target 自动判成功。`q` 是 operator abort，会中止本次 run；GUI Close 仍然只关闭显示层，不停止 trial/session，也不会产生 `MANUAL_COMPLETED` 或 `ABORTED_BY_OPERATOR`。

终端按键第一版只保证 Windows `msvcrt` 非阻塞读取。运行时会打印：

```text
Operator commands:
  e = end current trial as MANUAL_COMPLETED
  q = abort whole run
  GUI close = close display only, does not stop trial
```

保护性自动停止由 termination config 控制，默认值是：

```json
{
  "max_trial_duration_seconds": 600.0,
  "max_detach_count": 20,
  "manual_completion_enabled": true,
  "timeout_enabled": true,
  "detach_limit_enabled": true
}
```

其中 `max_detach_count=20` 表示允许 20 次 detach/release，第 21 次触发 `FAILED_TOO_MANY_DETACHES`。`timeout_enabled=true` 时，trial 运行超过 `max_trial_duration_seconds` 会记录 `trial_outcome=FAILED_TIMEOUT`、`end_reason=trial_timeout`。`detach_limit_enabled=true` 时，`TrialController` 的 `total_detach_count > max_detach_count` 会记录 `trial_outcome=FAILED_TOO_MANY_DETACHES`、`end_reason=too_many_detaches`。slip 和 blocked 只是事件/诊断，不导致失败。

可以把 termination config 写成 JSON 后传入：

```powershell
python run_live_integrated_session.py ^
  --map-config maps\examples\xoy_turn.json ^
  --host 127.0.0.1 ^
  --port 8888 ^
  --out-dir data\live_integrated_session\debug_termination ^
  --termination-config config\termination_debug.json
```

也支持 `.yaml` / `.yml`，但会 lazy import `PyYAML`；如果没安装，会给出明确的安装提示。最终生效的配置会写入 `out_dir/session/termination_config.json`，并同时进入 `summary.json`、`session_meta.json`、`trial_config.json` 和 `trial_summary.json`。`--duration-seconds` 是 debug 外层运行时长上限，结果是 `trial_outcome=DURATION_REACHED`、`end_reason=duration_reached`；它和保护性 `max_trial_duration_seconds` 的 `trial_outcome=FAILED_TIMEOUT`、`end_reason=trial_timeout` 严格分开。

### Cue / Haptic Abstraction

cue 层只做非硬件提示；haptic Stage 1 是独立输出层。两者都观察现有 `TrialController` / `BlockController` 输出，不修改 contact、slip、blocked、detach 或 trial outcome 逻辑。cue priority 只影响 GUI/console 显示一个主要提示；haptic 不复用 cue priority，`matrix` 和 `vibration` 两个 target 可以同时生成 command。

支持的 sink：

```text
none       不生成 cue command，不写 cue_log.csv
logging    只记录 cue command，不向受试者显示，默认
console    worker 线程异步打印一次性文本提示
gui_text   GUI 中显示居中文本 overlay；GUI 关闭后的新 cue fallback 到 console
```

`gui_text` 必须和 live `--gui` 一起使用；replay 默认就会打开 GUI，因此不能和 `--headless` 一起使用。live GUI Close 仍然只关闭显示层，不停止 trial/session；replay GUI Close 会停止剩余回放。

live 中用 GUI 文本 cue 和实验可见性 profile 调试：

```powershell
python run_live_integrated_session.py ^
  --map-config maps\examples\xoy_turn.json ^
  --host 127.0.0.1 ^
  --port 8888 ^
  --out-dir data\live_integrated_session\cue_gui_01 ^
  --gui ^
  --cue-sink gui_text ^
  --visual-profile experiment_visibility_feedback
```

已有 session 可以先用 replay 验证 cue 触发和显示，不需要连接实机：

```powershell
python run_replay_debug_gui.py ^
  --session-dir data\live_integrated_session\debug_01\session ^
  --out-dir data\debug_gui\cue_replay_01 ^
  --replay-timing fixed ^
  --cue-sink gui_text ^
  --visual-profile experiment_visibility_feedback
```

第一版 cue 类型：

```text
contact_enter
contact_exit
slip_pinch_insufficient
slip_track_blocked
blocked_directional
```

当 `TRACK_BLOCKED` 同时有 slip 和明确方向时，cue 显示层优先生成 `blocked_directional`，避免同一 GUI/console 状态产生两个 RT 起点。haptic 层不受这个显示优先级压制：matrix 仍可输出 blocked direction，vibration 也可按配置记录 slip。`primary_blocked_surface` 表示撞到的 task 边界，例如 `X_POS`；cue `direction` 表示建议修正方向，例如 `X_NEG`。第一版保留 `Z_POS / Z_NEG`，不翻译成 left/right/up/down。

cue config 示例：

```json
{
  "enable_contact_cue": true,
  "enable_contact_exit_cue": true,
  "enable_slip_cue": true,
  "enable_blocked_directional_cue": true,
  "min_cue_interval_ms": 0,
  "repeat_policy": "edge_only",
  "message_language": "en"
}
```

JSON/YAML 未知字段、非法类型和非 `edge_only` repeat policy 会清晰失败，不会静默回退。最终生效配置始终写入新 live session 的 `session/cue_config.json`，即使 `--cue-sink none`；`cue_sink` 是运行时输出方式，不写进 cue config。

所有非 `none` sink 在 run 结束后写 `session/cue_log.csv`。`cue_count` 表示通过配置与限流、进入 sink 的 command 数量；被配置或 rate limit 抑制的候选只进入 summary 的 suppressed 统计，不作为 RT 起点。`success=true` 只表示 sink 接受 command，不表示受试者看到提示，也不表示任务成功；实际显示状态看 `display_status`、`displayed_monotonic_ms` 和 `not_displayed_reason`。

```text
created_monotonic_ms     从 frame result 生成 command 的时间
issued_monotonic_ms      sink 接受 command 的时间
displayed_monotonic_ms   console 实际打印或 GUI 首次 render 文本的时间
ack_monotonic_ms         第一版始终为空，没有硬件 acknowledgement
```

GUI 的 `displayed_monotonic_ms` 表示一次 GUI refresh 已应用文本，受 `gui_fps` 量化影响；它不是物理屏幕扫描时间，也不证明受试者真正看到。当前不在线计算 behavioral RT。后续 RT analyzer 应联合 `cue_log.csv`、逐帧状态、events 和 timing diagnostics 离线计算。

`experiment_visibility_feedback` 中物块消失/重现本身是视觉刺激，但 Stage 1 不把它写入 `cue_log.csv`，也不把它当作 behavioral RT 起点。如果以后需要分析 block visibility visual RT，应新增独立 visual-state render diagnostics，不要混入 cue command log。

### Haptic TCP Stage 1

Stage 1 只实现 Matrix / electrotactile ESP32 的 TCP packet 编码与非阻塞发送；vibration ESP32 协议仍未确定，本阶段只记录 vibration command，不连接、不编码、不发送。replay 默认 `haptic_enabled=false`，不会向真实硬件发送。

Matrix ESP32 packet:

```text
MAGIC(AA 55 AA 55) + payload_length(1B) + payload(N<=128B) + checksum(1B)
checksum = sum(payload) & 0xFF
payload = HV507 channel list，每个 channel 必须是 0..127
```

live 使用 haptic config：

```powershell
python run_live_integrated_session.py ^
  --map-config maps\examples\xoy_turn.json ^
  --host 127.0.0.1 ^
  --port 8888 ^
  --out-dir data\live_integrated_session\haptic_01 ^
  --haptic-config configs\haptic_matrix.json
```

示例配置：

```json
{
  "enabled": true,
  "matrix": {
    "enabled": true,
    "required": true,
    "host": "192.168.x.x",
    "port": 12345,
    "connect_timeout_s": 3.0,
    "send_timeout_s": 0.05,
    "startup_settle_seconds": 7.0,
    "max_queue_size": 8,
    "latest_only": true,
    "feedback_mode": "latched_once",
    "resend_interval_ms": 100,
    "direction_semantics": "blocked_surface",
    "direction_channel_map": {
      "X_NEG": [1, 2, 3],
      "X_POS": [4, 5, 6],
      "Y_NEG": [7, 8, 9],
      "Y_POS": [10, 11, 12],
      "Z_NEG": [],
      "Z_POS": []
    },
    "combination_channel_map": {
      "X_POS+Y_POS": [20, 21, 22],
      "X_POS+Y_NEG": [23, 24, 25],
      "X_NEG+Y_POS": [26, 27, 28],
      "X_NEG+Y_NEG": [29, 30, 31]
    },
    "missing_combination_policy": "skip"
  },
  "vibration": {
    "enabled": true,
    "host": "",
    "port": 12345,
    "protocol": "pending",
    "enable_contact": true,
    "enable_release": true,
    "enable_slip": true,
    "enable_slip_pinch_insufficient": true,
    "enable_slip_track_blocked": true,
    "enable_slip_track_blocked_in_target_region": true
  }
}
```

`matrix.direction_semantics` 默认是 `blocked_surface`，channel map 表示撞到哪一侧边界，例如 `BLOCKED_X_NEG -> X_NEG`。也可以改成 `correction_direction`，channel map 表示应该往哪个方向退回，例如 `BLOCKED_X_NEG -> X_POS`。haptic log 会同时保存 `primary_blocked_surface`、`correction_direction`、`blocked_surface_set`、`correction_direction_set`、`matrix_direction_used` 和 `matrix_direction_semantics`。

单方向 blocked 继续查 `direction_channel_map`。多方向 blocked 会按 X/Y/Z 轴顺序规范化成组合 key，例如 `X_POS+Y_POS`，然后查 `combination_channel_map`。默认 `missing_combination_policy=skip`，组合缺失时不会把 `X_POS` 和 `Y_POS` 的通道相加，而是在 `haptic_command_log.csv` 中记录 `send_status=not_sent`、`not_sent_reason=missing_combination_mapping`。如需调试并集行为，可以显式设置 `missing_combination_policy=union_single_directions`，但正式实验建议为每个组合单独配置硬件通道。

Matrix `feedback_mode=latched_once` 时，blocked start 或方向变化才发送一次 channel frame；blocked 持续且方向不变不重复发送。`continuous_resend` 会在 blocked active 期间按 `resend_interval_ms` 重发当前方向。blocked end、trial end、invalid、abort 只记录 `state_end`，不发送 `clear_all`、`stop_all` 或默认 zero-channel frame。

如果只想测试 Matrix ESP32 硬件链路，不依赖 MANUS/Vive、trial、地图或 blocked 状态，可以运行独立 smoke：

```powershell
python run_matrix_haptic_smoke.py --host 192.168.x.x --port 12345 --channels 1,2,3
```

脚本只会连接 Matrix ESP32、等待 `--startup-settle-seconds`，发送一次 channel list，写一个 JSON smoke log，然后退出。默认 log 写到 `data/haptic_smoke/matrix_haptic_smoke_<timestamp>.json`；也可以用 `--out data\haptic_smoke\smoke_01.json` 指定路径。这个入口适合先确认 ESP32 IP/端口、TCP parser、HV507 channel 映射是否工作，再进入正式 live trial。

如果 `matrix.enabled=true` 且 `matrix.required=true`，程序会在 trial 开始前连接 Matrix ESP32，并等待 `startup_settle_seconds`。连接失败会明显停止在 trial 前，`run_stop_reason=matrix_haptic_connect_failed`，不会进入 trial。trial loop 中只提交 haptic command，不执行阻塞式 connect/send；队列有界，发送失败或队列替换写入 `session/haptic_command_log.csv`。

Vibration Stage 1 只记录：

```text
contact_enter -> vibration one_shot, protocol_pending
contact_exit  -> vibration one_shot, protocol_pending
slip_start    -> vibration state_start, protocol_pending
slip_end      -> vibration state_end, protocol_pending
```

Slip 开关是分层的：`enable_slip=false` 关闭所有 slip vibration；`PINCH_INSUFFICIENT` 由 `enable_slip_pinch_insufficient` 控制；`TRACK_BLOCKED` 在 target_region 外由 `enable_slip_track_blocked` 控制，在 map 定义的 `target_region` 内由 `enable_slip_track_blocked_in_target_region` 控制。target 判断复用 `trial_config.target_region` 的 box，不新造距离阈值。

live haptic enabled 时会写：

```text
session/haptic_config.json
session/haptic_command_log.csv
```

summary / session_meta / trial_config / trial_summary 会包含 `haptic_enabled`、`matrix_haptic_enabled`、`vibration_haptic_enabled`、`haptic_mode`、`haptic_count`、`haptic_type_counts`、`effective_haptic_config` 和 `haptic_command_log_path`。`sent_monotonic_ms` 只是电脑端 socket send 完成时间，不代表真实硬件物理 onset 或受试者感知时间。

TODO: 当前 Matrix ESP32 parser 对 empty payload frame 可能不解析，且 HV507/latch 结构下电脑端不应假设 zero/empty frame 是可靠 hardware clear。如果未来需要硬件级 clear/stop，需要单独修改或确认 ESP32 parser、HV507 blanking、LE/BL 控制或硬件侧安全机制。

如果 calibration quality 出来后你在 `Continue with this calibration? [y/N]` 里选择 no 或直接回车，runner 会重新进入四段 calibration，而不是退出整个流程。只有 calibration 采集失败后，在 `Retry calibration? [y/N]` 里选择 no，才会停止本次 run。

Pinch distance threshold 标定默认不会自动运行。需要在 live integrated 命令里显式加：

```powershell
--calibrate-pinch-threshold
```

如果要修改 open/closed 距离之间的阈值比例，可以额外传 `--pinch-threshold-config path\to\pinch_threshold_config.json`。配置既支持平铺字段，也支持包一层 `pinch_threshold_calibration`：

```json
{
  "pinch_threshold_calibration": {
    "repeat_count": 3,
    "sample_window_seconds": 1.0,
    "min_valid_samples": 10,
    "on_fraction": 0.25,
    "off_fraction": 0.35,
    "min_required_range_m": 0.015,
    "max_repeat_spread_m": 0.03,
    "require_tracker_valid": false
  }
}
```

计算公式是 `closed_distance + fraction * (open_distance - closed_distance)`。`on_fraction` 对应 pinch on/grab 阈值，`off_fraction` 对应 pinch off/release 阈值，必须满足 `0 < on_fraction < off_fraction < 1`。标定输出会写到 `session/pinch_threshold_calibration.json`，其中包含实际 `on_fraction/off_fraction` 和最终 `pinch_on_threshold_m/pinch_off_threshold_m`；`trial_config`、`summary`、`session_meta` 和 `trial_summary` 也会记录最终 effective threshold。若要直接复用固定阈值，可以传：

```powershell
--pinch-threshold-json path\to\pinch_threshold_calibration.json
```

如果一直没有发送端连接，或连接后一直没有有效 tracker，runner 不会无限等待；它会写 `out_dir/summary.json` 后退出。常见停止原因：

```text
stream_wait_timeout
valid_tracker_timeout
client_disconnected_before_trial
client_disconnected_during_trial
no_new_frame_timeout
keyboard_interrupt
```

实机排查时优先看 `summary.json` 中这些字段：

```text
source_stop_reason
pump_stop_reason
no_new_frame_count
max_no_new_frame_gap_seconds
latest_buffer_overwritten_frame_count
latest_buffer_last_frame_index
calibration_segment_time_mode
```

`calibration_segment_time_mode=monotonic_live` 表示 live calibration 用的是本机 `time.monotonic()` 控制采样时长，不会把 raw timestamp 和本机 monotonic clock 混在一起比较。

输出位置：

```text
out_dir/raw_frames.jsonl
out_dir/calibration.json
out_dir/summary.json
out_dir/session/session_meta.json
out_dir/session/calibration.json
out_dir/session/trial_config.json
out_dir/session/raw_frames.jsonl
out_dir/session/device_frames.jsonl
out_dir/session/processed_frames.csv
out_dir/session/events.csv
out_dir/session/haptic.csv
out_dir/session/termination_config.json
out_dir/session/cue_config.json              新 live session 始终写入
out_dir/session/cue_log.csv                 仅 cue sink 非 none 时写入
out_dir/session/haptic_config.json          新 live session 始终写入
out_dir/session/haptic_command_log.csv      仅 haptic enabled 时写入
out_dir/session/trial_summary.json
out_dir/session/timing_diagnostics.csv      live integrated 默认始终写入
out_dir/session/gui_diagnostics.csv       仅 --gui 时，优先写入这里
```

校验 calibration / trial_config / session_meta 是否使用同一个 calibration：

```powershell
python -c "import json, pathlib; s=pathlib.Path(r'data\live_integrated_session\debug_01\session'); c=json.loads((s/'calibration.json').read_text()); m=json.loads((s/'session_meta.json').read_text()); t=json.loads((s/'trial_config.json').read_text()); print(c['calibration_id'], m['calibration_id'], t['calibration_id']); print(t['task_coordinate_system']==c['task_coordinate_system'])"
```

期望输出中三个 `calibration_id` 一致，最后一行是 `True`。这表示 trial 使用的 task 坐标系和保存到 session 的 calibration JSON 一致。

`--anchor-current-pinch-debug` 只能用于调试地图位置。启用后 runner 只平移 `MapConfig`，不会修改 calibration；并且会在 `session_meta.json`、`trial_config.json`、`summary.json` 中写：

```text
map_anchor_mode = current_pinch_debug
is_formal_experiment = false
warning = Map was translated to current pinch for debugging; this is not a formal calibrated trial.
```

单次 `Ctrl+C` 会进入安全收尾：停止接收线程、finalize session、写 `summary.json`，并记录：

```text
run_stop_reason = keyboard_interrupt
phase_at_stop
session_finalized
```

trial outcome 相关字段会优先使用 runner-level `trial_outcome` / `end_reason`，而不是只依赖 `TrialController.state`。常见结果包括：

```text
MANUAL_COMPLETED             实验员按 e，或兼容保留的 subject_end 完成 trial
ABORTED_BY_OPERATOR          实验员按 q 中止 run
FAILED_TIMEOUT               保护性 trial timeout
FAILED_TOO_MANY_DETACHES     detach/release 次数超过阈值
SOURCE_STOPPED               source 主动停止
CLIENT_DISCONNECTED          live client 断开
NO_NEW_FRAME_TIMEOUT         trial running 中长时间没有新帧
DURATION_REACHED             --duration-seconds debug stop
MAX_FRAMES_REACHED           --max-frames debug stop
INTERRUPTED                  KeyboardInterrupt / GUI error 等中断
```

后处理时不要只看 `trial_outcome=MANUAL_COMPLETED`：实验员按 `e` 会写 `end_reason=operator_manual_complete`、`operator_command=e`、`manual_completed=true`；兼容保留的 `subject_end` 会写 `end_reason=subject_end`、`operator_command=null`、`manual_completed=false`。

target 诊断只记录，不自动结束 trial。summary/trial summary 中会尽量写入：

```text
block_center_task_position_at_end
pinch_task_position_at_end
block_center_in_target_at_end
distance_to_target_at_end
contact_state_at_end
block_motion_state_at_end
stop_reason_at_end
detach_state_at_end
slip_active_at_end
slip_reason_at_end
blocked_force_active_at_end
logical_haptic_label_at_end
first_target_entry_time
first_target_entry_frame_index
last_snapshot_time
last_frame_index
```

如果 `target_region` 缺失或无法识别，上述 target 诊断会写 `null`，并在 warnings 里记录 `target_region missing; target diagnostics are limited.`。

### Stage 5C LiveTrialRunner 结构

Stage 5C 之后，`run_live_integrated_session.py` 仍然负责完整流程编排：等待 live stream、采集四段 calibration、确认 calibration、加载 `MapConfig`、创建 `SessionRecorder`、保存 summary/session。真正的 trial 实时控制循环已经抽到 `live_trial_runner.py`：

```text
LatestFrameBuffer
  -> parse_raw_manus_vive_frame()
  -> ManusViveExperimentAdapter
  -> TrialController.update()
  -> CueRuntime observation
  -> SessionRecorder
  -> DashboardSnapshot callback
```

`LiveTrialRunner` 不做 calibration、不验证地图、不画 GUI。它只接收已经准备好的 `TaskCoordinateSystem`、`TrackRegion`、`EngineConfig`、`SessionRecorder`、latest-frame buffer，以及可选的非阻塞 haptic runtime，然后按固定频率推进现有 `TrialController`。这次重构不改 `BlockController` / `TrialController` 的 contact、slip、blocked 语义。

replay/live debug GUI 已经通过 `snapshot_callback(snapshot)` 订阅这层输出，显示层不需要直接调用 `TrialController`。如果 callback 抛异常，runner 会继续运行，并在 summary 中记录 `callback_error_count`、`mean_callback_latency_ms`、`max_callback_latency_ms` 和 warning。Stage 1 Matrix haptic 通过独立 runtime 接入；trial loop 只提交 command，不在 loop 内执行阻塞式 TCP connect/send。

`run_live_integrated_session.py` 的输出仍保持旧字段为主，同时会额外写：

```text
live_trial_runner_summary
callback_error_count
mean_callback_latency_ms
max_callback_latency_ms
gui_enabled
gui_closed
gui_requested_stop
gui_snapshot_update_count
gui_overwritten_snapshot_count
gui_diagnostics_path
gui_close_time
timing_enabled
timing_diagnostics_path
timing_mode
timing_is_live_latency
timing_record_count
published_frame_count
consumed_frame_count
processed_frame_count
overwritten_before_consume_count
cue_enabled
cue_sink
cue_mode
is_live_cue_timing
cue_log_path
cue_count
cue_type_counts
suppressed_cue_count
suppressed_cue_type_counts
effective_cue_config
requested_visual_profile
visual_profile
effective_visual_profile
status_panel
effective_status_panel_visible
show_axes / effective_axes_visible
show_grid / effective_grid_visible
trial_outcome
end_reason
operator_command
operator_command_time
operator_command_monotonic_ms
trial_end_monotonic_ms
operator_command_to_trial_stop_latency_ms
manual_completed
operator_aborted
trial_start_time
trial_end_time
trial_duration_seconds
detach_count
block_center_task_position_at_end
pinch_task_position_at_end
block_center_in_target_at_end
distance_to_target_at_end
contact_state_at_end
block_motion_state_at_end
stop_reason_at_end
detach_state_at_end
slip_active_at_end
slip_reason_at_end
blocked_force_active_at_end
logical_haptic_label_at_end
first_target_entry_time
first_target_entry_frame_index
last_snapshot_time
last_frame_index
```

调试这层结构时可以只跑：

```powershell
pytest -q tests/test_termination_config.py tests/test_live_trial_runner.py tests/test_live_integrated_session.py
```

### Stage 5D Replay / Live Debug GUI Prototype

`run_replay_debug_gui.py` 和 `run_live_integrated_session.py --gui` 共用第一版实验员调试 GUI 原型。GUI 本身只消费 `DashboardSnapshot`，不直接读 socket、不直接读 raw JSONL 推进 trial，也不实现 parser / adapter / TrialController / BlockController 逻辑。

内部数据流是：

```text
raw/session replay runner
  -> LiveTrialRunner
  -> DashboardSnapshot
  -> LatestSnapshotStore
  -> Debug GUI

live integrated runner
  -> LiveTrialRunner worker thread
  -> DashboardSnapshot
  -> LatestSnapshotStore
  -> Debug GUI on main thread
```

安装 GUI 依赖：

```powershell
pip install PySide6 pyqtgraph
```

从已有 session 启动 replay GUI：

```powershell
python run_replay_debug_gui.py --session-dir data\offline_replay\exp13_map_xoy_turn\session --replay-timing fixed --replay-fps 60 --out-dir data\debug_gui\exp13
```

replay 默认打开 GUI，不需要传 `--gui`；为了和 live CLI 使用习惯一致，显式传 `--gui` 也可以。`--gui-fps` 控制 GUI 刷新频率，`--replay-fps` 控制 fixed replay 的数据回放频率，两者不是同一个参数。

如果只想验证 replay 链路、不打开 GUI：

```powershell
python run_replay_debug_gui.py --session-dir data\offline_replay\exp13_map_xoy_turn\session --max-frames 200 --replay-timing fast --headless
```

不使用 session，而是显式传文件：

```powershell
python run_replay_debug_gui.py ^
  --raw-jsonl path\to\raw_frames.jsonl ^
  --calibration-json path\to\calibration.json ^
  --trial-config-json path\to\trial_config.json ^
  --replay-timing fixed ^
  --replay-fps 60
```

旧 session 如果没有 `trial_config.json`，replay 不会猜测原始地图、物块尺寸或阈值。可以在保留 `--session-dir` 的同时，用原始 `trial_config.json` 或原始 MapConfig JSON 显式补充：

```powershell
python run_replay_debug_gui.py ^
  --session-dir path\to\legacy_session ^
  --trial-config-json maps\examples\xoy_turn.json ^
  --out-dir data\debug_gui\legacy_replay ^
  --replay-timing fixed ^
  --cue-sink gui_text
```

`--raw-jsonl` 和 `--calibration-json` 也可以在 `--session-dir` 基础上显式覆盖。使用 MapConfig JSON 作为 `--trial-config-json` 时，地图几何会正确读取，但其中没有保存的 trial 阈值会回退到 replay 默认值；要精确复现原 trial，优先使用原始 `trial_config.json`。

`--replay-timing` 可选：

```text
raw      按 raw timestamp 间隔回放，默认
fixed    固定 --replay-fps 回放，适合调 GUI
fast     不等待，适合测试或快速 smoke
```

replay 只有传入 `--out-dir` 时才会写 `timing_diagnostics.csv`，并且只写到 replay 输出目录，不修改输入 session。replay timing 行会标记 `mode=replay`、`is_live_latency=false`；parser / adapter / trial update duration 可用于性能比较，但 replay 的等待时间和 GUI latency 不能解释为真实 live latency。

replay 默认 `--cue-sink logging`。使用 `--session-dir` 时，cue config 优先级是：显式 `--cue-config`、输入 `session/cue_config.json`、默认配置。只有传 `--out-dir` 时才写 `out_dir/cue_log.csv` 和 `out_dir/cue_config.json`，绝不修改输入 session。replay cue 行和 summary 标记 `cue_mode=replay`、`is_live_cue_timing=false`，只能用于调试 cue 触发与日志结构，不能解释为真实受试者 RT。

当前 GUI 支持：

```text
debug_all
  显示 track、target、initial block、当前 block、pinch、block-pinch line、状态面板、坐标轴和网格

experiment_visibility_feedback
  block_visible=false 时不显示任何 task marker
  block_visible=true 时显示完整 block geometry + pinch marker
  不显示 track、target、initial block、block-pinch line；默认隐藏状态面板、坐标轴和网格

experiment_blank
  不显示 task geometry 或 marker，只保留 cue overlay 或空白画面
```

旧名称 `experiment_markers_when_hidden` 仍可作为 deprecated alias 使用，内部和落盘的 effective profile 会规范化为 `experiment_visibility_feedback`。实验 profile 使用隐藏的 scene/map bounds 固定视图范围，不会随着 marker 移动自动缩放。第一版 “hand marker” 实际只指 pinch center marker，不新增 wrist/tracker/skeleton marker。

GUI 是 x-y task view，z 方向第一版以数值显示。GUI 不直接修改 `TrialController` / `BlockController`，也不接真实 haptic hardware。缺少 `PySide6` / `pyqtgraph` 时，入口会提示安装命令；非 GUI 测试不依赖这两个包。

replay GUI 的退出语义：

```text
关闭窗口、按 Q / Esc，或在终端按 Ctrl+C，会停止剩余 replay
run_replay_debug_gui.py 会等待 replay worker 安全收尾并写出已有 summary / cue / timing 输出
```

live `--gui` 的线程模型是：Qt GUI event loop 在主线程运行；`LiveTrialRunner.run_until_done()` 在 worker 线程运行；`snapshot_callback` 只做轻量 `LatestSnapshotStore.publish(snapshot)`。如果 GUI 刷新慢，store 只保留最新 snapshot，不排队旧帧，避免显示延迟越积越大。

live `--gui` 的关闭语义：

```text
GUI Close 只关闭显示层，不 stop trial/session
不实现 Stop / Abort / Pause / Resume
trial ended 后 GUI 保留最后一帧，不自动关闭
summary/session finalize 会等用户关闭 GUI 后继续
```

live `--gui` 的 runtime stats 第一版显示已有且可靠的字段：snapshot age、GUI fps/render lag、overwritten snapshot count、raw dropped frames、parse errors。`receive_fps` 如果没有可靠来源，显示为 N/A，不硬造。

已知风险：当前 live `--gui` 是 debug display，不是正式实验 lifecycle GUI。实验员和受试者仍共用一个窗口，experiment profile 只是隐藏调试 geometry，不是真正的双窗口/双屏实验界面。在 Windows/Qt 下，`Ctrl+C` 行为可能不如纯 CLI 模式直接；如果 Qt 窗口获得焦点，终端里的 `e/q` 读取也可能不如纯 CLI 稳定。如果需要停止整个 run，仍依赖现有 runner 的 `KeyboardInterrupt` / `request_stop` / `stop_event` 机制。后续正式 GUI 阶段需要区分 experimenter view / participant view，并单独设计 Stop / Abort / Pause / Resume 和完整生命周期控制。

## Session 输出校验

`validate_session_outputs.py` 只读 session 和外层 summary，不移动、不复制、不重写任何文件。默认读取 `session_dir.parent / summary.json`：

```powershell
python validate_session_outputs.py --session-dir data\live_integrated_session\debug_01\session
```

如果 summary 在其他位置：

```powershell
python validate_session_outputs.py --session-dir data\live_integrated_session\debug_01\session --summary-json path\to\summary.json
```

validator 会检查当前 live integrated 单 trial 的必需 artifact、`trial_outcome`、`end_reason`、effective termination config、末帧 block / pinch / target 诊断，以及 summary / trial summary / termination config / calibration / map / trial id 的一致性。新 cue-aware live session 还会检查 `cue_config.json` 一致性、`cue_log.csv` header、行数、type counts、cue_id 唯一性与 trial/mode 一致性；`cue_count=0` 的 header-only cue log 可以直接通过。`--gui` 启用但缺少 `gui_diagnostics.csv`，live integrated 中 `timing_enabled=true` 但缺少 `timing_diagnostics.csv`，或启用 cue 但缺少 `cue_log.csv`，会报告 ERROR。raw 为空、末帧诊断为 null、GUI 行数很少、timing 可选字段为空等情况会报告 WARNING；默认 WARNING 不让校验失败，可用 `--strict` 将 WARNING 视为失败。

## Timing Diagnostics

live integrated 从等待 stream、calibration 到 trial 全程使用内存 collector 记录 `session/timing_diagnostics.csv`。所有进入 `LatestFrameBuffer.put()` 的 frame 都有 timing 行；被覆盖或没有被 runner 消费的 frame 会保留部分字段为空，并通过以下字段区分：

```text
phase
frame_published
frame_consumed
frame_processed
overwritten_before_consume
```

常用时间点和延迟字段：

```text
raw_receive_monotonic_ms
frame_published_monotonic_ms
frame_consumed_monotonic_ms
parse_start_monotonic_ms / parse_end_monotonic_ms / parse_duration_ms
adapter_start_monotonic_ms / adapter_end_monotonic_ms / adapter_duration_ms
trial_update_start_monotonic_ms / trial_update_end_monotonic_ms / trial_update_duration_ms
snapshot_created_monotonic_ms
snapshot_published_monotonic_ms
gui_render_monotonic_ms
raw_to_frame_publish_latency_ms
frame_wait_age_ms
raw_to_trial_update_latency_ms
frame_to_trial_update_latency_ms
trial_update_to_snapshot_latency_ms
snapshot_publish_to_gui_render_latency_ms
operator_command_to_trial_stop_latency_ms
```

`snapshot_published_monotonic_ms` 表示 `LiveTrialRunner` 将 snapshot 发布给订阅者的时刻；没有 GUI 时它仍然存在，只有 GUI render 相关字段为空。GUI 重复绘制最后一帧时只记录该 frame 的首次 render。operator command 会写入最后一个已知 frame；trial 尚无 frame 时会创建 `event_type=operator_command`、`frame_index=null` 的 session-level 行。

发送方字段 `combined_monotonic_ms`、`skeleton_receive_monotonic_ms`、`tracker_receive_monotonic_ms` 只属于发送方时钟域，只能在发送方字段之间比较，例如 `skeleton_tracker_sync_delta_ms`。绝对不要用它们减 Python `time.monotonic()` 字段。Python latency 只由 Python monotonic 字段相减。

运行 timing 后处理：

```powershell
python analyze_timing.py --session-dir data\live_integrated_session\debug_01\session
```

默认输出 `session/timing_analysis_summary.json` 并向终端打印 JSON。已有输出不会被覆盖，除非传 `--overwrite`；也可以用 `--out` 指定其他路径。summary 包含 median / p95 / max 延迟、各 phase transport 统计、published / consumed / processed / overwritten 计数、`max_no_frame_gap_ms` 和 operator command 到 stop 的延迟。

timing collector 在控制 loop 和 GUI render 中只更新线程安全内存，不做逐帧文件 I/O，run 结束后一次写 CSV。代价是进程硬崩溃时 timing log 可能丢失。本阶段只做 system timing diagnostics，并记录 cue / haptic command；不在线计算受试者 behavioral reaction time。Matrix haptic 的 `sent_monotonic_ms` 只是电脑端发送完成时间，behavioral RT analyzer 仍是后续独立阶段。

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

`offline_replay_autocalibrated.py --write-session`、`offline_replay_formal_calibrated.py --write-session` 和 `run_live_integrated_session.py` 都会生成兼容的标准 session 核心文件：

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
```

live integrated session 还会根据运行配置生成：

```text
termination_config.json
cue_config.json                 新 live session 始终写入
cue_log.csv                    cue sink 非 none 时写入
timing_diagnostics.csv         live integrated 默认始终写入
gui_diagnostics.csv            仅 --gui 时写入
```

运行 `analyze_session.py` 后会额外生成 `analysis_summary.json` 和 `plots/`。replay debug runner 只有传入 `--out-dir` 时才写自己的 cue/timing 输出，并且不会修改输入 session。

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

`haptic.csv` 记录现有状态机产生的逐帧逻辑 haptic 状态，不代表真实硬件已经发送或被受试者感知。`haptic_command_log.csv` 记录 Stage 1 haptic command 与 Matrix TCP 发送状态；`cue_log.csv` 记录通过 cue 配置和限流后进入 sink 的命令级提示。它们适合检查 contact/slip/blocked 提示语义，但不能直接当作 behavioral RT 结果。

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

如果运行启用了 cue，先看 summary 中的 `cue_count`、`cue_type_counts`、`suppressed_cue_count` 和 `suppressed_cue_type_counts`，再用 `cue_log.csv` 检查每条 command 的 `cue_type`、`direction`、`trigger_reason`、`display_status` 和时间戳。`success=true` 只表示 sink 接受 command，不表示受试者看见提示或完成任务。

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
pinch_grab_threshold = 0.1
pinch_release_threshold = 0.12
```

单位按米理解。如果真实 pinch distance 常见值远大于 0.035，需要重新评估 node、坐标尺度或阈值。

### 图里轨迹为什么同一数据不同参数会不一样

`offline_replay_autocalibrated.py` 的 `task_trajectory_xyz.png` 画的是输入点在临时 task 坐标系下的轨迹。不同 `calibration_mode`、`calibration_frames`、scene 参数可能让 task 坐标系或物块行为不同，所以结果可能不一样。

如果你看的是 session analyzer 的 `trajectory_track_map.png`，它同时画 pinch path、block path、轨道、target 和事件点。物块路径当然会随 block size / track width / scene mode 改变。

## 当前限制

- 现有 GUI 是 replay/live debug display，不是正式 experiment lifecycle GUI；实验员和受试者仍共用一个窗口，也没有 Stop / Abort / Pause / Resume 的完整界面控制。
- 当前只有 Matrix / electrotactile ESP32 的 Stage 1 TCP 输出；vibration 仍是 protocol pending/logging。`haptic.csv` 是逻辑状态，`haptic_command_log.csv` 是 command/发送记录；还没有硬件 acknowledgement 或 behavioral RT analyzer。
- `run_live_integrated_session.py` 是单次 trial 的连续标定 + trial 调试流程，仍标记 `is_formal_experiment=false`；target 进入只做诊断，trial 主要由实验员按 `e` 完成。
- 当前不直接依赖 MANUS/Vive SDK，而是接收 `manus_vive_com` 发送的 combined JSON；也没有完整 MANUS/Vive rotation fusion。
- 没有正式在线 calibration GUI；当前只有命令行版 live table-line calibration 和 integrated runner。
- 没有全局自动 YAML 配置。cue / termination config 可以单独读取 JSON/YAML，地图和标定仍使用各自 JSON。
- `offline_replay_autocalibrated.py` 的 MapConfig replay 仍使用 post-hoc auto calibration，不能标记成正式实验；`offline_replay_formal_calibrated.py` 使用 formal calibration，但仍是 offline replay。
- `run_live_raw_preview.py` 只是实时 raw stream smoke test，不启动正式 trial；MapTemplate replay 也是旧数据诊断/探索工具，不是正式实验地图。

比较自然的下一步是：把 integrated runner 发展成正式实验员/受试者 GUI 和完整实验序列，接入真实 haptic hardware，并在 cue、timing 和行为响应之间建立可验证的 RT 分析链路。
