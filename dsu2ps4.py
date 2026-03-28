from __future__ import annotations

import argparse
import ctypes
import inspect
import logging
import math
import os
import random
import socket
import struct
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import yaml

try:
	import vgamepad as vg
except ImportError as exc:
	raise SystemExit(
		"Missing dependency 'vgamepad'. Run: pip install -r requirements.txt"
	) from exc


MAGIC_CLIENT = b"DSUC"
MAGIC_SERVER = b"DSUS"

DSU_PROTOCOL_VERSION = 1001

MSG_PROTOCOL_VERSION = 0x100000
MSG_CONTROLLER_INFO = 0x100001
MSG_CONTROLLER_DATA = 0x100002

DSU_MIN_PACKET_SIZE = 20
DSU_DATA_PAYLOAD_SIZE = 80


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

	local_ip: str = "0.0.0.0"
	local_port: int = 0
	socket_timeout_sec: float = 0.02

	subscription_interval_sec: float = 2.0
	connection_timeout_sec: float = 5.0
	recenter_on_packet_gap_sec: float = 0.2
	deadzone: float = 0.03
	invert_stick_y: bool = True
	map_dpad: bool = True
	suppress_dpad_when_sticks_active: bool = True
	dpad_suppress_threshold: int = 20
	skip_duplicate_packet_numbers: bool = False
	log_stick_raw: bool = False
	log_stick_interval_sec: float = 0.2
	log_level: str = "INFO"

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
	touch_1: TouchPoint
	touch_2: TouchPoint


def clamp_int(value: int, low: int, high: int) -> int:
	return max(low, min(value, high))


def clamp_float(value: float, low: float, high: float) -> float:
	return max(low, min(value, high))


def as_int(value: Any, default: int) -> int:
	try:
		return int(value)
	except (TypeError, ValueError):
		return default


def as_float(value: Any, default: float) -> float:
	try:
		return float(value)
	except (TypeError, ValueError):
		return default


def as_bool(value: Any, default: bool) -> bool:
	if isinstance(value, bool):
		return value
	if isinstance(value, str):
		lowered = value.strip().lower()
		if lowered in {"1", "true", "yes", "on"}:
			return True
		if lowered in {"0", "false", "no", "off"}:
			return False
	return default


def load_config(path: Path) -> BridgeConfig:
	if path.exists():
		text = path.read_text(encoding="utf-8")
		raw = yaml.safe_load(text) or {}
	else:
		raw = {}

	dsu = raw.get("dsu", {}) if isinstance(raw, dict) else {}
	network = raw.get("network", {}) if isinstance(raw, dict) else {}
	runtime = raw.get("runtime", {}) if isinstance(raw, dict) else {}
	touch_raw = raw.get("touch", {}) if isinstance(raw, dict) else {}

	touch = TouchConfig(
		source_max_x=max(1, as_int(touch_raw.get("source_max_x"), 320)),
		source_max_y=max(1, as_int(touch_raw.get("source_max_y"), 240)),
		target_max_x=max(1, as_int(touch_raw.get("target_max_x"), 1919)),
		target_max_y=max(1, as_int(touch_raw.get("target_max_y"), 941)),
		right_stick_enabled=as_bool(touch_raw.get("right_stick_enabled"), True),
		right_stick_invert_y=as_bool(touch_raw.get("right_stick_invert_y"), False),
		mouse_fallback_enabled=as_bool(touch_raw.get("mouse_fallback_enabled"), True),
		assume_touch_click=as_bool(touch_raw.get("assume_touch_click"), False),
		mouse_auto_detect_input_range=as_bool(
			touch_raw.get("mouse_auto_detect_input_range"),
			True,
		),
		mouse_input_max_x=max(0, as_int(touch_raw.get("mouse_input_max_x"), 0)),
		mouse_input_max_y=max(0, as_int(touch_raw.get("mouse_input_max_y"), 0)),
		mouse_monitor_index=max(0, as_int(touch_raw.get("mouse_monitor_index"), 0)),
	)

	return BridgeConfig(
		dsu_host=str(dsu.get("host", "127.0.0.1")),
		dsu_port=clamp_int(as_int(dsu.get("port"), 26760), 1, 65535),
		dsu_slot=clamp_int(as_int(dsu.get("slot"), 0), 0, 3),
		local_ip=str(network.get("local_ip", "0.0.0.0")),
		local_port=clamp_int(as_int(network.get("local_port"), 0), 0, 65535),
		socket_timeout_sec=clamp_float(
			as_float(network.get("socket_timeout_sec"), 0.02),
			0.001,
			1.0,
		),
		subscription_interval_sec=clamp_float(
			as_float(runtime.get("subscription_interval_sec"), 2.0),
			0.2,
			10.0,
		),
		connection_timeout_sec=clamp_float(
			as_float(runtime.get("connection_timeout_sec"), 5.0),
			1.0,
			60.0,
		),
		recenter_on_packet_gap_sec=clamp_float(
			as_float(runtime.get("recenter_on_packet_gap_sec"), 0.2),
			0.0,
			2.0,
		),
		deadzone=clamp_float(as_float(runtime.get("deadzone"), 0.03), 0.0, 0.4),
		invert_stick_y=as_bool(runtime.get("invert_stick_y"), True),
		map_dpad=as_bool(runtime.get("map_dpad"), True),
		suppress_dpad_when_sticks_active=as_bool(
			runtime.get("suppress_dpad_when_sticks_active"),
			True,
		),
		dpad_suppress_threshold=clamp_int(
			as_int(runtime.get("dpad_suppress_threshold"), 20),
			0,
			127,
		),
		skip_duplicate_packet_numbers=as_bool(
			runtime.get("skip_duplicate_packet_numbers"),
			False,
		),
		log_stick_raw=as_bool(runtime.get("log_stick_raw"), False),
		log_stick_interval_sec=clamp_float(
			as_float(runtime.get("log_stick_interval_sec"), 0.2),
			0.02,
			5.0,
		),
		log_level=str(runtime.get("log_level", "INFO")).upper(),
		touch=touch,
	)


def build_dsu_packet(client_id: int, message_type: int, payload: bytes = b"") -> bytes:
	packet = bytearray(DSU_MIN_PACKET_SIZE + len(payload))

	packet_length = 4 + len(payload)
	struct.pack_into(
		"<4sHHIII",
		packet,
		0,
		MAGIC_CLIENT,
		DSU_PROTOCOL_VERSION,
		packet_length,
		0,
		client_id,
		message_type,
	)

	if payload:
		packet[DSU_MIN_PACKET_SIZE:] = payload

	crc = zlib.crc32(packet) & 0xFFFFFFFF
	struct.pack_into("<I", packet, 8, crc)
	return bytes(packet)


