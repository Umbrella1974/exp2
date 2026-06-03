你现在要在 `exp2` 仓库的 `GUI-stage` 分支上实现下一阶段：**Manual Trial End + Protective Auto Stop + Trial Outcome Recording**。

请先阅读当前代码，不要依赖 README。重点阅读：

* `run_live_integrated_session.py`
* `live_trial_runner.py`
* `trial_controller.py`
* `block_controller.py`
* `trial_config` / `engine_config` / map config 相关代码
* recorder / summary / session 输出相关代码
* GUI-stage 当前已有 GUI、replay、live runner 测试
* 当前已有的 keyboard quit / user_quit_checker 逻辑
* 当前已有的 detach / release / contact / slip / blocked 事件或计数字段

本阶段目标：

实现一个适合当前实验逻辑的 trial 结束机制：

1. **不做自动 target success。**
2. 物块到达 target 后，不由程序自动判成功。
3. 实验流程是：受试者认为到终点后，由实验员手动结束当前 trial。
4. 程序记录手动结束时的 block / pinch / target / contact / detach 等状态。
5. 程序提供保护性自动停止：

   * 超过最大 trial 时间仍未手动结束，则自动结束并记录失败。
   * 手部完全脱离物块 / detach / release 次数过多，则自动结束并记录失败。
6. slip 和 blocked 只是事件，不导致失败。
7. GUI 仍然只是 debug display，不负责 trial 结束判定。

核心边界：

1. 不要改 parser / adapter 核心逻辑。
2. 不要改 BlockController 的核心运动逻辑，除非只是读取已有状态用于记录。
3. 不要让 GUI 直接推进 trial。
4. 不要让 GUI 直接修改 `TrialController` / `BlockController` 内部状态。
5. 不要实现 haptic。
6. 不要实现正式多 trial session lifecycle。
7. 不要实现 calibration GUI。
8. 不要实现 target 自动成功判定。
9. 不要把 GUI Close 变成 stop trial。

---

## 一、trial outcome / end reason 设计

请先检查项目里是否已经有类似 `TrialState`、`TrialOutcome`、`stop_reason`、`end_reason` 的结构。优先复用已有结构；如果已有结构不够表达本阶段需求，可以小幅扩展，但不要大重构。

需要至少能区分以下结果：

```text
MANUAL_COMPLETED
FAILED_TIMEOUT
FAILED_TOO_MANY_DETACHES
ABORTED_BY_OPERATOR
INTERRUPTED
SOURCE_STOPPED
CLIENT_DISCONNECTED
NO_NEW_FRAME_TIMEOUT
DURATION_REACHED
MAX_FRAMES_REACHED
```

说明：

* `MANUAL_COMPLETED`：实验员确认当前 trial 完成，手动结束。它不是程序自动 target success。
* `FAILED_TIMEOUT`：超过配置的最大 trial 时长，保护性自动失败。
* `FAILED_TOO_MANY_DETACHES`：手部完全脱离物块 / detach / release 次数超过配置阈值，保护性自动失败。
* `ABORTED_BY_OPERATOR`：实验员中止整个 run 或中止当前 trial，不算正常完成。
* `INTERRUPTED`：KeyboardInterrupt / 异常中断。
* `SOURCE_STOPPED`、`CLIENT_DISCONNECTED`、`NO_NEW_FRAME_TIMEOUT`：保留当前 live source 停止语义。
* `DURATION_REACHED`、`MAX_FRAMES_REACHED`：保留当前 debug runner 外层停止语义。

如果当前代码已有 `ENDED_BY_SUBJECT` 之类状态，不要盲目删除。可以兼容保留，但 summary 中应明确记录新的 `trial_outcome` / `end_reason`，避免只看到模糊的 ended。

---

## 二、operator command 语义

新增或扩展现有 keyboard command 机制，但不要做 GUI 按钮。

第一版优先支持 CLI / terminal keyboard command：

```text
e = end current trial as MANUAL_COMPLETED
q = abort whole run / operator abort
```

可选：

```text
k = close GUI display only
```

