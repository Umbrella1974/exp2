# Exp2 Constrained Block Experiment Engine

这个仓库实现的是一个受约束物块交互实验引擎。当前重点是离线 smoke/regression、session 记录、事后可视化分析，以及正式实验地图配置的基础层。

核心实时数据流是：

```text
raw JSON / JSONL
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

基础依赖：

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

当前应为：

```text
130 passed
```

常用局部测试：

```powershell
pytest -q tests/test_batch_offline_replay_report.py
pytest -q tests/test_session_recorder.py tests/test_offline_replay_session_output.py
pytest -q tests/test_analyze_session.py
pytest -q tests/test_map_config.py tests/test_map_generator.py
```

## 常用入口

### `run_live_preview.py`

用于快速检查一个 JSONL 文件能不能被 parser/adapter/TrialController 跑通。它是 preview/smoke 工具，不适合批量离线评估。

```powershell
python run_live_preview.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_15.jsonl --max-frames 1000 --print-every 10 --trial-id smoke_001
```

常用参数：

```text
--raw-jsonl         输入 JSONL，每行一个 raw dict
--max-frames       最多处理多少帧
--print-every      每隔多少帧打印一次
--trial-id         trial id，默认 preview
--timestamp-scale  timestamp 转秒比例，默认 0.001
--calibration      可选 calibration JSON
```

文件输入可以用 `for frame in source`，因为 JSONL 有 EOF。以后真实 socket/live source 不要用 `for`，因为 socket 的 `None` 表示“当前没有新帧”，不是数据结束。实时流应该持续调用 `next_frame()`。

### `offline_replay_autocalibrated.py`

用于对未正式标定 raw JSONL 做 post-hoc 离线 replay。它会自动构造临时 task coordinate system，自动生成临时 block/track scene，然后跑现有 pipeline。

它适合 smoke test 和调试，不是正式实验分析工具。

宽通道示例：

```powershell
python offline_replay_autocalibrated.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_14.jsonl --out-dir data\offline_replay\experiment_14_block060 --max-frames 5000 --calibration-frames 100 --scene-mode wide-track --block-size 0.6
```

窄通道 blocked 压力测试：

```powershell
python offline_replay_autocalibrated.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_15.jsonl --out-dir data\offline_replay\experiment_15_narrow --max-frames 5000 --calibration-frames 100 --scene-mode narrow-corridor --block-size 0.6 --narrow-track-width 0.08
```

写标准 session 目录：

```powershell
python offline_replay_autocalibrated.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_14.jsonl --out-dir data\offline_replay\experiment_14_block060 --max-frames 5000 --calibration-frames 100 --scene-mode wide-track --block-size 0.6 --write-session
```

默认 session 写到：

```text
out_dir/session
```

如果该目录已存在且非空，会报错，不会静默覆盖。也可以显式指定：

```powershell
--session-dir data\offline_sessions\experiment_14_block060 --session-id exp14_block060 --subject-id S001 --notes "offline smoke test"
```

重要说明：

- `offline_replay_autocalibrated.py` 会为了离线完整 replay 基本关闭 trial timeout 和 too-many-detach 限制。
- replay 时会强制 `subject_end=False`，避免一段 raw 中途结束后无法观察后续帧。
- 自动 calibration 和自动 scene 都是 post-hoc 临时结果，不代表正式实验标定。
- 当前临时物块初始中心来自第一帧有效输入点，命令行只暴露 `--block-size`，没有暴露手动 block center。

### `batch_offline_replay_report.py`

用于一条命令批量跑多组 raw JSONL，并生成 `batch_summary.csv` / `batch_summary.json`。它是 regression baseline 工具，防止后续改代码时破坏已经跑通的 parser / adapter / TrialController / BlockController pipeline。

```powershell
python batch_offline_replay_report.py --cases data/offline_replay_batch/cases_example.json --out-dir data/offline_replay_batch --overwrite
```

如果 expectation 失败时需要非零退出码：

```powershell
python batch_offline_replay_report.py --cases data/offline_replay_batch/cases_example.json --out-dir data/offline_replay_batch --overwrite --stop-on-fail
```

`--overwrite` 只清理当前 `case_id` 对应子目录，不删除整个 `out_dir`。

### `analyze_session.py`

用于读取 Stage 4A session 目录，生成 `analysis_summary.json` 和可视化图像。它只做事后分析，不重新运行 TrialController / BlockController，不写回 `events.csv`。

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
--out-dir              可选，只改变图像输出目录
--no-plots             只生成 analysis_summary.json
--event-label-limit    图中最多标注多少事件文字，默认 40
--overwrite            覆盖 analysis_summary.json 和本脚本生成的 PNG
--time-column          sample_time / trial_time / raw_timestamp
```

输出：

```text
session/analysis_summary.json
session/plots/timeseries_xyz_with_events.png
session/plots/pinch_distance_with_events.png
session/plots/trajectory_track_map.png
session/plots/state_timeline.png
session/plots/haptic_timeline.png
```

`--out-dir` 只改变图片目录，`analysis_summary.json` 固定写在 `session_dir` 下。

## Session 输出

`--write-session` 会生成：

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

`raw_frames.jsonl` 严格保存原始 raw dict，不额外写 `frame_index`，不改字段。

`device_frames.jsonl` 保存 parser 后的 DeviceFrame 摘要，包括新版 timing 字段：

```text
combined_monotonic_ms
skeleton_receive_monotonic_ms
tracker_receive_monotonic_ms
sync_delta_ms
skeleton_callback_index
tracker_callback_index
tracker_last_update_time
```

`processed_frames.csv` 是最常用的逐帧状态表，包含：

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

注意：offline replay 中如果前几帧在 trial start time 之前，`trial_time` 可能为负数。这表示 pre-roll 数据，不是状态机错误。

