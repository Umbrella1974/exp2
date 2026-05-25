# Constrained Block Experiment Engine

这个项目是一个分阶段实现的实验交互引擎。当前代码已经包含：

- Stage 1：受轨道约束的虚拟木块核心交互逻辑。
- Stage 2：trial 生命周期、坐标系、mock/replay、CSV recorder。
- Stage 3：MANUS/Vive raw JSON 的稳定 adapter contract。
- Stage 3.1：JSONL raw frame source 和 `run_live_preview.py` smoke preview。

当前没有接 MANUS SDK、Vive SDK、socket、UI 或触觉硬件。真实设备数据需要先变成 raw JSON dict，再进入：

```text
RawFrameSource
  -> parse_raw_manus_vive_frame()
  -> ManusViveExperimentAdapter
  -> ExperimentInputSample
  -> TrialController
  -> BlockController
```

## 环境准备

建议在项目根目录运行命令：

```powershell
cd D:\research_history\first_one\research_code\exp2
```

需要 Python 3.10+。当前测试依赖主要是：

```powershell
pip install pytest numpy
```

如果后面要真的读取 YAML 配置，还需要：

```powershell
pip install pyyaml
```

注意：当前代码还没有自动读取 YAML 的 loader，`config_example.yaml` 是配置模板和记录文件，不会被 `run_live_preview.py` 自动加载。

## 运行测试

完整测试：

```powershell
pytest -q
```

只测试 Stage 3 raw/parser/adapter：

```powershell
pytest tests/test_raw_frame_source.py tests/test_raw_manus_vive_parser.py tests/test_manus_vive_adapter.py -q
```

只测试 preview pipeline：

```powershell
pytest tests/test_run_live_preview_pipeline.py tests/test_stage3_fake_live_pipeline.py -q
```

如果完整测试失败，先看失败文件名。`test_block_controller.py` 失败通常是 Stage 1 状态机问题；`test_raw_manus_vive_parser.py` 失败通常是 raw JSON schema 或 parser 问题；`test_run_live_preview_pipeline.py` 失败通常是 preview wiring 问题。

## 用 JSONL 跑 Smoke Preview

`run_live_preview.py` 目前只读 JSONL 文件。每一行必须是一个 raw JSON dict。

基本命令：

```powershell
python run_live_preview.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_01.jsonl --max-frames 1000 --print-every 10 --trial-id smoke_001
```

常用参数：

```text
--raw-jsonl       JSONL 数据文件路径
--max-frames     最多处理多少帧
--print-every    每隔多少帧打印一次状态
--trial-id       preview trial id，默认 preview
--timestamp-scale 原始 timestamp 转秒的比例，默认 0.001
--calibration    可选 calibration JSON 路径
```

如果 raw timestamp 是 epoch 毫秒，使用默认 `--timestamp-scale 0.001`。如果 timestamp 已经是秒，用：

```powershell
python run_live_preview.py --raw-jsonl data.jsonl --timestamp-scale 1.0
```

打印输出里最重要的字段：

```text
tracker_valid         tracker 数据是否可用
hand_valid            MANUS hand frame 是否可用
pinch_valid           thumb/index 节点是否足够计算 pinch
pinch_distance        thumb/index tip 距离
pinch_center_world    adapter 输出的 world 坐标
pinch_center_task     TrialController 转换后的 task 坐标
contact_state         pinch center 是否进入当前 block box
pinch_state           pinch distance 是否足够夹持
block_motion_state    block 当前运动状态
stop_reason           停止原因，例如 TRACKING_INVALID / PINCH_INSUFFICIENT / TRACK_BLOCKED
```

## 如何判断输出有没有问题

如果 `tracker_valid=False` 很多，优先检查：

- raw 里的 `trackers` 是否存在。
- 默认 `tracker_index=0` 是否选错 tracker。
- tracker 是否有 `position: [x, y, z]`。
- tracker 的 `valid` 是否为 `false`。

如果 `hand_valid=True` 但 `pinch_valid=False`，优先检查：

- node id 是否正确。
- 默认 thumb tip 是 `4`，index tip 是 `9`。
- 对应 node 是否有 3D `position`。

如果 `pinch_state=PINCH_INSUFFICIENT` 很多，优先检查：

- `pinch_distance` 分布是否明显大于默认阈值。
- 默认 `pinch_grab_threshold=0.025`，`pinch_release_threshold=0.035`，单位按米理解。

如果一直 `contact_state=OUTSIDE_BLOCK`，优先检查：

