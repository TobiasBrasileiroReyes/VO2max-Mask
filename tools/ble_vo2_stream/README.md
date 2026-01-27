# VO2 BLE Stream Plotter

This tool connects to the ESP32 BLE stream (`SpiroVO2-RAW`), decodes pressure/O2/CO2 packets, computes breath volume, and plots results in real time.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python vo2_ble_plot.py --weight-kg 70
```

If you omit `--weight-kg`, the script will prompt you.

## Notes
- Pressure is streamed at **200 Hz** in batched BLE packets.
- Breath volume is computed on the PC from the venturi equation (one dot per breath).
- VO2 is computed using the O2 stream (1 Hz) and smoothed ventilation.
- VO2Max is the **highest rolling average** (default 15 s). Use `--vo2-window-sec` to adjust.
- VO2Max and kcal are shown as numeric readouts (no graphs).
- Use **Start Recording** to write a CSV in `Recordings/` at the repo root.
- Breath detection can be tuned to reduce false positives:
  - `--pressure-deadband-pa` (default 0.1)
  - `--flow-start-l-s` / `--flow-end-l-s`
  - `--min-breath-l`, `--min-breath-s`
  - `--breath-start-hold-ms`, `--breath-end-hold-ms`
- The app keeps scanning and will reconnect automatically if the device appears or disconnects.
- CO2 expects an **SCD30** sensor and is plotted in % (ppm / 10000).

If you see no data, verify that your ESP32 is advertising as `SpiroVO2-RAW` and that BLE is enabled on your machine.
