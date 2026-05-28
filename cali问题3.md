[LIVE PREVIEW] MAIN=CONTACT RELEASE CONTACT=CONTACT RELEASE (OUTSIDE_BLOCK) PINCH=PINCH VALID, distance=0.079 m MOTION=FREE (FREE_VISIBLE) STOP=NONE FEEDBACK=NONE
{
  "adapter_error_count": 0,
  "blocked_frame_count": 0,
  "calibration_id": "live_table_682793bed2ce",
  "calibration_type": "formal_table_lines",
  "calibration_validation_warnings": [],
  "dropped_frame_count": 5089,
  "engine_config": {
    "block_size": [
      0.2,
      0.2,
      0.2
    ],
    "max_detach_count": 1000000000,
    "pinch_grab_threshold": 0.1,
    "pinch_release_threshold": 0.12,
    "slip_motion_threshold": 0.0001,
    "trial_timeout_seconds": 1000000000.0
  },
  "hand_invalid_frame_count": 0,
  "haptic_hardware_enabled": false,
  "is_formal_experiment": false,
  "is_live_trial": true,
  "large_delta_frame_count": 0,
  "logical_haptic_label_counts": {
    "NONE": 787
  },
  "map_id": "xoy_turn",
  "map_validation_warnings": [],
  "max_processing_latency_ms": 16.00000006146729,
  "mean_processing_latency_ms": 1.8068614993907104,
  "mean_receive_fps": 8.482990847871132,
  "mode": "live_visual_preview",
  "parse_error_count": 0,
  "pinch_valid_frame_count": 787,
  "run_stop_reason": "keyboard_interrupt",
  "scene_type": "map_config",
  "session_dir": "data\\live_trial_preview\\debug_01\\session_20260528_135220",
  "slip_active_frame_count": 0,
  "subject_id": null,
  "total_processed_frames": 786,
  "total_received_frames": 6176,
  "tracker_invalid_frame_count": 0,
  "trial_controller_started": true,
  "trial_id": "live_visual_preview",
  "warnings": [
    "MVP live visual preview: not a formal experiment runner.",
    "Logical haptic feedback is displayed and recorded; hardware haptic is disabled."
  ]
}
[LIVE PREVIEW] interrupted by user


