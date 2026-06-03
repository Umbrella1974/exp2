你现在要在 `exp2` 仓库的 `GUI-stage` 分支上收束当前 live integrated single-trial 实验流程。

当前系统已经有：

* live / replay 数据链路
* calibration
* map / trial config
* LiveTrialRunner
* TrialController / BlockController
* Debug GUI / live `--gui`
* manual trial end：`e`
* operator abort：`q`
* termination config
* timeout / detach protective stop
* trial_outcome / end_reason / target diagnostics

本阶段目标不是新增 haptic，也不是做正式多 trial session。目标是把当前单 trial 流程整理成一个**预实验可用、可复现、可检查**的完整流程。

请先阅读代码，不要依赖 README。重点看：

* `run_live_integrated_session.py`
* `live_trial_runner.py`
* `trial_controller.py`
* `termination_config.py`
* summary / recorder / session 输出相关代码
* GUI-stage 当前测试
* README 当前运行说明

## 目标 1：锁定 manual_completed 语义

如果还没有完成，请将：

```text
manual_completed
```

的语义收紧为：

```text
manual_completed = true only when end_reason == operator_manual_complete
```

也就是说：

* 实验员按 `e`：`manual_completed=true`
* `subject_end`：可以继续映射到 `trial_outcome=MANUAL_COMPLETED`，但 `manual_completed=false`
* `q`：`operator_aborted=true`
* GUI close：不产生 manual complete / abort

如果已有实现，请确认并补测试。

## 目标 2：补 integrated 落盘测试

请补测试固定以下行为：

模拟按 `e` 后，验证：

* `out_dir/summary.json`
* `session/trial_summary.json`

都包含一致的关键字段：

```text
trial_outcome = MANUAL_COMPLETED
end_reason = operator_manual_complete
operator_command = e
manual_completed = true
operator_aborted = false
```

如果存在 `subject_end` 路径测试，请补充：

```text
trial_outcome = MANUAL_COMPLETED
end_reason = subject_end
operator_command = null
manual_completed = false
```

## 目标 3：明确 operator command 运行提示

启动 live integrated runner 时，应清楚打印 operator command 提示，例如：

```text
Operator commands:
  e = end current trial as MANUAL_COMPLETED
  q = abort whole run
  GUI close = close display only, does not stop trial
```

如果当前平台只支持 Windows `msvcrt` 非阻塞键盘输入，请在 README / known risks 中说明：

* 第一版 operator command 主要保证 Windows terminal
* `--gui` 模式下，如果 Qt 窗口获得焦点，终端 e/q 可能不如纯 CLI 稳
* GUI close 仍然不停止 trial

## 目标 4：补 session artifact validation

新增一个轻量的 session artifact validation 逻辑，可以是函数、测试 helper 或独立脚本，例如：

```bash
python validate_session_outputs.py --session-dir <session_dir>
```

或等价形式。

它至少检查当前单 trial session 是否包含：

* raw frames，若该 run 应该保存 raw
* calibration 输出
* trial config / map config 或等价配置
* effective termination config
* summary.json
* trial_summary.json
* GUI diagnostics，若 `--gui` 启用
* trial_outcome
* end_reason
* termination config
* final block / pinch / target diagnostics

不要让 validation 改写 session。它只读文件并报告缺失项。

如果不想新增 CLI 脚本，也可以先新增可测试函数，但 README 要说明如何使用。

## 目标 5：明确 debug stop 和正式 protective stop 的区别

请确认 README 和 summary 字段清楚区分：

```text
--duration-seconds
```

是 debug runner 外层停止，产生类似：

```text
trial_outcome = DURATION_REACHED
end_reason = debug_duration_reached
```

而 termination config 中的：

```text
max_trial_duration_seconds
```

是保护性 trial timeout，产生：

```text
trial_outcome = FAILED_TIMEOUT
end_reason = trial_timeout
```

不要混淆两者。

## 约束

* 不做 haptic。
* 不做多 trial formal session。
* 不做 GUI Stop / Abort / Pause / Resume 按钮。
* 不改 parser / adapter 核心逻辑。
* 不改 BlockController 核心运动逻辑。
* 不让 GUI 直接推进 trial。
* 不让 GUI close 停止 trial。
* 不实现 target 自动成功判定。
* 不重构 TrialController，除非先说明原因和风险。

## 测试要求

请至少补充或更新测试覆盖：

1. `manual_completed` 只在 `operator_manual_complete` 时为 true。
2. `subject_end` 不会让 `manual_completed=true`。
3. 按 `e` 后，`summary.json` 和 `trial_summary.json` 都落盘正确 outcome。
4. `q` 后，summary 能区分 `ABORTED_BY_OPERATOR` 和旧的 `user_quit` 兼容字段。
5. GUI close 不产生 manual complete / abort。
6. session validation 能发现缺失 summary / trial_summary / termination config。
7. 现有 GUI / replay / live tests 仍然通过。

## 完成后请输出

1. 修改了哪些文件。
2. 是否修改了 `manual_completed` 语义。
3. 新增了哪些测试。
4. session validation 如何运行。
5. operator command 提示在哪里显示。
6. README / known risks 更新了什么。
7. 是否改动核心 parser / adapter / controller / block 逻辑。
8. 测试结果。
