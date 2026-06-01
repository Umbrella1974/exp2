你现在要修改 exp2 的 stage-5 分支。

目标：
修复当前 integrated live session 在实机运行前的稳定性问题。
本轮是小修补，不新增大功能，不改核心实验逻辑。

当前已有：
- run_live_integrated_session.py
- calibration_live_runner.py
- latest_frame_buffer.py
- live_raw_stream.py
- calibration_io.py / calibration_geometry.py / calibration_sampling.py
- map_config.py
- SessionRecorder
- TrialController / BlockController

已知问题：
1. live calibration 中存在时间系混用：
   live stream 模式下，segment duration 应该用 time.monotonic() 判断；
   但当前代码可能把 raw frame_time / sample_time 与 time.monotonic() 的 live_end 比较。
   这会导致真实 live 模式下 calibration segment 可能第一帧就结束或异常结束。

2. 等待 valid tracker 时，如果没有数据、client 断开、tracker 一直 invalid，可能无限等待。

3. trial loop 中，如果 client 断开或 source 停止，可能无限等待最新帧，除非 duration/max_frames 到达。

4. raw logging 每帧 flush 可以先不重构，但需要在 summary 中记录 pump / latest buffer 状态，便于判断是否被 I/O 拖慢。

不要修改：
- BlockController 核心逻辑
- TrialController 核心逻辑
- TaskCoordinateSystem 核心逻辑
- parser / adapter 语义
- haptic_feedback 语义
- calibration 几何语义
- MapConfig 语义

============================================================
一、修复 live calibration segment 时间判断
============================================================

修改 calibration_live_runner.py。

要求：
1. 明确区分两种时间模式：

A. simulated raw JSONL mode:
- 使用 frame_time / raw timestamp / sample_time 判断 segment duration
- segment_elapsed = frame_time - segment_start_frame_time
- 适用于 --raw-jsonl / simulated live

B. real live stream mode:
- 使用 time.monotonic() 判断 segment duration
- segment_elapsed = time.monotonic() - segment_start_monotonic
- raw frame_time 只用于记录，不参与 break 判断

2. 禁止在 real live stream mode 下比较：
   frame_time > live_end
   或 raw timestamp 与 time.monotonic()。

3. 在 segment summary 中记录：
- time_mode = "monotonic_live" 或 "frame_time_simulated"
- segment_start_monotonic
- segment_end_monotonic
- segment_start_frame_time
- segment_end_frame_time
- duration_seconds_measured
- valid_sample_count
- received_frame_count

4. 新增/更新测试：
- real live mode 下 raw timestamp 很大时，不应导致 segment 第一帧就结束。
- simulated mode 下用 frame_time 切 segment。
- frame_time 缺失时，simulated mode 要清晰失败或 warning，并提示使用 live mode / 离线工具。

============================================================
二、等待 stream / valid tracker 加 timeout 和断流检测
============================================================

修改 run_live_integrated_session.py。

新增 CLI 参数：
- --stream-wait-timeout-seconds 默认 60
- --valid-tracker-timeout-seconds 默认 60
- --valid-pinch-timeout-seconds 默认 60，可选，如果当前没有显式等待 pinch，可暂时只记录

行为：
1. WAITING_FOR_STREAM：
   - 如果超时仍没有收到任何 raw frame：
     run_stop_reason = "stream_wait_timeout"
     phase = ERROR 或 STOPPED
     写 summary
     安全退出

2. WAITING_FOR_VALID_TRACKER：
   - 如果超时仍没有 tracker_valid：
     run_stop_reason = "valid_tracker_timeout"
     phase = ERROR 或 STOPPED
     写 summary
     安全退出

3. 如果 LiveRawStreamServer / pump 检测到 client disconnected 或 source stopped：
   - 不要无限等待
   - run_stop_reason = "client_disconnected_before_trial" 或 "source_stopped_before_trial"
   - 写 summary
   - 安全退出

4. 等待 loop 中必须检查：
   - stop_event
   - pump.stop_reason
   - source/server stop reason
   - timeout deadline

============================================================
三、trial loop 中处理断流
============================================================

修改 trial running loop。

要求：
1. 如果 get_latest() 长时间没有新帧：
   - 不要立刻退出
   - 但要记录 no_new_frame_count
   - 如果 source 已停止 / client 已断开，则退出 trial loop

