确认这 3 个边界决策。

## 1. “输入无效”的范围

同意你的建议。

定义如下：

* adapter 已经产出 `FrameOutput`，但 `tracker_valid=false`、`hand_valid=false`、`pinch_valid=false`、pinch center 缺失，或被统一映射为 `tracking_valid=false`：

  * 视为 invalid frame。
  * 不生成新的 cue。
  * 立即清空 GUI cue overlay。
  * 不写 `cue_clear` 日志。

* parse / adapter 异常：

  * 也应立即清空 GUI cue overlay。
  * 不生成新的 cue。
  * 不写 `cue_clear` 日志。
  * 可以在 runtime stats / warning / diagnostics 中记录异常，但不要把异常本身当作 cue。

* 短暂无新帧：

  * 不视为 invalid frame。
  * 不立即清空 overlay。
  * 继续依赖现有 no-new-frame timeout / source stop 流程。
  * 避免因为短暂帧间隔造成 cue 闪烁。

## 2. Cue 在首次 render 前已经失效

同意你的建议。

如果 cue 已进入 `gui_text` sink，但在 GUI 第一次 render 前发生以下情况：

* 状态解除；
* tracking invalid；
* parse / adapter 异常导致 overlay 清空；
* 同优先级的新 signature 替换了它；
* 更高优先级 cue 替换了它；
* GUI 关闭；

则旧 cue 不再显示。

cue log 记录：

```text
success = true
displayed_monotonic_ms = null
display_status = not_displayed
not_displayed_reason = state_cleared_before_render
```

或根据具体情况使用：

```text
not_displayed_reason = cue_replaced_before_render
not_displayed_reason = gui_closed_before_render
not_displayed_reason = invalid_before_render
not_displayed_reason = higher_priority_before_render
```

这些 cue 不能作为视觉 cue RT 起点。

## 3. Trial 开始前失败时的 `cue_config.json`

同意你的建议。

保持现有 session 生命周期：

* 如果 calibration 失败、map loading 失败、trial config 构建失败，或其他 trial 开始前错误导致 `SessionRecorder` / 标准 session 目录没有创建：

  * 不要为了 cue config 单独创建 session 目录。
  * 不要改变现有失败流程。
  * 外层 `summary.json` 中仍应记录 effective cue config、cue_sink、cue_enabled 等信息，便于知道本次 run 的配置。
  * validator 不应要求不存在的 session 目录中有 `cue_config.json`。

* 只有成功创建标准 session 目录后：

  * 才要求写 `session/cue_config.json`。
  * validator 才检查 session-level cue config 和 summary / trial summary 的一致性。

这样不会为了 cue 功能破坏现有 live integrated lifecycle。



确认这 4 个边界决策。

## 1. Parse / adapter 异常后的重新提示

同意你的建议。

parse / adapter 异常会清空 GUI overlay，但不应推进 Controller 状态，也不应重置 cue edge 判断为“下次有效帧重新进入状态”。

规则：

* parse / adapter 异常：

  * 清空 GUI overlay。
  * 不生成新 cue。
  * 不写 `cue_clear`。
  * 不推进 TrialController / BlockController 状态。
  * 不把异常本身作为 cue edge。

* 下一张有效帧：

  * 如果仍然是异常前相同的 cue 状态 / state signature，不重新生成 cue。
  * 只有真实 edge、状态解除后重新进入、或 `state_signature_changed` 时，才生成新 cue。
  * 这样避免把短暂 parse/adapter 异常恢复制造成新的 RT 起点。

## 2. 首次 render 前被高优先级 cue 替换的状态

同意区分两种情况。

### 同一帧内多个候选 cue

先统一排序。

低优先级 cue 记录：

```text id="myoafi"
display_status = not_displayed_lower_priority
displayed_monotonic_ms = null
```

### 前一帧已经 pending、但尚未 render 的 cue 被后续高优先级 cue 替换

旧 pending cue 记录：

```text id="wkyyzj"
display_status = not_displayed
displayed_monotonic_ms = null
not_displayed_reason = higher_priority_before_render
```

这样可以区分：

* 同一帧内优先级压制；
* 跨帧 pending cue 被后续更高优先级 cue 替换。

## 3. Trial / session 结束时 overlay 清空

同意。

当 trial / session 因以下原因结束时：

* operator manual complete
* operator abort
* timeout
* too many detaches
* source stop
* client disconnect
* no new frame timeout
* duration reached
* max frames reached
* KeyboardInterrupt / interrupted

GUI overlay 应立即清空。

要求：

* 不生成新的 `cue_clear` command。
* 不写 `cue_clear` 到 cue log。
* 已经显示过的 cue 保持原有日志状态，例如 `display_status=displayed`。
* pending 但尚未显示的 cue 可以记录为：

  * `display_status=not_displayed`
  * `not_displayed_reason=trial_ended_before_render` 或 `session_ended_before_render`
* GUI 可继续保留最后一帧，但 cue overlay 不应继续显示过期提示。

