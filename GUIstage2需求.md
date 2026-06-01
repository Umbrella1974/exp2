你现在要在 `exp2` 仓库的 `GUI-stage` 分支上实现下一阶段：把现有 Debug GUI 接入 live integrated runner 的可选 `--gui` 模式。

请先阅读当前代码，不要依赖 README。重点阅读：

* `run_live_integrated_session.py`
* `LiveTrialRunner`
* `DashboardSnapshot`
* `LatestSnapshotStore`
* `debug_view_model.py`
* `debug_gui.py`
* `replay_debug_runner.py`
* `run_replay_debug_gui.py`
* `live_session_state.py`，如果存在
* live socket / raw stream / pump stats 相关代码
* session summary / recorder 输出相关代码
* 当前 GUI-stage 已有 tests

本阶段目标：

在 `run_live_integrated_session.py` 中增加可选 `--gui` / `--gui-fps` 能力，让 live integrated session 在 trial running 阶段可以显示 Debug GUI。

核心边界不变：

1. GUI 只是显示层。
2. GUI 不直接监听 socket。
3. GUI 不直接读取 raw JSONL。
4. GUI 不直接推进 trial。
5. GUI 不直接修改 `TrialController` / `BlockController` 内部状态。
6. GUI 不修改 parser / adapter 核心逻辑。
7. GUI 不负责采集 calibration。
8. GUI 不负责正式实验 lifecycle 控制。
9. GUI 不接 haptic hardware。
10. GUI 不伪造 `DashboardSnapshot`。

优先实现最小稳定方案，不要扩大范围。

线程模型要求：

PySide6 / Qt GUI event loop 原则上应运行在主线程。

第一版 live `--gui` 不要求 GUI 覆盖 calibration / waiting 全流程。calibration 仍然主要走现有 CLI 流程。

建议方案：

1. `run_live_integrated_session.py` 启动后，前面的 live socket、calibration、map/trial config 准备流程尽量保持原样。
2. 在 trial running 即将开始时，如果启用了 `--gui`：

   * 创建 `LatestSnapshotStore`。
   * 将 `LiveTrialRunner` 的 snapshot callback 接到 `store.publish(snapshot)`。
   * GUI 在主线程运行。
   * live trial runner / trial loop 放到 worker thread。
   * GUI timer 以 `--gui-fps`，默认 30Hz，读取 `LatestSnapshotStore` 最新 snapshot 并显示。
3. 如果实现上述线程模型需要明显重构 integrated runner，请先停止并说明：

   * 为什么需要重构
   * 要改哪些文件
   * 风险是什么
   * 有没有更小替代方案
   * 是否可能先只完成函数级接入测试
     等确认后再做。
4. 不要采用“PySide6 GUI 在子线程中运行”的方案，除非先说明原因、平台风险和替代方案，并得到确认。

calibration / waiting 阶段：

1. `DashboardSnapshot` 主要代表 trial running 阶段。
2. calibration / waiting / review 阶段不要伪造 `DashboardSnapshot`。
3. 第一版可以不新增完整 `LiveSessionStatusStore`。
4. 如果 GUI 在没有 snapshot 时打开，只显示 waiting 文案，例如：

   * `Waiting for trial snapshot...`
   * `Calibration is handled by CLI. Follow terminal instructions.`
5. 不要为了显示 calibration 状态而给 `DashboardSnapshot` 塞假字段。
6. 如果你认为必须新增轻量 `LiveSessionStatusStore`，请先说明必要性和新增字段，不要大改。

GUI Close / Quit 语义：

1. 第一版只做 Close display。
2. 关闭 GUI 窗口默认只关闭显示层，不停止 trial/session。
3. GUI 不提供 Stop Experiment / Pause / Resume / Save / Next Trial 按钮。
4. 如果用户关闭 GUI：

   * trial/session 应继续按现有逻辑运行。
   * 记录 `gui_closed=true`。
   * 不调用 stop，除非未来明确实现 `Quit whole run`。
5. 本阶段不要实现 `Quit whole run`。
6. 如果未来实现 `Quit whole run`，必须通过现有 runner 的 `request_stop` / `stop_event` 等机制，不允许 GUI 直接修改 `TrialController` / `BlockController` 内部状态。
7. summary/log 中应记录：

   * `gui_enabled`
   * `gui_closed`
   * `gui_requested_stop`，第一版应为 false
   * 如方便，可记录 `gui_close_time` 或类似字段

GUI dependency 行为：

1. `PySide6` / `pyqtgraph` 必须 lazy import。
2. 不加 `--gui` 时，缺少 PySide6 / pyqtgraph 不能影响原 live runner。
3. 如果用户显式传了 `--gui`，但缺少 PySide6 / pyqtgraph，应清晰失败并退出，不要静默降级成 CLI 无 GUI。
4. 失败信息应包含安装建议，例如：
   `pip install PySide6 pyqtgraph`