def parse_dsu_packet(raw: bytes) -> Optional[ParsedDsuPacket]:
	if len(raw) < DSU_MIN_PACKET_SIZE:
		return None

	magic, _, packet_length, received_crc, sender_id, message_type = struct.unpack_from(
		"<4sHHIII", raw, 0
	)

	if magic != MAGIC_SERVER:
		return None

	if packet_length < 4:
		return None

	expected_size = 16 + packet_length
	if expected_size > len(raw):
		return None

	packet_for_crc = bytearray(raw[:expected_size])
	packet_for_crc[8:12] = b"\x00\x00\x00\x00"
	calculated_crc = zlib.crc32(packet_for_crc) & 0xFFFFFFFF
	if calculated_crc != received_crc:
		return None

	payload = bytes(raw[DSU_MIN_PACKET_SIZE:expected_size])
	return ParsedDsuPacket(
		sender_id=sender_id,
		message_type=message_type,
		payload=payload,
	)


def parse_controller_frame(payload: bytes) -> Optional[ControllerFrame]:
	if len(payload) < DSU_DATA_PAYLOAD_SIZE:
		return None

	touch_1_active, touch_1_id, touch_1_x, touch_1_y = struct.unpack_from("<BBHH", payload, 36)
	touch_2_active, touch_2_id, touch_2_x, touch_2_y = struct.unpack_from("<BBHH", payload, 42)

	return ControllerFrame(
		slot=payload[0],
		connected=payload[11] != 0,
		packet_number=struct.unpack_from("<I", payload, 12)[0],
		buttons_1=payload[16],
		buttons_2=payload[17],
		home_pressed=payload[18] != 0,
		touchpad_click_pressed=payload[19] != 0,
		left_x=payload[20],
		left_y=payload[21],
		right_x=payload[22],
		right_y=payload[23],
		r2_analog=payload[34],
		l2_analog=payload[35],
		touch_1=TouchPoint(
			active=touch_1_active != 0,
			touch_id=touch_1_id,
			x=touch_1_x,
			y=touch_1_y,
		),
		touch_2=TouchPoint(
			active=touch_2_active != 0,
			touch_id=touch_2_id,
			x=touch_2_x,
			y=touch_2_y,
		),
	)


def dsu_axis_to_normalized(raw_value: int) -> float:
	# DSU protocol sticks are unsigned bytes: 0..255 with center at 128.
	raw = clamp_int(raw_value, 0, 255)
	if raw >= 128:
		normalized = (raw - 128) / 127.0
	else:
		normalized = (raw - 128) / 128.0
	return clamp_float(normalized, -1.0, 1.0)


def apply_stick_deadzone(x_normalized: float, y_normalized: float, deadzone: float) -> Tuple[float, float]:
	x_value = clamp_float(x_normalized, -1.0, 1.0)
	y_value = clamp_float(y_normalized, -1.0, 1.0)
	deadzone = clamp_float(deadzone, 0.0, 0.95)

	magnitude = math.hypot(x_value, y_value)
	if magnitude > 1.0:
		x_value /= magnitude
		y_value /= magnitude
		magnitude = 1.0

	if magnitude <= deadzone:
		return 0.0, 0.0

	scaled_magnitude = (magnitude - deadzone) / max(0.000001, 1.0 - deadzone)
	scale = scaled_magnitude / max(0.000001, magnitude)
	x_out = clamp_float(x_value * scale, -1.0, 1.0)
	y_out = clamp_float(y_value * scale, -1.0, 1.0)
	return x_out, y_out


def normalized_to_ds4_axis(normalized: float) -> int:
	value = (clamp_float(normalized, -1.0, 1.0) + 1.0) * 127.5
	return clamp_int(int(round(value)), 0, 255)


def is_stick_active(raw_x: int, raw_y: int, threshold: int) -> bool:
	if threshold <= 0:
		return True

	threshold_normalized = clamp_float(threshold / 127.0, 0.0, 1.0)
	x_active = abs(dsu_axis_to_normalized(raw_x)) >= threshold_normalized
	y_active = abs(dsu_axis_to_normalized(raw_y)) >= threshold_normalized
	return x_active or y_active


def scale_touch(raw_x: int, raw_y: int, cfg: TouchConfig) -> Tuple[int, int]:
	raw_x = clamp_int(raw_x, 0, cfg.source_max_x)
	raw_y = clamp_int(raw_y, 0, cfg.source_max_y)

	x_ratio = raw_x / float(cfg.source_max_x)
	y_ratio = raw_y / float(cfg.source_max_y)

	x_out = clamp_int(int(round(x_ratio * cfg.target_max_x)), 0, cfg.target_max_x)
	y_out = clamp_int(int(round(y_ratio * cfg.target_max_y)), 0, cfg.target_max_y)
	return x_out, y_out


def axis_ratio_from_touch_size(raw_value: int, touch_size: int) -> float:
	# Interpret touch_size as pixel count (e.g. 320), so valid coordinates are 0..319.
	denominator = max(1, touch_size - 1)
	clamped = clamp_int(raw_value, 0, denominator)
	return clamped / float(denominator)


def axis_ratio_from_touch_max(raw_value: int, max_value: int) -> float:
	max_inclusive = max(1, max_value)
	clamped = clamp_int(raw_value, 0, max_inclusive)
	return clamped / float(max_inclusive)


def touch_to_stick(
	raw_x: int,
	raw_y: int,
	touch_cfg: TouchConfig,
	deadzone: float = 0.0,
	invert_y: bool = False,
	input_max_x: Optional[int] = None,
	input_max_y: Optional[int] = None,
) -> Tuple[float, float]:
	if input_max_x is None:
		input_max_x = max(1, touch_cfg.source_max_x - 1)
	if input_max_y is None:
		input_max_y = max(1, touch_cfg.source_max_y - 1)

	x_ratio = axis_ratio_from_touch_max(raw_x, input_max_x)
	y_ratio = axis_ratio_from_touch_max(raw_y, input_max_y)

	x_norm = x_ratio * 2.0 - 1.0
	y_norm = y_ratio * 2.0 - 1.0

	if invert_y:
		y_norm = -y_norm

	return apply_stick_deadzone(x_norm, y_norm, deadzone)