## 4. `--cue-config` 本身无效

同意。

如果用户显式传入的 `--cue-config` 文件无效，例如：

* 文件不存在；
* JSON / YAML 解析失败；
* YAML 依赖缺失；
* 未知字段；
* 字段类型错误；
* 配置值非法；

则应清晰失败，不要静默回退到默认 cue config。

规则：

* 不使用默认 config 替代无效 config。
* 不生成 effective cue config。
* 如果外层 summary 仍会生成，则记录：

  * `requested_cue_config_path`
  * `cue_config_error`
  * `effective_cue_config = null`
  * `cue_enabled = false` 或保持未启动状态，按现有错误处理风格决定
* 这是“开始前失败仍记录 effective config”的例外：因为此时不存在有效 effective config。
* 不为了 cue config 错误创建 session 目录。


整体确认你的默认建议。最重要的范围决策如下：

## 1. `experiment_visibility_feedback` 的物块消失/重现不进入 cue log

确认：Stage 1 中，`cue_log.csv` 只记录 cue sink command，不把物块可见性变化当作已显示 cue。

也就是说：

* `experiment_visibility_feedback` 下，物块消失 / 重现本身确实是视觉刺激。
* 但本阶段不把它记录为 cue command。
* 不在 `cue_log.csv` 中为 block visibility onset / offset 生成 cue。
* 不把物块消失 / 重现作为 behavioral RT 起点。
* README 需要明确：当前 cue RT 只针对 cue sink command；block visibility visual RT 尚不支持。
* 如果以后需要分析物块消失 / 重现 RT，应新增独立 visual-state render diagnostics / visual stimulus log，不混入 cue_log.csv。

因此，默认 `cue_sink=logging` 时，contact cue 的 `display_status=not_applicable` 是可以接受的；它代表 cue command 被记录但没有实际 console/gui cue display，不代表 block visibility visual onset。

---

## Cue 生成语义

## 2. 终止 trial 的那一帧是否生成新 cue

同意：终止 trial 的那一帧不生成新 cue。

包括：

* subject_end
* operator manual complete
* operator abort
* timeout
* too many detaches
* source stop
* client disconnect
* no new frame timeout
* duration reached
* max frames reached

理由：trial 已结束或即将结束，受试者没有后续响应机会，生成 cue 会制造无意义 RT 起点。

## 3. Processed invalid frame 恢复后的重新提示

同意。

* invalid frame 清空 overlay。
* recovery frame 不因为“恢复有效”本身生成 cue。
* 下一张正常有效帧继续遵守现有 Controller edge / state signature 规则。
* parse / adapter 异常仍按已确认规则：不推进状态，不重新提示相同状态。

也就是说，不因为 tracking invalid -> valid 的恢复本身生成 cue。

## 4. `blocked_directional` 的方向来源

同意。

第一版只从以下来源推断方向：

* `primary_blocked_surface`
* 或方向明确的 `track_state`

不要从连续且可能抖动的 `force_vector_task` 推断方向。

如果无法确定方向：

* 若 `slip_active=true` 且 `enable_slip_cue=true`，回退为 `slip_track_blocked`
* 否则不生成 directional cue

`force_vector_task` 可以写入 details，但不作为第一版 direction 决策来源。

## 5. `state_signature` 精确组成

同意。

冻结第一版 signature：

```text id="d5du3n"
slip signature =
  cue_type + slip_reason + source_state

blocked signature =
  cue_type + track_state + primary_blocked_surface + direction
```

连续数值只写 details，不参与 signature，例如：

* force_vector
* force magnitude
* blocked amount
* continuous coordinates

避免连续数值抖动导致逐帧刷 cue。

## 6. Directional 禁用时是否回退 generic slip

同意。

规则：

* 如果方向存在，但 `enable_blocked_directional_cue=false`：

  * 如果 `enable_slip_cue=true` 且 `slip_active=true`，允许生成 `slip_track_blocked`
* 如果 directional cue 只是被 rate limit 抑制：

  * 不回退 generic slip
  * 避免绕过 rate limit 产生重复提示

## 7. `cue_sink=none` 与 suppressed 统计

同意。

规则：

* `cue_sink=none`：完全不生成 candidate，`cue_count=0`，`suppressed_cue_count=0`
* 非 `none` sink 下：

  * 被 enable flag 禁用的真实 edge/signature candidate 计入 config-suppressed
  * 被 rate limit 抑制的 candidate 计入 rate-limit suppressed
* 不把 `none` 模式下未生成的东西计入 suppressed

## 8. Priority 是否作用于未发出的高优先级状态

同意。

priority 只作用于通过配置和 rate limit、实际进入 sink 的 command。

被禁用或被限流的高优先级状态，不应阻止低优先级 cue 显示。

---

## 时间与异步显示

## 9. Console 是否遵守跨帧 priority 生命周期

同意。

Console 和 GUI 一样遵守跨帧 priority 生命周期。

