(exp2) D:\11111code\exp2>python live_calibrate_table.py --use-live-stream --live-host 127.0.0.1 --live-port 8888 --out data\calibration\live_table_calibration.json
Live table-line calibration
Segments: origin, long_axis_line, width_axis_line, diagonal_line
Listening on 127.0.0.1:8888; start the sender before sampling.
[origin] Keep the calibration point still at the table origin.
Collecting 5.000 seconds; min samples=10.
[LIVE] waiting for sender before origin...
[LIVE] waiting... client_connected=0 queued=0 received=0
[LIVE] waiting... client_connected=0 queued=0 received=0
[LIVE] waiting... client_connected=0 queued=0 received=0
[LIVE] waiting... client_connected=0 queued=0 received=0
[LIVE] waiting... client_connected=1 queued=0 received=0
[LIVE] stream ready: client_connected=1 queued=6 received=6
Press Enter to start this segment...
[origin] elapsed=0.45s valid=0 tracker=0 hand=0
[origin] elapsed=0.94s valid=0 tracker=0 hand=0
[origin] elapsed=1.41s valid=0 tracker=0 hand=0
[origin] elapsed=1.88s valid=0 tracker=0 hand=0
[origin] elapsed=2.36s valid=0 tracker=0 hand=0
[origin] elapsed=2.84s valid=0 tracker=0 hand=0
[origin] elapsed=3.31s valid=0 tracker=0 hand=0
[origin] elapsed=3.78s valid=0 tracker=0 hand=0
[origin] elapsed=4.27s valid=0 tracker=0 hand=0
[origin] elapsed=4.73s valid=0 tracker=0 hand=0
[long_axis_line] Move along the long table edge / intended x direction.
Collecting 5.000 seconds; min samples=10.
[LIVE] waiting for sender before long_axis_line...
[LIVE] waiting... client_connected=1 queued=0 received=1160
[LIVE] stream ready: client_connected=1 queued=6 received=1166
Press Enter to start this segment...
[long_axis_line] elapsed=0.45s valid=0 tracker=0 hand=0
[long_axis_line] elapsed=0.92s valid=0 tracker=0 hand=0
[long_axis_line] elapsed=1.41s valid=0 tracker=0 hand=0
[long_axis_line] elapsed=1.88s valid=0 tracker=0 hand=0
[long_axis_line] elapsed=2.34s valid=0 tracker=0 hand=0
[long_axis_line] elapsed=2.81s valid=0 tracker=0 hand=0
[long_axis_line] elapsed=3.28s valid=0 tracker=0 hand=0
[long_axis_line] elapsed=3.77s valid=0 tracker=0 hand=0
[long_axis_line] elapsed=4.23s valid=0 tracker=0 hand=0
[long_axis_line] elapsed=4.70s valid=0 tracker=0 hand=0
[width_axis_line] Move along the table width direction.
Collecting 5.000 seconds; min samples=10.
[LIVE] waiting for sender before width_axis_line...
[LIVE] waiting... client_connected=1 queued=0 received=2370
[LIVE] stream ready: client_connected=1 queued=6 received=2376
Press Enter to start this segment...
[width_axis_line] elapsed=0.47s valid=0 tracker=0 hand=0
[width_axis_line] elapsed=0.94s valid=0 tracker=0 hand=0
[width_axis_line] elapsed=1.42s valid=0 tracker=0 hand=0
[width_axis_line] elapsed=1.89s valid=0 tracker=0 hand=0
[width_axis_line] elapsed=2.38s valid=0 tracker=0 hand=0
[width_axis_line] elapsed=2.84s valid=0 tracker=0 hand=0
[width_axis_line] elapsed=3.31s valid=0 tracker=0 hand=0
[width_axis_line] elapsed=3.80s valid=0 tracker=0 hand=0
[width_axis_line] elapsed=4.27s valid=0 tracker=0 hand=0
[width_axis_line] elapsed=4.75s valid=0 tracker=0 hand=0
[diagonal_line] Move along the table diagonal.
Collecting 5.000 seconds; min samples=10.
[LIVE] waiting for sender before diagonal_line...
[LIVE] waiting... client_connected=1 queued=0 received=2967
[LIVE] stream ready: client_connected=1 queued=6 received=2973
Press Enter to start this segment...
[diagonal_line] elapsed=0.47s valid=0 tracker=0 hand=0
[diagonal_line] elapsed=0.94s valid=0 tracker=0 hand=0
[diagonal_line] elapsed=1.42s valid=0 tracker=0 hand=0
[diagonal_line] elapsed=1.89s valid=0 tracker=0 hand=0
[diagonal_line] elapsed=2.36s valid=0 tracker=0 hand=0
[diagonal_line] elapsed=2.84s valid=0 tracker=0 hand=0
[diagonal_line] elapsed=3.31s valid=0 tracker=0 hand=0
[diagonal_line] elapsed=3.78s valid=0 tracker=0 hand=0
[diagonal_line] elapsed=4.27s valid=0 tracker=0 hand=0
[diagonal_line] elapsed=4.73s valid=0 tracker=0 hand=0
{
  "errors": [
    "origin: only 0 valid calibration points; need at least 10.",
    "long_axis_line: only 0 valid calibration points; need at least 10.",
    "width_axis_line: only 0 valid calibration points; need at least 10.",
    "diagonal_line: only 0 valid calibration points; need at least 10."
  ],
  "live_metrics_summary": {
    "bad_json_line_count": 0,
    "collection_mode": "live_stream",
    "dropped_frame_count": 1135,
    "invalid_sample_count": 1268,
    "parse_error_count": 1268,
    "queue_cleared_before_segment": true,
    "received_frame_count": 1268,
    "source_stop_reason": null,
    "valid_sample_count": 0
  },
  "segment_summaries": [
    {
      "adapter_error_count": 0,
      "duration_seconds": 5.0,
      "end_monotonic_time": 646520.281,
      "errors": [
        "origin: only 0 valid calibration points; need at least 10."
      ],
      "frame_end": 1159,
      "frame_start": 843,
      "hand_valid_count": 0,
      "invalid_sample_count": 317,
      "label": "origin",
      "parse_error_count": 317,
      "point_count": 0,
      "point_source": "tracker_position_world",
      "queue_cleared_before_segment": true,
      "received_frame_count": 317,
      "segment_type": "static_point",
      "start_monotonic_time": 646515.281,
      "tracker_valid_count": 0,
      "valid_sample_count": 0,
      "warnings": []
    },
    {
      "adapter_error_count": 0,
      "duration_seconds": 5.0,
      "end_monotonic_time": 646539.375,
      "errors": [
        "long_axis_line: only 0 valid calibration points; need at least 10."
      ],
      "frame_end": 2369,
      "frame_start": 2052,
      "hand_valid_count": 0,
      "invalid_sample_count": 318,
      "label": "long_axis_line",
      "parse_error_count": 318,
      "point_count": 0,
      "point_source": "tracker_position_world",
      "queue_cleared_before_segment": true,
      "received_frame_count": 318,
      "segment_type": "line",
      "start_monotonic_time": 646534.375,
      "tracker_valid_count": 0,
      "valid_sample_count": 0,
      "warnings": []
    },
    {
      "adapter_error_count": 0,
      "duration_seconds": 5.0,
      "end_monotonic_time": 646548.828,
      "errors": [
        "width_axis_line: only 0 valid calibration points; need at least 10."
      ],
      "frame_end": 2966,
      "frame_start": 2651,
      "hand_valid_count": 0,
      "invalid_sample_count": 316,
      "label": "width_axis_line",
      "parse_error_count": 316,
      "point_count": 0,
      "point_source": "tracker_position_world",
      "queue_cleared_before_segment": true,
      "received_frame_count": 316,
      "segment_type": "line",
      "start_monotonic_time": 646543.828,
      "tracker_valid_count": 0,
      "valid_sample_count": 0,
      "warnings": []
    },
    {
      "adapter_error_count": 0,
      "duration_seconds": 5.0,
      "end_monotonic_time": 646557.578,
      "errors": [
        "diagonal_line: only 0 valid calibration points; need at least 10."
      ],
      "frame_end": 3520,
      "frame_start": 3204,
      "hand_valid_count": 0,
      "invalid_sample_count": 317,
      "label": "diagonal_line",
      "parse_error_count": 317,
      "point_count": 0,
      "point_source": "tracker_position_world",
      "queue_cleared_before_segment": true,
      "received_frame_count": 317,
      "segment_type": "line",
      "start_monotonic_time": 646552.578,
      "tracker_valid_count": 0,
      "valid_sample_count": 0,
      "warnings": []
    }
  ],
  "warnings": []
}
calibration failed; errors are present, so no file was saved.