5. `--gui` dependency preflight 应尽量发生在启动不可逆 live 流程之前，避免出现 GUI 缺依赖但 live socket/session 已经启动一半的情况。
6. 如果由于现有流程限制无法完全提前 preflight，请说明，并确保失败时流程干净退出。

snapshot callback 要求：

1. `LiveTrialRunner.snapshot_callback` 或等价 callback 中只能做轻量操作。
2. callback 只 publish 到 `LatestSnapshotStore`，或做非常轻量的 stats 更新。
3. 绝不允许在 callback 内做 GUI 绘图。
4. 绝不允许在 callback 内做耗时日志写入。
5. 绝不允许因为 GUI 绘图污染 LiveTrialRunner callback latency。
6. 如果已有 text display callback，`--gui` 不应破坏它。

text display 行为：

1. 第一版保留现有 `display_mode` / text status 行为。
2. `--gui` 只是额外订阅 snapshot 并显示 GUI。
3. 不要因为 `--gui` 默认关闭现有 CLI/text 输出。
4. 如果 GUI 和 text 输出同时存在，README/说明中写清楚：GUI 是 debug display，CLI 仍然负责 calibration prompts 和主要流程提示。

live runtime stats：

GUI 第一版应尽量显示已有可获得的 runtime stats，但不要为了显示所有诊断字段而污染 `DashboardSnapshot`。

优先显示：

1. snapshot age
2. GUI fps / render lag
3. overwritten snapshot count，注意这不是 raw dropped frames
4. trial/debug state
5. pinch/block/map/status/event 信息
6. 如果现有 runner/pump/raw stream 已经暴露，则显示：

   * receive fps
   * parse error count
   * raw dropped frame count
   * sync delta
   * frame age

建议使用独立 `DebugRuntimeStats` 或 view model 合并这些诊断信息，不要强行把 raw stream stats 塞进 `DashboardSnapshot`。

必须区分：

* `overwritten_snapshot_count`：`LatestSnapshotStore` 中旧 snapshot 被新 snapshot 覆盖，表示 GUI 跳过了旧显示帧。
* `raw_dropped_frame_count`：socket/raw stream 层真实丢帧或队列丢弃。

不要把二者都叫 `dropped_frame_count`。

GUI 日志：

1. live `--gui` 默认写 GUI 诊断日志。
2. 日志可记录：

   * mode
   * GUI fps
   * snapshot age
   * render lag
   * overwritten snapshot count
   * gui_closed
   * warnings
3. 日志建议写到 live session 归档目录内，例如：

   * `session/gui_diagnostics.csv`
     或如果当前代码结构更自然，则写到 integrated runner 的 `out_dir/gui_diagnostics.csv`。
4. 更推荐写入 session 目录，便于和本次 live run 一起归档。
5. GUI 日志不允许阻塞控制 loop。
6. 如果日志写入可能影响刷新，降频写入或缓冲写入。

trial ended 后 GUI 行为：

1. trial ended 后 GUI 保留最后一帧。
2. 不自动关闭 GUI。
3. 用户手动关闭窗口。
4. 关闭窗口不 retroactively stop trial。
5. 如果 trial worker 已结束但 GUI 仍打开，GUI 可以显示 `trial ended` 或等价已有状态。
6. 不要为了显示 ended 状态伪造新的 trial snapshot；可以显示最后 snapshot + runtime/status text。

scene 来源：

1. GUI scene 继续优先从 `trial_config` 构建。
2. 不要本阶段改成直接依赖 `MapConfig`，除非当前 replay GUI 已经这样做或有明确必要。
3. 这样 live 和 replay 统一，减少分叉。
4. 如果 live 有 `--anchor-current-pinch-debug`、`--ignore-task-z` 等特殊配置，应在 GUI status/warnings 中尽量显示，但不要为此大改核心逻辑。
5. 如果这些 warning 已经存在于 `trial_config["warnings"]` 或 runtime warnings，就复用它们。

正式实验边界：

1. live `--gui` 仍然是 debug display，不是正式实验 GUI。
2. 不要新增正式实验 lifecycle 控制。
3. 不要新增 pause/resume 控制。
4. 不要新增 haptic control。
5. summary/README 中继续标记或说明：

   * `is_formal_experiment=false`
   * GUI 是 debug display
   * calibration 仍主要看 CLI
   * GUI 主要完整显示 trial/debug snapshot

测试要求：

请补充或更新测试，至少覆盖：

