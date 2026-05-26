你现在要修改 exp2 的 stage3-communication-layer 分支。

目标：
新增地图 / 轨道配置模块，用于正式实验中配置每一个轨道部分，而不是只依赖 offline_replay_autocalibrated.py 中的临时自动方块场景。

重要背景：
当前项目已有：
- BlockController 使用 TrackRegion / Box3D 判断轨道约束
- offline_replay_autocalibrated.py 会自动生成 wide-track / fitted-corridor / narrow-corridor
- 这些自动场景只用于未正式标定数据的 smoke test，不能作为正式实验地图配置

本阶段目标：
新增 MapConfig 层，把手动配置或规则生成的地图，编译成 BlockController 可用的 TrackRegion。

不要修改：
- BlockController 核心逻辑
- TrialController 核心逻辑
- TaskCoordinateSystem 核心逻辑
- ManusViveExperimentAdapter
- raw parser / adapter
- haptic 状态机

新增模块一：
map_config.py

实现以下数据结构和函数：

1. MapBoxSpec
字段：
- id: str
- min: list[float] length 3
- max: list[float] length 3
- label: str | None
- metadata: dict

2. MapConfig
字段：
- map_id: str
- description: str | None
- coordinate_space: "task"
- unit: "m"
- block_initial_center_task: list[float] length 3
- block_size: list[float] length 3
- track_boxes: list[MapBoxSpec]
- target_region: MapBoxSpec | None
- metadata: dict

3. load_map_config(path) -> MapConfig
读取 JSON 地图配置。

4. validate_map_config(config) -> list[str]
返回 warnings / errors。
至少检查：
- map_id 存在
- coordinate_space 必须是 "task"
- unit 必须是 "m"
- block_initial_center_task 是 3D
- block_size 是 3D 且每个维度 > 0
- track_boxes 非空
- 每个 box min/max 是 3D
- box min < max
- block_initial_center_task 必须位于至少一个 track box 内
- target_region 如果存在，也必须和 track 有交集
- 相邻 track box 如果配置了 order，则检查是否面接触或有体积重叠
- 如果存在明显 gap，返回 warning

5. compile_map_to_track_region(config) -> tuple[TrackRegion, np.ndarray, np.ndarray]
返回：
- TrackRegion
- block_initial_center_task
- block_size

不要改 geometry.py 的核心行为。
只是把 map JSON 转成现有 Box3D / TrackRegion。

6. map_config_to_trial_config(config) -> dict
生成可写入 session/trial_config.json 的 dict。
必须包含：
- map_id
- map_source
- block_initial_center_task
- block_size
- track_boxes
- target_region
- is_generated=false

新增模块二：
map_generator.py

第一版只做一个简单规则生成器，不要复杂随机迷宫。

实现 generate_orthogonal_corridor_map(...)

参数：
- map_id
- seed
- num_segments
- start
- initial_direction
- segment_length_range
- track_width
- z_tolerance
- allowed_turns，例如 ["left", "right", "straight"]
- plane，例如 "xoy"
- junction_overlap 默认 0.03

规则：
- 生成由 axis-aligned AABB boxes 组成的连续轨道
- 每个 segment 是一个 box
- 相邻 segment 必须面接触或重叠 junction_overlap
- 不允许中间留 gap
- 输出 MapConfig
- 保存 generator_seed 和 generator_params 到 metadata
- 不要求生成复杂 3D 迷宫，先支持 xoy 平面即可
- xoz / yoz 可作为 TODO

新增示例地图：

maps/examples/xoy_straight.json
maps/examples/xoy_turn.json
maps/examples/xoy_two_turns.json

新增测试 tests/test_map_config.py

至少覆盖：
- 可以加载 example map
- validate_map_config 通过
- block_initial_center 在 track 内
- box min/max 错误会报 warning/error
- block_initial_center 不在 track 内会报 error
- compile_map_to_track_region 后 point_in_track 可用
- target_region 和 track 无交集时 warning/error
- map_config_to_trial_config 输出可 JSON 序列化

新增测试 tests/test_map_generator.py

