from __future__ import annotations

import logging
import random
import socket
import struct
import time
from typing import Optional

from .helpers import dsu_axis_to_normalized
from .mapper import Ds4Mapper
from .models import BridgeConfig, ControllerFrame
from .protocol import (
	MSG_CONTROLLER_DATA,
	MSG_CONTROLLER_INFO,
	MSG_PROTOCOL_VERSION,
	build_dsu_packet,
	parse_controller_frame,
	parse_dsu_packet,
)


class DsuToPs4Bridge:
	def __init__(self, config: BridgeConfig) -> None:
		self.config = config
		self.server_addr = (self.config.dsu_host, self.config.dsu_port)
		self.client_id = random.getrandbits(32)

		self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		self.sock.bind(("0.0.0.0", 0))
		self.sock.settimeout(2.0)

		self.mapper = Ds4Mapper()
		self.last_packet_number: Optional[int] = None
		self.last_stick_log_time = 0.0
		self.last_motion_stream_time = 0.0

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
			"touch1(a=%d id=%d x=%d y=%d) touch2(a=%d id=%d x=%d y=%d) "
			"gyro(%+.2f,%+.2f,%+.2f)dps accel(%+.3f,%+.3f,%+.3f)g tclick=%d pkt=%s",
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
			frame.gyro_pitch,
			frame.gyro_yaw,
			frame.gyro_roll,
			frame.accel_x,
			frame.accel_y,
			frame.accel_z,
			1 if frame.touchpad_click_pressed else 0,
			frame.packet_number,
		)

	def maybe_stream_motion_raw_normalized(self, frame: ControllerFrame) -> None:
		if not self.config.debug_log:
			return

		now = time.monotonic()
		if (now - self.last_motion_stream_time) < 0.2:  # Log max 5 times per sec
			return

		self.last_motion_stream_time = now

		logging.info(
			"Motion DSU [P,Y,R|X,Y,Z]: (%+7.2f, %+7.2f, %+7.2f) | (%+5.2f, %+5.2f, %+5.2f)",
			frame.gyro_pitch,
			frame.gyro_yaw,
			frame.gyro_roll,
			frame.accel_x,
			frame.accel_y,
			frame.accel_z,
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
			self.maybe_stream_motion_raw_normalized(frame)
			self.mapper.apply_frame(frame, self.config)

	def close(self) -> None:
		self.mapper.release_all()
		self.sock.close()