class WindowsMouseController:
	LEFT_BUTTON_DOWN = 0x0002
	LEFT_BUTTON_UP = 0x0004
	MONITORINFOF_PRIMARY = 0x00000001
	SM_CXSCREEN = 0
	SM_CYSCREEN = 1

	def __init__(self) -> None:
		self.left_pressed = False
		self.available = os.name == "nt" and hasattr(ctypes, "windll")
		self.user32 = None
		self.monitor_index = 0
		self.monitors: list[Tuple[int, int, int, int, bool]] = []
		self.invalid_monitor_index_warned: Optional[int] = None

		if self.available:
			try:
				self.user32 = ctypes.windll.user32
				self.refresh_monitors()
			except Exception:
				self.available = False

	def set_monitor_index(self, monitor_index: int) -> None:
		self.monitor_index = max(0, int(monitor_index))
		self.refresh_monitors()

	def refresh_monitors(self) -> None:
		if not self.available or self.user32 is None:
			self.monitors = []
			return

		monitors: list[Tuple[int, int, int, int, bool]] = []

		class _Rect(ctypes.Structure):
			_fields_ = [
				("left", ctypes.c_long),
				("top", ctypes.c_long),
				("right", ctypes.c_long),
				("bottom", ctypes.c_long),
			]

		class _MonitorInfo(ctypes.Structure):
			_fields_ = [
				("cbSize", ctypes.c_ulong),
				("rcMonitor", _Rect),
				("rcWork", _Rect),
				("dwFlags", ctypes.c_ulong),
			]

		monitor_enum_proc = ctypes.WINFUNCTYPE(
			ctypes.c_int,
			ctypes.c_void_p,
			ctypes.c_void_p,
			ctypes.POINTER(_Rect),
			ctypes.c_ssize_t,
		)

		def enum_callback(hmonitor, _hdc, _rect, _lparam):
			info = _MonitorInfo()
			info.cbSize = ctypes.sizeof(_MonitorInfo)
			if self.user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
				left = int(info.rcMonitor.left)
				top = int(info.rcMonitor.top)
				right = int(info.rcMonitor.right)
				bottom = int(info.rcMonitor.bottom)
				is_primary = bool(info.dwFlags & self.MONITORINFOF_PRIMARY)
				monitors.append((left, top, right, bottom, is_primary))
			return 1

		callback = monitor_enum_proc(enum_callback)
		self.user32.EnumDisplayMonitors(0, 0, callback, 0)

		if not monitors:
			screen_w = max(1, int(self.user32.GetSystemMetrics(self.SM_CXSCREEN)))
			screen_h = max(1, int(self.user32.GetSystemMetrics(self.SM_CYSCREEN)))
			monitors.append((0, 0, screen_w, screen_h, True))

		monitors.sort(key=lambda mon: (0 if mon[4] else 1, mon[1], mon[0]))
		self.monitors = monitors

	def get_active_monitor_rect(self) -> Tuple[int, int, int, int]:
		if not self.monitors:
			self.refresh_monitors()

		if not self.monitors or self.user32 is None:
			return (0, 0, 1, 1)

		index = self.monitor_index
		if index >= len(self.monitors):
			if self.invalid_monitor_index_warned != index:
				logging.warning(
					"touch.mouse_monitor_index=%s is out of range (0..%s). Using monitor 0.",
					index,
					len(self.monitors) - 1,
				)
				self.invalid_monitor_index_warned = index
			index = 0
		else:
			self.invalid_monitor_index_warned = None

		left, top, right, bottom, _ = self.monitors[index]
		return left, top, right, bottom

	def move_normalized(self, x_norm: float, y_norm: float) -> None:
		if not self.available or self.user32 is None:
			return

		left, top, right, bottom = self.get_active_monitor_rect()
		screen_w = max(1, right - left)
		screen_h = max(1, bottom - top)

		x_norm = clamp_float(x_norm, 0.0, 1.0)
		y_norm = clamp_float(y_norm, 0.0, 1.0)

		x_pos = left + clamp_int(int(round(x_norm * (screen_w - 1))), 0, screen_w - 1)
		y_pos = top + clamp_int(int(round(y_norm * (screen_h - 1))), 0, screen_h - 1)
		self.user32.SetCursorPos(x_pos, y_pos)

	def set_left_button(self, pressed: bool) -> None:
		if not self.available or self.user32 is None:
			return

		if self.left_pressed == pressed:
			return

		flag = self.LEFT_BUTTON_DOWN if pressed else self.LEFT_BUTTON_UP
		self.user32.mouse_event(flag, 0, 0, 0, 0)
		self.left_pressed = pressed

	def release(self) -> None:
		self.set_left_button(False)


