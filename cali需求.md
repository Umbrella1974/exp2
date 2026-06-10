你现在要在 `exp2` 仓库的 `cue-stage` 分支上做两个相关修改：

1. 保存 live 使用的 pinch node / adapter 配置，并让 replay 自动读取 session 配置。
2. 新增可选 pinch-distance threshold calibration，用于按受试者/手指个体化确定 pinch on/off 阈值。

请先阅读当前代码，不要依赖 README。重点看：

* `run_live_integrated_session.py`
* `run_replay_debug_gui.py`
* `replay_debug_runner.py`
* `manus_vive_adapter.py`
* `pinch_feature_extractor.py`
* `device_frame_models.py`
* calibration / session / trial_config 输出相关代码
* summary / trial_summary / session_meta 输出相关代码
* 当前 tests

## Part A：保存 pinch node / adapter config，并让 replay 自动复现

### 目标

如果 live 运行时使用了：

```text
--index-node 14
```

那么 replay 时不应每次都必须手动再传：

```text
--index-node 14
```

session 应保存当次实际使用的 pinch node / adapter 配置，replay 应自动读取。

### live 保存字段

在 live integrated session 写出的 `trial_config.json` 或等价 trial config 输出中，保存当次实际使用的 adapter / pinch node 配置。

至少包括：

```json
{
  "thumb_node": 4,
  "index_node": 14,
  "tracker_index": 0,
  "skeleton_index": 0,
  "pinch_position_mode": "nodes_world"
}
```

同时建议新增更清楚的结构：

```json
{
  "pinch_node_config": {
    "thumb_node": 4,
    "secondary_node": 14,
    "secondary_node_role": "index_node_cli_arg",
    "tracker_index": 0,
    "skeleton_index": 0,
    "pinch_position_mode": "nodes_world"
  }
}
```

说明：

* 旧字段 `index_node` 可以保留兼容。
* `secondary_node` 更准确，因为现在可能用中指 node，而不一定是食指。
* 不要改变当前 pinch / contact / slip 逻辑，只保存配置。

### replay 自动读取优先级

调整 replay 参数解析或 replay runner 配置构建逻辑：

```text
CLI 显式传入 --thumb-node / --index-node / --tracker-index / --skeleton-index / --pinch-position-mode
    优先级最高

否则读取 session/trial_config.json 中保存的字段
    thumb_node / index_node / tracker_index / skeleton_index / pinch_position_mode
    或 pinch_node_config 中的 thumb_node / secondary_node / tracker_index / skeleton_index / pinch_position_mode

如果旧 session 没有这些字段
    回退到当前代码默认值
```

不要让旧 session 因为缺字段报错。

### 测试

新增或更新测试：

1. live 写出的 `trial_config.json` 包含 thumb_node / index_node / tracker_index / skeleton_index / pinch_position_mode。
2. live 写出的 `pinch_node_config` 包含 thumb_node / secondary_node / secondary_node_role / tracker_index / skeleton_index / pinch_position_mode。
3. replay 显式 CLI 参数优先于 session trial_config。
4. replay 没显式传时，自动使用 session trial_config 中的 node 配置。
5. 旧 trial_config 缺字段时，仍回退默认值，不报错。

---

## Part B：pinch-distance threshold calibration

### 背景

当前 pinch distance 阈值不应对所有受试者、所有手指、所有硬件状态使用同一通用值。实际使用中，食指或中指 node、MANUS 手套拟合、受试者手型都会影响 pinch distance。

需要新增一个可选的 pinch-distance threshold calibration 流程，用于得到本次 session 的：

```text
pinch_on_threshold_m
pinch_off_threshold_m
```

### 标定原则

使用两个参考状态：

1. `open`：自然张开拇指和目标手指，达到实验中表示松开的典型距离，不要求用力张到最大。
2. `closed`：拇指和目标手指捏紧。

每个状态做 3 次重复。每次保持一个短窗口，例如 1 秒。每次取窗口内 pinch distance 的 median。三次重复再取 median，得到：

```text
open_distance_m
closed_distance_m
```

计算：

```text
range_m = open_distance_m - closed_distance_m

pinch_on_threshold_m  = closed_distance_m + 0.40 * range_m
pinch_off_threshold_m = closed_distance_m + 0.50 * range_m
```

语义：

```text
distance <= pinch_on_threshold_m
    进入 pinch active / considered pinched

distance >= pinch_off_threshold_m
    退出 pinch active / considered released

between on/off thresholds
    保持上一 pinch 状态
```

这是 hysteresis，不要用单一阈值。

### 配置项

新增 CLI 参数或 config 支持：

