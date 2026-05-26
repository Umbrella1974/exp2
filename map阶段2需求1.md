你现在要修改 exp2 的 stage4-offline 分支。

目标：
让已有真实 raw JSONL 数据可以在 MapConfig 地图场景中离线 replay，并生成 session + analyze_session.py 可视化。

重要背景：
当前已有：
- offline_replay_autocalibrated.py
- session_recorder.py
- analyze_session.py
- map_config.py
- map_generator.py
- maps/examples/*.json
- BlockController / TrackRegion / Box3D

当前 offline_replay_autocalibrated.py 会自动构造临时 task coordinate system，并自动生成 wide-track / fitted-corridor / narrow-corridor 临时场景。
这些 auto scene 只适合 smoke test，不适合作为正式地图配置。

本次目标：
在不修改核心实验逻辑的情况下，让 offline_replay_autocalibrated.py 可选使用 MapConfig JSON 作为 scene 来源。

不要修改：
- BlockController 核心逻辑
- TrialController 核心逻辑
- TaskCoordinateSystem 核心逻辑
- ManusViveExperimentAdapter
- raw parser / adapter
- haptic 状态语义
- analyze_session.py 的核心分析语义

新增/修改内容：

一、offline_replay_autocalibrated.py 增加参数

新增 CLI 参数：
- --map-config path，可选
- --map-id-override 可选，仅写入 trial_config/session metadata，不改变原 map 文件
- --allow-map-validation-warnings，布尔，可选

行为：
1. 如果不传 --map-config：
   - 保持现有行为完全不变
   - 继续使用 scene-mode 生成 auto scene

2. 如果传入 --map-config：
   - 仍然使用现有 auto calibration 逻辑构造临时 TaskCoordinateSystem
   - 不再使用 scene-mode 生成 block/track
   - 使用 load_map_config(path) 读取地图
   - 使用 validate_map_config(config) 校验地图
   - 如果 validation.errors 非空，报错退出
   - 如果 validation.warnings 非空：
       - 默认继续运行，但把 warnings 写入 summary/session warnings
       - 如果你认为更安全，也可以要求 --allow-map-validation-warnings 才继续，但默认行为请在帮助文本里写清楚
   - 使用 compile_map_to_track_region(config) 得到：
       - TrackRegion
       - block_initial_center_task
       - block_size
   - 用这些结果创建 TrialController / BlockController
   - trial_config.json / session/trial_config.json 使用 map_config_to_trial_config(config) 的输出
   - 同时保留：
       - scene_type = "map_config"
       - is_formal_scene = true 或 false 请谨慎处理，建议 false，因为 calibration 仍是 post-hoc auto
       - map_config_path
       - map_validation_warnings

重要：
即使使用 MapConfig，当前 replay 的 calibration 仍然是 post-hoc auto calibration。
所以 session_meta.json / calibration.json 仍必须明确：
- calibration_type = "post_hoc_auto"
- is_formal_calibration = false
- warning: "This session uses a configured map but post-hoc auto calibration; it must not be treated as a formal experimental trial."

scene 的标记建议：
- scene_type = "map_config"
- map_source_type 来自 map_config_to_trial_config
- is_formal_scene = false
原因：地图是配置的，但本次 replay 的 task coordinate system 不是正式受试者标定。

二、summary / session 输出

当 --map-config 启用时，summary.json 和 session/trial_summary.json 至少增加：
- map_config_used = true
- map_id
- map_config_path
- map_config_version
- map_source_type
- track_box_count
- target_region_present
- map_validation_errors
- map_validation_warnings

scene_auto.json 的处理：
- 如果 --map-config 未启用，保持现有 scene_auto.json
- 如果 --map-config 启用，可以：
    1. 继续写 scene_auto.json，但内容标明 scene_type="map_config"
    或
    2. 写 scene_config.json
  为了兼容旧流程，建议仍写 scene_auto.json，但里面内容来自 map_config_to_trial_config(config)，并明确 scene_type="map_config"。

三、analyze_session.py 兼容检查

analyze_session.py 目前已经支持 trial_config["track_boxes"]。
请确认使用 --map-config replay 后：
- session/trial_config.json 中包含完整 track_boxes
- analyze_session.py 的 trajectory_track_map 使用多段 track boxes 绘制
- analysis_summary.json 中 trajectory_map_used_track_boxes = true
- target_region_present = true 如果 map 有 target_region

如果当前 analyze_session.py 已经支持，不要大改，只补测试。

四、小修补

1. 将 manual example maps 中 target_region.metadata.type 统一为 "target_region"。
2. analysis_summary.json 可选增加：
   - valid_track_box_count
   - skipped_track_box_count
   如果实现简单就做；如果复杂，可以只记录 warnings。

五、测试

新增或更新 tests/test_offline_replay_map_config.py

使用 fake raw JSONL 和 maps/examples/xoy_straight.json 或临时 map config 测试：

至少覆盖：
1. 不传 --map-config 时，offline_replay_autocalibrated.py 旧行为不变。
2. 传 --map-config 后，可以跑完整 replay。
3. 输出 summary.json 中 map_config_used = true。
4. session/trial_config.json 中包含 track_boxes 和 target_region。
5. analyze_session.py 读取该 session 后：
   - 生成 analysis_summary.json
   - trajectory_map_used_track_boxes = true
   - track_box_count > 0
6. 如果 map validation 有 error，offline replay 应清晰失败。
7. raw_frames.jsonl 仍保持原始 raw dict，不写 frame_index。
8. 不依赖真实设备，不依赖 manus_vive_com。
9. matplotlib 不可用时不应导致测试失败。

六、文档 / README

README 补充一个用 MapConfig replay 旧 raw 数据的例子：

python offline_replay_autocalibrated.py \
  --raw-jsonl path/to/raw_frames.jsonl \
  --out-dir data/offline_replay/exp14_map_xoy_turn \
  --max-frames 5000 \
  --calibration-frames 100 \
  --map-config maps/examples/xoy_turn.json \
  --write-session

然后：

python analyze_session.py \
  --session-dir data/offline_replay/exp14_map_xoy_turn/session \
  --overwrite

说明：
- 这个流程使用配置地图，但 calibration 仍然是 post-hoc auto，不是正式实验。
- 该流程用于检查真实数据在指定 MapConfig 地图中的 pipeline 表现和可视化，不作为正式实验结果。

七、禁止事项

不要：
- 不要修改 BlockController / TrialController / TaskCoordinateSystem 核心逻辑
- 不要在 replay 中实现新的物理规则
- 不要在 analyzer 中重新跑控制器
- 不要接 socket
- 不要接 haptic hardware
- 不要把 post-hoc auto calibration 的 map replay 标记成正式实验
- 不要让绘图失败影响 replay 或 summary 输出

完成后运行 pytest -q，并报告：
1. 修改文件
2. 新增/更新测试
3. pytest 是否通过
4. 如何用 --map-config 跑旧 raw JSONL
5. 如何运行 analyze_session.py 查看多段地图可视化

同意你的判断，按下面语义实现：

1. --allow-map-validation-warnings 不采用“显式确认但不改变行为”的设计，这样语义太别扭。
   本阶段默认：validation errors 阻止 replay；validation warnings 不阻止 replay，只写入 summary/session warnings。
   如果要做严格模式，请改成 --strict-map-validation：传入后 warnings 也会导致失败。
   如果实现上想更简单，可以先不加 strict 参数，只采用默认 warnings 继续运行。

2. --map-id-override 只影响本次 replay 输出：
   - summary.json
   - scene_auto.json
   - session/trial_config.json
   不修改原始 map JSON 文件。
   同时在输出 metadata 中保留：
   - original_map_id
   - map_id_overridden = true

3. map validation errors 时，CLI 清晰失败并非零退出即可，不要求写 summary.json。
   不要为了失败 case 额外创建 error summary。
   validation warnings 则继续运行并写入 warnings。

4. 其他按你说的执行：
   --map-config 使用 MapConfig 的 TrackRegion / block_initial_center_task / block_size；
   session 仍标记为 post_hoc_auto calibration 和 is_formal_scene=false；
   trial_config.json 以 map_config_to_trial_config(config) 为主体，再补 replay/session 字段；
   analyze_session.py 现有 track_boxes 支持只补必要测试。

同意，可以开始实现。采用 --strict-map-validation，不采用 --allow-map-validation-warnings。

最终语义确认如下：

1. 默认：
   - validation errors 阻止 replay
   - validation warnings 不阻止 replay，只写入 summary/session warnings

2. --strict-map-validation：
   - validation errors 阻止 replay
   - validation warnings 也阻止 replay
   - 失败信息要明确说明是 strict mode 因 warnings 失败，不要把 warnings 混成 errors

3. summary.json / session trial_summary.json / scene_auto.json / session trial_config.json 中记录：
   - map_config_used
   - map_id
   - original_map_id
   - map_id_overridden true/false
   - map_config_path
   - map_config_version
   - map_source_type
   - track_box_count
   - target_region_present
   - strict_map_validation true/false
   - map_validation_errors
   - map_validation_warnings

4. --map-id-override 只影响本次输出，不修改原始 map JSON。

5. 即使使用 map_config，session_meta.json 和 calibration.json 仍必须明确：
   - calibration_type = post_hoc_auto
   - is_formal_calibration = false

6. trial_config.json / scene_auto.json 中：
   - scene_type = map_config
   - is_formal_scene = false

其他按你的方案执行，继续不修改核心控制器逻辑。