class Ds4Mapper:
	def __init__(self) -> None:
		self.pad = vg.VDS4Gamepad()
		self.button_state: Dict[str, bool] = {}
		self.special_button_state: Dict[str, bool] = {}
		self.last_dpad_state: Optional[Tuple[bool, bool, bool, bool]] = None
		self.touch_warned = False
		self.mouse_warned = False
		self.mouse_controller = WindowsMouseController()
		self.mouse_touch_active_prev = False
		self.mouse_range_mode: Optional[str] = None
		self.mouse_range_max_x = 1
		self.mouse_range_max_y = 1
		self.mouse_monitor_index_applied: Optional[int] = None
		self.prev_touch_1: Optional[TouchPoint] = None
		self.prev_touch_2: Optional[TouchPoint] = None
		self.right_stick_touch_latched = False
		self.right_stick_touch_last_seen_sec = 0.0
		self.right_stick_touch_latch_timeout_sec = 5.0
		self.last_right_stick_touch: Optional[TouchPoint] = None
		self.right_stick_range_mode: Optional[str] = None
		self.right_stick_range_max_x = 1
		self.right_stick_range_max_y = 1

		self.buttons: Dict[str, Any] = {
			"triangle": self.get_enum_member("DS4_BUTTONS", ["DS4_BUTTON_TRIANGLE"]),
			"circle": self.get_enum_member("DS4_BUTTONS", ["DS4_BUTTON_CIRCLE"]),
			"cross": self.get_enum_member("DS4_BUTTONS", ["DS4_BUTTON_CROSS"]),
			"square": self.get_enum_member("DS4_BUTTONS", ["DS4_BUTTON_SQUARE"]),
			"l1": self.get_enum_member("DS4_BUTTONS", ["DS4_BUTTON_SHOULDER_LEFT"]),
			"r1": self.get_enum_member("DS4_BUTTONS", ["DS4_BUTTON_SHOULDER_RIGHT"]),
			"l2": self.get_enum_member("DS4_BUTTONS", ["DS4_BUTTON_TRIGGER_LEFT"]),
			"r2": self.get_enum_member("DS4_BUTTONS", ["DS4_BUTTON_TRIGGER_RIGHT"]),
			"share": self.get_enum_member("DS4_BUTTONS", ["DS4_BUTTON_SHARE"]),
			"options": self.get_enum_member("DS4_BUTTONS", ["DS4_BUTTON_OPTIONS"]),
			"l3": self.get_enum_member("DS4_BUTTONS", ["DS4_BUTTON_THUMB_LEFT"]),
			"r3": self.get_enum_member("DS4_BUTTONS", ["DS4_BUTTON_THUMB_RIGHT"]),
			"ps": self.get_enum_member("DS4_BUTTONS", ["DS4_BUTTON_PS"]),
			"touchpad_click": self.get_enum_member("DS4_BUTTONS", ["DS4_BUTTON_TOUCHPAD"]),
			"dpad_up": self.get_enum_member(
				"DS4_BUTTONS",
				["DS4_BUTTON_DPAD_NORTH", "DS4_BUTTON_DPAD_UP"],
			),
			"dpad_down": self.get_enum_member(
				"DS4_BUTTONS",
				["DS4_BUTTON_DPAD_SOUTH", "DS4_BUTTON_DPAD_DOWN"],
			),
			"dpad_left": self.get_enum_member(
				"DS4_BUTTONS",
				["DS4_BUTTON_DPAD_WEST", "DS4_BUTTON_DPAD_LEFT"],
			),
			"dpad_right": self.get_enum_member(
				"DS4_BUTTONS",
				["DS4_BUTTON_DPAD_EAST", "DS4_BUTTON_DPAD_RIGHT"],
			),
		}

		self.special_buttons: Dict[str, Any] = {
			"ps": self.get_enum_member("DS4_SPECIAL_BUTTONS", ["DS4_SPECIAL_BUTTON_PS"]),
			"touchpad_click": self.get_enum_member(
				"DS4_SPECIAL_BUTTONS",
				["DS4_SPECIAL_BUTTON_TOUCHPAD"],
			),
		}

		self.directional_pad_method = getattr(self.pad, "directional_pad", None)
		self.dpad_directions = getattr(vg, "DS4_DPAD_DIRECTIONS", None)
		self.dpad_direction_values = {
			"none": self.get_enum_member("DS4_DPAD_DIRECTIONS", ["DS4_BUTTON_DPAD_NONE"]),
			"north": self.get_enum_member("DS4_DPAD_DIRECTIONS", ["DS4_BUTTON_DPAD_NORTH"]),
			"northeast": self.get_enum_member(
				"DS4_DPAD_DIRECTIONS",
				["DS4_BUTTON_DPAD_NORTHEAST"],
			),
			"east": self.get_enum_member("DS4_DPAD_DIRECTIONS", ["DS4_BUTTON_DPAD_EAST"]),
			"southeast": self.get_enum_member(
				"DS4_DPAD_DIRECTIONS",
				["DS4_BUTTON_DPAD_SOUTHEAST"],
			),
			"south": self.get_enum_member("DS4_DPAD_DIRECTIONS", ["DS4_BUTTON_DPAD_SOUTH"]),
			"southwest": self.get_enum_member(
				"DS4_DPAD_DIRECTIONS",
				["DS4_BUTTON_DPAD_SOUTHWEST"],
			),
			"west": self.get_enum_member("DS4_DPAD_DIRECTIONS", ["DS4_BUTTON_DPAD_WEST"]),
			"northwest": self.get_enum_member(
				"DS4_DPAD_DIRECTIONS",
				["DS4_BUTTON_DPAD_NORTHWEST"],
			),
		}

		self.touch_method = self.find_touch_method()
		self.touch_release_method = self.find_touch_release_method()

	@staticmethod
	def get_enum_member(enum_name: str, candidate_names: list[str]) -> Any:
		enum_obj = getattr(vg, enum_name, None)
		if enum_obj is None:
			return None

		for name in candidate_names:
			if hasattr(enum_obj, name):
				return getattr(enum_obj, name)
		return None

	def find_touch_method(self) -> Optional[Callable[..., Any]]:
		for method_name in ("touch_finger", "touch", "set_touch"):
			method = getattr(self.pad, method_name, None)
			if callable(method):
				return method
		return None

	def find_touch_release_method(self) -> Optional[Callable[..., Any]]:
		for method_name in ("release_touch_finger", "release_touch", "clear_touch"):
			method = getattr(self.pad, method_name, None)
			if callable(method):
				return method
		return None

	def set_button(self, name: str, pressed: bool) -> None:
		button_value = self.buttons.get(name)
		if button_value is None:
			return

		old_state = self.button_state.get(name)
		if old_state == pressed:
			return

		if pressed:
			self.pad.press_button(button=button_value)
		else:
			self.pad.release_button(button=button_value)

		self.button_state[name] = pressed

	@staticmethod
	def call_special_button_method(
		method: Callable[..., Any],
		button_value: Any,
		allow_no_arg: bool,
	) -> Optional[str]:
		for kwargs in (
			{"button": button_value},
			{"special_button": button_value},
		):
			try:
				method(**kwargs)
				return "ok"
			except TypeError:
				continue

		for args in ((button_value,),):
			try:
				method(*args)
				return "ok"
			except TypeError:
				continue

		if allow_no_arg:
			try:
				method()
				return "noarg"
			except TypeError:
				pass

		return None

	def set_special_button(self, name: str, pressed: bool) -> bool:
		button_value = self.special_buttons.get(name)
		press_special = getattr(self.pad, "press_special_button", None)
		release_special = getattr(self.pad, "release_special_button", None)

		if button_value is None or not callable(press_special) or not callable(release_special):
			return False

		old_state = self.special_button_state.get(name)
		if old_state == pressed:
			return True

		if pressed:
			call_result = self.call_special_button_method(
				press_special,
				button_value,
				allow_no_arg=False,
			)
		else:
			call_result = self.call_special_button_method(
				release_special,
				button_value,
				allow_no_arg=True,
			)

		if call_result is None:
			return False

		if not pressed and call_result == "noarg":
			self.special_button_state.clear()
		else:
			self.special_button_state[name] = pressed

		return True

	def set_ps_button(self, pressed: bool) -> None:
		if not self.set_special_button("ps", pressed):
			self.set_button("ps", pressed)

	def set_touchpad_click(self, pressed: bool) -> None:
		if not self.set_special_button("touchpad_click", pressed):
			self.set_button("touchpad_click", pressed)

	def set_trigger(self, side: str, value: int) -> None:
		value = clamp_int(value, 0, 255)

		trigger_method = getattr(self.pad, f"{side}_trigger", None)
		if callable(trigger_method):
			trigger_method(value=value)
			return

		trigger_float_method = getattr(self.pad, f"{side}_trigger_float", None)
		if callable(trigger_float_method):
			trigger_float_method(value_float=value / 255.0)

	def set_joystick(self, side: str, x_normalized: float, y_normalized: float) -> None:
		x_value = clamp_float(x_normalized, -1.0, 1.0)
		y_value = clamp_float(y_normalized, -1.0, 1.0)

		joystick_float_method = getattr(self.pad, f"{side}_joystick_float", None)
		if callable(joystick_float_method):
			joystick_float_method(
				x_value_float=x_value,
				y_value_float=y_value,
			)
			return

		joystick_method = getattr(self.pad, f"{side}_joystick", None)
		if callable(joystick_method):
			joystick_method(
				x_value=normalized_to_ds4_axis(x_value),
				y_value=normalized_to_ds4_axis(y_value),
			)

	def set_dpad(self, up: bool, down: bool, left: bool, right: bool) -> None:
		state = (up, down, left, right)
		if self.last_dpad_state == state:
			return

		if callable(self.directional_pad_method) and self.dpad_directions is not None:
			direction_name = "none"
			if up and right and not down and not left:
				direction_name = "northeast"
			elif down and right and not up and not left:
				direction_name = "southeast"
			elif down and left and not up and not right:
				direction_name = "southwest"
			elif up and left and not down and not right:
				direction_name = "northwest"
			elif up and not down:
				direction_name = "north"
			elif down and not up:
				direction_name = "south"
			elif left and not right:
				direction_name = "west"
			elif right and not left:
				direction_name = "east"

			direction_value = self.dpad_direction_values.get(direction_name)
			if direction_value is not None:
				self.directional_pad_method(direction=direction_value)
				self.last_dpad_state = state
				return

		self.set_button("dpad_up", up)
		self.set_button("dpad_down", down)
		self.set_button("dpad_left", left)
		self.set_button("dpad_right", right)
		self.last_dpad_state = state

	def call_touch_method(
		self,
		method: Callable[..., Any],
		finger_index: int,
		active: bool,
		touch_id: int,
		x_value: int,
		y_value: int,
	) -> bool:
		attempts_kwargs = [
			{
				"index": finger_index,
				"is_active": active,
				"touch_id": touch_id,
				"x_value": x_value,
				"y_value": y_value,
			},
			{
				"index": finger_index,
				"active": active,
				"id": touch_id,
				"x": x_value,
				"y": y_value,
			},
			{
				"finger": finger_index,
				"active": active,
				"touch_id": touch_id,
				"x": x_value,
				"y": y_value,
			},
			{
				"index": finger_index,
				"x_value": x_value,
				"y_value": y_value,
			},
			{
				"finger": finger_index,
				"x": x_value,
				"y": y_value,
			},
			{
				"x": x_value,
				"y": y_value,
			},
			{
				"x_value": x_value,
				"y_value": y_value,
			},
		]

		signature: Optional[inspect.Signature]
		try:
			signature = inspect.signature(method)
		except (TypeError, ValueError):
			signature = None

		for kwargs in attempts_kwargs:
			call_kwargs = kwargs
			if signature is not None:
				params = signature.parameters
				has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
				if not has_var_kw:
					call_kwargs = {k: v for k, v in kwargs.items() if k in params}
					required = [
						p.name
						for p in params.values()
						if p.kind
						in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
						and p.default is inspect.Signature.empty
					]
					if any(name not in call_kwargs for name in required):
						continue

			try:
				method(**call_kwargs)
				return True
			except TypeError:
				continue

		attempts_args = [
			(finger_index, active, touch_id, x_value, y_value),
			(finger_index, touch_id, x_value, y_value),
			(finger_index, x_value, y_value),
			(x_value, y_value),
		]

		for args in attempts_args:
			try:
				method(*args)
				return True
			except TypeError:
				continue

		return False

	def apply_touch_point(self, finger_index: int, touch: TouchPoint, touch_cfg: TouchConfig) -> None:
		if not touch.active and callable(self.touch_release_method):
			if self.call_touch_method(
				self.touch_release_method,
				finger_index,
				False,
				touch.touch_id,
				0,
				0,
			):
				return

		if self.touch_method is None:
			if touch.active and not self.touch_warned:
				logging.warning(
					"Touch coordinates received but this vgamepad build has no touch API. "
					"Touchpad click will still work. To send real touch coordinates, use a "
					"vgamepad build with DS4 touch support."
				)
				self.touch_warned = True
			return

		x_value, y_value = (0, 0)
		if touch.active:
			x_value, y_value = scale_touch(touch.x, touch.y, touch_cfg)

		self.call_touch_method(
			self.touch_method,
			finger_index,
			touch.active,
			touch.touch_id,
			x_value,
			y_value,
		)

	def apply_touch_mouse_fallback(self, frame: ControllerFrame, touch_cfg: TouchConfig) -> None:
		if not self.mouse_controller.available:
			if not self.mouse_warned:
				logging.warning(
					"Touch-to-mouse fallback is enabled but mouse control is unavailable on this platform."
				)
				self.mouse_warned = True
			return

		is_touch_active = frame.touch_1.active or frame.touch_2.active
		if is_touch_active and not self.mouse_touch_active_prev:
			self.reset_mouse_touch_range_state()
		elif not is_touch_active:
			self.reset_mouse_touch_range_state()

		active_touch: Optional[TouchPoint] = None
		if frame.touch_1.active:
			active_touch = frame.touch_1
		elif frame.touch_2.active:
			active_touch = frame.touch_2

		if active_touch is not None:
			input_max_x, input_max_y = self.resolve_mouse_touch_input_range(active_touch, touch_cfg)
			x_norm = clamp_int(active_touch.x, 0, input_max_x) / float(input_max_x)
			y_norm = clamp_int(active_touch.y, 0, input_max_y) / float(input_max_y)
			self.mouse_controller.move_normalized(x_norm, y_norm)

		mouse_click = frame.touchpad_click_pressed or (
			touch_cfg.assume_touch_click and is_touch_active
		)
		self.mouse_controller.set_left_button(mouse_click)
		self.mouse_touch_active_prev = is_touch_active

	def reset_mouse_touch_range_state(self) -> None:
		self.mouse_range_mode = None
		self.mouse_range_max_x = 1
		self.mouse_range_max_y = 1

	@staticmethod
	def is_over_threshold(raw_value: int, configured_max: int, ratio: float = 1.25) -> bool:
		configured_max = max(1, configured_max)
		return raw_value > int(round(configured_max * ratio))

	def resolve_mouse_touch_input_range(
		self,
		touch: TouchPoint,
		touch_cfg: TouchConfig,
	) -> Tuple[int, int]:
		if touch_cfg.mouse_input_max_x > 0 and touch_cfg.mouse_input_max_y > 0:
			self.mouse_range_mode = "manual"
			self.mouse_range_max_x = max(1, touch_cfg.mouse_input_max_x)
			self.mouse_range_max_y = max(1, touch_cfg.mouse_input_max_y)
			return self.mouse_range_max_x, self.mouse_range_max_y

		if not touch_cfg.mouse_auto_detect_input_range:
			self.mouse_range_mode = "source"
			self.mouse_range_max_x = max(1, touch_cfg.source_max_x)
			self.mouse_range_max_y = max(1, touch_cfg.source_max_y)
			return self.mouse_range_max_x, self.mouse_range_max_y

		raw_x = max(0, touch.x)
		raw_y = max(0, touch.y)
		source_over = self.is_over_threshold(raw_x, touch_cfg.source_max_x) or self.is_over_threshold(
			raw_y,
			touch_cfg.source_max_y,
		)
		target_over = self.is_over_threshold(raw_x, touch_cfg.target_max_x) or self.is_over_threshold(
			raw_y,
			touch_cfg.target_max_y,
		)

		if self.mouse_range_mode is None:
			if target_over:
				self.mouse_range_mode = "u16"
			elif source_over:
				self.mouse_range_mode = "target"
			else:
				self.mouse_range_mode = "source"
		elif self.mouse_range_mode == "source":
			if target_over:
				self.mouse_range_mode = "u16"
			elif source_over:
				self.mouse_range_mode = "target"
		elif self.mouse_range_mode == "target" and target_over:
			self.mouse_range_mode = "u16"

		if self.mouse_range_mode == "source":
			self.mouse_range_max_x = max(1, touch_cfg.source_max_x)
			self.mouse_range_max_y = max(1, touch_cfg.source_max_y)
		elif self.mouse_range_mode == "target":
			self.mouse_range_max_x = max(1, touch_cfg.target_max_x)
			self.mouse_range_max_y = max(1, touch_cfg.target_max_y)
		else:
			self.mouse_range_max_x = 65535
			self.mouse_range_max_y = 65535

		return self.mouse_range_max_x, self.mouse_range_max_y

	@staticmethod
	def clone_touch_point(touch: TouchPoint) -> TouchPoint:
		return TouchPoint(
			active=touch.active,
			touch_id=touch.touch_id,
			x=touch.x,
			y=touch.y,
		)

	@staticmethod
	def touch_delta_squared(current: TouchPoint, previous: Optional[TouchPoint]) -> int:
		if previous is None:
			return 0
		dx = current.x - previous.x
		dy = current.y - previous.y
		return dx * dx + dy * dy

	def reset_right_stick_touch_state(self) -> None:
		self.prev_touch_1 = None
		self.prev_touch_2 = None
		self.right_stick_touch_latched = False
		self.right_stick_touch_last_seen_sec = 0.0
		self.last_right_stick_touch = None
		self.right_stick_range_mode = None
		self.right_stick_range_max_x = 1
		self.right_stick_range_max_y = 1

	def resolve_right_stick_touch_input_range(
		self,
		touch: TouchPoint,
		touch_cfg: TouchConfig,
	) -> Tuple[int, int]:
		raw_x = max(0, touch.x)
		raw_y = max(0, touch.y)

		source_max_x = max(1, touch_cfg.source_max_x - 1)
		source_max_y = max(1, touch_cfg.source_max_y - 1)
		target_max_x = max(1, touch_cfg.target_max_x)
		target_max_y = max(1, touch_cfg.target_max_y)

		source_over = self.is_over_threshold(raw_x, source_max_x) or self.is_over_threshold(
			raw_y,
			source_max_y,
		)
		target_over = self.is_over_threshold(raw_x, target_max_x) or self.is_over_threshold(
			raw_y,
			target_max_y,
		)
		u12_over = self.is_over_threshold(raw_x, 4095) or self.is_over_threshold(raw_y, 4095)

		if self.right_stick_range_mode is None:
			if u12_over:
				self.right_stick_range_mode = "u16"
			elif target_over:
				self.right_stick_range_mode = "u12"
			elif source_over:
				self.right_stick_range_mode = "target"
			else:
				self.right_stick_range_mode = "source"
		elif self.right_stick_range_mode == "source":
			if u12_over:
				self.right_stick_range_mode = "u16"
			elif target_over:
				self.right_stick_range_mode = "u12"
			elif source_over:
				self.right_stick_range_mode = "target"
		elif self.right_stick_range_mode == "target":
			if u12_over:
				self.right_stick_range_mode = "u16"
			elif target_over:
				self.right_stick_range_mode = "u12"
		elif self.right_stick_range_mode == "u12" and u12_over:
			self.right_stick_range_mode = "u16"

		if self.right_stick_range_mode == "source":
			self.right_stick_range_max_x = source_max_x
			self.right_stick_range_max_y = source_max_y
		elif self.right_stick_range_mode == "target":
			self.right_stick_range_max_x = target_max_x
			self.right_stick_range_max_y = target_max_y
		elif self.right_stick_range_mode == "u12":
			self.right_stick_range_max_x = 4095
			self.right_stick_range_max_y = 4095
		else:
			self.right_stick_range_max_x = 65535
			self.right_stick_range_max_y = 65535

		return self.right_stick_range_max_x, self.right_stick_range_max_y

	def select_touch_for_right_stick(self, frame: ControllerFrame) -> Optional[TouchPoint]:
		now = time.monotonic()

		direct_touch: Optional[TouchPoint] = None
		if frame.touch_1.active:
			direct_touch = frame.touch_1
		elif frame.touch_2.active:
			direct_touch = frame.touch_2

		touch_1_moved = self.touch_delta_squared(frame.touch_1, self.prev_touch_1) > 0
		touch_2_moved = self.touch_delta_squared(frame.touch_2, self.prev_touch_2) > 0
		touch_1_has_signal = frame.touch_1.touch_id != 0 or touch_1_moved
		touch_2_has_signal = frame.touch_2.touch_id != 0 or touch_2_moved

		chosen_touch: Optional[TouchPoint] = None

		if direct_touch is not None:
			chosen_touch = direct_touch
		elif frame.touchpad_click_pressed or touch_1_has_signal or touch_2_has_signal:
			if touch_1_has_signal and not touch_2_has_signal:
				chosen_touch = frame.touch_1
			elif touch_2_has_signal and not touch_1_has_signal:
				chosen_touch = frame.touch_2
			elif touch_1_has_signal and touch_2_has_signal:
				delta_1 = self.touch_delta_squared(frame.touch_1, self.prev_touch_1)
				delta_2 = self.touch_delta_squared(frame.touch_2, self.prev_touch_2)
				chosen_touch = frame.touch_2 if delta_2 > delta_1 else frame.touch_1
			elif self.last_right_stick_touch is not None:
				chosen_touch = self.last_right_stick_touch
			else:
				chosen_touch = frame.touch_1

		if chosen_touch is not None:
			self.right_stick_touch_latched = True
			self.right_stick_touch_last_seen_sec = now
			self.last_right_stick_touch = self.clone_touch_point(chosen_touch)
		elif self.right_stick_touch_latched:
			clear_signal = (
				not frame.touchpad_click_pressed
				and not frame.touch_1.active
				and not frame.touch_2.active
				and frame.touch_1.touch_id == 0
				and frame.touch_2.touch_id == 0
				and frame.touch_1.x == 0
				and frame.touch_1.y == 0
				and frame.touch_2.x == 0
				and frame.touch_2.y == 0
			)
			timed_out = (now - self.right_stick_touch_last_seen_sec) > self.right_stick_touch_latch_timeout_sec
			if clear_signal or timed_out or self.last_right_stick_touch is None:
				self.right_stick_touch_latched = False
				self.last_right_stick_touch = None
				self.right_stick_range_mode = None
				self.right_stick_range_max_x = 1
				self.right_stick_range_max_y = 1
			else:
				chosen_touch = self.last_right_stick_touch

		self.prev_touch_1 = self.clone_touch_point(frame.touch_1)
		self.prev_touch_2 = self.clone_touch_point(frame.touch_2)

		return chosen_touch

	def apply_frame(self, frame: ControllerFrame, cfg: BridgeConfig) -> None:
		if not frame.connected:
			self.release_all()
			return

		active_touch: Optional[TouchPoint] = None
		if cfg.touch.right_stick_enabled:
			active_touch = self.select_touch_for_right_stick(frame)
		else:
			self.reset_right_stick_touch_state()

		dpad_left = (frame.buttons_1 & 0x80) != 0
		dpad_down = (frame.buttons_1 & 0x40) != 0
		dpad_right = (frame.buttons_1 & 0x20) != 0
		dpad_up = (frame.buttons_1 & 0x10) != 0

		if not cfg.map_dpad:
			dpad_left = False
			dpad_down = False
			dpad_right = False
			dpad_up = False
		elif cfg.suppress_dpad_when_sticks_active:
			stick_threshold = cfg.dpad_suppress_threshold
			stick_active = (
				is_stick_active(frame.left_x, frame.left_y, stick_threshold)
				or is_stick_active(frame.right_x, frame.right_y, stick_threshold)
			)
			if stick_active:
				dpad_left = False
				dpad_down = False
				dpad_right = False
				dpad_up = False

		self.set_dpad(up=dpad_up, down=dpad_down, left=dpad_left, right=dpad_right)

		self.set_button("options", (frame.buttons_1 & 0x08) != 0)
		self.set_button("r3", (frame.buttons_1 & 0x04) != 0)
		self.set_button("l3", (frame.buttons_1 & 0x02) != 0)
		self.set_button("share", (frame.buttons_1 & 0x01) != 0)

		self.set_button("triangle", (frame.buttons_2 & 0x80) != 0)
		self.set_button("circle", (frame.buttons_2 & 0x40) != 0)
		self.set_button("cross", (frame.buttons_2 & 0x20) != 0)
		self.set_button("square", (frame.buttons_2 & 0x10) != 0)
		self.set_button("r1", (frame.buttons_2 & 0x08) != 0)
		self.set_button("l1", (frame.buttons_2 & 0x04) != 0)

		l2_pressed = (frame.buttons_2 & 0x01) != 0 or frame.l2_analog > 0
		r2_pressed = (frame.buttons_2 & 0x02) != 0 or frame.r2_analog > 0
		self.set_button("l2", l2_pressed)
		self.set_button("r2", r2_pressed)

		self.set_ps_button(frame.home_pressed)
		self.set_touchpad_click(frame.touchpad_click_pressed)

		self.set_trigger("left", frame.l2_analog)
		self.set_trigger("right", frame.r2_analog)

		left_x = dsu_axis_to_normalized(frame.left_x)
		left_y = dsu_axis_to_normalized(frame.left_y)
		right_x = dsu_axis_to_normalized(frame.right_x)
		right_y = dsu_axis_to_normalized(frame.right_y)

		if cfg.invert_stick_y:
			left_y = -left_y
			right_y = -right_y

		left_x, left_y = apply_stick_deadzone(left_x, left_y, cfg.deadzone)
		right_x, right_y = apply_stick_deadzone(right_x, right_y, cfg.deadzone)

		if cfg.touch.right_stick_enabled:
			if active_touch is not None:
				input_max_x, input_max_y = self.resolve_right_stick_touch_input_range(active_touch, cfg.touch)
				right_x, right_y = touch_to_stick(
					active_touch.x,
					active_touch.y,
					cfg.touch,
					deadzone=cfg.deadzone,
					invert_y=cfg.touch.right_stick_invert_y,
					input_max_x=input_max_x,
					input_max_y=input_max_y,
				)
			else:
				right_x, right_y = 0, 0

		self.set_joystick("left", left_x, left_y)
		self.set_joystick("right", right_x, right_y)

		if self.mouse_monitor_index_applied != cfg.touch.mouse_monitor_index:
			self.mouse_controller.set_monitor_index(cfg.touch.mouse_monitor_index)
			self.mouse_monitor_index_applied = cfg.touch.mouse_monitor_index

		if cfg.touch.right_stick_enabled:
			self.mouse_controller.release()
		elif self.touch_method is not None:
			self.apply_touch_point(0, frame.touch_1, cfg.touch)
			self.apply_touch_point(1, frame.touch_2, cfg.touch)
			self.mouse_controller.release()
		elif cfg.touch.mouse_fallback_enabled:
			self.apply_touch_mouse_fallback(frame, cfg.touch)
		else:
			self.mouse_controller.release()
			if (frame.touch_1.active or frame.touch_2.active) and not self.touch_warned:
				logging.warning(
					"Touch coordinates received but this vgamepad build has no touch API. "
					"Enable touch.mouse_fallback_enabled for mouse emulation."
				)
				self.touch_warned = True

		self.pad.update()

	def recenter_sticks(self) -> None:
		self.set_joystick("left", 0, 0)
		self.set_joystick("right", 0, 0)
		self.pad.update()

	def release_all(self) -> None:
		reset_method = getattr(self.pad, "reset", None)
		if callable(reset_method):
			reset_method()
			self.mouse_controller.release()
			self.mouse_touch_active_prev = False
			self.reset_mouse_touch_range_state()
			self.reset_right_stick_touch_state()
			self.mouse_monitor_index_applied = None
			self.pad.update()
			self.button_state.clear()
			self.special_button_state.clear()
			self.last_dpad_state = None
			return

		for button_name in list(self.button_state.keys()):
			if self.button_state.get(button_name):
				self.set_button(button_name, False)

		for special_name in list(self.special_button_state.keys()):
			if self.special_button_state.get(special_name):
				self.set_special_button(special_name, False)

		self.set_trigger("left", 0)
		self.set_trigger("right", 0)
		self.set_joystick("left", 0, 0)
		self.set_joystick("right", 0, 0)
		self.set_dpad(up=False, down=False, left=False, right=False)
		self.mouse_controller.release()
		self.mouse_touch_active_prev = False
		self.reset_mouse_touch_range_state()
		self.reset_right_stick_touch_state()
		self.mouse_monitor_index_applied = None
		self.pad.update()


