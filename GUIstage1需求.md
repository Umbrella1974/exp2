你现在要在 `exp2` 仓库的 `stage-5` 分支基础上开发一个实时 Debug GUI 原型。

请先阅读代码，不要依赖 README。重点阅读现有 live / replay / trial / calibration 相关代码，尤其是：

* `run_live_integrated_session.py`
* `LiveTrialRunner`
* `DashboardSnapshot`
* `TrialController`
* `BlockController`
* calibration 相关代码
* map / trial config 相关代码
* live socket / raw stream 相关代码
* parser / adapter / raw frame source 相关代码
* stage-5 现有集成 runner

这次任务的目标不是做正式实验 GUI，而是做一个“实验员调试 GUI”。它的用途是：

1. 在有设备时，显示主程序 live 运行时产生的实时状态。
2. 在没有设备时，用已有 session / `raw_frames.jsonl` 做 replay debug。
3. 帮助实验员看懂手部位置、pinch、物块、地图、运动状态、控制状态、事件状态。
4. 不负责正式实验生命周期管理。
5. 不负责完整 calibration GUI。
6. 不接触 haptic hardware。

最重要的架构要求：

**GUI 只是显示层，不是数据采集层，也不是实验控制层。**

GUI 不应该直接监听 socket。
GUI 不应该直接读取 raw JSONL 后自己推进 trial。
GUI 不应该自己实现 parser / adapter / TrialController / BlockController 逻辑。
GUI 不应该直接修改 `TrialController` / `BlockController` 内部状态。
GUI 只消费 `DashboardSnapshot` 或由 `DashboardSnapshot` 转换出来的 GUI view model。

正确方向是：

```text
live socket / raw replay source
        ↓
runner / session layer
        ↓
parser
        ↓
adapter
        ↓
LiveTrialRunner
        ↓
TrialController / BlockController
        ↓
DashboardSnapshot
        ↓
LatestSnapshotStore
        ↓
Debug GUI
```

也就是说，live 和 replay 的差异应该发生在 runner / source 层，而不是 GUI widget 层。

第一阶段目标：

1. 新增或改造一个可选 Debug GUI 显示层。
2. 优先不要破坏现有 `run_live_integrated_session.py` 的无 GUI 模式。
3. 如果接入现有 live 主程序，优先使用 `--gui` 之类的可选参数。
4. 如果 replay 需要独立入口，可以新增一个 replay debug runner，例如：

   * `run_replay_debug_gui.py`
   * 或 `run_debug_gui.py --mode replay`
5. 但无论入口怎么设计，GUI 本身都只读 snapshot，不直接接 socket，不直接处理 raw JSONL 控制 trial。

建议实现方式：

1. 新增 `LatestSnapshotStore`

   * 线程安全。
   * 只保存最新 `DashboardSnapshot`。
   * 不排队。
   * 控制线程写入最新 snapshot。
   * GUI timer 读取最新 snapshot。
   * 宁可丢中间帧，也不要播放旧 snapshot 导致显示延迟。

2. 新增 snapshot 到 GUI view model 的转换层

   * 例如 `snapshot_to_debug_view_model(snapshot)`。
   * 这个层负责从 `DashboardSnapshot` 中提取 GUI 需要显示的信息。
   * GUI 只画 view model，不直接依赖复杂 controller 内部对象。
   * 这个转换层必须可以单元测试，不依赖 PySide6 / pyqtgraph。

3. 新增 Debug GUI

   * 优先使用 `PySide6 + PyQtGraph`。
   * 不要使用 matplotlib 做实时动态刷新。
   * `PySide6` 和 `pyqtgraph` 必须 lazy import。
   * 缺少 GUI 依赖时，入口脚本打印安装建议，例如：
     `pip install PySide6 pyqtgraph`
   * 非 GUI 测试不能因为缺少 PySide6 / pyqtgraph 失败。
   * GUI 本体测试可以在依赖缺失时 skip。

4. GUI 刷新方式

   * GUI 使用 timer 默认 30Hz 刷新。
   * 刷新频率可配置，例如 `--gui-fps 30`。
   * GUI 每次刷新只读取 latest snapshot。
   * GUI 不阻塞控制 loop。
   * `LiveTrialRunner` 的 callback 中不能直接做复杂绘图。

live 模式要求：

