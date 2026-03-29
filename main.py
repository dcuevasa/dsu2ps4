from __future__ import annotations

import argparse
import logging
from pathlib import Path

from logic.bridge import DsuToPs4Bridge
from logic.config import load_config
from logic.helpers import clamp_int


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
		config.debug_log = True
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
		config.debug_log = True

	log_level = "DEBUG" if config.debug_log else "INFO"
	setup_logging(log_level)

	bridge = DsuToPs4Bridge(config)

	try:
		bridge.run()
	except KeyboardInterrupt:
		logging.info("Stopping DSU to PS4 bridge")
	finally:
		bridge.close()


if __name__ == "__main__":
	main()