class DsuToPs4Bridge:
	def __init__(self, config: BridgeConfig) -> None:
		self.config = config
		self.server_addr = (self.config.dsu_host, self.config.dsu_port)
		self.client_id = random.getrandbits(32)

		self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		self.sock.bind((self.config.local_ip, self.config.local_port))
		self.sock.settimeout(self.config.socket_timeout_sec)

		self.mapper = Ds4Mapper()
		self.last_packet_number: Optional[int] = None
		self.last_stick_log_time = 0.0

	def send_message(self, message_type: int, payload: bytes = b"") -> None:
		packet = build_dsu_packet(self.client_id, message_type, payload)
		self.sock.sendto(packet, self.server_addr)

	def request_protocol_version(self) -> None:
		self.send_message(MSG_PROTOCOL_VERSION)

	def request_controller_info(self) -> None:
		payload = struct.pack("<iB", 1, self.config.dsu_slot)
		self.send_message(MSG_CONTROLLER_INFO, payload)

	def subscribe_controller_data(self) -> None:
		payload = struct.pack("<BB6s", 0x01, self.config.dsu_slot, b"\x00" * 6)
		self.send_message(MSG_CONTROLLER_DATA, payload)

	def handle_controller_info(self, payload: bytes) -> None:
		if len(payload) < 12:
			return

		slot = payload[0]
		state = payload[1]
		model = payload[2]
		connection_type = payload[3]
		battery = payload[10]

		logging.debug(
			"Controller info: slot=%s state=%s model=%s connection=%s battery=%s",
			slot,
			state,
			model,
			connection_type,
			battery,
		)

	def maybe_log_sticks(self, frame: ControllerFrame) -> None:
		if not self.config.log_stick_raw:
			return

		now = time.monotonic()
		if (now - self.last_stick_log_time) < self.config.log_stick_interval_sec:
			return

		self.last_stick_log_time = now
		lx_n = dsu_axis_to_normalized(frame.left_x)
		ly_n = dsu_axis_to_normalized(frame.left_y)
		rx_n = dsu_axis_to_normalized(frame.right_x)
		ry_n = dsu_axis_to_normalized(frame.right_y)

		logging.info(
			"Stick raw L(%3d,%3d) R(%3d,%3d) norm L(%+.3f,%+.3f) R(%+.3f,%+.3f) "
			"touch1(a=%d id=%d x=%d y=%d) touch2(a=%d id=%d x=%d y=%d) tclick=%d pkt=%s",
			frame.left_x,
			frame.left_y,
			frame.right_x,
			frame.right_y,
			lx_n,
			ly_n,
			rx_n,
			ry_n,
			1 if frame.touch_1.active else 0,
			frame.touch_1.touch_id,
			frame.touch_1.x,
			frame.touch_1.y,
			1 if frame.touch_2.active else 0,
			frame.touch_2.touch_id,
			frame.touch_2.x,
			frame.touch_2.y,
			1 if frame.touchpad_click_pressed else 0,
			frame.packet_number,
		)

	def run(self) -> None:
		logging.info(
			"Subscribing to DSU server %s:%s (slot %s)",
			self.config.dsu_host,
			self.config.dsu_port,
			self.config.dsu_slot,
		)
		logging.info("Creating virtual DS4 and forwarding DSU packets (including touch)")

		self.request_protocol_version()
		self.request_controller_info()
		self.subscribe_controller_data()

		last_subscription = time.monotonic()
		last_info_request = time.monotonic()
		last_data_received = time.monotonic()
		timeout_warned = False
		gap_recenter_applied = False

		while True:
			now = time.monotonic()

			if now - last_subscription >= self.config.subscription_interval_sec:
				self.subscribe_controller_data()
				last_subscription = now

			if now - last_info_request >= max(5.0, self.config.subscription_interval_sec * 2.0):
				self.request_controller_info()
				last_info_request = now

			try:
				raw_data, _ = self.sock.recvfrom(1024)
			except socket.timeout:
				if (
					not gap_recenter_applied
					and self.config.recenter_on_packet_gap_sec > 0.0
					and (now - last_data_received) >= self.config.recenter_on_packet_gap_sec
				):
					self.mapper.recenter_sticks()
					gap_recenter_applied = True

				if now - last_data_received >= self.config.connection_timeout_sec:
					if not timeout_warned:
						logging.warning(
							"No DSU controller data for %.1f seconds. Waiting for packets...",
							now - last_data_received,
						)
						timeout_warned = True
					self.mapper.release_all()
				continue
			except ConnectionResetError:
				# Windows can report ICMP port unreachable as a reset on UDP sockets.
				continue

			packet = parse_dsu_packet(raw_data)
			if packet is None:
				continue

			if packet.message_type == MSG_CONTROLLER_INFO:
				self.handle_controller_info(packet.payload)
				continue

			if packet.message_type != MSG_CONTROLLER_DATA:
				continue

			frame = parse_controller_frame(packet.payload)
			if frame is None:
				continue

			if frame.slot != self.config.dsu_slot:
				continue

			timeout_warned = False
			last_data_received = time.monotonic()
			gap_recenter_applied = False

			if (
				self.config.skip_duplicate_packet_numbers
				and self.last_packet_number is not None
				and frame.packet_number == self.last_packet_number
			):
				continue

			self.last_packet_number = frame.packet_number
			self.maybe_log_sticks(frame)
			self.mapper.apply_frame(frame, self.config)

	def close(self) -> None:
		self.mapper.release_all()
		self.sock.close()


