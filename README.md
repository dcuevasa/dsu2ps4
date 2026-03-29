# dsu2ps4

DSU-to-PS4 bridge for Windows.

This tool listens for incoming DSU controller data packets over UDP and maps them to a local virtual DualShock 4 using ViGEm/vgamepad.

## Features

- Subscribes to a remote DSU server by IP and slot
- Parses DSU controller packets (buttons, sticks, triggers, touch)
- Forwards input to a virtual DualShock 4 (vgamepad)
- Maps touch position to the right stick (enabled by default)
- Touchpad click mapping + touch coordinate forwarding when supported by your vgamepad build

## Requirements

1. Windows
2. ViGEmBus driver installed
3. Python 3.10+

Install Python dependencies:

```bash
pip install -r requirements.txt
```

## Configuration

Edit `config.yaml`:

- `dsu.host`: IP address of your DSU server
- `dsu.port`: DSU UDP port (default `26760`)
- `dsu.slot`: DSU controller slot (`0` to `3`)
- `runtime.recenter_on_packet_gap_sec`: recenter sticks if packet stream pauses briefly (`0` disables)
- `runtime.invert_stick_y`: invert vertical stick axis (recommended `true` for most DSU sources)
- `runtime.motion_enabled`: forward DSU gyro/accel to virtual DS4 using extended reports when available
- `runtime.motion_gyro_scale`: conversion scale from DSU gyro deg/s to DS4 short units (default `16.0`)
- `runtime.motion_accel_scale`: conversion scale from DSU accel g to DS4 short units (default `8192.0`)
- `runtime.motion_axis_preset`: axis preset for DS4 gyro output (`dualshock` or `dsu`)
- `runtime.motion_invert_yaw`: invert yaw if left/right turning is mirrored
- `runtime.motion_deadzone_dps`: single deadzone for all gyro axes in deg/s
- `runtime.motion_stream_raw_normalized`: stream normalized raw motion output to logs
- `runtime.motion_stream_interval_sec`: interval for normalized motion stream logs
- `runtime.motion_normalize_range_dps`: divisor used to normalize gyro deg/s to `[-1, 1]` in logs
- `runtime.deadzone`: radial deadzone for both sticks (`0.0` to `0.4`, good start: `0.08`-`0.15`)
- `runtime.map_dpad`: enable/disable DSU D-Pad mapping
- `runtime.suppress_dpad_when_sticks_active`: avoids menu arrow movement when sticks are moved
- `runtime.dpad_suppress_threshold`: stick threshold (raw DSU units) for D-Pad suppression
- `runtime.skip_duplicate_packet_numbers`: drop repeated packet counters (disabled by default)
- `runtime.log_stick_raw`: debug raw stick values in logs
- `runtime.log_stick_interval_sec`: interval for raw stick logs
- `touch.source_max_x` / `touch.source_max_y`: touchscreen size in pixels (for 3DS, `320` and `240`; valid coordinates are `0..size-1`)
- `touch.target_max_x` / `touch.target_max_y`: outgoing DS4 touch range
- `touch.right_stick_enabled`: map touch to right stick (default `true`); when not touching, right stick stays centered
- `touch.right_stick_invert_y`: invert Y only for touch-to-right-stick mode
- `touch.mouse_fallback_enabled`: map touch to system mouse when DS4 touch API is unavailable
- `touch.assume_touch_click`: hold mouse left click while touch is active (for DSU sources without separate touch-click)
- `touch.mouse_auto_detect_input_range`: auto-detect touch input range for mouse fallback
- `touch.mouse_input_max_x` / `touch.mouse_input_max_y`: force manual input range for mouse fallback
- `touch.mouse_monitor_index`: monitor index used by mouse fallback (`0` = primary)

For 3DS-sized touch screens, `source_max_x: 320` and `source_max_y: 240` are good defaults.

## Run

```bash
python dsu2ps4.py
```

Useful overrides:

```bash
python dsu2ps4.py --dsu-ip 192.168.1.50 --slot 0 --verbose
```

Force click while touching (mouse fallback mode):

```bash
python dsu2ps4.py --dsu-ip 192.168.1.50 --slot 0 --assume-touch-click
```

Use monitor 1 for mouse fallback:

```bash
python dsu2ps4.py --dsu-ip 192.168.1.50 --slot 0 --mouse-monitor 1
```

Disable touch-to-right-stick and use normal touch behavior:

```bash
python dsu2ps4.py --dsu-ip 192.168.1.50 --slot 0 --no-touch-right-stick
```

Joystick troubleshooting (raw packet logging):

```bash
python dsu2ps4.py --dsu-ip 192.168.1.50 --slot 0 --log-stick-raw --verbose
```

## Notes

- The script renews DSU subscription automatically.
- If no data is received for a while, all virtual controls are released.
- If your vgamepad version does not expose touch coordinate APIs, touchpad click still works.
- The touch warning means your current vgamepad build cannot inject DS4 touch coordinates. You can still play normally without touch.
- With `touch.right_stick_enabled: true`, touch replaces right stick and returns it to center when no touch is active.
- Physical sticks now follow strict DSU protocol decoding (`0..255`, center `128`) with radial deadzone and DS4-safe output mapping.
- DSU motion data (gyro + accel) is now forwarded to the virtual DS4 through extended reports when your vgamepad build supports `DS4_REPORT_EX`.
- Motion defaults now use a simplified preset system (`dualshock` by default) plus optional yaw inversion.
- With `runtime.motion_stream_raw_normalized: true`, logs continuously print normalized raw gyro output for quick axis debugging.
- Touch-to-right-stick now uses the same normalized/radial pipeline for smoother transitions.
- Right-stick touch mode now tolerates DSU sources that do not set the touch `active` flag consistently by inferring activity from touch movement, touch ID, and touch-click.
- Right-stick touch mode now auto-detects common input ranges (`320x240`, `1919x941`, `0..4095`, `0..65535`) to reduce calibration issues.
- If moving a stick also behaves like menu arrows, keep `suppress_dpad_when_sticks_active: true` (or set `map_dpad: false`).
- With `touch.mouse_fallback_enabled: true`, DSU touch controls the Windows cursor as a fallback.
- If touch does not cover full screen, set `touch.mouse_input_max_x` and `touch.mouse_input_max_y` to your real DSU range (examples: `320x240`, `1919x941`, `65535x65535`).
- Monitor order for `touch.mouse_monitor_index`: primary monitor first (`0`), then remaining monitors sorted top-left.
- If joystick movement is still noisy near center, increase `runtime.deadzone` slightly (for example from `0.10` to `0.12`).