* 高优先级 cue 活跃时，后来的低优先级 cue 不打印。
* 这些低优先级 cue 仍写 log。
* `display_status=not_displayed_lower_priority`
* `displayed_monotonic_ms=null`

虽然 console 是一次性输出，但其显示资格仍按当前活跃 priority 判断。

## 10. `min_cue_interval_ms` 时间基准

同意。

live 和 replay 都优先使用：

```text id="6413kr"
source_trial_time
```

做 rate limit。

如果 `source_trial_time` 不可用、为空、回退或不单调，则 fallback 到：

```text id="m6lsmc"
created_monotonic_ms
```

并记录 warning。

限流只参考上一次实际 emitted cue，不参考已 suppressed candidate。

## 11. Console worker 未打印的过期 cue

同意。

当以下情况发生时，取消 pending console cue，不再打印：

* 状态解除
* invalid frame
* parse / adapter 异常
* 更高优先级 cue 替换
* trial ended / session ended
* GUI/console display target 不再有效

cue log 记录对应原因：

```text id="eh34yh"
state_cleared_before_display
invalid_before_display
cue_replaced_before_display
trial_ended_before_display
session_ended_before_display
console_queue_replaced
```

## 12. `success` 与异步失败

同意。

`success=true` 只表示 sink 已接受 command。

即使之后未 render、未打印、被替换或 worker 输出失败，也不回改 `success=false`，而是通过：

* `display_status`
* `not_displayed_reason`
* `error`

表达最终显示状态。

只有 sink 提交阶段直接拒绝或异常时，才写 `success=false`。

## 13. `displayed_monotonic_ms`

同意。

* `displayed_monotonic_ms` 固定为首次显示时间。
* 重复 GUI render 不更新该字段。
* 新增 `displayed_frame_index`，记录 GUI 实际显示 cue 时使用的 snapshot frame。
* README 说明：该时间代表 GUI refresh 已应用文本，不代表物理屏幕扫描时间，也不代表受试者真正看到；它受 `gui_fps` 量化影响。

## 14. cue log 写入时机

同意。

cue log 必须等 pending cue 已显示或取消、Console worker 已有界关闭后再写。

Replay GUI 场景下，如果 GUI render 可能晚于 replay worker 结束：

* 不应在 replay worker 结束时立即写最终 cue log。
* 应延后到 GUI 退出后写，或在 GUI 退出后重写一次最终 cue log。
* 确保最终 cue log 包含真实 `display_status` / `displayed_monotonic_ms`。

---

## 输出、Replay 与 Validator

## 15. Cue ID 和日志顺序

同意。

新增：

```text id="twg5it"
cue_sequence_index
cue_id
source_frame_id
displayed_frame_index
```

规则：

* `cue_sequence_index` 在 trial/run 内递增。
* `cue_id` 使用确定性 id，例如包含 run/trial 前缀 + sequence，或其他稳定方案。
* CSV 按创建顺序写。
* `source_frame_id` 用于追踪 cue 来自哪一帧。
* `displayed_frame_index` 用于追踪 GUI 实际在哪个 snapshot frame 显示 cue。
* 这对 dropped-frame / latest-only GUI 审计很有价值。

## 16. Effective config 标准字段名与 validator

同意。

统一使用：

```text id="vs7wcj"
effective_cue_config
```

写入：

* `session_meta.json`
* `trial_config.json`
* 外层 summary
* `trial_summary.json`

`session/cue_config.json` 的内容应与 `effective_cue_config` 完全一致。

Validator 检查：

* cue config 一致性
* cue log CSV header
* cue row 数量
* type counts
* cue_id 唯一性
* trial / mode 一致性

如果 `cue_count=0`，header-only cue log 直接接受，不 warning。

## 17. 无效 cue config 的 CLI 行为

同意。

与 termination config 保持一致：

* 配置阶段直接退出码 `2`
* 不运行 session
* 不生成 summary
* 不回退默认配置

Replay 自动加载到损坏的 `session/cue_config.json` 时：

* 清晰失败
* 不回退默认配置

## 18. Replay 是否自动继承输入 session 的 visual profile

同意。

Replay 默认仍然是：

```text id="ec4ymn"
debug_all
```

不要自动继承输入 session 的 visual profile。否则打开旧 session 时可能得到空白实验画面，不利于 debug。

如果需要复现实验显示条件，用户显式传：

```text id="zn5604"
--visual-profile experiment_visibility_feedback
```

或其他 profile。

另外：

* initial block footprint 只在 `debug_all` 显示。
* 两个 experiment profile 都隐藏 initial block footprint。

## 19. Replay 无 `--out-dir` 时的内存接口

同意。

在 `ReplayDebugResult` 中暴露只读：

```text id="e232yl"
cue_records
```

否则“在内存中生成 cue”只有 summary count，调用方无法检查具体 command。

要求：

* 不修改输入 session。
* `cue_records` 可用于测试和调试。
* 如果没有 cue 或 `cue_sink=none`，返回空列表。