但 `k` 不是本阶段必须项。如果实现 `k` 会导致线程/Qt 复杂度增加，则不要做。GUI display-only close 继续通过窗口关闭即可。

语义要求：

### `e`：手动完成当前 trial

按下 `e` 后：

1. 当前 trial 停止。
2. 记录 `trial_outcome = MANUAL_COMPLETED`。
3. 记录 `end_reason = operator_manual_complete` 或等价命名。
4. 保存最后一个可用 snapshot / block / pinch / target 诊断状态。
5. 不要把 `e` 解释成 source stopped 或 failure。
6. 不要要求 block 自动进入 target 才能按 `e`。
7. 允许记录“手动结束时 block center 是否在 target 内”，但这只是诊断字段，不是自动判定。

### `q`：operator abort

按下 `q` 后：

1. 请求停止当前 live run。
2. 记录 `trial_outcome = ABORTED_BY_OPERATOR` 或等价 outcome。
3. 记录 `end_reason = operator_abort`。
4. 尽量走现有 `request_stop` / `stop_event` / runner stop 机制。
5. 不要让 GUI 或 keyboard command 直接修改 `TrialController` / `BlockController` 内部字段。
6. 不要把 `q` 仍然只记录成泛泛的 `user_quit` 而丢失 outcome 语义；可以兼容保留 `run_stop_reason=user_quit`，但应另有明确 operator abort 字段。

### GUI close

保持当前语义：

1. GUI Close 只关闭显示层。
2. 不停止 trial。
3. 不结束 session。
4. 不产生 `MANUAL_COMPLETED`。
5. 不产生 `ABORTED_BY_OPERATOR`。
6. summary 继续记录 `gui_closed=true`。

---

## 三、保护性自动停止

当前 live integrated runner 里如果存在类似：

```text
trial_timeout_seconds = 1e9
max_detach_count = 1_000_000_000
```

这类 debug-only 大值，请不要继续作为正式默认 trial 保护条件。

新增一个可配置的 termination config，用于控制：

```text
max_trial_duration_seconds: 600
max_detach_count: 20
manual_completion_enabled: true
timeout_enabled: true
detach_limit_enabled: true
```

默认值可以先用：

```text
max_trial_duration_seconds = 600
max_detach_count = 20
```

这只是预实验默认值，后续会根据预实验调整。

配置要求：

1. 新增 `--termination-config` 参数，允许从单独配置文件读取。
2. 优先使用项目已有配置读取机制。
3. 如果项目已有 YAML 依赖或配置系统，优先支持 YAML。
4. 如果项目没有 YAML 依赖，不要为了 YAML 大改依赖系统；可以支持 JSON，或对 `.yaml` 做 lazy import PyYAML 并在缺依赖时清晰报错。
5. 不管用 YAML 还是 JSON，都要把最终生效的 termination config 写入 session 输出目录，便于复现实验。
6. summary 中记录生效的 termination config。
7. 如果没有传 `--termination-config`，使用明确的默认值，并在 summary 中记录这些默认值。

保护性停止行为：

### timeout

如果 trial 运行时间超过 `max_trial_duration_seconds`，且 trial 还没有被手动结束：

```text
trial_outcome = FAILED_TIMEOUT
end_reason = trial_timeout
```

然后停止当前 trial/run，并保存数据。

### detach / release 次数过多

如果手部完全脱离物块次数超过 `max_detach_count`：

```text
trial_outcome = FAILED_TOO_MANY_DETACHES
end_reason = too_many_detaches
```

然后停止当前 trial/run，并保存数据。

注意：

* 请检查当前代码已有术语。如果代码用的是 `detach_count`，就继续用 detach。
* 如果代码用的是 release 或 contact lost，请沿用已有命名，不要强行新造不一致字段。
* GUI 文案可以写成 `detach/release count`，但核心字段尽量复用已有项目术语。
* slip 发生多少次都不导致失败。
* blocked 只是事件，不导致失败。

---

## 四、target 诊断：记录，但不自动结束

本实验当前不需要自动判断“block 到 target 就成功”。

但需要记录 target 相关诊断，尤其是手动结束时：

