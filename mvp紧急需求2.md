你现在要修改 exp2 的 mvp-live-visual-preview 分支，做一个明天现场前的稳定性小修补。

目标：
保证 run_live_trial_visual_preview.py 在现场采数时，即使 Ctrl+C 停止、重复运行 out-dir、matplotlib 出问题，也尽量能保存完整 summary/session。

修改要求：

1. 修复 KeyboardInterrupt 收尾：
   - 捕获 Ctrl+C 后不要重新 raise。
   - 设置 run_stop_reason = "keyboard_interrupt"。
   - 跳出主循环并继续执行正常收尾：
     - close raw file
     - close visual display
     - stop live source
     - 写 summary.json
     - 如果 write_session=true，调用 session_recorder.finalize(summary)
   - main() 最后可以返回 130 或 0 都可以，但必须保证文件写完。
   - summary.json 中保留 run_stop_reason="keyboard_interrupt"。

2. 增加 --overwrite-session：
   - 默认 false。
   - 如果 true，SessionRecorder(session_dir, overwrite=True)。
   - README 示例里提醒：现场重复使用同一个 out-dir 时可以加 --overwrite-session。
   - 不要默认删除旧数据，除非用户显式传 --overwrite-session。

   或者如果 session_dir 未显式指定，则默认生成：
out_dir/session_YYYYMMDD_HHMMSS

3. blocked_frame_count 统计口径改成：
   - stop_reason == TRACK_BLOCKED
   - 或 blocked_force_active == True

4. 如果实现成本不高，给 run_live_trial_visual_preview.py 增加 raw JSONL simulated source：
   - --raw-jsonl path
   - --simulate-live
   - 与 live TCP stream 互斥
   - 用于无设备时测试 GUI/text/session
   - 如果时间不够，这项可以先不做，但前 1-3 项必须做。

5. 测试：
   - 新增/更新测试，确认 KeyboardInterrupt 或手动停止后仍写 summary.json。
   - 测试 --overwrite-session 能覆盖已有 session。
   - 测试 blocked_frame_count 对 blocked_force_active 生效。
   - pytest -q 通过。

不要修改 BlockController / TrialController / haptic_feedback / parser / adapter 核心逻辑。