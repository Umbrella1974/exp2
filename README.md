# Exp2 Constrained Block Experiment Engine

这个仓库实现的是一个受约束物块交互实验引擎。当前代码主要用于离线 smoke/regression 测试和后续真实实验接入前的管线验证。

当前核心数据流是：

```text
raw JSON / JSONL
  -> JsonlRawFrameSource
  -> parse_raw_manus_vive_frame()
  -> ManusViveExperimentAdapter
  -> ExperimentInputSample
  -> TrialController
  -> BlockController
  -> frames / events / summary
```

当前还没有接入正式 GUI、真实 socket、MANUS SDK、Vive SDK 或触觉硬件。`ManusSocketRawFrameSource` 现在只是 receiver 适配壳。

## 环境准备

在项目根目录运行命令：

```powershell
cd D:\research_history\first_one\research_code\exp2
```

推荐 Python 3.10+。基础依赖：

```powershell
pip install numpy pytest
```

如果希望 `offline_replay_autocalibrated.py` 额外生成 PNG 图，可以安装：

```powershell
pip install matplotlib
```

没有 `matplotlib` 时，核心 replay 和 CSV/JSON 输出仍然可以工作，图像生成会被跳过并写入 warning。

## 跑测试

完整测试：

```powershell
pytest -q
```

只跑 batch 离线报告工具：

```powershell
pytest -q tests/test_batch_offline_replay_report.py
```

只跑 raw/parser/adapter 相关测试：

```powershell
pytest -q tests/test_raw_frame_source.py tests/test_raw_manus_vive_parser.py tests/test_manus_vive_adapter.py
```

当前全量测试应为：

```text
98 passed
```

## 三个常用入口

### 1. `run_live_preview.py`

用于快速检查一个 JSONL 文件能不能被 parser/adapter/TrialController 跑通。它更像实时预览 smoke test，不适合作为批量离线评估工具。

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

如果 raw timestamp 已经是秒，使用：

```powershell
python run_live_preview.py --raw-jsonl data.jsonl --timestamp-scale 1.0
```

文件输入可以用 `for frame in source`，因为 JSONL 有 EOF。以后真实 socket/live source 不要用 `for`，因为 socket 的 `None` 表示“当前没有新帧”，不是数据结束。实时流应该持续调用：

```python
while running:
    raw = source.next_frame()
    if raw is None:
        continue
    ...
```

### 2. `offline_replay_autocalibrated.py`

用于对一段未正式标定的 raw JSONL 做 post-hoc 离线 replay。它会自动构造临时 task coordinate system，自动生成临时 block/track scene，然后跑现有 TrialController / BlockController。

它适合 smoke test 和调试，不是正式实验分析工具。

示例，宽通道正样本：

```powershell
python offline_replay_autocalibrated.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_14.jsonl --out-dir data\offline_replay\experiment_14_block060 --max-frames 5000 --calibration-frames 100 --scene-mode wide-track --block-size 0.6
```

示例，窄通道 blocked 压力测试：

```powershell
python offline_replay_autocalibrated.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_15.jsonl --out-dir data\offline_replay\experiment_15_narrow --max-frames 5000 --calibration-frames 100 --scene-mode narrow-corridor --block-size 0.6 --narrow-track-width 0.08
```

示例，PCA 校准方式：

```powershell
python offline_replay_autocalibrated.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_12.jsonl --out-dir data\offline_replay\experiment_12_pca --max-frames 5000 --calibration-mode pca --calibration-frames 100 --scene-mode wide-track --block-size 0.6
```

常用参数：

```text
--raw-jsonl              输入 raw JSONL
--out-dir                输出目录
--max-frames             最多 replay 多少帧
--calibration-mode       initial-window 或 pca
--calibration-frames     用前多少个有效点估计临时坐标系
--scene-mode             wide-track / fitted-corridor / narrow-corridor
--block-size             临时物块尺寸，三个轴同值
--track-margin           wide-track 外扩边界
--track-width            fitted-corridor 通道宽度
--narrow-track-width     narrow-corridor 通道宽度
--z-tolerance            corridor 的 z 容差
```

重要说明：

- `offline_replay_autocalibrated.py` 会为了离线完整 replay 基本关闭 trial timeout 和 too-many-detach 限制。
- 它会强制 replay 时 `subject_end=False`，避免一段 raw 里提前结束后无法继续观察后续帧。
- 它的 task coordinate system 和 scene 都是临时自动估计，不代表正式实验标定。
- 当前临时物块初始中心来自第一帧有效输入点，命令行只暴露 `--block-size`，没有暴露手动 block center。

