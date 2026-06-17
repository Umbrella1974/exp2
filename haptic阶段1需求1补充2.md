整体同意你的组合方向方案，但请补充和确认以下几点。

## 1. GUI cue 也要支持组合 blocked 显示

这次问题不只是 haptic，也包括 cue/GUI 提示太频繁、看不清。

请修改 GUI cue blocked 显示逻辑：

* 不再只显示 primary blocked direction。
* 如果同时 blocked `X_POS` 和 `Y_POS`，GUI 应显示组合信息，例如：

  ```text
  BLOCKED X_POS + Y_POS
  MOVE X_NEG + Y_NEG
  ```

  或至少：

  ```text
  MOVE X_NEG + Y_NEG
  ```
* blocked 状态持续且组合方向没有变化时，不要每帧生成新的弹出提示。
* 只有 blocked surface set / correction direction set 变化时才更新显示。
* blocked 解除时清空显示。

也就是说，GUI blocked cue 应按组合 direction signature 更新，而不是每帧按 primary direction 反复弹。

## 2. haptic matrix 也要使用组合方向

Matrix haptic 不应只使用 `primary_blocked_surface`。

规则：

* 单方向 blocked：继续使用 `direction_channel_map`
* 多方向 blocked：使用 `combination_channel_map`
* 组合 key 按固定顺序规范化，例如 `X_POS+Y_POS`，轴顺序为 X, Y, Z。
* 默认不要把组合解释为单方向通道并集。

配置示例：

```json
{
  "matrix": {
    "direction_semantics": "blocked_surface",
    "direction_channel_map": {
      "X_POS": [1, 2],
      "Y_POS": [5, 6]
    },
    "combination_channel_map": {
      "X_POS+Y_POS": [20, 21, 22],
      "X_POS+Y_NEG": [23, 24, 25],
      "X_NEG+Y_POS": [26, 27, 28],
      "X_NEG+Y_NEG": [29, 30, 31]
    },
    "missing_combination_policy": "skip"
  }
}
```

如果组合缺失：

```text
send_status = not_sent
not_sent_reason = missing_combination_mapping
matrix_direction_used = X_POS+Y_POS
```

## 3. 不要假设组合等于单方向相加

默认：

```json
"missing_combination_policy": "skip"
```

不要默认 fallback 到：

```text
X_POS channels + Y_POS channels
```

以后可以可选支持：

```json
"missing_combination_policy": "union_single_directions"
```

但默认不要启用。

## 4. blocked_surface 和 correction_direction 都要保留

默认仍然使用：

```json
"direction_semantics": "blocked_surface"
```

含义：

```text
BLOCKED_X_POS + BLOCKED_Y_POS -> X_POS+Y_POS
```

如果配置为：

```json
"direction_semantics": "correction_direction"
```

则：

```text
BLOCKED_X_POS + BLOCKED_Y_POS -> X_NEG+Y_NEG
```

日志里请同时保存：

```text
blocked_surface_set
correction_direction_set
matrix_direction_semantics
matrix_direction_used
```

避免后续分析时混淆。

## 5. 请确认 `all_blocked_surfaces` 是否真实存在

你提到 `blocked_info.all_blocked_surfaces`。请确认当前代码里是否已经有这个字段。

如果已经有，直接复用。

如果没有，不要在 haptic 层重新发明 blocked 判断。应在现有轨道阻挡几何计算处，把同一套判断得到的所有 blocked surfaces 输出到 frame result / cue details / haptic input。haptic 层只消费已有结果，不重新判断轨道阻挡。

## 6. target 和 Z 方向扩展

第一版至少支持 XY 组合：

```text
X_POS+Y_POS
X_POS+Y_NEG
X_NEG+Y_POS
X_NEG+Y_NEG
```

结构上应自然支持后续 Z 组合，例如：

```text
X_POS+Z_POS
Y_NEG+Z_POS
X_POS+Y_NEG+Z_POS
```

validator 应检查组合 key 是否由合法方向组成。

## 7. pinch threshold calibration 配置位置

另外请回答并确认 pinch calibration 阈值在哪里改。

`50%` 应该对应：

```text
off_fraction = 0.50
```

请确保 `on_fraction` / `off_fraction` 不被硬编码，能够通过 config 或 CLI 修改。例如：

```json
{
  "pinch_threshold_calibration": {
    "on_fraction": 0.40,
    "off_fraction": 0.50
  }
}
```

或者通过 threshold JSON 直接指定：

```json
{
  "pinch_on_threshold_m": 0.045,
  "pinch_off_threshold_m": 0.055
}
```

effective on/off fraction 和最终 threshold 都要写入 calibration output、trial_config、summary，方便复现。