def build_arg_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(
		description="Subscribe to a DSU server and forward controller data to a virtual PS4 pad."
	)
	parser.add_argument(
		"--config",
		default="config.yaml",
		help="Path to YAML config file (default: config.yaml)",
	)
	parser.add_argument(
		"--dsu-ip",
		default=None,
		help="Override DSU server host from config",
	)
	parser.add_argument(
		"--slot",
		type=int,
		default=None,
		help="Override DSU slot (0-3)",
	)
	parser.add_argument(
		"--verbose",
		action="store_true",
		help="Enable debug logging",
	)
	parser.add_argument(
		"--assume-touch-click",
		action="store_true",
		help="When using touch-to-mouse fallback, hold left click while touch is active.",
	)
	parser.add_argument(
		"--mouse-monitor",
		type=int,
		default=None,
		help="Monitor index for touch-to-mouse fallback (0 is primary, then top-left order).",
	)
	parser.add_argument(
		"--touch-right-stick",
		dest="touch_right_stick",
		action="store_true",
		help="Map touch position to right stick.",
	)
	parser.add_argument(
		"--no-touch-right-stick",
		dest="touch_right_stick",
		action="store_false",
		help="Disable touch-to-right-stick mapping and use normal touch handling.",
	)
	parser.set_defaults(touch_right_stick=None)
	parser.add_argument(
		"--skip-duplicate-packets",
		action="store_true",
		help="Ignore frames with repeated DSU packet_number.",
	)
	parser.add_argument(
		"--log-stick-raw",
		action="store_true",
		help="Print raw DSU stick bytes periodically in debug logs.",
	)
	return parser


