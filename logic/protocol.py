from __future__ import annotations

import struct
import zlib
from typing import Optional

from .models import ControllerFrame, ParsedDsuPacket, TouchPoint


MAGIC_CLIENT = b"DSUC"
MAGIC_SERVER = b"DSUS"

DSU_PROTOCOL_VERSION = 1001

MSG_PROTOCOL_VERSION = 0x100000
MSG_CONTROLLER_INFO = 0x100001
MSG_CONTROLLER_DATA = 0x100002

DSU_MIN_PACKET_SIZE = 20
DSU_DATA_PAYLOAD_SIZE = 80


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
	motion_timestamp_us = struct.unpack_from("<Q", payload, 48)[0]
	accel_x = struct.unpack_from("<f", payload, 56)[0]
	accel_y = struct.unpack_from("<f", payload, 60)[0]
	accel_z = struct.unpack_from("<f", payload, 64)[0]
	gyro_pitch = struct.unpack_from("<f", payload, 68)[0]
	gyro_yaw = struct.unpack_from("<f", payload, 72)[0]
	gyro_roll = struct.unpack_from("<f", payload, 76)[0]

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
		motion_timestamp_us=motion_timestamp_us,
		accel_x=accel_x,
		accel_y=accel_y,
		accel_z=accel_z,
		gyro_pitch=gyro_pitch,
		gyro_yaw=gyro_yaw,
		gyro_roll=gyro_roll,
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
