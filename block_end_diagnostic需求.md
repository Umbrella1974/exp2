# Block End Diagnostic 需求书

## 背景

当前 `analyze_session.py` 的 `trajectory_track_map.png` 会画出：

- `pinch path`
- `block path`
- `block start`
- `block end`
- track boxes / target region / events

其中 `block end` 不是一个事件，而是 `processed_frames.csv` 中最后一个可用 `block_center_task_*` 坐标。这个点是状态机计算后的物块最终位置，不是 raw 数据里的原始字段。

因此，当图上看到 block path 在某处停止时，用户还需要手动检查：

- 最后一次真实移动发生在哪一帧
- 下一帧为什么没有继续移动
- 是 `PINCH_INSUFFICIENT`
- 是 `TRACK_BLOCKED`
- 是 `contact_exit`
- 是 `tracking_invalid`
- 是 raw/replay 文件结束
- 还是只是后续没有移动但状态仍然正常

本功能目标是让 analyzer 自动解释“为什么 block end 在这里”。

## 目标

在 `analyze_session.py` 中新增只读诊断功能：`block end diagnostic`。

该功能不改变任何实验状态机、不重新运行 `TrialController` / `BlockController`，只根据已有 session 输出文件解释：

1. `block end` 对应的最终物块位置。
2. 最后一次 block center 发生变化的帧。
3. 最后一次移动之后，第一帧停止移动的直接原因。
4. 如果能进一步解释，则给出可读说明，例如：
   - pinch distance 超过 release threshold
   - pinch 离开 block AABB 的具体轴和距离
   - track blocked 的具体 `track_state`
   - tracking invalid
   - 数据结束

## 非目标

本功能不做以下事情：

- 不修改 `BlockController` 核心逻辑。
- 不修改 `TrialController` 核心逻辑。
- 不修改 `TaskCoordinateSystem`。
- 不修改 replay 结果。
- 不重新判定 slip / blocked / contact。
- 不把 `target_region` 当作 trial 结束条件。
- 不把 `block end` 改成一个真实事件。
- 不引入 GUI。

## 术语定义

### block end

`processed_frames.csv` 中最后一个有效 `block_center_task_x/y/z`。

注意：它只是最终记录位置，不是事件。

### last moved frame

最后一个满足以下条件的帧：

```text
当前帧 block_center_task 与上一帧 block_center_task 的距离 > movement_epsilon
```

默认 `movement_epsilon = 1e-9`，用于避免浮点噪声。第一版可以不暴露 CLI 参数。

### first stop frame

`last moved frame` 之后的第一帧。

如果 `last moved frame` 已经是最后一帧，则说明 replay/recording 在移动后直接结束。

### block end reason

根据 `first stop frame` 的已有字段归类出来的解释标签。

## 输入文件

该功能只读取 session 目录中的已有文件：

必需：

```text
processed_frames.csv
trial_config.json
```

可选：

```text
events.csv
trial_summary.json
session_meta.json
```

## 输出位置

写入：

```text
session/analysis_summary.json
```

不写回：

```text
processed_frames.csv
events.csv
trial_config.json
```

## 新增 summary 字段

建议在 `analysis_summary.json` 中新增：

```json
{
  "block_end_frame_index": 1881,
  "block_end_time": 1779301408.49,
  "block_end_position_task": {
    "x": 0.18285335349698914,
    "y": -0.054629369275190916,
    "z": 0.3989990000000002
  },
  "block_last_moved_frame_index": 360,
  "block_last_moved_time": 1779301384.293,
  "block_last_moved_position_task": {
    "x": 0.18285335349698914,
    "y": -0.054629369275190916,
    "z": 0.3989990000000002
  },
  "block_first_stop_frame_index": 361,
  "block_first_stop_time": 1779301384.309,
  "block_end_reason": "contact_exit",
  "block_end_subreason": "UNEXPECTED_DETACH",
  "block_end_explanation": "After the last moved frame, pinch_center_task left the reconstructed block AABB on x+ by 0.003655 m.",
  "block_end_diagnostic_available": true,
  "block_end_nearby_frames": []
}
```

其中 `block_end_nearby_frames` 可以第一版先不放全量，或只放紧凑字段。

建议紧凑字段：

```json
{
  "frame_index": 361,
  "time": 1779301384.309,
  "pinch_distance": 0.016551218988340412,
  "contact_state": "OUTSIDE_BLOCK",
  "pinch_state": "PINCH_VALID",
  "block_motion_state": "FREE_VISIBLE",
  "stop_reason": "NONE",
  "track_state": "INSIDE_TRACK",
  "detach_state": "UNEXPECTED_DETACH",
  "slip_active": false,
  "slip_reason": ""
}
```

## reason 分类规则

第一版按以下优先级分类 `block_end_reason`：

### 1. recording_ended_while_moving

条件：

```text
last_moved_frame 是最后一帧
```

解释：

```text
The replay ended immediately after the last moved frame; no following frame is available to explain why movement stopped.
```

### 2. tracking_invalid

条件：

