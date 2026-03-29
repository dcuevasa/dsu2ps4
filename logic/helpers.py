from __future__ import annotations

import math
from typing import Any, Optional, Tuple

from .models import TouchConfig


def clamp_int(value: int, low: int, high: int) -> int:
	return max(low, min(value, high))


def clamp_float(value: float, low: float, high: float) -> float:
	return max(low, min(value, high))


def to_i16(value: float, scale: float) -> int:
	scaled = int(round(value * scale))
	return clamp_int(scaled, -32768, 32767)


def apply_axis_deadzone(value: float, deadzone: float) -> float:
	if abs(value) < max(0.0, deadzone):
		return 0.0
	return value


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