1. block center 是否在 target 内。
2. block center 到 target center 或 target region 的距离。
3. 第一次 block center 进入 target 的时间，如果容易实现。
4. 手动结束时 block / pinch 的 task 坐标。
5. 手动结束时 block 是否接触 / 是否处于 blocked / 是否最近发生 slip 或 detach。

要求：

1. 判断对象是 block center。
2. target 诊断只用于 summary / metrics。
3. 不要因为 block center 进入 target 自动结束 trial。
4. 不要新增 target dwell 自动成功。
5. 如果当前 trial_config 中 target 表达不够清晰，请先说明，不要硬写错误判断。
6. 如果无法可靠计算 target distance，可先记录 `block_center_in_target_at_end` 和 `block_center_task_position_at_end`，并在 summary 中写 warning。

---

## 五、summary / recorder / metrics 输出

请在 session summary / trial summary / metrics 中记录足够字段，至少包括：

```text
trial_outcome
end_reason
run_stop_reason
operator_command
manual_completed
operator_aborted
trial_start_time
trial_end_time
trial_duration_seconds
max_trial_duration_seconds
detach_count
max_detach_count
timeout_enabled
detach_limit_enabled
block_center_task_position_at_end
pinch_task_position_at_end
block_center_in_target_at_end
distance_to_target_at_end
first_target_entry_time
last_snapshot_time
last_frame_index
source_stop_reason
gui_enabled
gui_closed
```

字段命名请优先贴合当前代码风格，不要求完全按上述名字，但语义必须清楚。

要求：

1. `trial_outcome` 和 `end_reason` 必须清楚。
2. 不要只写 `user_quit`，导致分不清 manual completed 和 abort。
3. 不要因为 GUI close 写成 trial abort。
4. 如果 trial 因 source disconnected / no frame timeout 停止，也要保留对应 reason。
5. 如果 runner 因 debug `--duration-seconds` 或 `--max-frames` 停止，也要和正式 timeout 区分。
6. raw data 不要被覆盖。
7. replay debug 不要修改原 session。

---

## 六、和 LiveTrialRunner / TrialController 的关系

请尽量以小改实现。

优先方案：

1. 把 termination config 注入当前 `TrialController` / engine config 中已有的 timeout / detach 机制。
2. 如果当前 `TrialController` 已经支持 timeout 和 detach too many，只需要把 live integrated runner 里原来的超大 debug 值替换为 termination config 值。
3. 对 manual complete / operator abort，优先在 runner 层通过现有 `request_stop` / stop mechanism 记录 outcome，不要强行塞进 block/controller 内部。
4. 如果现有 `TrialController` 有合适状态，可以小幅扩展。
5. 不要为了本阶段大重构 TrialController。

需要保证：

* LiveTrialRunner 仍然可以被 replay 测试。
* GUI snapshot callback 仍然只 publish 到 store。
* GUI 绘制不进入 callback。
* operator command 不依赖 GUI。
* 无 GUI 模式也可以按 `e` / `q` 结束。

---

## 七、CLI 参数建议

在 `run_live_integrated_session.py` 中新增或调整参数：

```text
--termination-config PATH
```

可以考虑保留调试参数：

```text
--duration-seconds
--max-frames
```

但要区分：

* `max_trial_duration_seconds`：正式保护性 trial timeout，产生 `FAILED_TIMEOUT`。
* `--duration-seconds`：debug runner 外层运行时长上限，产生 `DURATION_REACHED` 或等价 reason。

不要把二者混为一谈。

启动时请打印当前关键按键提示，例如：

```text
Operator commands:
  e = end current trial as MANUAL_COMPLETED
  q = abort whole run
  GUI close = close display only, does not stop trial
```

如果平台不支持非阻塞 keyboard command，请清晰说明。当前优先支持 Windows / msvcrt 即可，但不要让非 Windows 测试失败。

---

## 八、测试要求

请新增或更新测试，至少覆盖：

### config

1. 默认 termination config 正确。
2. 从 config 文件读取 `max_trial_duration_seconds` 和 `max_detach_count`。
3. 生效 config 能写入 summary/session 输出。
4. 缺失或非法 config 能清晰失败。

