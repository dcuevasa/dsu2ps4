# dsu2ps4

DSU-to-PS4 bridge for Windows.

This tool listens to DSU controller packets over UDP and forwards them to a local virtual DualShock 4 using ViGEm/vgamepad.

## Project Layout

- `main.py`: application entrypoint (CLI and startup flow)
- `logic/`: all runtime logic
- `logic/models.py`: dataclasses and runtime config models
- `logic/config.py`: YAML parsing and config loading
- `logic/helpers.py`: math and conversion helpers
- `logic/protocol.py`: DSU packet protocol encode/decode
- `logic/mouse.py`: Windows touch-to-mouse fallback controller
- `logic/mapper.py`: DSU frame to virtual DS4 mapping
- `logic/bridge.py`: bridge runtime loop and DSU subscription lifecycle
- `config.yaml`: editable runtime settings

## Features

- Subscribes to a DSU server by IP and slot
- Parses DSU controller packets (buttons, sticks, triggers, touch, motion)
- Forwards inputs to a virtual DualShock 4
- Supports native DS4 touch coordinate forwarding when vgamepad exposes touch APIs
- Optional touch-to-right-stick mode
- Optional touch-to-mouse fallback mode
- Gyro/accelerometer forwarding through DS4 extended reports
- Raw motion byte packing workaround for vgamepad alignment issue

## Requirements

1. Windows
2. ViGEmBus driver installed
3. Python 3.10+

Install Python dependencies:

```bash
pip install -r requirements.txt
```

## Configuration

Edit `config.yaml`.

### dsu

- `host`: DSU server IP
- `port`: DSU UDP port (default `26760`)
- `slot`: DSU slot (`0..3`)

### runtime

- `debug_log`: enable verbose runtime motion logging
- `deadzone`: radial stick deadzone (`0.0..0.4`)
- `invert_left_stick_y`: invert left/right stick Y axis mapping
- `motion_enabled`: forward gyro/accel to DS4 extended report when available
- `invert_gyro_pitch`, `invert_gyro_yaw`, `invert_gyro_roll`: gyro axis inversion toggles
- `invert_accel_x`, `invert_accel_y`, `invert_accel_z`: accel axis inversion toggles

Optional advanced runtime keys supported by the loader:

- `log_stick_raw`
- `skip_duplicate_packet_numbers`
- `subscription_interval_sec`
- `connection_timeout_sec`
- `recenter_on_packet_gap_sec`
- `log_stick_interval_sec`
- `map_dpad`
- `suppress_dpad_when_sticks_active`
- `dpad_suppress_threshold`
- `invert_stick_y` (alias of `invert_left_stick_y`)

### touch

- `source_max_x`, `source_max_y`: DSU touch input bounds
- `target_max_x`, `target_max_y`: DS4 touch target bounds
- `right_stick_enabled`: when `true`, touch controls right stick
- `invert_right_stick_y`: invert Y only for touch-to-right-stick mode
- `mouse_fallback_enabled`: use Windows mouse when DS4 touch API is unavailable
- `assume_touch_click`: hold left click while touch is active
- `mouse_monitor_index`: monitor index for mouse fallback

Optional advanced touch keys supported by the loader:

- `mouse_auto_detect_input_range`
- `mouse_input_max_x`
- `mouse_input_max_y`

## Run

Start with defaults:

```bash
python main.py
```

Useful overrides:

```bash
python main.py --dsu-ip 192.168.1.50 --slot 0 --verbose
```

Force click while touching (mouse fallback mode):

```bash
python main.py --dsu-ip 192.168.1.50 --slot 0 --assume-touch-click
```

Use monitor 1 for mouse fallback:

```bash
python main.py --dsu-ip 192.168.1.50 --slot 0 --mouse-monitor 1
```

Use native DS4 touchpad behavior (disable touch-to-right-stick):

```bash
python main.py --dsu-ip 192.168.1.50 --slot 0 --no-touch-right-stick
```

Enable raw stick debug output:

```bash
python main.py --dsu-ip 192.168.1.50 --slot 0 --log-stick-raw --verbose
```

## Notes

- The bridge renews DSU subscriptions automatically.
- If no DSU data is received for a while, virtual controls are released.
- If your vgamepad build does not expose DS4 touch APIs, touchpad click still works.
- For native PS4 touch behavior, keep `touch.right_stick_enabled: false`.
- `dsu2ps4.py` remains as a compatibility wrapper and calls `main.py`.