```text
first_stop_frame.tracker_valid == False
```

解释重点：

```text
tracking invalid caused interaction to stop on the first non-moving frame.
```

### 3. track_blocked

条件：

```text
first_stop_frame.stop_reason == TRACK_BLOCKED
或 first_stop_frame.blocked_force_active == True
```

subreason：

```text
track_state
```

例：

```text
TRACK_BLOCKED / BLOCKED_Z_POS
```

解释重点：

```text
candidate block center was clamped by track boundary.
```

### 4. pinch_insufficient

条件：

```text
first_stop_frame.stop_reason == PINCH_INSUFFICIENT
或 first_stop_frame.pinch_state == PINCH_INSUFFICIENT
```

解释中应包含：

```text
pinch_distance
pinch_threshold.release
```

例：

```text
pinch_distance 0.110325 exceeded release threshold 0.11.
```

### 5. large_delta

条件：

```text
first_stop_frame.stop_reason == LARGE_DELTA
或 first_stop_frame.large_delta == True
```

解释中应包含：

```text
max_hand_delta_per_frame 如果可从 trial_config 或默认配置读到
```

### 6. contact_exit

条件：

```text
first_stop_frame.contact_state == OUTSIDE_BLOCK
或 first_stop_frame.detach_state != NONE
```

subreason：

```text
detach_state
```

如果有 block_size、block center、pinch center，则进一步计算：

```text
pinch_center_task 是否在 block AABB 内
如果不在，指出离开轴、方向、距离
```

例：

```text
pinch_center_task left the reconstructed block AABB on x+ by 0.003655 m.
```

### 7. not_grabbed_or_free_visible

条件：

```text
first_stop_frame.block_motion_state == FREE_VISIBLE
但没有明确 detach_state
```

解释：

```text
The first non-moving frame is free-visible/outside interaction, but no explicit detach subtype was available.
```

### 8. unchanged_after_last_move

条件：

```text
first_stop_frame 仍然是 PINCH_VALID + INSIDE_BLOCK
但 block_center 没有变化
```

可能解释：

```text
movement was below min_block_move_distance
或 candidate center equals previous center
```

如果能从 frame 计算 pinch delta，则加入：

```text
pinch_delta_norm
```

### 9. unknown

条件：

```text
以上都无法解释
```

解释：

```text
No clear stop reason could be inferred from recorded fields.
```

## block AABB 诊断

对于 `contact_exit` 类原因，如果可获得：

```text
block_center_task_x/y/z
pinch_center_task_x/y/z
block_size
```

则重建物块 AABB：

```text
min = block_center - block_size / 2
max = block_center + block_size / 2
```

然后计算每个轴的 outside amount：

```text
如果 pinch[axis] < min[axis]:
  direction = axis-
  amount = min[axis] - pinch[axis]

如果 pinch[axis] > max[axis]:
  direction = axis+
  amount = pinch[axis] - max[axis]
```

取 outside amount 最大的轴作为主要解释。

如果 pinch 在 AABB 内，但 `contact_state=OUTSIDE_BLOCK`，写 warning：

```text
block_end diagnostic found contact_exit, but pinch_center_task is inside reconstructed block AABB; inspect block_size or recording semantics.
```

## block_size 来源

复用已有 footprint diagnostic 的 block_size 解析优先级：

1. `trial_config["block_size"]`
2. `trial_config["block_size_task"]`
3. `processed_frames.csv` 中的 `block_size`
4. `processed_frames.csv` 中的 `block_size_x/y/z`
5. 找不到则不失败，只写 warning

## 建议新增内部接口

在 `analyze_session.py` 中新增纯函数：

```python
def _block_end_diagnostic(
    rows: list[dict[str, str]],
    trial_config: dict[str, Any],
    times: TimeSeries,
    warnings: list[str],
    *,
    nearby_window: int = 5,
    movement_epsilon: float = 1e-9,
) -> dict[str, Any]:
    ...
```

职责：

- 找 `block_end`
- 找 `last_moved_frame`
- 找 `first_stop_frame`
- 调用 `_classify_block_end_reason`
- 生成 summary payload

新增：

```python
def _classify_block_end_reason(
    *,
    last_moved_row: dict[str, str],
    first_stop_row: dict[str, str] | None,
    trial_config: dict[str, Any],
    block_size: list[float] | None,
) -> dict[str, Any]:
    ...
```

新增：

```python
def _block_end_aabb_explanation(
    row: dict[str, str],
    block_size: list[float],
) -> dict[str, Any]:
    ...
```

可复用已有 helper：

- `_float_or_none`
- `_int_or_none`
- `_bool`
- `_row_block_center`
- `_row_pinch_center`
- `_resolve_block_size`
- `_point_inside_aabb`

如果已有 helper 位置不方便，可以轻微调整顺序，但不要改变现有行为。

## CLI 参数

第一版建议增加一个可选参数：

```text
--block-end-window 默认 5
```

含义：

```text
analysis_summary.json 中 block_end_nearby_frames 前后保留多少帧。
```