- 当前是否使用了 demo task 坐标系。
- `pinch_center_task` 是否在 block box 附近。
- 默认 block 初始中心是 `(0,0,0)`，默认 block size 是 `1 x 1 x 1`。
- 真实实验通常需要 calibration 和真实 block/track 配置。

## Calibration 文件

`--calibration` 接收的是 JSON，不是 YAML。保存格式由 `calibration_io.py` 定义：

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

如果不传 `--calibration`，preview 使用 demo 坐标系：

```text
origin_world = [0, 0, 0]
x_axis_world 约等于 [1, 0, 0]
z_axis_world = [0, 0, 1]
```

这只适合 smoke test，不适合判断真实实验空间是否对齐。

## YAML 怎么改

当前仓库没有自动读取 YAML 的逻辑。`config_example.yaml` 是推荐模板，用来记录一次实验或一次 preview 的配置。现在你需要把 YAML 里的值手动对应到命令行参数或 Python config 里。

最常改的是 `preview`：

```yaml
preview:
  raw_jsonl: "D:/download_edge/dataansys/raw_frames_15_parts/experiment_01.jsonl"
  trial_id: "smoke_001"
  max_frames: 1000
  print_every: 10
  timestamp_scale: 0.001
  calibration: null
```

对应运行命令：

```powershell
python run_live_preview.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_01.jsonl --max-frames 1000 --print-every 10 --trial-id smoke_001 --timestamp-scale 0.001
```

如果 timestamp 已经是秒：

```yaml
preview:
  timestamp_scale: 1.0
```

如果有 calibration JSON：

```yaml
preview:
  calibration: "D:/path/to/calibration.json"
```

对应命令加：

```powershell
--calibration D:\path\to\calibration.json
```

设备 adapter 相关字段在 `adapter`：

```yaml
adapter:
  skeleton_index: 0
  tracker_index: 0
  thumb_tip_node_id: 4
  index_tip_node_id: 9
  local_offset: [0.0, 0.0, 0.0]
  local_scale: 1.0
  use_tracker_rotation: false
```

修改建议：

- 如果 hand 数据有效但 pinch 一直无效，优先改 `thumb_tip_node_id` 和 `index_tip_node_id`。
- 如果 tracker 有多个，且第 0 个不是目标 tracker，改 `tracker_index`。
- 如果 skeleton 有多个，且第 0 个不是目标手，改 `skeleton_index`。
- 如果 `pinch_center_world` 整体偏移固定，改 `local_offset`。
- 如果 MANUS local 坐标尺度明显不对，改 `local_scale`。
- `use_tracker_rotation` 当前保留为 future TODO，第一版还没有做完整 rotation fusion。

核心交互阈值在 `engine`：

```yaml
engine:
  pinch_grab_threshold: 0.025
  pinch_release_threshold: 0.035
  max_hand_delta_per_frame: 0.25
  trial_timeout_seconds: 60.0
  max_detach_count: 3
```

修改建议：

- 如果真实 pinch distance 常见值大于 `0.035`，需要重新评估 pinch 阈值。
- 如果经常出现 `STOPPED_BY_LARGE_DELTA`，可能是坐标尺度过大、采样跳变或 `max_hand_delta_per_frame` 太小。
- 如果 preview 第一帧 timeout，通常是 `timestamp_scale` 不对。

## 多 Trial JSONL 注意事项

当前 `run_live_preview.py` 把一个 JSONL 当成一个 preview trial。它适合 smoke test，不适合一次性评估十几组实验。

如果一个 JSONL 包含多组 trial，需要先按边界切分。边界可以来自：

- raw 里的 `trial_id`
- raw 里的 `subject_end=true`
- 文件名或外部记录

否则第一组 `subject_end` 后，后续帧会继续进入同一个已经结束的 trial。

## 文件型 Source 和真实 Socket Source

JSONL 文件有结尾，所以 preview 里可以使用：

```python
for frame in source:
    ...
```

真实 socket/live source 以后不要这样写。socket 的 `None` 表示“当前没有新帧”，不表示数据结束。实时流应该使用：

```python
while running:
    raw = source.next_frame()
    if raw is None:
        continue
    ...
```

## 当前还没做的事

- 没有正式实验 UI。
- 没有触觉硬件控制。
- 没有真实 socket 接入。
- 没有自动读取 YAML 配置。
- 没有完整 MANUS/Vive rotation fusion。
- 没有多 trial JSONL 离线诊断脚本。

下一步最值得做的是增加一个 `analyze_raw_jsonl.py`，统计 tracker/hand/pinch 有效率、pinch distance 分布、pinch center 范围和每个 trial 的事件摘要。
