from __future__ import annotations

from pathlib import Path

import yaml

from .helpers import as_bool, as_float, as_int, clamp_float, clamp_int
from .models import BridgeConfig, TouchConfig


def load_config(path: Path) -> BridgeConfig:
	if path.exists():
		text = path.read_text(encoding="utf-8")
		raw = yaml.safe_load(text) or {}
	else:
		raw = {}

	dsu = raw.get("dsu", {}) if isinstance(raw, dict) else {}
	runtime = raw.get("runtime", {}) if isinstance(raw, dict) else {}
	touch_raw = raw.get("touch", {}) if isinstance(raw, dict) else {}

	touch = TouchConfig(
		source_max_x=max(1, as_int(touch_raw.get("source_max_x"), 320)),
		source_max_y=max(1, as_int(touch_raw.get("source_max_y"), 240)),
		target_max_x=max(1, as_int(touch_raw.get("target_max_x"), 1919)),
		target_max_y=max(1, as_int(touch_raw.get("target_max_y"), 941)),
		right_stick_enabled=as_bool(touch_raw.get("right_stick_enabled"), True),
		right_stick_invert_y=as_bool(touch_raw.get("invert_right_stick_y"), False),
		mouse_fallback_enabled=as_bool(touch_raw.get("mouse_fallback_enabled"), True),
		assume_touch_click=as_bool(touch_raw.get("assume_touch_click"), False),
		mouse_auto_detect_input_range=as_bool(touch_raw.get("mouse_auto_detect_input_range"), True),
		mouse_input_max_x=max(0, as_int(touch_raw.get("mouse_input_max_x"), 0)),
		mouse_input_max_y=max(0, as_int(touch_raw.get("mouse_input_max_y"), 0)),
		mouse_monitor_index=max(0, as_int(touch_raw.get("mouse_monitor_index"), 0)),
	)

	invert_stick_y = as_bool(runtime.get("invert_stick_y"), True)
	if "invert_left_stick_y" in runtime:
		invert_stick_y = as_bool(runtime.get("invert_left_stick_y"), True)

	return BridgeConfig(
		dsu_host=str(dsu.get("host", "127.0.0.1")),
		dsu_port=clamp_int(as_int(dsu.get("port"), 26760), 1, 65535),
		dsu_slot=clamp_int(as_int(dsu.get("slot"), 0), 0, 3),
		debug_log=as_bool(runtime.get("debug_log"), False),
		log_stick_raw=as_bool(runtime.get("log_stick_raw"), False),
		skip_duplicate_packet_numbers=as_bool(runtime.get("skip_duplicate_packet_numbers"), False),
		subscription_interval_sec=max(0.1, as_float(runtime.get("subscription_interval_sec"), 3.0)),
		connection_timeout_sec=max(0.1, as_float(runtime.get("connection_timeout_sec"), 10.0)),
		recenter_on_packet_gap_sec=max(0.0, as_float(runtime.get("recenter_on_packet_gap_sec"), 0.5)),
		log_stick_interval_sec=max(0.1, as_float(runtime.get("log_stick_interval_sec"), 2.0)),
		map_dpad=as_bool(runtime.get("map_dpad"), True),
		suppress_dpad_when_sticks_active=as_bool(runtime.get("suppress_dpad_when_sticks_active"), True),
		dpad_suppress_threshold=clamp_int(as_int(runtime.get("dpad_suppress_threshold"), 20), 0, 255),
		deadzone=clamp_float(as_float(runtime.get("deadzone"), 0.1), 0.0, 0.4),
		invert_stick_y=invert_stick_y,
		motion_enabled=as_bool(runtime.get("motion_enabled"), True),
		invert_gyro_pitch=as_bool(runtime.get("invert_gyro_pitch"), False),
		invert_gyro_yaw=as_bool(runtime.get("invert_gyro_yaw"), False),
		invert_gyro_roll=as_bool(runtime.get("invert_gyro_roll"), True),
		invert_accel_x=as_bool(runtime.get("invert_accel_x"), False),
		invert_accel_y=as_bool(runtime.get("invert_accel_y"), False),
		invert_accel_z=as_bool(runtime.get("invert_accel_z"), True),
		touch=touch,
	)