2. 新增 CLI 参数：
   - --no-frame-timeout-seconds 默认 5.0

3. 如果 TRIAL_RUNNING 中超过 no_frame_timeout_seconds 没有新帧：
   - 如果 source 仍 active，可以 warning 并继续或退出，第一版建议退出更安全：
     run_stop_reason = "no_new_frame_timeout"
   - 如果 source 已断开：
     run_stop_reason = "client_disconnected_during_trial"

4. summary.json 中记录：
   - no_new_frame_count
   - max_no_new_frame_gap_seconds
   - source_stop_reason
   - pump_stop_reason
   - latest_buffer_overwritten_frame_count
   - latest_buffer_last_frame_index

============================================================
四、Ctrl+C 安全退出复查
============================================================

确保以下位置按 Ctrl+C 都能安全退出并写 summary：
- 等待连接
- 等待 valid tracker
- 等待用户按 Enter 开始 calibration segment
- calibration segment 采样中
- calibration review 等待确认中
- 等待用户按 Enter 开始 trial
- trial running 中

要求：
- 不要在 except KeyboardInterrupt 中直接 re-raise，除非 summary/session 已写完。
- 统一设置：
  run_stop_reason = "keyboard_interrupt"
  phase_at_stop = 当前 phase
  session_finalized = true/false
- 如果 trial 已开始，尽量 finalize session。
- 如果 trial 未开始，也写 partial summary。

============================================================
五、summary 字段
============================================================

summary.json 增加或确认：
- run_stop_reason
- phase_at_stop
- session_finalized
- source_stop_reason
- pump_stop_reason
- stream_wait_timeout_seconds
- valid_tracker_timeout_seconds
- no_frame_timeout_seconds
- no_new_frame_count
- max_no_new_frame_gap_seconds
- latest_buffer_overwritten_frame_count
- latest_buffer_last_frame_index
- calibration_segment_time_mode

============================================================
六、测试
============================================================

更新 tests/test_live_integrated_session.py 和相关测试。

至少覆盖：
1. real live calibration mode 下 raw timestamp 很大，不会导致 segment 立刻结束。
2. stream wait timeout 会写 summary，并 run_stop_reason=stream_wait_timeout。
3. valid tracker timeout 会写 summary，并 run_stop_reason=valid_tracker_timeout。
4. trial 中 source disconnected 会退出，并写 run_stop_reason=client_disconnected_during_trial 或 no_new_frame_timeout。
5. Ctrl+C 在等待 input 阶段也能写 partial summary。
6. Ctrl+C 在 trial running 中能 finalize session。
7. summary 包含最新 buffer/pump/source stop fields。

不要依赖真实设备。
不要依赖 manus_vive_com。
运行 pytest -q。

============================================================
七、README
============================================================

README 更新 integrated runner 实机测试建议：
- 第一次实机测试建议加 --duration-seconds 60
- 建议用 --display-mode text
- 如果一直等不到 tracker，会 timeout 并写 summary
- Ctrl+C 会安全保存 partial summary/session

完成后报告：
1. 修改文件
2. 新增/更新测试
3. pytest 是否通过
4. 修复了哪些实机卡死/提前结束风险


同意这些实现假设，可以按这个范围推进。

补充确认：

1. --valid-pinch-timeout-seconds 第一版只记录到 config/summary，不新增强制 WAITING_FOR_VALID_PINCH 行为。

2. 超时或断流时：
   - run_stop_reason 写具体原因
   - phase_at_stop 写触发退出时的实际 phase
   - 安全收尾后可以进入 STOPPED
   如实现方便，可额外写 final_phase=STOPPED。

3. trial 断流原因区分：
   - 明确 client disconnected：client_disconnected_during_trial
   - source 未显式断开但长时间无新帧：no_new_frame_timeout

4. calibration 时间修复只局限在 calibration_live_runner.py 的 segment collection：
   - real live mode 只用 time.monotonic() 控制 segment duration
   - simulated mode 才用 frame_time/raw timestamp
   不改 calibration geometry、采样点语义、验证阈值。

5. 本轮只做稳定性补丁，不做 LiveTrialRunner 重构、不做 GUI、不做 haptic。
重点是避免实机第一次跑时 calibration 提前结束、等待阶段无限卡住、trial 断流无限卡住，并把 pump/latest/source 状态写入 summary。