1. 不加 `--gui` 时，`run_live_integrated_session.py` 的参数解析和默认行为不变。
2. 加 `--gui` 时，会创建或使用 `LatestSnapshotStore`。
3. live runner 的 snapshot callback 能 publish 到 store。
4. callback 不调用 GUI 绘图函数。
5. 缺少 PySide6 / pyqtgraph 且传了 `--gui` 时，能清晰失败，不进入脏状态。
6. 缺少 PySide6 / pyqtgraph 但不传 `--gui` 时，非 GUI live runner 和测试不受影响。
7. GUI close 只记录 `gui_closed`，不 request_stop，不修改 TrialController / BlockController。
8. 如果有 summary writer 测试，确认 summary/log 中包含 `gui_enabled`、`gui_closed`、`gui_requested_stop`。
9. 如果 GUI 本体难以自动化测试，至少通过 monkeypatch/stub 验证 `--gui` 接入路径，不要让测试 import 真实 PySide。
10. 现有 replay GUI 测试仍然通过。
11. 全量测试应通过，或者明确列出无法通过的原因。

实现顺序建议：

1. 阅读当前 GUI-stage 代码，确认 replay GUI 已有接口。
2. 给 `run_live_integrated_session.py` 增加 `--gui` / `--gui-fps` 参数，但先不要启动 GUI。
3. 增加 GUI dependency preflight，确保不加 `--gui` 不受影响。
4. 将 live runner 的 snapshot callback 接到 `LatestSnapshotStore.publish()`。
5. 用单元测试验证 snapshot publish 路径。
6. 设计 trial running 阶段的 GUI 启动方式，优先 GUI 主线程 + trial worker。
7. 如果需要小幅拆分 integrated runner 中的 trial-running 部分，可以做，但不要重构 calibration / parser / adapter / controller。
8. 接入 Debug GUI 显示层。
9. 写 GUI diagnostics log。
10. 写 summary/log 字段。
11. 更新 README 或运行说明。
12. 跑测试。

约束：

1. 优先小改。
2. 不要重构核心 trial / controller / block / parser / adapter 逻辑。
3. 不要让 GUI 直接监听 socket。
4. 不要让 GUI 直接推进 trial。
5. 不要把 GUI 绘图放进 callback。
6. 不要伪造 `DashboardSnapshot`。
7. 不要实现 calibration GUI。
8. 不要实现正式实验 lifecycle GUI。
9. 不要实现 haptic GUI。
10. 如果你认为必须重构，请先停止并说明原因、范围、风险和替代方案，等确认后再做。

完成后请输出：

1. 修改了哪些文件。
2. 新增了哪些文件。
3. 如何运行 live integrated runner + GUI。
4. 不加 `--gui` 时是否保持原行为。
5. GUI dependency preflight 如何工作。
6. GUI 线程模型具体如何实现。
7. calibration / waiting 阶段显示什么。
8. trial running 阶段 snapshot 如何进入 GUI。
9. GUI close 后 trial/session 是否继续。
10. GUI log 写到哪里。
11. summary 中新增了哪些 GUI 字段。
12. 哪些 runtime stats 已经显示，哪些暂未显示。
13. 哪些部分是临时实现。
14. 当前已知风险。
15. 测试结果。

确认这些默认决策。

1. trial ended 后 GUI 不自动关闭，保留最后一帧，用户手动关闭。可以接受 summary/session finalize 等 GUI close 后继续。但 trial worker 结束后不要继续追加 trial 数据或伪造 snapshot；GUI 只是显示最后 snapshot。

2. GUI 中途关闭后，trial/session 继续运行。第一版不做 reopen，不做 Stop whole run。

3. --gui 依赖 preflight 尽量在 live socket、session、raw log、calibration 启动前完成。缺依赖时清晰失败；不加 --gui 时完全不导入 GUI 依赖。

4. live GUI diagnostics 优先写入 session/gui_diagnostics.csv；如果 session 目录尚未建立，再 fallback 到 out_dir/gui_diagnostics.csv。

5. live runtime stats 第一版只显示已有且可靠的字段：snapshot age、GUI fps/render lag、overwritten snapshot count、pump/raw dropped frames、parse errors。receive_fps 如果没有可靠来源，先不显示或显示 N/A，不要硬造。

补充：GUI close 只关闭显示层，不 stop trial；trial ended 后 GUI 保留最后一帧；不做 pause/resume，不做正式 lifecycle 控制。

补充确认：

当前项目还没有完整的正式 trial success / termination 逻辑，例如 block 到达 target 后何时算成功、是否需要 dwell、是否需要 release 等。因此本阶段 live --gui 不要新增或修改 trial 终止判定。

live --gui 只显示现有 LiveTrialRunner / TrialController 产生的状态。runner 仍然只按现有停止条件停止，例如 timeout、detach too many、duration_seconds、max_frames、user_quit、subject_end、KeyboardInterrupt 等。

如果 runner 因现有条件结束，GUI 保留最后 snapshot。  
如果 runner 没有自然结束，GUI 不负责让 trial 结束。  
GUI Close 仍然只关闭显示层，不 stop trial。

正式 trial success / termination criteria 后续单独设计，不放进本阶段。