### 3. `batch_offline_replay_report.py`

用于一条命令批量跑多组 JSONL，并生成 `batch_summary.csv` / `batch_summary.json`。它是回归基线工具，用来防止后续改代码时把已经跑通的 parser / adapter / TrialController / BlockController pipeline 改坏。

它不是正式实验分析工具。

基本命令：

```powershell
python batch_offline_replay_report.py --cases data/offline_replay_batch/cases_example.json --out-dir data/offline_replay_batch --overwrite
```

如果希望 expectation 失败时返回非零退出码：

```powershell
python batch_offline_replay_report.py --cases data/offline_replay_batch/cases_example.json --out-dir data/offline_replay_batch --overwrite --stop-on-fail
```

参数说明：

```text
--cases              必填，batch cases JSON
--out-dir            默认 data/offline_replay_batch
--overwrite          只清理当前 case_id 对应的输出子目录
--stop-on-fail       有 FAIL/ERROR 时提前停止并返回非零退出码
--python-executable  默认使用当前 sys.executable
```

`cases_example.json` 里可以写：

```json
{
  "cases": [
    {
      "id": "experiment_14_block060",
      "raw_jsonl": "D:/download_edge/dataansys/raw_frames_15_parts/experiment_14.jsonl",
      "description": "positive moving sample",
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

支持的 expectation op：

```text
==
!=
>
>=
<
<=
between
```

`between` 示例：

```json
{"metric": "pinch_distance_mean", "op": "between", "value": [0.01, 0.20]}
```

case id 只能包含字母、数字、下划线、短横线和点号。`--overwrite` 不会删除整个 `out_dir`，只会删除对应 `out_dir/case_id`。

## 输出文件怎么看

单个 offline replay case 输出目录通常包含：

```text
frames.csv
events.csv
calibration_auto.json
scene_auto.json
summary.json
task_trajectory_xyz.png
pinch_distance_over_time.png
block_center_xyz_over_time.png
contact_state_over_time.png
stop_reason_counts.png
```

如果没有安装 `matplotlib`，PNG 可能不存在，不影响 CSV/JSON。

### `summary.json`

最常看的字段：

```text
total_raw_frames                  原始帧数
replayed_raw_frames               实际 replay 帧数
valid_input_frames                有可用输入点的帧数
valid_pinch_frames                有可用 pinch center/input point 的帧数
tracker_invalid_frame_count       tracker invalid 帧数
invalid_input_frame_count         输入无效帧数
generated_contact_enter_count     contact_enter 事件数
generated_contact_exit_count      contact_exit 事件数
slip_active_frame_count           slip active 帧数
blocked_frame_count               blocked 帧数
large_delta_frame_count           large delta 帧数
pinch_distance_min/mean/max       pinch 距离统计
task_trajectory_range             输入点在 task 坐标系下的范围
warnings                          离线工具产生的警告
```

注意：`task_trajectory_range` 是输入点轨迹范围，不是物块轨迹。它是 world input point 转到 task coordinate system 后的范围。同一个 raw 样本如果 calibration 参数不同，task trajectory 也会不同。

### `frames.csv`

判断状态机是否真的移动物块，优先看：

```text
pinch_state
contact_state
block_motion_state
stop_reason
block_center_task_x
block_center_task_y
block_center_task_z
events
```

几个常见组合：

```text
PINCH_VALID + INSIDE_BLOCK + GRABBED_MOVING
```

说明抓取有效、接触物块、物块正在移动。

```text
PINCH_INSUFFICIENT + INSIDE_BLOCK + GRABBED_PINCH_INSUFFICIENT
```

说明手在物块里，但 pinch 距离不满足抓取阈值，通常会触发 slip 相关反馈。

```text
PINCH_VALID + OUTSIDE_BLOCK
```

说明 pinch 有效，但没接触到物块，不应该移动物块。

```text
GRABBED_BLOCKED 或 stop_reason=TRACK_BLOCKED
```

说明通道约束阻挡了物块移动。

真正的物块轨迹在：

```text
block_center_task_x
block_center_task_y
block_center_task_z
```

不是 `task_trajectory_range`。

### `events.csv`

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

`block_moved` 是 moving 状态从 false 到 true 的边缘事件，不是每一帧移动都写一条。每帧运动情况要看 `frames.csv`。

## 当前几类离线样本的意义

根据目前跑过的 `experiment_10` 到 `experiment_15`：

```text
experiment_12 / 14 / 15   正样本，可以看到 GRABBED_MOVING
experiment_12 / 14 / 15 narrow/fitted   通道压力测试，可以看到 GRABBED_BLOCKED
experiment_10 / 11        pinch 距离太大，适合作 no-pinch 负样本
experiment_13             有 PINCH_VALID 但在物块外，适合作 outside-block 负样本
```

这些结果适合做 regression baseline，不适合直接写成正式实验结论。

## YAML 目前怎么用

`config_example.yaml` 当前只是配置模板和记录文件，代码不会自动读取它。

也就是说，如果你在 YAML 里修改了参数，它不会自动影响：

```text
run_live_preview.py
offline_replay_autocalibrated.py
batch_offline_replay_report.py
```

现在需要手动把 YAML 里的值对应到命令行参数，或者改 Python 里的 config。

例如 YAML 里记录：

```yaml
offline_replay:
  raw_jsonl: "D:/download_edge/dataansys/raw_frames_15_parts/experiment_14.jsonl"
  out_dir: "data/offline_replay/experiment_14_block060"
  max_frames: 5000
  calibration_mode: "initial-window"
  calibration_frames: 100
  scene_mode: "wide-track"
  block_size: 0.6
