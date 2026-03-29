from __future__ import annotations

import ctypes
import logging
import os
from typing import Optional, Tuple

from .helpers import clamp_float, clamp_int


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