1. live 模式下，数据仍然由现有主程序 / integrated runner 获得。
2. 如果在 `run_live_integrated_session.py` 中增加 `--gui`：

   * 不要破坏原来的 CLI 行为。
   * 不加 `--gui` 时，现有流程应该保持不变。
   * 加 `--gui` 时，主程序照常负责 live socket、calibration、map/trial config、LiveTrialRunner、recorder。
   * GUI 只显示主程序产生的 `DashboardSnapshot`。
3. 不要让 Debug GUI 自己监听同一个 live socket 来替代主程序，除非先明确说明这是另一个 debug runner，并得到确认。
4. 当前阶段不要做正式 GUI 对 calibration / trial lifecycle 的控制按钮。
5. 第一版 GUI 只需要 Quit / Close 能力。

replay 模式要求：

1. replay 模式用于没有设备时调试 GUI 和控制链路。
2. replay 应该由 replay runner 负责读取数据，而不是 GUI widget 负责读取数据。
3. replay 优先从已有 session 读取：

   * `raw_frames.jsonl`
   * calibration 文件
   * trial config / map config
   * session meta
4. 请先阅读 stage-5 实际 session 输出结构，不要假设文件名一定叫 `calibration.json` 或 `trial_config.json`。如果实际文件名不同，以代码实际输出为准。
5. 如果提供 `--session-dir`，优先从 session 中自动查找 replay 所需文件。
6. 如果提供单独 `--raw-jsonl`，必须同时显式提供 calibration 和 map/trial config 所需文件。
7. replay 不要直接读取 processed csv 画图。
8. replay 应重新走：

   * raw frame
   * parser
   * adapter
   * `LiveTrialRunner`
   * `TrialController` / `BlockController`
   * `DashboardSnapshot`
   * GUI
9. 如果缺少 calibration 或 map / trial config，不要静默使用默认坐标系继续运行。必须清晰失败并说明缺了什么。
10. replay 默认按原始时间戳节奏播放。
11. 可以额外支持固定频率播放，例如 60Hz。
12. replay 最好支持 pause / resume；如果实现成本明显增加，可以先不做，但需要在最终说明中写清楚。

GUI 第一版显示内容：

主视图以 2D task view 为主，但代码结构要方便以后扩展到 3D。当前可以先显示 x-y 平面，z 用数值、状态文字或高度条显示。

主视图需要显示：

1. 当前 pinch / hand 位置。
2. 当前 block 位置。
3. map / track region / target / start position。
4. pinch 到 block 的相对关系，例如 dx / dy / dz 或距离。
5. 当前运动状态。
6. 当前控制状态。
7. 当前 trial / block / contact / release / slip / blocked / event / state 信息。

状态面板至少显示：

1. tracker 是否 valid。
2. skeleton / hand 是否 valid。
3. pinch 是否 valid。
4. pinch distance。
5. pinch/task 坐标。
6. block/task 坐标。
7. dx / dy / dz。
8. contact / release / moving / blocked / slip 等已有状态或事件。
9. trial ended / success / timeout 等已有 trial 状态。
10. sync delta / frame age / dropped frames / parse error count / receive fps 等诊断信息，如果现有代码能提供。
11. 当前 replay/live mode。
12. snapshot age。
13. GUI fps 或 render lag。

术语要求：

不要发明代码里不存在的状态名。
如果项目里没有 `dragger` 这个概念，就不要新造 `dragger` 术语。
请优先复用项目现有命名，例如：

* trial
* block
* contact
* release
* slip
* blocked
* event
* state
* snapshot
* calibration
* map
* task coordinates

如果需要新增 GUI 文案，可以写成人能看懂的描述，但不要改核心数据模型里的概念名。

可视化要求：

1. 2D task view 要自动缩放。
2. 自动缩放优先保证 map、block、pinch 都可见。
3. 图注、文字、legend 不能遮挡 block 和 map。
4. 状态说明尽量放在 plot 外侧或独立 side panel。
5. 不要把 legend 直接压在地图或物块上。
6. 如果需要显示坐标轴、单位、方向，请保持简洁。
7. 当前先不用做复杂 3D，但类和文件设计不要把所有逻辑写死成只能 2D，后续应能扩展到 3D view。

GUI 日志：

1. 默认开启简单 GUI 诊断日志。
2. 日志可以记录：

   * mode
   * GUI fps
   * snapshot age
   * render lag
   * replay frame index
   * warnings
   * dropped GUI frames
3. 日志可以写成 csv 或 jsonl。
4. 如果 GUI 日志影响实时刷新，必须降频记录或异步记录。
5. GUI 日志不允许阻塞控制 loop。

依赖和测试要求：

