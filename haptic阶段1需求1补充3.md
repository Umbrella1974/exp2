这个方案可行，同意实现 haptic Matrix 方向过滤层。

## 核心边界

该过滤层只影响：

```text
Matrix haptic output key
```

不得影响：

```text
TrialController
BlockController
GUI cue
processed_frames.csv
events.csv
地图 / target / track 判定
cue log 的原始 blocked 信息
```

也就是说，真实几何 blocked set 仍然保留；只是 Matrix 触觉硬件实际查表和发送时，可以忽略某些轴。

## 配置

在 `haptic_config.py` 的 `matrix` 配置下新增：

```json
"ignore_direction_axes": []
```

合法值只允许：

```text
X
Y
Z
```

默认空列表，保证旧行为完全不变。

示例：

```json
{
  "matrix": {
    "direction_semantics": "blocked_surface",
    "ignore_direction_axes": ["Z"]
  }
}
```

## 过滤规则

在 `haptic_runtime.py` 中，读取已有 blocked surfaces 后，生成 `matrix_direction_used` 前进行过滤。

例如：

```text
原始 blocked_surface_set = X_POS+Y_NEG+Z_POS
ignore_direction_axes = ["Z"]
过滤后 blocked_surface_set = X_POS+Y_NEG
```

如果：

```json
"direction_semantics": "blocked_surface"
```

则：

```text
matrix_direction_used = X_POS+Y_NEG
```

如果：

```json
"direction_semantics": "correction_direction"
```

则必须先过滤 blocked surface，再求 correction：

```text
原始 blocked = X_POS+Y_NEG+Z_POS
过滤后 blocked = X_POS+Y_NEG
correction key = X_NEG+Y_POS
matrix_direction_used = X_NEG+Y_POS
```

不要先求 correction 再过滤。

## 过滤后为空

如果过滤后没有任何方向，例如：

```text
原始 blocked_surface_set = Z_POS
ignore_direction_axes = ["Z"]
```

则不发送 Matrix haptic command，记录：

```text
send_status = skipped
not_sent_reason = direction_filtered_empty
matrix_direction_used = null 或 ""
```

不要 fallback 到 primary direction，也不要自动发送其他方向。

## 日志字段

为了避免分析混淆，haptic log 中保留原始集合和实际使用集合。

建议字段：

```text
blocked_surface_set
correction_direction_set
matrix_filtered_blocked_surface_set
matrix_filtered_correction_direction_set
matrix_direction_used
matrix_direction_semantics
matrix_ignored_direction_axes
```

含义：

```text
blocked_surface_set:
  原始几何 blocked set

correction_direction_set:
  原始 correction set

matrix_filtered_blocked_surface_set:
  忽略轴之后的 blocked set

matrix_filtered_correction_direction_set:
  忽略轴之后再求 correction 的 set

matrix_direction_used:
  实际用于查 direction_channel_map / combination_channel_map 的 key

matrix_ignored_direction_axes:
  例如 Z
```

## 查表规则

查表时使用过滤后的 `matrix_direction_used`：

* 单方向：查 `direction_channel_map`
* 多方向：查 `combination_channel_map`

例如：

```json
{
  "matrix": {
    "ignore_direction_axes": ["Z"],
    "direction_semantics": "blocked_surface",
    "combination_channel_map": {
      "X_POS+Y_NEG": [21]
    }
  }
}
```

运行时：

```text
X_POS+Y_NEG+Z_POS -> X_POS+Y_NEG -> [21]
```

## validator

更新 validator / config validation：

* `ignore_direction_axes` 只能包含 `X/Y/Z`
* 默认空列表
* 不配置时旧行为不变
* `direction_channel_map` key 仍必须是合法单方向
* `combination_channel_map` key 仍必须由合法方向组成
* validator 不要要求原始 3D blocked key 必须存在于 `combination_channel_map`，因为运行时可能通过 `ignore_direction_axes` 过滤成 XY key

## 测试

新增或更新测试：

1. `X_POS+Y_NEG+Z_POS` + ignore `Z`：

   * `matrix_filtered_blocked_surface_set = X_POS+Y_NEG`
   * `matrix_direction_used = X_POS+Y_NEG`
   * 发送配置中的 `[21]`

2. `Z_POS` + ignore `Z`：

   * 不发送
   * `send_status=skipped`
   * `not_sent_reason=direction_filtered_empty`

3. 不配置 `ignore_direction_axes`：

   * 旧行为不变
   * `X_POS+Y_NEG+Z_POS` 仍要求完整组合映射

4. `direction_semantics=correction_direction`：

   * 先过滤 blocked surface
   * 再求 correction
   * `X_POS+Y_NEG+Z_POS` + ignore `Z` -> `X_NEG+Y_POS`

5. 日志同时包含原始 set 和过滤后实际发送 key。

## README

README 中说明：

* `ignore_direction_axes` 只影响 Matrix haptic 输出。
* 它不改变轨道阻挡判定，也不改变 GUI cue。
* 适合 XY 平面实验中忽略 Z 方向混入。
* 过滤后为空时不发送 Matrix haptic。

确认这 3 个实现细节。

## 1. `matrix_ignored_direction_axes` 的 CSV 格式

同意。

在 `haptic_command_log.csv` 中，`matrix_ignored_direction_axes` 写成普通字符串：

```text
Z
```

如果以后多个轴：

```text
Y+Z
```

不要写 JSON list。这样 CSV 更容易肉眼检查。

## 2. `matrix_direction_used` 为空时的 CSV 表达

同意。

当过滤后为空，例如：

```text
Z_POS + ignore Z
```

导致：

```text
not_sent_reason = direction_filtered_empty
```

时，CSV 中：

```text
matrix_direction_used = ""
```

也就是空字符串，不写 `"null"`。

JSON summary / config 中如果需要空值，可以使用 `null`，但 CSV 使用空字符串，保持和现有风格一致。

## 3. 原始 correction 和过滤后 correction 的关系

同意。

请保持两套 correction 语义：

```text
correction_direction_set
```

从原始 `blocked_surface_set` 取反得到。

例如：

```text
blocked_surface_set = X_POS+Y_NEG+Z_POS
correction_direction_set = X_NEG+Y_POS+Z_NEG
```

而：

```text
matrix_filtered_correction_direction_set
```

从过滤后的 blocked set 取反得到。

例如：

```text
ignore_direction_axes = Z
matrix_filtered_blocked_surface_set = X_POS+Y_NEG
matrix_filtered_correction_direction_set = X_NEG+Y_POS
```

也就是：原始 correction 记录真实几何；filtered correction 记录 Matrix 实际输出逻辑。
用于 Matrix 查表的 `matrix_direction_used` 根据 `direction_semantics` 从 filtered blocked 或 filtered correction 中选择。

## 4. 修改范围

同意你的预计范围：

```text
haptic_config.py
haptic_runtime.py
validate_session_outputs.py 如有必要
tests/test_haptic_config.py
tests/test_haptic_runtime.py
README.md
```

不要修改：

```text
TrialController
BlockController
GUI cue
processed_frames
events
```
