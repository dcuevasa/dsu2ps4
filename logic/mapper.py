from __future__ import annotations

import ctypes
import inspect
import logging
import struct
import time
from typing import Any, Callable, Dict, Optional, Tuple

try:
	import vgamepad as vg
except ImportError as exc:
	raise SystemExit(
		"Missing dependency 'vgamepad'. Run: pip install -r requirements.txt"
	) from exc

from .helpers import (
	apply_stick_deadzone,
	clamp_float,
	clamp_int,
	dsu_axis_to_normalized,
	is_stick_active,
	normalized_to_ds4_axis,
	scale_touch,
	to_i16,
	touch_to_stick,
)
from .models import BridgeConfig, ControllerFrame, TouchConfig, TouchPoint
from .mouse import WindowsMouseController


class Ds4Mapper:
	MOTION_GYRO_SCALE = 16.0
	MOTION_ACCEL_SCALE = 8192.0

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
		self.extended_report = None
		self.extended_report_available = False
		self.motion_unavailable_warned = False
		self.motion_update_failed_warned = False
		self.motion_timestamp_counter = 0
		self.motion_mapping_logged = False

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
		self._setup_extended_report()

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

	def _setup_extended_report(self) -> None:
		update_extended = getattr(self.pad, "update_extended_report", None)
		if not callable(update_extended):
			return

		try:
			vigem_commons = vg.win.vigem_commons
			report_ex_type = getattr(vigem_commons, "DS4_REPORT_EX", None)
			if report_ex_type is None:
				return

			self.extended_report = report_ex_type()
			init_report = getattr(vigem_commons, "DS4_REPORT_INIT", None)
			if callable(init_report):
				init_report(self.extended_report.Report)
			self.extended_report_available = True
		except Exception:
			self.extended_report = None
			self.extended_report_available = False

	def _copy_basic_report_to_extended(self) -> None:
		if self.extended_report is None:
			return

		src = self.pad.report
		dst = self.extended_report.Report
		dst.bThumbLX = src.bThumbLX
		dst.bThumbLY = src.bThumbLY
		dst.bThumbRX = src.bThumbRX
		dst.bThumbRY = src.bThumbRY
		dst.wButtons = src.wButtons
		dst.bSpecial = src.bSpecial
		dst.bTriggerL = src.bTriggerL
		dst.bTriggerR = src.bTriggerR

	def _apply_motion_to_extended_report(self, frame: ControllerFrame, cfg: BridgeConfig) -> None:
		if self.extended_report is None:
			return

		report = self.extended_report.Report
		self._copy_basic_report_to_extended()

		# Direct raw mapping to emulate physical PS4 hardware
		# No deadzones to maintain 1:1 real feeling
		gyro_pitch = frame.gyro_pitch
		gyro_yaw = frame.gyro_yaw
		gyro_roll = frame.gyro_roll

		if not self.motion_mapping_logged:
			logging.info("Motion output set to RAW hardware emulation mapping.")
			self.motion_mapping_logged = True

		self.motion_timestamp_counter = (self.motion_timestamp_counter + 1) & 0xFFFF
		report.wTimestamp = self.motion_timestamp_counter

		# Directly map PS4 raw hardware inputs
		# Pitch, Yaw, Roll, mappings (as determined by DS4 mapping convention via testing).
		mapped_gyro_pitch = -gyro_pitch if cfg.invert_gyro_pitch else gyro_pitch
		mapped_gyro_yaw = -gyro_yaw if cfg.invert_gyro_yaw else gyro_yaw
		mapped_gyro_roll = -gyro_roll if cfg.invert_gyro_roll else gyro_roll

		gyro_x_i16 = to_i16(mapped_gyro_pitch, self.MOTION_GYRO_SCALE)
		gyro_y_i16 = to_i16(mapped_gyro_yaw, self.MOTION_GYRO_SCALE)
		gyro_z_i16 = to_i16(mapped_gyro_roll, self.MOTION_GYRO_SCALE)

		mapped_accel_x = -frame.accel_x if cfg.invert_accel_x else frame.accel_x
		mapped_accel_y = -frame.accel_y if cfg.invert_accel_y else frame.accel_y
		mapped_accel_z = -frame.accel_z if cfg.invert_accel_z else frame.accel_z

		# PS4 Real Accelerometer Mapping:
		# DS4 X (Right/Left) = DSU X (Right/Left)
		# DS4 Y (Up/Down) = DSU Y (Up/Down)
		# DS4 Z (Forward/Back) = DSU Z (Forward/Back)
		accel_x_i16 = to_i16(mapped_accel_x, self.MOTION_ACCEL_SCALE)
		accel_y_i16 = to_i16(mapped_accel_y, self.MOTION_ACCEL_SCALE)
		accel_z_i16 = to_i16(mapped_accel_z, self.MOTION_ACCEL_SCALE)

		# BUG FIX: vgamepad library in Python has a memory alignment padding bug in DS4_REPORT_EX.
		# Instead of starting wGyroX at offset 12 (right after wTimestamp at 9-10 & bBatteryLvl at 11),
		# Python places it at offset 14. We must use struct and ctypes to pack raw byte data
		# exactly at offset 12 so the target ViGEm device maps it perfectly 1:1, preventing crossed axes.
		packed_imu_data = struct.pack(
			"<hhhhhh",
			gyro_x_i16,
			gyro_y_i16,
			gyro_z_i16,
			accel_x_i16,
			accel_y_i16,
			accel_z_i16,
		)
		ctypes.memmove(ctypes.addressof(self.extended_report) + 12, packed_imu_data, 12)

	def push_report(self, frame: Optional[ControllerFrame], cfg: Optional[BridgeConfig]) -> None:
		if cfg is not None and cfg.motion_enabled and self.extended_report_available and frame is not None:
			try:
				self._apply_motion_to_extended_report(frame, cfg)
				self.pad.update_extended_report(self.extended_report)
				return
			except Exception as exc:
				if not self.motion_update_failed_warned:
					logging.warning(
						"DS4 motion forwarding failed. Falling back to standard report updates: %s",
						exc,
					)
					self.motion_update_failed_warned = True
				self.extended_report_available = False

		if cfg is not None and cfg.motion_enabled and not self.extended_report_available:
			if not self.motion_unavailable_warned:
				logging.warning(
					"This vgamepad build disabled DS4 extended report support. "
					"Buttons/sticks work, but motion forwarding is unavailable."
				)
				self.motion_unavailable_warned = True

		self.pad.update()

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

		self.push_report(frame, cfg)

	def recenter_sticks(self) -> None:
		self.set_joystick("left", 0, 0)
		self.set_joystick("right", 0, 0)
		self.push_report(None, None)

	def release_all(self) -> None:
		reset_method = getattr(self.pad, "reset", None)
		if callable(reset_method):
			reset_method()
			self.mouse_controller.release()
			self.mouse_touch_active_prev = False
			self.reset_mouse_touch_range_state()
			self.reset_right_stick_touch_state()
			self.mouse_monitor_index_applied = None
			self.push_report(None, None)
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
		self.push_report(None, None)
