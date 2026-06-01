你现在在 `exp2` 仓库的 `GUI-stage` 分支上做一个小型 cleanup patch。不要重构核心逻辑，不要接 live `--gui`，不要修改 TrialController / BlockController / parser / adapter 的核心行为。

背景：

目前 replay GUI 已经基本能跑，`LatestSnapshotStore`、`DebugViewModel`、auto scale、状态文本、replay debug runner 和 PySide6/pyqtgraph GUI 都已经实现。现在只做进入下一阶段前的两个小清理。

请先阅读当前代码，重点看：

* `run_replay_debug_gui.py`
* `debug_gui.py`
* `latest_snapshot_store.py`
* `debug_view_model.py`
* `replay_debug_runner.py`
* 相关 tests

任务 1：GUI 依赖检查顺序

检查 `run_replay_debug_gui.py` 当前是否会在确认 PySide6 / pyqtgraph 可用之前就启动 replay worker / replay thread。

目标：

* 如果 GUI 模式需要 PySide6 / pyqtgraph，应该在启动 replay worker 之前先做 GUI dependency preflight。
* 如果缺少 PySide6 / pyqtgraph，程序应清晰打印安装建议，例如：
  `pip install PySide6 pyqtgraph`
* 缺 GUI 依赖时，不应该留下已经启动但没有被干净停止的 replay worker/thread。
* 非 GUI 测试不能因为缺少 PySide6 / pyqtgraph 失败。
* 如果已有 lazy import / dependency check 机制，请复用，不要重复实现一套复杂逻辑。

允许的实现方式：

* 优先在启动 replay runner/thread 之前调用一个轻量 GUI dependency check。
* 或者调整现有 GUI import 顺序，确保缺依赖时提前失败。
* 如果项目已经有 `GuiDependencyError` 或类似异常，请继续使用它。
* 不要为了这个问题重构 replay runner。

任务 2：区分 GUI snapshot 覆盖计数和真实 raw dropped frame

检查当前代码里是否把 `LatestSnapshotStore` 的 dropped / overwritten snapshot 计数显示成了 `dropped_frame_count`。

需要明确区分：

* raw stream / socket 层的 dropped frames：表示设备数据或接收队列层面丢帧。
* GUI snapshot store 的 overwritten snapshots：表示 `LatestSnapshotStore` 只保留最新 snapshot，旧 snapshot 在 GUI 读取前被覆盖。这是正常的 latest-only 策略，不等于设备丢帧。

目标：

* 如果当前 view model / runtime stats / GUI label 中把 snapshot 覆盖数叫做 `dropped_frame_count`，请改成更准确的名字，例如：

  * `overwritten_snapshot_count`
  * 或 `dropped_gui_snapshot_count`
* 推荐使用 `overwritten_snapshot_count`，含义最清楚。
* GUI 显示文本也应避免写成 “dropped frames”，可以写：

  * `overwritten snapshots`
  * 或 `GUI skipped snapshots`
* 如果为了兼容现有代码需要保留旧字段，请只作为兼容 alias，并在内部主要使用新字段。
* 不要把 raw stream dropped frames 和 GUI overwritten snapshots 混在一起。
* 如果以后 raw stream dropped frames 要显示，应使用独立字段，例如 `raw_dropped_frame_count`。

测试要求：

请更新或新增测试，至少覆盖：

1. GUI dependency preflight 不会启动 replay worker 后才失败。
2. `LatestSnapshotStore` stats 的命名和 view model 显示语义正确。
3. 如果存在 runtime stats/view model 转换测试，请更新字段名。
4. 非 GUI 测试不依赖 PySide6 / pyqtgraph。
5. 现有 replay GUI 相关测试仍然通过。

约束：

* 不要接 `run_live_integrated_session.py --gui`，这属于下一阶段。
* 不要改正式 live runner 的行为。
* 不要修改核心 trial/control/parser/adapter 逻辑。
* 不要让 GUI 直接读 socket。
* 不要让 GUI 直接推进 trial。
* 不要把 calibration GUI 加进来。
* 这个任务应该是小 patch。如果你认为需要大改，请先说明原因和风险，等确认后再做。

完成后请输出：

1. 修改了哪些文件。
2. dependency preflight 是如何保证 replay worker 不会提前启动的。
3. `dropped_frame_count` 是否改名；如果保留兼容 alias，请说明。
4. 如何区分 raw dropped frames 和 GUI overwritten snapshots。
5. 跑了哪些测试，结果如何。