### operator command

5. `e` 被解析为 manual complete。
6. `q` 被解析为 operator abort。
7. 无按键时不影响 runner。
8. GUI close 不产生 manual complete / abort。

### runner / outcome

9. manual complete 后 runner 停止，并记录 `MANUAL_COMPLETED`。
10. operator abort 后 runner 停止，并记录 `ABORTED_BY_OPERATOR`。
11. timeout 后记录 `FAILED_TIMEOUT`。
12. detach/release 超限后记录 `FAILED_TOO_MANY_DETACHES` 或项目现有等价 outcome。
13. slip 多次不导致失败。
14. blocked 事件不导致失败。
15. block center 进入 target 不自动结束 trial。

### diagnostics / summary

16. manual end 时记录 block center / pinch position。
17. manual end 时记录 block center 是否在 target 内。
18. summary 能区分：

    * manual complete
    * operator abort
    * timeout
    * too many detaches
    * source disconnected
    * no new frame timeout
    * duration reached
19. `--duration-seconds` debug stop 不被误写成 `FAILED_TIMEOUT`。
20. GUI close 只记录 `gui_closed`，不停止 trial。

### regression

21. 不加新参数时现有 replay GUI 测试仍通过。
22. 不加 `--gui` 时 live integrated runner 行为不被 GUI 依赖影响。
23. 不改 parser / adapter / block motion 核心逻辑。
24. 全量测试通过，或明确说明无法通过的原因。

---

## 九、README / 运行说明

更新 README 或对应运行说明，写清楚：

1. 当前系统仍然不是正式实验 GUI。
2. 当前 trial 完成逻辑是人工完成：

   * 受试者认为到终点后，实验员按 `e` 结束当前 trial。
3. 程序不会因为 block center 进入 target 自动成功。
4. `q` 是 operator abort。
5. GUI close 只关闭显示层，不停止实验。
6. timeout / detach limit 是保护性自动停止。
7. termination config 如何配置。
8. `--duration-seconds` 和 `max_trial_duration_seconds` 的区别。
9. 当前 slip / blocked 只是事件，不导致失败。

---

## 十、完成后请输出

完成后请汇报：

1. 修改了哪些文件。
2. 新增了哪些文件。
3. 新增了哪些 CLI 参数。
4. termination config 的格式和默认值。
5. `e` / `q` / GUI close 的最终语义。
6. manual complete 如何进入 runner / summary。
7. timeout 和 detach limit 如何生效。
8. target 诊断记录了哪些字段。
9. summary 中新增了哪些字段。
10. 是否改动 TrialController / LiveTrialRunner；如果改了，说明原因和范围。
11. 是否改动 parser / adapter / BlockController 核心逻辑。
12. 哪些测试新增/更新。
13. 测试结果。
14. 当前已知风险。

确认这些默认决策。

补充要求：
1. e/q 第一版只保证 Windows msvcrt，可以接受；--gui 下 Qt 窗口焦点导致终端按键不稳要写入 known risks。
2. max_detach_count=20 表示允许 20 次，第 21 次触发失败，使用 total_detach_count > max_detach_count，不改。
3. MANUAL_COMPLETED 不塞进 TrialState，放在 LiveTrialRunner/runner outcome 层记录。但 summary/trial summary 必须以 runner-level trial_outcome/end_reason 为准，不能只依赖 TrialController.state。
4. q 可以保留 run_stop_reason=user_quit 兼容旧字段，但必须新增 trial_outcome=ABORTED_BY_OPERATOR、end_reason=operator_abort、operator_command=q。
5. termination config 支持 JSON；yaml/yml 用 lazy PyYAML，缺依赖清晰报错。
6. target_region 缺失时 target diagnostics 写 null，并记录 warning，不硬判。
7. --duration-seconds 是 debug 外层 stop，写 DURATION_REACHED；termination timeout 是保护性失败，写 FAILED_TIMEOUT，二者严格分开。
8. 建议在 event/metrics/summary 中记录 operator_manual_complete / operator_abort 事件及其时间，用于后处理。