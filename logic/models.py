from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TouchConfig:
	source_max_x: int = 320
	source_max_y: int = 240
	target_max_x: int = 1919
	target_max_y: int = 941
	right_stick_enabled: bool = True
	right_stick_invert_y: bool = False
	mouse_fallback_enabled: bool = True
	assume_touch_click: bool = False
	mouse_auto_detect_input_range: bool = True
	mouse_input_max_x: int = 0
	mouse_input_max_y: int = 0
	mouse_monitor_index: int = 0


@dataclass
class BridgeConfig:
	dsu_host: str = "127.0.0.1"
	dsu_port: int = 26760
	dsu_slot: int = 0

	debug_log: bool = False
	log_stick_raw: bool = False
	skip_duplicate_packet_numbers: bool = False
	subscription_interval_sec: float = 3.0
	connection_timeout_sec: float = 10.0
	recenter_on_packet_gap_sec: float = 0.5
	log_stick_interval_sec: float = 2.0
	map_dpad: bool = True
	suppress_dpad_when_sticks_active: bool = True
	dpad_suppress_threshold: int = 20
	deadzone: float = 0.1
	invert_stick_y: bool = True

	motion_enabled: bool = True
	invert_gyro_pitch: bool = False
	invert_gyro_yaw: bool = False
	invert_gyro_roll: bool = True
	invert_accel_x: bool = False
	invert_accel_y: bool = False
	invert_accel_z: bool = True

	touch: TouchConfig = field(default_factory=TouchConfig)


@dataclass
class ParsedDsuPacket:
	sender_id: int
	message_type: int
	payload: bytes


@dataclass
class TouchPoint:
	active: bool
	touch_id: int
	x: int
	y: int


@dataclass
class ControllerFrame:
	slot: int
	connected: bool
	packet_number: int
	buttons_1: int
	buttons_2: int
	home_pressed: bool
	touchpad_click_pressed: bool
	left_x: int
	left_y: int
	right_x: int
	right_y: int
	l2_analog: int
	r2_analog: int
	motion_timestamp_us: int
	accel_x: float
	accel_y: float
	accel_z: float
	gyro_pitch: float
	gyro_yaw: float
	gyro_roll: float
	touch_1: TouchPoint
	touch_2: TouchPoint