```text
--calibrate-pinch-threshold
--pinch-threshold-config PATH
```

也可以增加非交互参数用于复用已有阈值：

```text
--pinch-threshold-json PATH
```

实际命名请贴合当前代码风格。

默认行为：

* 如果不传 `--calibrate-pinch-threshold`，保持现有默认阈值逻辑，不破坏旧流程。
* 如果传了 `--calibrate-pinch-threshold`，在 trial running 前执行 pinch threshold calibration。
* calibration 应使用当前实际的 thumb_node / index_node / skeleton_index / pinch_position_mode 配置。

### 标定流程建议

交互流程可以用 CLI：

```text
Pinch distance calibration
Using thumb_node=4, secondary_node=14

OPEN reference:
  Repeat 1/3: keep thumb and target finger naturally open...
  Collecting 1.0s...
  median distance = ...

CLOSED reference:
  Repeat 1/3: pinch thumb and target finger tightly...
  Collecting 1.0s...
  median distance = ...

Computed:
  open_distance_m = ...
  closed_distance_m = ...
  pinch_on_threshold_m = ...
  pinch_off_threshold_m = ...

Accept? [Enter=yes / r=redo / q=abort]
```

可以第一版只支持 CLI，不做 GUI calibration。

### 采样要求

每次 repeat：

* 从 live frame source 采集当前 pinch distance。
* 只使用 valid frame。
* 每次采样窗口默认 1.0 秒。
* 每个窗口内至少需要 `min_valid_samples`，否则要求重做该 repeat。
* 每个窗口使用 median distance。
* 三个 repeat 最终也建议用 median，而不是 mean，以抗异常值。

### 质量检查

需要 sanity check：

```text
open_distance_m > closed_distance_m
range_m >= min_required_range_m
open / closed 每组重复的离散程度不能太大
pinch_on_threshold_m < pinch_off_threshold_m
```

建议默认：

```text
repeat_count = 3
sample_window_seconds = 1.0
on_fraction = 0.40
off_fraction = 0.50
min_required_range_m = 0.015
```

这些值应可配置，后续预实验调整。

如果质量检查失败：

* 清晰提示原因。
* 允许 redo。
* 不要静默使用坏阈值。

### 保存输出

如果执行了 pinch threshold calibration，写出：

```text
session/pinch_threshold_calibration.json
```

内容至少包括：

```json
{
  "enabled": true,
  "method": "three_repeats_window_median",
  "thumb_node": 4,
  "secondary_node": 14,
  "secondary_node_role": "index_node_cli_arg",
  "tracker_index": 0,
  "skeleton_index": 0,
  "pinch_position_mode": "nodes_world",
  "repeat_count": 3,
  "sample_window_seconds": 1.0,
  "closed_distance_m": 0.018,
  "open_distance_m": 0.085,
  "range_m": 0.067,
  "on_fraction": 0.40,
  "off_fraction": 0.50,
  "pinch_on_threshold_m": 0.0448,
  "pinch_off_threshold_m": 0.0515,
  "quality": {
    "valid": true,
    "min_required_range_m": 0.015,
    "open_repeat_values_m": [],
    "closed_repeat_values_m": []
  }
}
```

同时将 effective pinch thresholds 写入：

* `trial_config.json`
* `session_meta.json`
* `summary.json`
* `trial_summary.json`，如果 trial summary 生成时可用

如果没有启用 calibration，也应在 summary 中记录使用的是默认阈值或配置阈值，便于复现。

### replay 行为

replay 应自动读取 session 中保存的 pinch threshold calibration / effective pinch thresholds。

优先级：

```text
CLI 显式传入 pinch threshold 参数
    最高

session/pinch_threshold_calibration.json 或 trial_config 中保存的 effective thresholds
    其次

代码默认阈值
    最后
```

旧 session 缺阈值字段时，不报错，回退默认逻辑。

### 对现有逻辑的约束

1. 不要改 parser / adapter 坐标语义。
2. 不要改 BlockController 运动逻辑。
3. 不要改 cue 生成语义。
4. 不要改 haptic/cue sink。
5. 只把 calibrated pinch thresholds 接入当前已有 pinch active / pinch insufficient / slip 判定逻辑。
6. 如果当前代码只有单阈值，请小心引入 hysteresis，并补测试。
7. 如果当前代码已经有 on/off threshold，请直接替换为 calibrated values。

### 测试要求

新增或更新测试：

1. open/closed repeat values 正确计算 median。
2. on/off threshold 公式正确：

   * on = closed + 0.40 * range
   * off = closed + 0.50 * range