至少覆盖：
- 同一个 seed 生成同样地图
- 不同 seed 可以生成不同地图
- 生成的 map validate 通过
- 相邻 segment 连续或重叠
- 不产生 gap
- block_initial_center 在 track 内
- 输出可写入 JSON

不要做：
- 不要接真实设备
- 不要接 socket
- 不要写 GUI
- 不要接 haptic hardware
- 不要在地图模块里实现 BlockController 规则
- 不要把地图生成结果当成正式随机实验设计，第一版只是基础工具

完成后运行 pytest -q，并报告：
1. 新增文件
2. 新增测试
3. 如何加载手动 map
4. 如何生成规则 map
5. 如何把 map 编译成 TrackRegion

补充约束和澄清：

1. MapBoxSpec 增加字段：
   - order: int | None = None

   ordered track boxes 用于检查相邻轨道段连续性。
   如果 order 缺失，则只做单个 box 合法性和 block/target 检查，不强行检查全局连通顺序。

2. validate_map_config 不要只返回 list[str]。
   请新增：
   @dataclass
   class MapValidationResult:
       errors: list[str]
       warnings: list[str]

       @property
       def is_valid(self) -> bool:
           return not self.errors

   validate_map_config(config) -> MapValidationResult

3. compile_map_to_track_region(config) 返回现有核心类型：
   tuple[TrackRegion, Vec3, Vec3]

   分别是：
   - TrackRegion
   - block_initial_center_task: Vec3
   - block_size: Vec3

   不要为了这个接口新增 numpy 依赖。
   MapBoxSpec 的 min/max 转成 Box3D 时：
   - center = (min + max) / 2
   - size = max - min

4. ordered boxes 的连续性检查：
   如果相邻 order 的 boxes 既没有体积重叠，也没有面接触且接触面有正面积，则记为 error。
   因为 BlockController / geometry 的 track union 不允许跨 gap 连续移动。
   如果只存在边接触或点接触，也应至少 warning，建议 error。

5. target_region 检查：
   target_region 如果存在，应与至少一个 track_box 有体积交集。
   如果完全不相交，error。
   如果只是面接触，可 warning。

6. generate_orthogonal_corridor_map 中：
   start 明确定义为 block_initial_center_task，也是第一段 corridor 的起点附近中心线位置。
   第一段 box 必须包含 start。
   每个生成的 segment box 需要写入：
   - id
   - order
   - label
   - metadata.segment_direction
   - metadata.segment_length
   - metadata.turn_from_previous

7. MapConfig.metadata 中保存：
   - generated: true / false
   - generator_name
   - generator_seed
   - generator_params

8. map_config_to_trial_config(config) 输出中必须包含完整 track_boxes，不只是外包围 bounds。
   这样 analyze_session.py 后续可以画出真实多段轨道，而不是只画一个大矩形。

9. 本阶段只实现 MapConfig core，不要求接入 offline_replay_autocalibrated.py 或 analyze_session.py。
   但是输出的 trial_config dict 必须为后续 session/analyzer 使用保留足够信息。

补充说明：
第一版 orthogonal corridor generator 只支持 xoy 平面上的正交轨道。
所有转弯都必须是 90° 转弯：
- "left" 表示相对当前方向逆时针旋转 90°
- "right" 表示相对当前方向顺时针旋转 90°
- "straight" 表示方向不变
不支持任意角度转弯，不支持斜向 segment，不支持 180° 掉头。
每个 segment 必须是 axis-aligned AABB。

同意，可以开始实现。再补充几个约束：

1. generate_orthogonal_corridor_map() 中 start 必须被第一段 track box 包含，且 block_initial_center_task = start。

2. generator 不生成 180 度掉头；每段方向只能是当前方向、左转或右转。

3. example map 要保持手工可读。每个 track box 都要有 id、order、label、min、max、metadata.direction、metadata.segment_length 等信息。

4. map_config_to_trial_config() 输出中增加 map_config_version=1，以及 map_source_type="manual" 或 "generated"。MapConfig.metadata 中也保留 generated/generator_name/generator_seed/generator_params。

其他按你的保守方案执行。本阶段仍然不接入 offline_replay_autocalibrated.py 或 analyze_session.py。