def setup_logging(level_name: str) -> None:
	level = getattr(logging, level_name.upper(), logging.INFO)
	logging.basicConfig(
		level=level,
		format="%(asctime)s | %(levelname)s | %(message)s",
	)


def main() -> None:
	parser = build_arg_parser()
	args = parser.parse_args()

	config = load_config(Path(args.config))

	if args.dsu_ip:
		config.dsu_host = args.dsu_ip
	if args.slot is not None:
		config.dsu_slot = clamp_int(args.slot, 0, 3)
	if args.verbose:
		config.log_level = "DEBUG"
	if args.assume_touch_click:
		config.touch.assume_touch_click = True
	if args.mouse_monitor is not None:
		config.touch.mouse_monitor_index = max(0, args.mouse_monitor)
	if args.touch_right_stick is not None:
		config.touch.right_stick_enabled = args.touch_right_stick
	if args.skip_duplicate_packets:
		config.skip_duplicate_packet_numbers = True
	if args.log_stick_raw:
		config.log_stick_raw = True
		if not args.verbose:
			config.log_level = "DEBUG"

	setup_logging(config.log_level)

	bridge = DsuToPs4Bridge(config)

	try:
		bridge.run()
	except KeyboardInterrupt:
		logging.info("Stopping DSU to PS4 bridge")
	finally:
		bridge.close()


if __name__ == "__main__":
	main()
