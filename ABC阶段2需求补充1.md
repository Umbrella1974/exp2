我看完后的判断是：**代码层面已经具备 Matrix ESP32 通讯能力，也已经实现了“状态1 → reset(状态1) → 状态2”的核心机制；但还不建议直接当作完全稳定版用，至少要改一个关键逻辑点，并补一个实机 sequence smoke test。**

现在 Matrix 通讯这部分是存在的：`matrix_haptic_protocol.py` 按 `AA 55 AA 55 + length + payload + checksum` 编码 channel list，`MatrixTcpWorker` 会连接配置里的 host/port，并在 worker 线程里对每个 step 调用 `sock.sendall(step.packet)`。所以只要 `haptic_config` 里 matrix enabled、host/port 正确、ESP32 在线，电脑端是会向 ESP32 发包的。这个结论是代码层面的，不等于我已经验证过你的真实硬件输出。([GitHub][1]) ([GitHub][2])

“状态1 → reset(状态1) → 状态2”这条链路也已经基本实现。`HapticRuntime` 现在会 resolve 出单一 Matrix main output，优先级是 blocked，然后 pinch insufficient，然后 contact valid；测试里也覆盖了 `contact_valid -> reset -> pinch_insufficient`，并期待日志顺序是 `matrix_contact_valid`、`matrix_reset_before_output_change`、`matrix_pinch_insufficient`，对应 channel list 是 `[1]`、`[10]`、`[2]`。([GitHub][3]) ([GitHub][4])

worker 侧也已经把 reset 和 main 作为一个 atomic sequence 处理了。`MatrixSendTask` 包含多个 `MatrixSendStep`，`submit_sequence()` 把一整组作为一个队列任务放进去；worker `_send_task()` 按 step 顺序 `sendall`，如果 reset 发送失败，会把后续 main 标为 skipped / `reset_failed`，这符合我们之前定的边界。([GitHub][2])

但是我看到一个需要修的关键点：`_process_matrix_state()` 里调用 `_queue_matrix_main_output(context, output, phase)` 后，不管这个函数是否真的成功 queue 了 main output，都会立刻执行：

```text
self._last_matrix_send_monotonic_ms = now_ms
self._active_matrix_output = output
```

这有风险。因为 `_queue_matrix_main_output()` 可能因为 `no_channel_mapping`、`queue_full`、`missing_reset_policy=error`、invalid channel list 等原因返回失败；但当前 `_process_matrix_state()` 仍然会把 runtime 的 active output 改成新状态。这样程序内部会以为 Matrix 已经切换了，实际 ESP32 可能没有收到任何新 main output。([GitHub][3])

这个点我建议改成：只有 `_queue_matrix_main_output()` 返回 true，才更新 `_active_matrix_output` 和 `_last_matrix_send_monotonic_ms`。如果 main 没有成功 queue，active output 应该保持原状态，下一帧可以继续尝试，日志也更符合硬件实际状态。

还有一个相关细节：现在测试确认了 `_previous_matrix_output_key` 在 main queue 失败时不更新，也确认 reset 失败后会回滚 previous key；这是好的。但我建议让 Codex 再检查 `_active_matrix_output` 是否也在这些失败场景下保持/回滚一致。测试目前重点检查的是 `_previous_matrix_output_key`，不一定覆盖 `_active_matrix_output` 的一致性。([GitHub][4])

此外，现在的示例 config 已经包含 `contact_valid_feedback`、`pinch_insufficient_feedback`、`reset_before_output_change` 字段，但默认都是 disabled 或空 channel list。这个适合作为模板，但不适合直接实机验证“状态1-reset-状态2”。你需要再加一个真正可跑的 pretest config，例如 contact valid 用 `[1]`，contact_valid 的 reset 用 `[10]`，pinch insufficient 用 `[2]`，否则你启用后也可能只看到 skipped / no mapping。([GitHub][5])

我还建议补一个 Matrix sequence smoke test。现在 `run_matrix_haptic_smoke.py` 的定位是“连接 Matrix ESP32，发送一个明确 channel list，然后退出”，这可以验证单包通讯，但不能验证 reset/main 的顺序。([GitHub][6]) 你现在需要的是额外测试：

```text
send contact_valid main channels
send reset for contact_valid
send pinch_insufficient main channels
```

最好直接在 smoke log 里写三行：`main contact_valid`、`reset contact_valid`、`main pinch_insufficient`。这样你不用启动 MANUS/Vive，也能确认 ESP32 端是否按你想的顺序响应。

所以我建议下一步只做这些修改，不要再扩新功能：

```text
1. 修 _process_matrix_state：只有 queue 成功才更新 _active_matrix_output / last send time。
2. 检查 reset failure 时 _active_matrix_output 是否也和 previous_matrix_output_key 一致回滚。
3. 增加一个可实机跑的 matrix sequence example config。
4. 增加或扩展 smoke test，支持发送 main(A) -> reset(A) -> main(B)。
5. 跑 haptic 相关 pytest。
6. 再用真实 ESP32 单独跑 sequence smoke test。
```

结论：**核心机制已经在代码里了，但还差“失败状态下的内部状态一致性修补”和“实机 sequence smoke test”。** 修完这两点，再接 live trial 会稳得多。

[1]: https://github.com/Umbrella1974/exp2/blob/ABC/matrix_haptic_protocol.py "exp2/matrix_haptic_protocol.py at ABC · Umbrella1974/exp2 · GitHub"
[2]: https://github.com/Umbrella1974/exp2/blob/ABC/haptic_tcp_worker.py "exp2/haptic_tcp_worker.py at ABC · Umbrella1974/exp2 · GitHub"
[3]: https://github.com/Umbrella1974/exp2/blob/ABC/haptic_runtime.py "exp2/haptic_runtime.py at ABC · Umbrella1974/exp2 · GitHub"
[4]: https://github.com/Umbrella1974/exp2/blob/ABC/tests/test_haptic_runtime.py "exp2/tests/test_haptic_runtime.py at ABC · Umbrella1974/exp2 · GitHub"
[5]: https://github.com/Umbrella1974/exp2/blob/ABC/configs/haptic_matrix_vibration.example.json "exp2/configs/haptic_matrix_vibration.example.json at ABC · Umbrella1974/exp2 · GitHub"
[6]: https://github.com/Umbrella1974/exp2/blob/ABC/run_matrix_haptic_smoke.py "exp2/run_matrix_haptic_smoke.py at ABC · Umbrella1974/exp2 · GitHub"