如果希望最小改动，也可以第一版不加 CLI，直接默认 5。

不建议第一版加入太多参数。

## 图像输出

第一版不强制改图。

可选增强：

- 在 `trajectory_track_map.png` 上保持现状。
- 在 `trajectory_track_map_with_block_footprint.png` 上可选标注 `last moved block position`。

但为了最小改动，第一版只写 summary，不改绘图。

## README 更新

README 增加一小节：

```text
如何理解 block end
```

说明：

- `block end` 是最后记录的 block center。
- `block_last_moved_frame_index` 是最后一次真实移动。
- `block_end_reason` 是 analyzer 根据下一帧状态推断出的停止原因。
- 常见原因包括 contact_exit、pinch_insufficient、track_blocked、tracking_invalid、recording_ended。

## 测试需求

更新或新增：

```text
tests/test_analyze_session.py
```

至少覆盖以下场景：

### 1. contact_exit / unexpected detach

构造：

```text
frame 1: GRABBED_MOVING, pinch inside block
frame 2: OUTSIDE_BLOCK, detach_state=UNEXPECTED_DETACH
```

断言：

```text
block_end_reason == "contact_exit"
block_end_subreason == "UNEXPECTED_DETACH"
explanation 包含 left reconstructed block AABB
```

### 2. pinch insufficient

构造：

```text
last moved frame: PINCH_VALID
first stop frame: PINCH_INSUFFICIENT, stop_reason=PINCH_INSUFFICIENT
trial_config.pinch_threshold.release = 0.11
pinch_distance = 0.12
```

断言：

```text
block_end_reason == "pinch_insufficient"
explanation 包含 release threshold
```

### 3. track blocked

构造：

```text
first stop frame.stop_reason = TRACK_BLOCKED
first stop frame.track_state = BLOCKED_X_POS
```

断言：

```text
block_end_reason == "track_blocked"
block_end_subreason == "BLOCKED_X_POS"
```

### 4. recording ended while moving

构造：

```text
最后一帧就是最后移动帧
```

断言：

```text
block_end_reason == "recording_ended_while_moving"
```

### 5. no movement

构造：

```text
所有帧 block_center 不变
```

断言：

```text
block_last_moved_frame_index is None
block_end_reason == "no_block_movement_detected"
```

## 验收标准

实现完成后：

1. `analyze_session.py --no-plots` 也会写 block end diagnostic 字段。
2. 不改变现有 plots 输出。
3. 不改变 `events.csv`。
4. 不改变 `processed_frames.csv`。
5. 对缺失 `block_size`、缺失 pinch 坐标、缺失时间列等情况不失败，只写 warning 或降级解释。
6. `pytest -q` 通过。

## 示例解释

### 示例 1：contact exit

```text
block_end_reason = contact_exit
block_end_subreason = UNEXPECTED_DETACH
block_end_explanation = After the last moved frame, pinch_center_task left the reconstructed block AABB on x+ by 0.003655 m.
```

### 示例 2：pinch insufficient

```text
block_end_reason = pinch_insufficient
block_end_subreason = PINCH_INSUFFICIENT
block_end_explanation = Movement stopped because pinch_distance 0.110325 exceeded release threshold 0.11.
```

### 示例 3：track blocked

```text
block_end_reason = track_blocked
block_end_subreason = BLOCKED_Z_POS
block_end_explanation = Movement stopped because the candidate block center was blocked by the track boundary: BLOCKED_Z_POS.
```

## 实现优先级

建议第一版只做：

1. summary 字段。
2. reason 分类。
3. AABB contact_exit 解释。
4. README。
5. 单元测试。

暂不做：

- 图上额外标注。
- 单独 CSV。
- 重新计算状态机。
- target reached 逻辑。

补充说明：

1. 只在当前帧和上一帧都具有有效 block_center_task_x/y/z 时计算移动距离。
   如果某帧 block center 缺失，则跳过该 pair，不把缺失/恢复当作移动。

2. analysis_summary.json 中记录：
   - block_end_movement_epsilon

3. 如果没有找到 last_moved_frame：
   - block_end_reason = "no_block_movement_detected"
   - block_last_moved_frame_index = null
   - block_first_stop_frame_index = null
   - block_end_diagnostic_available = true
   - block_end_explanation = "No block movement greater than movement_epsilon was detected in processed_frames.csv."

4. reason 分类以 first_stop_frame 为主。
   nearby_window 只用于输出 block_end_nearby_frames，第一版不要用附近窗口自动改写 reason。

5. release threshold 读取 best-effort：
   - trial_config["pinch_threshold"]["release"]
   - trial_config["pinch_thresholds"]["release"]
   - trial_config["pinch_release_threshold"]
   - trial_config["engine_config"]["pinch_release_threshold"]
   如果找不到，解释中只写 pinch_distance，并说明 release threshold unavailable。

6. 如果多个 reason 条件同时满足，按优先级选择第一个，同时可选记录 block_end_secondary_signals。

7. AABB 解释必须标记为 reconstructed diagnostic，不代表重新运行状态机。