1. `PySide6` / `pyqtgraph` 必须 lazy import。
2. 缺少 GUI 依赖时，非 GUI 单元测试仍然能跑。
3. view model / `LatestSnapshotStore` / replay runner 的非 GUI 部分必须不依赖 GUI 库。
4. GUI 本体测试可以在依赖缺失时 skip。
5. 请补充或更新测试，至少覆盖：

   * `LatestSnapshotStore` 只保留最新 snapshot。
   * `LatestSnapshotStore` 线程安全。
   * snapshot 到 debug view model 的转换。
   * 2D view range / auto scale 计算逻辑。
   * 状态文本生成逻辑。
   * replay runner 可以从小型 raw JSONL 或 fixture 中产生 snapshot。
6. 如果 GUI 本体难以自动化测试，至少保证非 GUI 部分有单元测试。
7. 保证现有测试仍然通过。

修改约束：

1. 优先新增文件。
2. 可以小改现有文件，例如：

   * 增加 `--gui` 参数。
   * 增加 callback 写入 `LatestSnapshotStore`。
   * 给 `DashboardSnapshot` 补少量 GUI 需要但已经存在于控制链路中的字段。
   * 增加 replay debug runner。
3. 不允许重构核心 trial / controller / block / parser / adapter 逻辑。
4. 不要把 GUI 绘制逻辑塞进核心控制逻辑。
5. 不要让 GUI 直接控制触觉硬件。
6. 不要删除现有 CLI runner。
7. 不要破坏现有无 GUI 模式。
8. 如果你认为必须重构，请先停止并说明：

   * 为什么必须重构
   * 要改哪些文件
   * 风险是什么
   * 有没有替代方案
   * 如何保证不破坏现有测试和行为
     等我确认后再做。

建议实现顺序：

1. 阅读代码，确认现有 snapshot / callback / session 输出结构。
2. 设计最小 `LatestSnapshotStore`。
3. 设计 `DebugViewModel` 或等价的纯 Python 数据结构。
4. 实现 snapshot 到 view model 的转换和测试。
5. 实现 2D view range / auto scale 计算和测试，确保 legend / 状态文本不遮挡 block 和 map。
6. 实现 PySide6 + PyQtGraph GUI，lazy import。
7. 把 GUI 接到现有 live integrated runner 的可选 `--gui` 模式，保证不加 `--gui` 时行为不变。
8. 实现 replay debug runner，从 session 或 raw JSONL + config 重新走 parser / adapter / LiveTrialRunner，产出 snapshot 给同一个 GUI。
9. 补测试。
10. 跑现有测试。

完成后请输出：

1. 修改了哪些文件。
2. 新增了哪些文件。
3. 如何运行 live + GUI。
4. 如何运行 replay + GUI。
5. 依赖项是什么。
6. 缺少依赖时如何处理。
7. 哪些部分是临时实现。
8. 哪些地方后续适合扩展到 3D。
9. 是否修改了核心逻辑；如果修改了，逐条说明原因。
10. 是否改变了 `run_live_integrated_session.py` 的无 GUI 行为。
11. 当前已知风险。
12. 测试结果。

GUI Close / Quit 语义：
- 默认关闭 GUI 只关闭显示层，不直接停止 trial/session。
- 如果实现 Quit whole run，必须通过现有 runner 的 request_stop / stop_event 机制。
- 不允许 GUI 直接修改 TrialController / BlockController 内部状态。
- summary/log 中记录 gui_closed 或 gui_requested_stop。

calibration / waiting 阶段：
- DashboardSnapshot 主要来自 trial running 阶段。
- calibration / waiting / review 阶段不要伪造 DashboardSnapshot。
- 如需显示这些阶段，请使用现有 LiveSessionStatus，或新增轻量 LiveSessionStatusStore。
- 第一版允许 calibration 仍主要由 CLI 提示，GUI 只完整显示 trial/debug 状态。

replay debug runner：
- replay 重新走 raw -> parser -> adapter -> LiveTrialRunner -> DashboardSnapshot。
- replay 默认不覆盖原 session。
- 如果写 replay 输出，必须写到单独 out-dir，不要修改原 session。
- 如果 session 中缺少 calibration/map/trial config，必须清晰失败。

实现优先级：
- 优先完成 LatestSnapshotStore、DebugViewModel、auto scale、状态文本和 replay GUI。
- 再接入 run_live_integrated_session.py --gui。
- 如果 PySide6/pyqtgraph 环境有问题，先保证 replay/text 或非 GUI 测试通过，不要破坏 live runner。