3. on threshold < off threshold。
4. open <= closed 时 calibration 失败。
5. range 太小时 calibration 失败。
6. valid sample 不足时 repeat 失败或要求重做。
7. live session 保存 `pinch_threshold_calibration.json`。
8. trial_config / summary 写入 effective thresholds。
9. replay 自动读取 session threshold。
10. CLI 显式阈值优先于 session threshold。
11. 未启用 calibration 时现有默认行为不变。
12. 旧 session 缺 threshold 字段时 replay 不报错。
13. 全量测试通过。

## 完成后请输出

1. 修改了哪些文件。
2. live 如何保存 pinch node / adapter config。
3. replay 如何自动读取 node config。
4. pinch threshold calibration 的 CLI 用法。
5. threshold 公式。
6. calibration 输出保存在哪里。
7. replay 如何复用 calibration 阈值。
8. 是否引入 hysteresis。
9. 是否修改了核心 parser / adapter / BlockController。
10. 新增了哪些测试。
11. 测试结果。


整体同意你的实现计划。当前代码已经有 `pinch_grab_threshold` / `pinch_release_threshold` hysteresis，所以 Part B 不要重写 pinch 状态机，只需要把 calibration 得到的：

```text
pinch_on_threshold_m
pinch_off_threshold_m
```

分别接到现有的：

```text
pinch_grab_threshold
pinch_release_threshold
```

保持现有状态机语义。

## 1. valid frame 定义

pinch threshold calibration 第一版不强制要求 `tracker_valid=True`。

有效 calibration sample 至少要求：

* parse ok
* adapter ok
* hand valid
* pinch feature valid
* `pinch_distance` 存在
* `pinch_distance` 是有限正数

理由：pinch distance 标定只依赖手指节点距离，不必依赖 tracker。

但请在 calibration result 中记录 tracker 有效情况，例如：

```json
{
  "required_tracker_valid": false,
  "tracker_valid_sample_fraction": 0.0,
  "warnings": []
}
```

如果实现成本很低，可以预留可选配置：

```text
--calibration-require-tracker-valid
```

但默认不要要求 tracker valid。

## 2. open/closed 重复离散度阈值

认可第一版默认：

```text
max_repeat_spread_m = 0.03
```

分别检查 open 和 closed 三次 repeat 的离散度：

```text
open_repeat_spread_m = max(open_repeat_values_m) - min(open_repeat_values_m)
closed_repeat_spread_m = max(closed_repeat_values_m) - min(closed_repeat_values_m)
```

如果任一超过 `max_repeat_spread_m`，calibration failed，并提示 redo。

该值后续可以根据预实验数据调整，因此请放入 config / result。

## 3. calibration 阶段用户按 q

同意。

如果用户在 pinch threshold calibration 阶段按 `q`：

```text
run_stop_reason = pinch_threshold_calibration_aborted
phase = pre_trial_pinch_threshold_calibration
trial_started = false
```

不启动 trial，不写正常 trial summary。

如果当前外层 summary 会生成，则记录该停止原因。

## 4. `--pinch-threshold-json` 文件格式

同意同时支持两种格式。

完整格式可以是 `pinch_threshold_calibration.json`：

```json
{
  "enabled": true,
  "method": "three_repeats_window_median",
  "pinch_on_threshold_m": 0.045,
  "pinch_off_threshold_m": 0.055,
  "open_distance_m": 0.085,
  "closed_distance_m": 0.018
}
```

也支持最小格式：

```json
{
  "pinch_on_threshold_m": 0.045,
  "pinch_off_threshold_m": 0.055
}
```

读取时必须校验：

* `pinch_on_threshold_m` 是有限正数
* `pinch_off_threshold_m` 是有限正数
* `pinch_on_threshold_m < pinch_off_threshold_m`

无效时清晰报错，不静默回退默认值。

## 5. replay 显式阈值参数

同意 replay 也新增同名参数：

```text
--pinch-grab-threshold
--pinch-release-threshold
```

replay 阈值解析优先级为：

```text
CLI 显式 --pinch-grab-threshold / --pinch-release-threshold
>
session/pinch_threshold_calibration.json 或 trial_config 中保存的 effective thresholds
>
代码默认值
```

旧 session 缺 threshold 字段时，不报错，回退默认值。

## 实现边界

保持你的实现计划：

* 在 `run_live_integrated_session.py` 写入 node config 和 effective thresholds。
* 在 `replay_debug_runner.py` / replay CLI 中把 node 和 threshold 解析改成 CLI → session → default。
* 新增一个小模块做 pinch threshold calibration。
* 不改 `manus_vive_adapter.py` 的坐标语义。
* 不改 `BlockController` 运动逻辑。
* 不改 cue/haptic 语义。
* 不重写现有 hysteresis 状态机。