(exp2_1) D:\11111code\exp2>python run_live_trial_visual_preview.py ^
More? --calibration-json D:\11111code\exp2\data\calibration\live_table_calibration.json ^
More? --map-config maps\examples\xoy_turn.json ^
More? --host 127.0.0.1 ^
More? --port 8888 ^
More? --out-dir data\live_trial_preview\debug_01 ^
More? --write-session ^
More? --show-visual
[LIVE PREVIEW] MAIN=CONTACT RELEASE CONTACT=CONTACT RELEASE (OUTSIDE_BLOCK) PINCH=PINCH VALID, distance=0.077 m MOTION=FREE (FREE_VISIBLE) STOP=NONE FEEDBACK=NONE
[LIVE PREVIEW] MAIN=CONTACT RELEASE CONTACT=CONTACT RELEASE (OUTSIDE_BLOCK) PINCH=PINCH VALID, distance=0.077 m MOTION=FREE (FREE_VISIBLE) STOP=NONE FEEDBACK=NONE
[LIVE PREVIEW] MAIN=CONTACT RELEASE CONTACT=CONTACT RELEASE (OUTSIDE_BLOCK) PINCH=PINCH VALID, distance=0.077 m MOTION=FREE (FREE_VISIBLE) STOP=NONE FEEDBACK=NONE
[LIVE PREVIEW] MAIN=CONTACT RELEASE CONTACT=CONTACT RELEASE (OUTSIDE_BLOCK) PINCH=PINCH VALID, distance=0.075 m MOTION=FREE (FREE_VISIBLE) STOP=NONE FEEDBACK=NONE
Exception in Tkinter callback
Traceback (most recent call last):
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\tkinter\__init__.py", line 1968, in __call__
    return self.func(*args)
           ^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\tkinter\__init__.py", line 862, in callit
    func(*args)
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\backends\_backend_tk.py", line 302, in idle_draw
    self.draw()
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\backends\backend_tkagg.py", line 10, in draw
    super().draw()
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\backends\backend_agg.py", line 382, in draw
    self.figure.draw(self.renderer)
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\artist.py", line 94, in draw_wrapper
    result = draw(artist, renderer, *args, **kwargs)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\artist.py", line 71, in draw_wrapper
    return draw(artist, renderer)
           ^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\figure.py", line 3264, in draw
    mimage._draw_list_compositing_images(
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\image.py", line 134, in _draw_list_compositing_images
    a.draw(renderer)
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\artist.py", line 71, in draw_wrapper
    return draw(artist, renderer)
           ^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\axes\_base.py", line 3190, in draw
    self._update_title_position(renderer)
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\axes\_base.py", line 3134, in _update_title_position
    ax.yaxis.get_tightbbox(renderer) # update offsetText
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\axis.py", line 1356, in get_tightbbox
    tlb1, tlb2 = self._get_ticklabel_bboxes(ticks_to_draw, renderer)
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\axis.py", line 1332, in _get_ticklabel_bboxes
    return ([tick.label1.get_window_extent(renderer)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\text.py", line 968, in get_window_extent
    with cbook._setattr_cm(fig, dpi=dpi):
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
KeyboardInterrupt
[LIVE PREVIEW] MAIN=CONTACT RELEASE CONTACT=CONTACT RELEASE (OUTSIDE_BLOCK) PINCH=PINCH VALID, distance=0.072 m MOTION=FREE (FREE_VISIBLE) STOP=NONE FEEDBACK=NONE
Exception in Tkinter callback
Traceback (most recent call last):
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\tkinter\__init__.py", line 1968, in __call__
    return self.func(*args)
           ^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\tkinter\__init__.py", line 862, in callit
    func(*args)
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\backends\_backend_tk.py", line 302, in idle_draw
    self.draw()
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\backends\backend_tkagg.py", line 10, in draw
    super().draw()
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\backends\backend_agg.py", line 382, in draw
    self.figure.draw(self.renderer)
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\artist.py", line 94, in draw_wrapper
    result = draw(artist, renderer, *args, **kwargs)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\artist.py", line 71, in draw_wrapper
    return draw(artist, renderer)
           ^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\figure.py", line 3264, in draw
    mimage._draw_list_compositing_images(
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\image.py", line 134, in _draw_list_compositing_images
    a.draw(renderer)
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\artist.py", line 71, in draw_wrapper
    return draw(artist, renderer)
           ^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\axes\_base.py", line 3226, in draw
    mimage._draw_list_compositing_images(
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\image.py", line 134, in _draw_list_compositing_images
    a.draw(renderer)
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\artist.py", line 71, in draw_wrapper
    return draw(artist, renderer)
           ^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\legend.py", line 764, in draw
    self._legend_box.draw(renderer)
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\artist.py", line 38, in draw_wrapper
    return draw(artist, renderer, *args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\offsetbox.py", line 383, in draw
    c.draw(renderer)
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\artist.py", line 38, in draw_wrapper
    return draw(artist, renderer, *args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\offsetbox.py", line 383, in draw
    c.draw(renderer)
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\artist.py", line 38, in draw_wrapper
    return draw(artist, renderer, *args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\offsetbox.py", line 383, in draw
    c.draw(renderer)
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\artist.py", line 38, in draw_wrapper
    return draw(artist, renderer, *args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\offsetbox.py", line 383, in draw
    c.draw(renderer)
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\artist.py", line 38, in draw_wrapper
    return draw(artist, renderer, *args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\offsetbox.py", line 822, in draw
    self._text.draw(renderer)
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\artist.py", line 71, in draw_wrapper
    return draw(artist, renderer)
           ^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\text.py", line 751, in draw
    with self._cm_set(text=self._get_wrapped_text()):
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\contextlib.py", line 144, in __exit__
    next(self.gen)
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\artist.py", line 1255, in _cm_set
    self.set(**orig_vals)
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\artist.py", line 146, in <lambda>
    cls.set = lambda self, **kwargs: Artist.set(self, **kwargs)
                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\artist.py", line 1243, in set
    return self._internal_update(cbook.normalize_kwargs(kwargs, self))
                                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\cbook.py", line 1797, in normalize_kwargs
    for k, v in kw.items():
                ^^^^^^^^^^
KeyboardInterrupt
Exception in Tkinter callback
Traceback (most recent call last):
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\tkinter\__init__.py", line 1968, in __call__
    return self.func(*args)
           ^^^^^^^^^^^^^^^^
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\tkinter\__init__.py", line 862, in callit
    func(*args)
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\backends\_backend_tk.py", line 302, in idle_draw
    self.draw()
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\backends\backend_tkagg.py", line 11, in draw
    self.blit()
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\backends\backend_tkagg.py", line 14, in blit
    _backend_tk.blit(self._tkphoto, self.renderer.buffer_rgba(),
  File "D:\Anaconda2020\Anaconda\envs\exp2_1\Lib\site-packages\matplotlib\backends\_backend_tk.py", line 144, in blit
    photoimage.tk.call(_blit_tcl_name, argsid)
_tkinter.TclError: pyimage3
{
  "adapter_error_count": 0,
  "blocked_frame_count": 0,
  "calibration_id": "live_table_682793bed2ce",
  "calibration_type": "formal_table_lines",
  "calibration_validation_warnings": [],
  "dropped_frame_count": 2606,
  "engine_config": {
    "block_size": [
      0.2,
      0.2,
      0.2
    ],
    "max_detach_count": 1000000000,
    "pinch_grab_threshold": 0.1,
    "pinch_release_threshold": 0.12,
    "slip_motion_threshold": 0.0001,
    "trial_timeout_seconds": 1000000000.0
  },
  "hand_invalid_frame_count": 0,
  "haptic_hardware_enabled": false,
  "is_formal_experiment": false,
  "is_live_trial": true,
  "large_delta_frame_count": 0,
  "logical_haptic_label_counts": {
    "NONE": 130
  },
  "map_id": "xoy_turn",
  "map_validation_warnings": [],
  "max_processing_latency_ms": 16.00000006146729,
  "mean_processing_latency_ms": 1.3153846160723612,
  "mean_receive_fps": 2.9924145769986676,
  "mode": "live_visual_preview",
  "parse_error_count": 0,
  "pinch_valid_frame_count": 130,
  "run_stop_reason": "keyboard_interrupt",
  "scene_type": "map_config",
  "session_dir": "data\\live_trial_preview\\debug_01\\session_20260528_135518",
  "slip_active_frame_count": 0,
  "subject_id": null,
  "total_processed_frames": 129,
  "total_received_frames": 3036,
  "tracker_invalid_frame_count": 0,
  "trial_controller_started": true,
  "trial_id": "live_visual_preview",
  "warnings": [
    "MVP live visual preview: not a formal experiment runner.",
    "Logical haptic feedback is displayed and recorded; hardware haptic is disabled."
  ]
}
[LIVE PREVIEW] interrupted by user