```

对应命令是：

```powershell
python offline_replay_autocalibrated.py --raw-jsonl D:\download_edge\dataansys\raw_frames_15_parts\experiment_14.jsonl --out-dir data\offline_replay\experiment_14_block060 --max-frames 5000 --calibration-mode initial-window --calibration-frames 100 --scene-mode wide-track --block-size 0.6
```

如果要批量跑，推荐把这些参数写进：

```text
data/offline_replay_batch/cases_example.json
```

而不是 YAML。

## Calibration JSON

`run_live_preview.py --calibration` 接收的是 JSON，不是 YAML。格式由 `calibration_io.py` 定义，示例：

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

如果不传 `--calibration`，preview 使用 demo 坐标系。这只适合 smoke test，不适合判断真实实验空间是否对齐。

`offline_replay_autocalibrated.py` 不读 calibration JSON。它会根据 raw 数据前一段有效输入点自动估计临时坐标系。

## 常见问题

### 一直 timeout

优先检查 `timestamp_scale`。如果 raw timestamp 是毫秒，默认 `0.001` 是对的；如果 raw timestamp 已经是秒，应使用 `--timestamp-scale 1.0`。离线 autocalibrated replay 已经基本关闭 timeout，`run_live_preview.py` 更容易暴露 timestamp 问题。

### `tracker_valid=False` 很多

检查：

```text
raw 里是否有 trackers
tracker_index 是否选错
tracker 是否有 position: [x, y, z]
tracker.valid 是否为 false
```

### `hand_valid=True` 但 pinch 无效

检查：

```text
thumb_node 是否正确，默认 4
index_node 是否正确，默认 9
对应 node 是否有 3D position
skeleton_index 是否选错
```

### `PINCH_INSUFFICIENT` 很多

当前核心阈值在 `config.py`：

```text
pinch_grab_threshold = 0.025
pinch_release_threshold = 0.035
```

单位按米理解。如果真实 pinch distance 常见值远大于 0.035，需要重新评估 node、坐标尺度或阈值。

### 有 `PINCH_VALID` 但物块不动

看 `contact_state`。如果是 `OUTSIDE_BLOCK`，说明手指闭合了但没有碰到物块，这是合理负样本。

### `task_trajectory_xyz.png` 是什么

现在它画的是输入点在 task 坐标系下的 x/y 轨迹。它不是物块轨迹。物块轨迹看 `block_center_xyz_over_time.png` 或 `frames.csv` 里的 `block_center_task_*`。

## 当前还没做的事

```text
没有正式 GUI
没有真实 socket live loop
没有触觉硬件控制
没有自动读取 YAML 配置
没有完整 MANUS/Vive rotation fusion
没有正式在线 calibration 流程
没有正式 scene 配置流程
```

下一步建议是：用 `batch_offline_replay_report.py` 固化当前几组离线样本的 baseline，然后再进入正式 calibration、真实 scene 配置和 live/socket/haptic 联调。