## MapConfig 和 MapGenerator

MapConfig 是正式实验地图配置的基础层，用手工 JSON 或规则生成的多段轨道，编译成 `BlockController` 已经支持的 `TrackRegion`。本阶段只做 map core，不接入 offline replay 或 analyzer。

### 手动加载 map

示例地图在：

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

输出会包含：

```text
map_config_version = 1
map_source_type = "manual" 或 "generated"
完整 track_boxes
target_region
block_initial_center_task
block_size
metadata.generated / generator_name / generator_seed / generator_params
```

### 规则生成 xoy 正交轨道

第一版 generator 只支持 xoy 平面上的 axis-aligned AABB corridor。每段只能 straight、left 或 right，转弯都是 90°，不支持斜向、不支持 180° 掉头。

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
)
```

每个生成 segment 都包含：

```text
id
order
label
min / max
metadata.segment_direction
metadata.segment_length
metadata.turn_from_previous
```

`start` 同时是 `block_initial_center_task`，并且第一段 track box 必须包含它。

## 输出文件怎么看

### `summary.json` / `trial_summary.json`

常看字段：

```text
total_raw_frames
replayed_raw_frames
valid_input_frames
valid_pinch_frames
tracker_invalid_frame_count
invalid_input_frame_count
generated_contact_enter_count
generated_contact_exit_count
slip_active_frame_count
blocked_frame_count
large_delta_frame_count
haptic_active_frame_count
haptic_event_count
pinch_distance_min/mean/max
task_trajectory_range
warnings
```

`task_trajectory_range` 是输入点在 task 坐标系下的范围，不是物块轨迹。真正的物块轨迹看 `block_center_task_x/y/z`。

### `frames.csv` / `processed_frames.csv`

判断物块是否真的移动，优先看：

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

说明抓取有效、接触物块、物块移动。

```text
PINCH_INSUFFICIENT + INSIDE_BLOCK + GRABBED_PINCH_INSUFFICIENT
```

说明手在物块里，但 pinch 距离不满足抓取阈值。

```text
PINCH_VALID + OUTSIDE_BLOCK
```

说明 pinch 有效，但没有接触物块，不应移动物块。

```text
GRABBED_BLOCKED 或 stop_reason=TRACK_BLOCKED
```

说明通道约束阻挡了移动。

### `events.csv`

`events.csv` 只记录 pipeline 已有离散事件。`analyze_session.py` 会在内部派生 slip/blocked/haptic 边缘用于 summary 和图，但不会写回 `events.csv`。

常见事件：

```text
tracking_invalid
tracking_recovered
contact_enter
contact_exit
pinch_valid
pinch_insufficient
block_moved
slip_start
slip_end
block_blocked_start
block_blocked_end
blocked_force_start
blocked_force_end
active_release
unexpected_detach
```

`block_moved` 是 moving 从 false 到 true 的边缘事件，不是每一帧移动都写一条。

## 当前离线样本经验

目前跑过的 `experiment_10` 到 `experiment_15` 大致可以这样用：

```text
experiment_12 / 14 / 15                正样本，可以看到 GRABBED_MOVING
experiment_12 / 14 / 15 narrow/fitted  通道压力测试，可以看到 GRABBED_BLOCKED
experiment_10 / 11                     pinch 距离太大，适合作 no-pinch 负样本
experiment_13                          有 PINCH_VALID 但在物块外，适合作 outside-block 负样本
```

这些适合做 regression baseline，不适合直接写成正式实验结论。

## YAML 目前怎么用

`config_example.yaml` 当前只是模板和记录文件，代码不会自动读取它。修改 YAML 不会自动影响：

```text
run_live_preview.py
offline_replay_autocalibrated.py
batch_offline_replay_report.py
analyze_session.py
```

现在需要手动把 YAML 里的值对应到命令行参数，或者写进 batch cases JSON / map JSON。

## Calibration JSON

`run_live_preview.py --calibration` 接收的是 JSON，不是 YAML。格式由 `calibration_io.py` 定义：

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

`offline_replay_autocalibrated.py` 不读 calibration JSON。它会根据 raw 数据前一段有效输入点自动估计临时坐标系。

## 常见问题

### 一直 timeout

优先检查 `timestamp_scale`。如果 raw timestamp 是毫秒，默认 `0.001` 是对的；如果 raw timestamp 已经是秒，应使用 `--timestamp-scale 1.0`。

### `tracker_valid=False` 很多

检查 raw 里是否有 `trackers`、`tracker_index` 是否选错、tracker 是否有 `position`、`valid` 是否为 false。

### `hand_valid=True` 但 pinch 无效

检查 `thumb_node`、`index_node`、`skeleton_index`，以及对应 node 是否有 3D `position`。

### `PINCH_INSUFFICIENT` 很多

当前核心阈值在 `config.py`：

```text
pinch_grab_threshold = 0.025
pinch_release_threshold = 0.035
```

单位按米理解。如果真实 pinch distance 常见值远大于 0.035，需要重新评估 node、坐标尺度或阈值。

### `task_trajectory_xyz.png` 是什么

它画的是输入点在 task 坐标系下的 x/y 轨迹，不是物块轨迹。物块轨迹看 `block_center_xyz_over_time.png` 或 `block_center_task_*`。

## 仍未完成

```text
没有正式 GUI
没有真实 socket live loop
没有触觉硬件控制
没有自动读取 YAML 配置
没有完整 MANUS/Vive rotation fusion
没有正式在线 calibration 流程
MapConfig 还没有接入 replay/session/analyzer
```

建议下一步：把 MapConfig 接入正式 trial/session 配置，然后让 analyzer 的 `trajectory_track_map` 使用完整多段 `track_boxes` 绘制真实轨道，而不是只画外包围边界。
