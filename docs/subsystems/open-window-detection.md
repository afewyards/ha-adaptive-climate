# Open Window Detection

Algorithmic detection of open windows based on rapid temperature drops (Danfoss Ally algorithm). Module: `adaptive/open_window_detection.py` (`OpenWindowDetector`).

**Configuration:**
- **Domain:** `open_window_detection:` block with `temp_drop` (°C), `detection_window` (min), `pause_duration` (min), `cooldown` (min), `action` (pause/notify)
- **Entity:** Can disable with `open_window_detection: false`
- **Precedence:** Contact sensors > entity config > domain config > defaults

**Detection algorithm:** Ring buffer tracks temps over `detection_window`. Triggers when `oldest - current >= temp_drop`.

**Integration:**
- `_async_update_temp` feeds detector
- `_async_control_heating` checks pause state
- Suppression on setpoint decrease and night setback transitions

**Status attribute:** See consolidated `status` attribute below.
